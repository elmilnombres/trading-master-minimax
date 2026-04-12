"""Minimal in-memory state store with JSON snapshot persistence."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from schemas.bias import MacroBias
from schemas.execution import RiskLimitState
from schemas.order import Order
from schemas.position import Position
from schemas.signal import Signal


SNAPSHOT_FILE = "snapshot.json"


@dataclass
class BotState:
    pending_orders: list[Order] = field(default_factory=list)
    active_signals: list[Signal] = field(default_factory=list)
    open_position: Position | None = None
    risk_limit_state: RiskLimitState | None = None
    current_bias: MacroBias | None = None
    is_frozen: bool = False


class StateStore:
    def __init__(self, bot_id: str, state_dir: Path) -> None:
        self._bot_id = bot_id
        self._state_dir = Path(state_dir)
        self._state: BotState = BotState()
        self._state_lock = threading.Lock()
        self._snapshot_callbacks: list = []

    def get_state(self) -> BotState:
        return self._state

    def load_from_snapshot(self) -> None:
        path = self._state_dir / SNAPSHOT_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        pending = []
        for raw in data.get("pending_orders", []):
            try:
                pending.append(Order.model_validate(raw))
            except Exception:
                pass
        self._state.pending_orders = pending
        active = []
        for raw in data.get("active_signals", []):
            try:
                active.append(Signal.model_validate(raw))
            except Exception:
                pass
        self._state.active_signals = active
        raw_pos = data.get("open_position")
        if raw_pos:
            try:
                self._state.open_position = Position.model_validate(raw_pos)
            except Exception:
                self._state.open_position = None
        raw_risk = data.get("risk_limit_state")
        if raw_risk:
            try:
                self._state.risk_limit_state = RiskLimitState.model_validate(raw_risk)
            except Exception:
                self._state.risk_limit_state = None
        self._state.is_frozen = bool(data.get("is_frozen", False))

    def snapshot(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_dir / SNAPSHOT_FILE
        pending = [o for o in self._state.pending_orders if not o.is_final()]
        data = {
            "pending_orders": [o.model_dump(mode="json") for o in pending],
            "active_signals": [s.model_dump(mode="json") for s in self._state.active_signals],
            "is_frozen": self._state.is_frozen,
        }
        if self._state.open_position:
            data["open_position"] = self._state.open_position.model_dump(mode="json")
        if self._state.risk_limit_state:
            data["risk_limit_state"] = self._state.risk_limit_state.model_dump(mode="json")
        path.write_text(json.dumps(data, indent=2, default=str))

    def on_state_change(self, callback) -> None:
        self._snapshot_callbacks.append(callback)

    def _fire_callbacks(self) -> None:
        for cb in self._snapshot_callbacks:
            try:
                cb(self._state)
            except Exception:
                pass

    def add_order(self, order: Order) -> None:
        with self._state_lock:
            if order.client_order_id and any(
                o.client_order_id == order.client_order_id for o in self._state.pending_orders
            ):
                return
            self._state.pending_orders.append(order)
        self.snapshot()
        self._fire_callbacks()

    def remove_order(self, order: Order) -> None:
        with self._state_lock:
            self._state.pending_orders = [
                o for o in self._state.pending_orders
                if o.client_order_id != order.client_order_id
            ]
        self.snapshot()
        self._fire_callbacks()

    def add_signal(self, signal: Signal) -> None:
        with self._state_lock:
            if any(s.signal_id == signal.signal_id for s in self._state.active_signals):
                return
            self._state.active_signals.append(signal)
        self.snapshot()
        self._fire_callbacks()

    def mark_signal_entered(self, signal_id: str, order_id: str, filled_price: float) -> None:
        with self._state_lock:
            for sig in self._state.active_signals:
                if sig.signal_id == signal_id:
                    sig.mark_entered(order_id, filled_price)
                    break
        self.snapshot()
        self._fire_callbacks()

    def mark_signal_closed(self, signal_id: str, exit_price: float, exit_reason: str, realized_pnl: float, result: str) -> None:
        with self._state_lock:
            for sig in self._state.active_signals:
                if sig.signal_id == signal_id:
                    sig.mark_closed(exit_price, exit_reason, realized_pnl, result)
                    self._state.active_signals.remove(sig)
                    break
        self.snapshot()
        self._fire_callbacks()

    def expire_signal(self, signal_id: str, reason: str) -> None:
        with self._state_lock:
            for sig in self._state.active_signals:
                if sig.signal_id == signal_id:
                    sig.mark_expired(reason)
                    self._state.active_signals.remove(sig)
                    break
        self.snapshot()
        self._fire_callbacks()

    def set_open_position(self, position: Position | None) -> None:
        with self._state_lock:
            self._state.open_position = position
        self.snapshot()
        self._fire_callbacks()

    def update_risk_limit_state(self, state: RiskLimitState) -> None:
        with self._state_lock:
            self._state.risk_limit_state = state
        self.snapshot()
        self._fire_callbacks()

    def set_frozen(self, frozen: bool = True) -> None:
        with self._state_lock:
            self._state.is_frozen = frozen
        self.snapshot()
        self._fire_callbacks()
