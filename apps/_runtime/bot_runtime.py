"""
Per-bot runtime wiring — orchestrates all Phase 1-3 components with Phase 4
signal construction and execution.

Owned by apps/_runtime/.
One BotRuntime instance per bot process (alpha, beta, gamma).

Responsibilities:
1. Bootstrap: load configs, build all components, fetch instrument info
2. Tick loop: market data fetch → reconciliation → position sync →
   strategy evaluate → signal execute → state persist → heartbeat write
3. Shutdown: cancel open orders, persist final state

Execution boundary (frozen Phase 3):
  ExecutionEngine → ExecutionAdapter (abstract)
  exchange/bybit/execution.py → BybitExecutionAdapter (concrete)

Strategy boundary (frozen Phase 4):
  StrategyAdapter.evaluate() → Signal | None
  BotRuntime handles execution and all exchange-reported closes.

Bot-owned freeze/unfreeze:
  StateStore.set_frozen() is called by the bot based on risk limit state.
  Supervisor reads is_frozen from the heartbeat file only — no state writes.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from pathlib import Path
from typing import NoReturn

from core.bot_id import BotId
from core.execution.engine import (
    ExecutionEngine,
    ExecutionAdapter,
    ExecutionConfig,
    ExecutionEngineInput,
)
from core.execution.reconciliation import ReconciliationService
from core.execution.lifecycle import OrderLifecycleManager, LifecycleEvent
from core.execution.idempotency import IdempotencyManager
from core.execution.errors import RetryableExchangeError
from exchange.bybit.client import BybitAPIError
from core.market_data.fetcher import MarketDataFetcher
from core.market_data.provider import MarketDataProvider
from core.market_data.types import MarketSnapshot
from core.risk.checks import PreOrderRiskChecker
from core.risk.limits import RiskLimitChecker
from core.state.store import StateStore
from core.strategy.adapter import StrategyAdapter

from exchange.bybit.client import BybitClient
from exchange.bybit.filters import InstrumentFilterCache, InstrumentFilter
from exchange.bybit.subaccount import BybitSubaccountClient
from exchange.bybit.execution import BybitExecutionAdapter
from exchange.bybit.adapter import BybitAdapter

from schemas.candle import Timeframe
from schemas.order import OrderStatus
from schemas.position import Position, PositionSide
from schemas.signal import Signal

from apps._runtime.bot_config import BotConfig, RuntimeConfig, load_bot_config, load_runtime_config
from apps._runtime.heartbeat import HeartbeatWriter


_BOT_ID_MAP: dict[str, BotId] = {
    "alpha_bot": BotId.ALPHA,
    "beta_bot": BotId.BETA,
    "gamma_bot": BotId.GAMMA,
}


class BotRuntime:
    """
    Per-bot runtime orchestrator.

    Tick loop order (every tick_interval_seconds):
      1. ReconciliationService.poll()        — sync order state from Bybit
      2. MarketDataProvider.get_snapshot()    — current market data
      3. _sync_positions()                   — detect fills, stops, TPs from Bybit
      4. StrategyAdapter.evaluate()           — produce Signal | None
      5. _execute_signal()                   — submit signal through ExecutionEngine
      6. HeartbeatWriter.write()             — aliveness signal
    """

    def __init__(
        self,
        bot_config: BotConfig,
        runtime_config: RuntimeConfig,
        strategy_adapter: StrategyAdapter,
    ):
        self._cfg = bot_config
        self._rt = runtime_config
        self._adapter_strat = strategy_adapter
        self._running = False
        self._instrument: InstrumentFilter | None = None

        # Bot ID enum
        self._bot_id = _BOT_ID_MAP.get(bot_config.bot_id)
        if self._bot_id is None:
            raise ValueError(f"Unknown bot_id: {bot_config.bot_id}")

        # Exchange clients
        self._subaccount_client = BybitSubaccountClient(
            api_key=bot_config.api_key,
            api_secret=bot_config.api_secret,
            subaccount_name=bot_config.subaccount_name,
        )
        self._bybit_client = self._subaccount_client.client

        # Instrument filter
        self._instrument_cache = InstrumentFilterCache(
            client=self._bybit_client,
            ttl_seconds=runtime_config.instrument_cache_ttl_seconds,
        )
        self._fetch_instrument_info(bot_config.symbol)

        # Market data
        self._fetcher = MarketDataFetcher(self._subaccount_client)
        self._market_data = MarketDataProvider(self._fetcher)

        # State store
        self._store = StateStore(
            bot_id=bot_config.bot_id,
            state_dir=runtime_config.state_dir / bot_config.bot_id,
        )
        self._store.load_from_snapshot()

        # Risk components
        self._risk_checker = RiskLimitChecker(
            bot_id=self._bot_id,
            capital_usdt=bot_config.capital_usdt,
        )
        risk_state = self._store.get_state().risk_limit_state
        if risk_state is not None:
            self._risk_checker.load_state(risk_state)
            self._store.update_risk_limit_state(risk_state)

        # Execution components
        self._pre_risk_checker = PreOrderRiskChecker(filter_cache=self._instrument_cache)
        self._lifecycle = OrderLifecycleManager()
        self._idempotency = IdempotencyManager()
        self._exec_adapter: ExecutionAdapter = BybitExecutionAdapter(
            subaccount_client=self._subaccount_client,
        )
        self._execution_engine = ExecutionEngine(
            config=ExecutionConfig(
                bot_id=self._bot_id,
                symbol=bot_config.symbol,
                capital_usdt=bot_config.capital_usdt,
                risk_per_trade_pct=bot_config.risk_per_trade_pct,
                risk_amount_usdt=bot_config.risk_amount_usdt,
                max_spread_bps=5.0,
            ),
            adapter=self._exec_adapter,
            risk_checker=self._pre_risk_checker,
            lifecycle_mgr=self._lifecycle,
            idempotency_mgr=self._idempotency,
            instrument_lot_size=self._instrument.lot_size,
            instrument_min_order_qty=self._instrument.min_order_qty,
        )

        # Reconciliation service
        self._reconciliation = ReconciliationService(
            adapter=self._exec_adapter,
            symbol=bot_config.symbol,
        )
        self._register_pending_orders()

        # Heartbeat
        self._heartbeat = HeartbeatWriter(
            bot_id=bot_config.bot_id,
            heartbeat_file=(
                runtime_config.heartbeat_dir / f"{bot_config.bot_id}_heartbeat.json"
            ),
        )

        # Snapshot on state change (async)
        self._store.on_state_change(self._deferred_snapshot)

    # ---- Startup ----

    def _fetch_instrument_info(self, symbol: str) -> None:
        """Fetch instrument filters. Fatal if Bybit is unreachable."""
        try:
            self._instrument = self._instrument_cache.fetch_filter(symbol, force=True)
        except Exception as e:
            print(f"[{self._cfg.bot_id}] FATAL: could not fetch instrument info "
                  f"for {symbol}: {e}. Is Bybit reachable?")
            sys.exit(1)

    def _register_pending_orders(self) -> None:
        """
        Register all client_order_ids that still need polling after restart.

        Covers:
          - pending_orders: non-terminal orders still active on Bybit
          - active_signals: the entry order's client_order_id for each open position.
            Even if the entry order is already filled, Bybit still attributes
            stop/TP fills to it — registering allows startup_reconcile to detect
            close events that happened before restart.
        """
        state = self._store.get_state()
        for order in state.pending_orders:
            if order.client_order_id:
                self._reconciliation.register_order(order.client_order_id, order.status)
        for sig in state.active_signals:
            if sig.client_order_id:
                self._reconciliation.register_order(sig.client_order_id, OrderStatus.SUBMITTED)

    # ---- Tick loop ----

    def run(self) -> NoReturn:
        """
        Start the bot's tick loop.
        Runs until shutdown() is called (SIGINT/SIGTERM).
        """
        self._running = True
        print(f"[{self._cfg.bot_id}] Tick loop started — {self._rt.tick_interval_seconds}s interval")

        # Startup reconciliation
        self._startup_reconcile()

        while self._running:
            try:
                self._tick()
            except Exception as e:
                print(f"[{self._cfg.bot_id}] Tick error: {e}")
            finally:
                self._heartbeat.write(
                    status="frozen" if self._store.get_state().is_frozen else "running",
                    is_frozen=self._store.get_state().is_frozen,
                )
                time.sleep(self._rt.tick_interval_seconds)

        self._shutdown()

    def _tick(self) -> None:
        """One tick of the bot loop."""
        symbol = self._cfg.symbol

        # 0. Pre-tick hard skip: if cooldown active, skip REST work entirely
        if self._subaccount_client.governor.should_skip_tick():
            self._heartbeat.write(
                status="frozen" if self._store.get_state().is_frozen else "running",
                is_frozen=self._store.get_state().is_frozen,
            )
            time.sleep(self._rt.tick_interval_seconds)
            return

        try:
            # 1. Reconciliation
            self._reconcile_tick()

            # 2. Market data snapshot
            snapshot = self._market_data.get_snapshot(symbol)

            # 3. Position sync — detect fills, stops, TPs from Bybit
            self._sync_positions(symbol, snapshot)

            # 4. Strategy evaluation
            timeframes = [
                Timeframe.M1, Timeframe.M5, Timeframe.M15,
                Timeframe.H1, Timeframe.H4, Timeframe.D1,
            ]
            candles = self._market_data.get_multi_timeframe_candles(
                symbol, timeframes, count=50
            )

            signal = self._adapter_strat.evaluate(
                symbol,
                candles,
                snapshot,
                previous_bias=self._store.get_state().current_bias,
            )

            # 5. Execute new signal
            if signal is not None:
                self._execute_signal(signal, snapshot)

            # Clean tick: advance post-cooldown recovery counter
            self._subaccount_client.governor.mark_tick_clean()

        except RetryableExchangeError as e:
            if e.code == 10006:
                # Governor already updated by client.py — just abort tick
                self._heartbeat.write(
                    status="frozen" if self._store.get_state().is_frozen else "running",
                    is_frozen=self._store.get_state().is_frozen,
                )
                time.sleep(self._rt.tick_interval_seconds)
                return
            raise

        except BybitAPIError as e:
            if e.code == 10006:
                # Market-data path: Governor already updated by client.py — just abort tick
                self._heartbeat.write(
                    status="frozen" if self._store.get_state().is_frozen else "running",
                    is_frozen=self._store.get_state().is_frozen,
                )
                time.sleep(self._rt.tick_interval_seconds)
                return
            raise

    # ---- Reconciliation ----

    def _startup_reconcile(self) -> None:
        """Reconcile known pending orders against Bybit on startup."""
        state = self._store.get_state()
        known_cids = [
            o.client_order_id for o in state.pending_orders
            if o.client_order_id
        ]
        known_cids.extend(
            sig.client_order_id for sig in state.active_signals
            if sig.client_order_id
        )
        if not known_cids:
            return

        findings = self._reconciliation.startup_reconcile(known_cids)
        for f in findings:
            self._apply_reconciliation_finding(f)

    def _reconcile_tick(self) -> None:
        """
        Poll Bybit for status of all known non-terminal orders.

        Adaptive poll interval:
        - No active orders and no local position → 60s interval
        - Active orders or open local position → 5s interval
        """
        local_state = self._store.get_state()
        has_active_position = local_state.open_position is not None
        self._reconciliation._set_adaptive_interval(has_active_position)

        findings = self._reconciliation.poll()
        for f in findings:
            self._apply_reconciliation_finding(f)

    def _apply_reconciliation_finding(self, f) -> None:
        """Apply one ReconciliationFinding to local state."""
        cid = f.client_order_id

        if f.action == "none":
            return

        if f.action == "update_state":
            exchange_status = f.exchange_status

            if exchange_status == OrderStatus.FILLED:
                self._on_order_filled(cid)
                self._lifecycle.apply(cid, LifecycleEvent.FILLED)

            elif exchange_status == OrderStatus.PARTIALLY_FILLED:
                self._lifecycle.apply(cid, LifecycleEvent.PARTIALLY_FILLED)

            elif exchange_status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
                event = (
                    LifecycleEvent.CANCELLED
                    if exchange_status == OrderStatus.CANCELLED
                    else LifecycleEvent.REJECTED
                )
                self._on_order_terminal(cid)
                self._lifecycle.apply(cid, event)

        elif f.action == "flag":
            # Order not found on Bybit — treat as terminal
            self._on_order_terminal(cid, reason="not_found_on_exchange")

    def _on_order_filled(self, client_order_id: str) -> None:
        """Handle an order confirmed filled by Bybit (exchange truth)."""
        lifecycle_record = None
        for record in self._lifecycle.active_orders():
            if record.client_order_id == client_order_id:
                lifecycle_record = record
                break

        if lifecycle_record is None:
            print(f"[{self._cfg.bot_id}] Filled order {client_order_id} not in lifecycle. "
                  "May be from previous session. Skipping.")
            return

        # Fetch position from Bybit to get entry price
        exchange_positions = self._exec_adapter.get_positions(self._cfg.symbol)
        if not exchange_positions:
            print(f"[{self._cfg.bot_id}] Order {client_order_id} filled but no position "
                  "on exchange. Will re-sync on next tick.")
            return

        position = BybitAdapter.parse_position(exchange_positions[0])
        if position is None:
            return

        # Link position to signal using the lifecycle record
        position.signal_id = lifecycle_record.signal_id
        position.bot_id = self._cfg.bot_id
        position.subaccount_name = self._cfg.subaccount_name

        # Mark signal entered
        self._store.mark_signal_entered(
            signal_id=lifecycle_record.signal_id,
            order_id=client_order_id,
            filled_price=position.entry_price or 0.0,
        )

        # Create open position
        self._store.set_open_position(position)

        print(f"[{self._cfg.bot_id}] Position opened: {position.side.value} "
              f"qty={position.qty} @ {position.entry_price}")

    def _on_order_terminal(self, client_order_id: str, reason: str = "terminal") -> None:
        """Handle a non-success terminal order (cancelled/rejected/not found)."""
        lifecycle_record = None
        for record in self._lifecycle.active_orders():
            if record.client_order_id == client_order_id:
                lifecycle_record = record
                break

        if lifecycle_record is None:
            return

        self._store.expire_signal(
            signal_id=lifecycle_record.signal_id,
            reason=f"order_{reason}: {client_order_id}",
        )

    # ---- Position sync ----

    def _sync_positions(self, symbol: str, snapshot: MarketSnapshot) -> None:
        """
        Sync local position state with Bybit.

        Phase A optimization:
        - If we already have a local position, trust it — no REST call needed.
        - REST call only when local position is None (cold recovery from Bybit).

        Exchange truth is authoritative when local position is None.
        If a position exists on Bybit but not locally, recover it.
        If local position exists and exchange has it too, trust local (tick-by-tick
        position QTY changes are handled by the reconciliation flow).
        """
        local_state = self._store.get_state()
        local_position = local_state.open_position

        # Phase A: skip REST call if we already track a position locally
        if local_position is not None:
            return

        # No local position — authoritative sync from Bybit (cold recovery)
        exchange_positions = self._exec_adapter.get_positions(symbol)

        if not exchange_positions:
            # No position anywhere — nothing to recover
            return

        position = BybitAdapter.parse_position(exchange_positions[0])
        if position is None:
            return

        # Position exists on Bybit but not in local state — recover it
        recovered_signal_id = self._recover_signal_id_for_position(symbol, position)
        position.signal_id = recovered_signal_id
        position.bot_id = self._cfg.bot_id
        position.subaccount_name = self._cfg.subaccount_name
        self._store.set_open_position(position)

    def _close_position_from_exchange(self, position: Position) -> None:
        """
        Close a position that is no longer reported by Bybit.

        close_reason: determined entirely in runtime (this file).
          - If exchange provides an explicit close reason → use it.
          - Otherwise → use the neutral runtime fallback "unknown_close_reason".
          - No heuristic threshold inference.

        realized_pnl: sourced directly from Bybit position data (separate field, not used
          to determine close_reason).

        exit_price: best available price at which the position was closed.
        """
        # Find the active signal linked to this position
        state = self._store.get_state()
        signal_id = position.signal_id or "recovery"

        active_signal = None
        for sig in state.active_signals:
            if sig.signal_id == signal_id:
                active_signal = sig
                break

        # Use exchange-reported realized PnL directly. Never used to infer close_reason.
        realized_pnl = position.realized_pnl if position.realized_pnl is not None else 0.0

        # Close reason: exchange truth when available, neutral fallback otherwise.
        # Do NOT infer from threshold proximity or PnL sign.
        # Any explicit exchange-provided reason field maps here.
        # Phase 4: no explicit close_reason field in Position schema — use neutral fallback.
        close_reason = "unknown_close_reason"

        # Exit price: best available reference (entry price as fallback)
        exit_price = position.entry_price or 0.0

        # Trade result classification from realized PnL
        if realized_pnl > 0:
            result = "win"
        elif realized_pnl < 0:
            result = "loss"
        else:
            result = "breakeven"

        # Apply to risk limit checker
        self._risk_checker.apply_trade_result(realized_pnl)
        self._store.update_risk_limit_state(self._risk_checker.state)

        # Close signal and clear position
        self._store.mark_signal_closed(
            signal_id=signal_id,
            exit_price=exit_price,
            exit_reason=close_reason,
            realized_pnl=realized_pnl,
            result=result,
        )
        self._store.set_open_position(None)

        print(f"[{self._cfg.bot_id}] Position closed: {close_reason} "
              f"pnl={realized_pnl:.4f} result={result}")

    def _recover_signal_id_for_position(
        self, symbol: str, exchange_position: Position
    ) -> str | None:
        """
        Recover the signal_id linked to an open position by querying Bybit.

        Queries Bybit for open orders, matches by symbol and quantity, then
        looks up the corresponding lifecycle record to retrieve signal_id.

        Returns the signal_id if found, None otherwise.
        The caller uses None to fall through to the recovery path.
        """
        open_orders = self._exec_adapter.get_open_orders(symbol)
        for order in open_orders:
            if (
                order.symbol == symbol
                and order.qty is not None
                and exchange_position.qty is not None
                and abs(order.qty - exchange_position.qty) < 1e-6
            ):
                # Found a matching Bybit order — look up its lifecycle record
                if order.client_order_id:
                    record = self._lifecycle.get(order.client_order_id)
                    if record is not None:
                        return record.signal_id
                # Also try finding by exchange order_id
                if order.order_id:
                    record = self._lifecycle.find_by_order_id(order.order_id)
                    if record is not None:
                        return record.signal_id
        return None

    # ---- Signal execution ----

    def _execute_signal(self, signal: Signal, snapshot: MarketSnapshot) -> None:
        """
        Execute one Signal through the ExecutionEngine.

        All risk checks are owned by ExecutionEngine.
        On acceptance, signal is registered with the ReconciliationService.
        """
        # Update ATR buffer on signal if not already set
        if signal.atr_buffer_at_entry is None and snapshot.atr_14_m1 is not None:
            signal.atr_buffer_at_entry = snapshot.atr_14_m1

        inp = ExecutionEngineInput(
            signal=signal,
            market_snapshot=snapshot,
            is_frozen=self._store.get_state().is_frozen,
            lot_size=self._instrument.lot_size,
            min_order_qty=self._instrument.min_order_qty,
            risk_limit_checker=self._risk_checker,
        )

        outcome = self._execution_engine.execute_signal(inp)

        if outcome.accepted:
            self._store.add_signal(signal)
            if outcome.client_order_id:
                self._reconciliation.register_order(
                    outcome.client_order_id,
                    OrderStatus.SUBMITTED,
                )
            print(f"[{self._cfg.bot_id}] Signal accepted: {signal.signal_id} "
                  f"order_id={outcome.order_id}")
        else:
            print(f"[{self._cfg.bot_id}] Signal rejected: {signal.signal_id} — "
                  f"{outcome.rejection_type.value}: {outcome.rejection_reason}")

    # ---- Shutdown ----

    def shutdown(self) -> None:
        """Initiate graceful shutdown. Sets _running=False; next tick calls _shutdown()."""
        print(f"[{self._cfg.bot_id}] Shutdown requested")
        self._running = False

    def _shutdown(self) -> None:
        """Graceful shutdown: persist state, write stopped heartbeat."""
        print(f"[{self._cfg.bot_id}] Persisting final state...")
        self._store.snapshot()

        self._heartbeat.write(status="stopped", is_frozen=False)

        print(f"[{self._cfg.bot_id}] Shutdown complete.")
        sys.exit(0)

    # ---- Deferred snapshot ----

    def _deferred_snapshot(self, state) -> None:
        """Emit state change event; snapshot asynchronously."""
        t = threading.Thread(target=self._store.snapshot, daemon=True)
        t.start()
