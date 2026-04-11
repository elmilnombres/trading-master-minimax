"""
Execution engine — orchestrates the full execution path for one signal.

Owned by core/execution/.
Validates signal inputs, runs risk checks, sizes position, and submits
orders through the ExecutionAdapter.

No strategy logic. No exchange-specific code (delegated to ExecutionAdapter).

Execution boundary:
  ExecutionEngine → ExecutionAdapter (abstract protocol)
  exchange/bybit/execution.py → implements ExecutionAdapter (Bybit concrete)
"""

from dataclasses import dataclass
from typing import Protocol

from schemas.execution import (
    ExecutionResult,
    ExecutionErrorType,
    OrderRequest,
    PreOrderRiskCheck,
)
from schemas.signal import Signal
from schemas.order import OrderSide, Order
from core.bot_id import BotId
from core.market_data.types import MarketSnapshot

from core.risk.sizing import PositionSizer, SizingInput, SizingResult
from core.risk.checks import PreOrderRiskChecker, PreOrderRiskCheckInput
from core.risk.limits import RiskLimitChecker
from core.execution.idempotency import IdempotencyManager, build_client_order_id
from core.execution.lifecycle import OrderLifecycleManager, LifecycleEvent
from core.execution.errors import RetryableExchangeError


class ExecutionAdapter(Protocol):
    """
    Abstract interface consumed by ExecutionEngine.

    Implemented by exchange/bybit/execution.py.
    """

    def submit_order(self, req: OrderRequest) -> ExecutionResult: ...
    def cancel_order(self, order_id: str, symbol: str) -> ExecutionResult: ...
    def get_order_by_client_id(self, client_order_id: str) -> Order | None: ...
    def get_open_orders(self, symbol: str) -> list[Order]: ...
    def get_positions(self, symbol: str) -> list[dict]: ...


@dataclass
class ExecutionConfig:
    """Configuration inputs for ExecutionEngine."""

    bot_id: BotId
    symbol: str
    capital_usdt: float
    risk_per_trade_pct: float
    risk_amount_usdt: float              # pre-computed: capital_usdt * risk_per_trade_pct
    max_spread_bps: float = 5.0


@dataclass
class ExecutionEngineInput:
    """
    Runtime inputs for one execution call.
    """

    signal: Signal
    market_snapshot: MarketSnapshot          # spread_bps, killzone state
    is_frozen: bool                         # from BotState.is_frozen
    lot_size: float                    # instrument qty unit
    min_order_qty: float               # instrument qty unit
    risk_limit_checker: RiskLimitChecker


@dataclass
class ExecutionOutcome:
    """
    Outcome of ExecutionEngine.execute_signal.

    One of:
    - accepted: execution was submitted and is in-flight
    - rejected: execution was blocked at some gate; execution must not proceed
    """

    accepted: bool
    client_order_id: str | None = None
    order_id: str | None = None          # exchange-assigned id (populated on success)
    result: ExecutionResult | None = None
    risk_check: PreOrderRiskCheck | None = None
    sizing: SizingResult | None = None
    rejection_reason: str | None = None
    rejection_type: ExecutionErrorType = ExecutionErrorType.NONE

    def is_submittable(self) -> bool:
        """True only when accepted AND we have a valid order_id from the exchange."""
        return (
            self.accepted
            and self.order_id is not None
            and self.result is not None
            and self.result.is_submittable()
        )


