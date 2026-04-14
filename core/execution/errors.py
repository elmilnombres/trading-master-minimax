"""
Shared execution exceptions — owned by core/execution/.

RetryableExchangeError is used by:
- exchange/bybit/execution.py (raises) — BybitExecutionAdapter.get_order_by_client_id
- core/execution/engine.py   (catches) — ExecutionEngine Gate 6a

Defined here so both modules reference the same class.
schemas/execution.py holds data contracts only — no exceptions there.
"""

from core.bot_id import BotId  # noqa: F401 — re-exported for convenience
from exchange.bybit.client import BybitAPIError


class RetryableExchangeError(Exception):
    """
    Raised when an exchange call fails in a way that makes idempotency
    unverifiable — the caller MUST NOT treat this as "never submitted".
    """

    def __init__(
        self,
        detail: str,
        code: int | None = None,
        original: Exception | None = None,
    ):
        self.detail = detail
        self._code = code
        self._original = original
        super().__init__(detail)

    @property
    def code(self) -> int | None:
        """Bybit retCode if this wraps a BybitAPIError."""
        if self._code is not None:
            return self._code
        if isinstance(self._original, BybitAPIError):
            return self._original.code
        return None

    @property
    def original(self) -> Exception | None:
        return self._original
