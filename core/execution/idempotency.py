"""
Deterministic idempotency manager — safe across restarts.

Owned by core/execution/.
Deterministic client_order_id generation using UUID5 so that
the same signal_id + attempt produces the same UUID on every run.

No exchange calls. No state — pure function.
"""

import uuid

NAMESPACE = uuid.NAMESPACE_DNS


def build_client_order_id(bot_id: str, symbol: str, signal_id: str, attempt: int) -> str:
    """
    Deterministic UUID5-based client_order_id.

    Format: uuid5(NAMESPACE, f"{bot_id}:{symbol}:{signal_id}:{attempt}")

    Properties:
    - Same inputs always produce the same UUID — safe for restart reconciliation.
    - attempt = 1 for first submission, incremented only on actual retry (not on
      rejection at the risk layer).
    - The UUID is opaque — no information is encoded in it.
    """
    name = f"{bot_id}:{symbol}:{signal_id}:{attempt}"
    return str(uuid.uuid5(NAMESPACE, name))


class IdempotencyManager:
    """
    Tracks which client_order_ids have been submitted this session.

    In-memory only. State is not persisted — on startup, the
    ReconciliationService queries Bybit by client_order_id to detect
    pre-existing orders before IdempotencyManager is consulted.

    This class exists to prevent accidental duplicate submissions within
    a running session (e.g. if the lifecycle manager is called twice).
    """

    def __init__(self):
        self._submitted: set[str] = set()

    def mark_submitted(self, client_order_id: str) -> None:
        """Record that this client_order_id was submitted this session."""
        self._submitted.add(client_order_id)

    def is_known_submitted(self, client_order_id: str) -> bool:
        """True if this client_order_id was already submitted this session."""
        return client_order_id in self._submitted

    def next_attempt(
        self, bot_id: str, symbol: str, signal_id: str, already_attempted: int
    ) -> str:
        """
        Generate client_order_id for the next submission attempt.

        already_attempted: number of attempts already made in this session.
        Returns the id for attempt already_attempted + 1.
        """
        return build_client_order_id(bot_id, symbol, signal_id, already_attempted + 1)