class ExecutionEngine:
    """
    Orchestrates signal validation, risk gating, sizing, and order submission.

    Policy gates (in order):
      1. Validate signal handoff contract (SignalExecutionInput fields)
      2. Validate stop level vs direction (BUY → stop < entry, SELL → stop > entry)
      3. RiskLimitChecker — daily/weekly drawdown limits
      4. PreOrderRiskChecker — spread, frozen state, instrument filters
      5. PositionSizer — qty from risk budget
      6. If sizing.rejected: reject — execution MUST NOT submit
      7. Build OrderRequest and submit via ExecutionAdapter

    If any gate fails, execution MUST NOT submit. No silent fallback.

    Note: risk_used_usdt is pre-fees / pre-slippage unless explicitly adjusted elsewhere.
    """

    def __init__(
        self,
        config: ExecutionConfig,
        adapter: ExecutionAdapter,
        risk_checker: PreOrderRiskChecker,
        lifecycle_mgr: OrderLifecycleManager,
        idempotency_mgr: IdempotencyManager,
        instrument_lot_size: float,
        instrument_min_order_qty: float,
    ):
        self.config = config
        self._adapter = adapter
        self._risk_checker = risk_checker
        self._lifecycle = lifecycle_mgr
        self._idempotency = idempotency_mgr
        self._lot_size = instrument_lot_size
        self._min_order_qty = instrument_min_order_qty
        self._position_sizer = PositionSizer()

    def execute_signal(
        self,
        inp: ExecutionEngineInput,
    ) -> ExecutionOutcome:
        """
        Execute a signal end-to-end.

        Returns ExecutionOutcome with accepted=True only if the order was
        successfully submitted to the exchange with an order_id.
        Any rejection reason is set in the outcome.
        """
        signal = inp.signal

        # ── Gate 1: Validate signal handoff contract ────────────────────────

        # SignalExecutionInput contract — fields are directly on Signal, not on a sub-object
        if not signal.symbol:
            return self._reject(
                ExecutionErrorType.INVALID_SIGNAL,
                "signal missing required field: symbol",
            )

        if not getattr(signal, "direction", None):
            return self._reject(
                ExecutionErrorType.INVALID_SIGNAL,
                "signal missing required field: direction",
            )

        direction = signal.direction
        entry_price: float | None = signal.entry_price
        stop_loss: float | None = signal.stop_loss

        # ── Gate 2: Stop level validation ────────────────────────────────────
        if stop_loss is None:
            return self._reject(
                ExecutionErrorType.MISSING_STOP,
                "signal has no structural stop_loss — reject",
            )

        if entry_price is None:
            return self._reject(
                ExecutionErrorType.INVALID_SIGNAL,
                "signal has no entry_price",
            )

        if direction == OrderSide.BUY:
            if not (stop_loss < entry_price):
                return self._reject(
                    ExecutionErrorType.INVALID_STOP_LEVEL,
                    f"BUY stop must be < entry_price: stop={stop_loss}, entry={entry_price}",
                )
        elif direction == OrderSide.SELL:
            if not (stop_loss > entry_price):
                return self._reject(
                    ExecutionErrorType.INVALID_STOP_LEVEL,
                    f"SELL stop must be > entry_price: stop={stop_loss}, entry={entry_price}",
                )

        # ── Gate 3: Drawdown limits ────────────────────────────────────────────
        allowed, reason = inp.risk_limit_checker.check()
        if not allowed:
            return self._reject(
                ExecutionErrorType.ACCOUNT_FROZEN,
                f"drawdown limit breached: {reason}",
            )

        # ── Gate 4: Pre-order risk checks ─────────────────────────────────────
        spread_bps = inp.market_snapshot.spread_bps
        is_frozen = inp.is_frozen

        # Size position first to get risk_used_usdt
        risk_amount = self.config.risk_amount_usdt
        sizing_inp = SizingInput(
            bot_id=self.config.bot_id,
            risk_amount_usdt=risk_amount,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            lot_size=inp.lot_size,
            min_order_qty=inp.min_order_qty,
        )
        sizing = self._position_sizer.size_position(sizing_inp)

        # ── Gate 5: Sizing rejection ──────────────────────────────────────────
        if sizing.rejected:
            return self._reject(
                ExecutionErrorType.RISK_BREACH,
                f"sizing rejected: qty={sizing.qty}, risk_used={sizing.risk_used_usdt}, "
                f"budget={sizing.risk_amount_usdt}",
                sizing=sizing,
            )

        # Pre-order risk checks
        check_inp = PreOrderRiskCheckInput(
            bot_id=self.config.bot_id,
            symbol=self.config.symbol,
            risk_amount_usdt=risk_amount,
            risk_used_usdt=sizing.risk_used_usdt,
            spread_bps=spread_bps,
            is_frozen=is_frozen,
            lot_size=inp.lot_size,
            min_order_qty=inp.min_order_qty,
            qty=sizing.qty,
        )
        risk_check = self._risk_checker.check(check_inp)

        if not risk_check.all_passed:
            return self._reject(
                ExecutionErrorType.RISK_BREACH,
                f"pre-order risk check failed: {risk_check.rule_fired} — {risk_check.reason}",
                risk_check=risk_check,
                sizing=sizing,
            )

        # ── Gate 6a: Exchange pre-query (restart-safe idempotency) ──────────
        signal_id = getattr(signal, "signal_id", "unknown")
        client_order_id = build_client_order_id(
            self.config.bot_id.value,
            self.config.symbol,
            signal_id,
            attempt=1,
        )

        try:
            existing = self._adapter.get_order_by_client_id(client_order_id)
        except RetryableExchangeError as e:
            # Fail-closed: if we cannot verify the order's status, reject.
            # We must not submit when idempotency is uncertain.
            return self._reject(
                ExecutionErrorType.IDEMPOTENCY_CHECK_FAILED,
                f"idempotency check failed — exchange query error: {e.detail}",
            )

        if existing is not None and not existing.status.is_final():
            return self._reject(
                ExecutionErrorType.IDEMPOTENCY_CONFLICT,
                f"client_order_id {client_order_id} already active on exchange: "
                f"status={existing.status.value}",
            )

        if existing is not None and existing.status.is_final():
            # Idempotent outcome — order already closed on exchange.
            # Do not re-submit; return a clean non-error outcome.
            return ExecutionOutcome(
                accepted=False,
                client_order_id=client_order_id,
                rejection_reason=f"idempotent — order already {existing.status.value} on exchange",
                rejection_type=ExecutionErrorType.NONE,
                risk_check=risk_check,
                sizing=sizing,
            )
        # None → not on exchange; proceed to Gate 6b

        # ── Gate 6b: In-memory session idempotency ────────────────────────────
        if self._idempotency.is_known_submitted(client_order_id):
            return self._reject(
                ExecutionErrorType.IDEMPOTENCY_CONFLICT,
                f"client_order_id {client_order_id} already submitted this session",
            )

        # Determine order type
        order_type = "Market" if entry_price is None else "Limit"

        # Build OrderRequest — side must be Bybit string format: "Buy" | "Sell"
        bybit_side = "Buy" if direction == OrderSide.BUY else "Sell"

        req = OrderRequest(
            client_order_id=client_order_id,
            signal_id=signal_id,
            bot_id=self.config.bot_id.value,
            symbol=self.config.symbol,
            side=bybit_side,
            order_type=order_type,
            price=entry_price if order_type == "Limit" else None,
            qty=sizing.qty,
            stop_price=None,
            sl_trigger_price=stop_loss,          # structural stop attached
            tp_trigger_price_1=signal.take_profit_1,
            tp_trigger_price_2=signal.take_profit_2,
            risk_used_usdt=sizing.risk_used_usdt,
            entry_price=entry_price,
        )

        # Create lifecycle record
        self._lifecycle.create(client_order_id, signal_id,
                                self.config.bot_id.value, sizing.qty)

        # ── Gate 7: Submit via adapter ────────────────────────────────────────
        result = self._adapter.submit_order(req)

        if result.is_submittable():
            self._idempotency.mark_submitted(client_order_id)
            self._lifecycle.apply(client_order_id, LifecycleEvent.SUBMITTED,
                                   order_id=result.order_id)
            signal.client_order_id = client_order_id
            return ExecutionOutcome(
                accepted=True,
                client_order_id=client_order_id,
                order_id=result.order_id,
                result=result,
                risk_check=risk_check,
                sizing=sizing,
            )
        else:
            # Rejection at exchange layer
            self._lifecycle.apply(client_order_id, LifecycleEvent.REJECTED)
            return ExecutionOutcome(
                accepted=False,
                client_order_id=client_order_id,
                result=result,
                risk_check=risk_check,
                sizing=sizing,
                rejection_reason=f"exchange error: {result.error_detail}",
                rejection_type=result.error,
            )

    def _reject(
        self,
        error_type: ExecutionErrorType,
        reason: str,
        risk_check: PreOrderRiskCheck | None = None,
        sizing: SizingResult | None = None,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            accepted=False,
            rejection_reason=reason,
            rejection_type=error_type,
            risk_check=risk_check,
            sizing=sizing,
        )
