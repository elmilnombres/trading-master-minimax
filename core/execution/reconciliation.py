"""
Startup + periodic reconciliation with Bybit — authoritative truth.

Owned by core/execution/.
Exchange truth is authoritative after restart.

ReconciliationService does not modify orders. It syncs state from Bybit
and reports what it finds. State mutations are made by the caller
(ExecutionEngine or bot app) based on reconciliation findings.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from schemas.order import Order, OrderStatus


class ExecutionAdapter(Protocol):
    """
    Abstract interface for exchange execution.

    Only the methods needed by ReconciliationService are declared here.
    Full interface in exchange/bybit/execution.py.
    """

    def get_order_by_client_id(self, client_order_id: str) -> Order | None: ...
    def get_open_orders(self, symbol: str) -> list[Order]: ...
    def get_positions(self, symbol: str) -> list[dict]: ...


@dataclass
class ReconciliationFinding:
    """
    Result of reconciling one order's state against Bybit.

    local_status: what we think the order status is
    exchange_status: what Bybit reports (None if unknown)
    action: what the caller should do
    """

    client_order_id: str
    local_status: OrderStatus | None
    exchange_status: OrderStatus | None
    action: str  # "none" | "update_state" | "cancel" | "flag"
    note: str | None = None


class ReconciliationService:
    """
    Syncs local order state with Bybit on startup and periodically.

    Policy:
    - On startup: query Bybit for all open orders by client_order_id.
      If an order exists on Bybit that we don't know about → report it.
    - On periodic poll: for each known active order, query Bybit.
      If status diverges → report the finding for the caller to act on.
    - Never modifies state — only reports findings.

    Authoritative rule: exchange truth wins on restart.

    poll_interval: set to SUPERVISOR_POLL_INTERVAL = 5s (frozen).
    """

    POLL_INTERVAL_SECONDS = 5  # frozen per CLAUDE.md SUPERVISOR_POLL_INTERVAL

    def __init__(self, adapter: ExecutionAdapter, symbol: str):
        self._adapter = adapter
        self._symbol = symbol
        self._known_orders: dict[str, OrderStatus] = {}  # client_order_id → local status

    def register_order(self, client_order_id: str, local_status: OrderStatus) -> None:
        """Record that we know about this order."""
        self._known_orders[client_order_id] = local_status

    def startup_reconcile(
        self, known_client_order_ids: list[str]
    ) -> list[ReconciliationFinding]:
        """
        On bot startup, reconcile a list of client_order_ids against Bybit.

        For each id, query Bybit.get_order_by_client_id.
        If Bybit has the order in a terminal state we missed → flag for update.
        If Bybit has the order in a non-terminal state we thought was terminal
          → flag for update (we may have stale state).

        Returns a list of findings for the caller to act on.
        """
        findings: list[ReconciliationFinding] = []

        for cid in known_client_order_ids:
            bybit_order = self._adapter.get_order_by_client_id(cid)

            if bybit_order is None:
                # Not found on Bybit — either fully filled/cancelled long ago,
                # or never actually submitted. Treat as terminal.
                local = self._known_orders.get(cid)
                findings.append(ReconciliationFinding(
                    client_order_id=cid,
                    local_status=local,
                    exchange_status=None,
                    action="none" if local and local.is_final() else "flag",
                    note="order not found on Bybit — assumed closed or never submitted",
                ))
                self._known_orders[cid] = OrderStatus.REJECTED  # conservative
            else:
                # Read local BEFORE updating — we need the pre-update value
                local = self._known_orders.get(cid)
                self._known_orders[cid] = bybit_order.status
                action = self._determine_action(cid, local=local,
                                               exchange=bybit_order.status)
                if action != "none":
                    findings.append(ReconciliationFinding(
                        client_order_id=cid,
                        local_status=local,
                        exchange_status=bybit_order.status,
                        action=action,
                        note=f"startup sync: exchange={bybit_order.status.value}",
                    ))

        return findings

    def poll(self) -> list[ReconciliationFinding]:
        """
        Periodic poll of all known active orders.

        Queries Bybit for current status of each known non-terminal order.
        Returns any divergences for the caller to act on.
        """
        findings: list[ReconciliationFinding] = []

        for cid, local_status in self._known_orders.items():
            if local_status.is_final():
                continue

            bybit_order = self._adapter.get_order_by_client_id(cid)

            if bybit_order is None:
                # Gone from Bybit — conservatively treat as cancelled
                self._known_orders[cid] = OrderStatus.CANCELLED
                findings.append(ReconciliationFinding(
                    client_order_id=cid,
                    local_status=local_status,
                    exchange_status=None,
                    action="update_state",
                    note="order disappeared from Bybit — treated as cancelled",
                ))
                continue

            if bybit_order.status != local_status:
                action = self._determine_action(cid, local_status, bybit_order.status)
                self._known_orders[cid] = bybit_order.status
                findings.append(ReconciliationFinding(
                    client_order_id=cid,
                    local_status=local_status,
                    exchange_status=bybit_order.status,
                    action=action,
                    note=f"status changed: local={local_status.value} → exchange={bybit_order.status.value}",
                ))

        return findings

    def _determine_action(
        self, client_order_id: str, local: OrderStatus | None, exchange: OrderStatus
    ) -> str:
        if exchange in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            return "update_state"
        if exchange == OrderStatus.PARTIALLY_FILLED:
            return "update_state"
        return "none"
