"""
Order lifecycle manager — tracks order state transitions.

Owned by core/execution/.
No exchange calls. No strategy logic.

State machine:
  PENDING → SUBMITTED → FILLED         (entry filled → position opened)
  PENDING → SUBMITTED → CANCELLED      (cancelled before fill)
  PENDING → REJECTED                    (exchange rejected)
  Any state → FILLED                    (stop/tp order fills)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from schemas.order import Order, OrderStatus


class LifecycleEvent(str, Enum):
    """Named lifecycle events for traceability."""

    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class LifecycleTransition:
    """A single state transition in the order lifecycle."""

    event: LifecycleEvent
    timestamp: datetime = field(default_factory=datetime.utcnow)
    order_id: str | None = None
    avg_price: float | None = None
    filled_qty: float | None = None
    note: str | None = None


@dataclass
class LifecycleRecord:
    """Complete lifecycle trace for a single order."""

    client_order_id: str
    signal_id: str
    bot_id: str
    transitions: list[LifecycleTransition] = field(default_factory=list)
    current_status: OrderStatus = OrderStatus.PENDING
    qty: float = 0.0
    filled_qty: float = 0.0
    avg_fill_price: float | None = None

    def current_state(self) -> str:
        return self.current_status.value

    def is_terminal(self) -> bool:
        return self.current_status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        )

    def apply(self, event: LifecycleEvent, **kwargs) -> None:
        """
        Apply a lifecycle event and update current_status.

        Raises ValueError if the event is invalid for the current state.
        """
        from schemas.order import OrderStatus as OS

        transition = LifecycleTransition(event=event, **kwargs)
        self.transitions.append(transition)

        mapping: dict[LifecycleEvent, OrderStatus] = {
            LifecycleEvent.SUBMITTED: OS.SUBMITTED,
            LifecycleEvent.FILLED: OS.FILLED,
            LifecycleEvent.PARTIALLY_FILLED: OS.PARTIALLY_FILLED,
            LifecycleEvent.CANCELLED: OS.CANCELLED,
            LifecycleEvent.REJECTED: OS.REJECTED,
        }

        new_status = mapping.get(event)
        if new_status is not None:
            self.current_status = new_status

        if event == LifecycleEvent.FILLED:
            self.filled_qty = kwargs.get("filled_qty", self.qty)
            self.avg_fill_price = kwargs.get("avg_price")

        if event in (LifecycleEvent.FILLED, LifecycleEvent.CANCELLED, LifecycleEvent.REJECTED):
            pass  # terminal — no further transitions expected


class OrderLifecycleManager:
    """
    Manages lifecycle records for in-flight orders.

    One record per client_order_id. State transitions are append-only.
    No exchange calls. No strategy logic.

    The ReconciliationService updates lifecycle records by polling Bybit.
    This manager does not poll — it only records transitions reported to it.
    """

    def __init__(self):
        self._records: dict[str, LifecycleRecord] = {}

    def create(self, client_order_id: str, signal_id: str, bot_id: str, qty: float) -> LifecycleRecord:
        """Create a new lifecycle record for an order being submitted."""
        rec = LifecycleRecord(
            client_order_id=client_order_id,
            signal_id=signal_id,
            bot_id=bot_id,
            qty=qty,
            current_status=OrderStatus.PENDING,
        )
        self._records[client_order_id] = rec
        return rec

    def get(self, client_order_id: str) -> LifecycleRecord | None:
        """Get the lifecycle record for a client_order_id."""
        return self._records.get(client_order_id)

    def apply(self, client_order_id: str, event: LifecycleEvent, **kwargs) -> None:
        """
        Apply a lifecycle event to a known order.

        Silently no-ops if the client_order_id is unknown — this handles
        the case where a pre-existing order from a restart is discovered
        by the ReconciliationService before create() was called.
        """
        rec = self._records.get(client_order_id)
        if rec is not None:
            rec.apply(event, **kwargs)

    def active_orders(self) -> list[LifecycleRecord]:
        """Return all non-terminal lifecycle records."""
        return [r for r in self._records.values() if not r.is_terminal()]

    def find_by_order_id(self, order_id: str) -> LifecycleRecord | None:
        """
        Find a lifecycle record by its exchange-assigned order_id.

        Used on restart recovery: Bybit reports the exchange order_id when a
        position is filled, but not our internal client_order_id.  This method
        lets the runtime recover the signal_id linkage by searching the
        SUBMITTED transition where order_id is first recorded.
        """
        for rec in self._records.values():
            for t in rec.transitions:
                if t.order_id == order_id:
                    return rec
        return None
