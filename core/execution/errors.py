"""
Shared execution exceptions — owned by core/execution/.

RetryableExchangeError is used by:
- exchange/bybit/execution.py (raises) — BybitExecutionAdapter.get_order_by_client_id
- core/execution/engine.py   (catches) — ExecutionEngine Gate 6a

Defined here so both modules reference the same class.
schemas/execution.py holds data contracts only — no exceptions there.
"""

from core.bot_id import BotId  # noqa: F401 — re-exported for convenience


class RetryableExchangeError(Exception):
    """
    Raised when an exchange call fails in a way that makes idempotency
    unverifiable — the caller MUST NOT treat this as "never submitted".

    This is the shared exception used by both the adapter (to raise)
    and the engine (to catch) so the two modules are always in sync.
    """

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)
