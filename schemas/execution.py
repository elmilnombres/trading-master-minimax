"""
Execution schemas — owned by core/execution/.

Defines the cross-layer execution contract:
- OrderRequest: handoff from signal layer to execution engine
- ExecutionResult: raw exchange response wrapped in typed result
- PreOrderRiskCheck: pre-submission risk gate result
- RiskLimitState: per-bot daily/weekly drawdown state

No strategy logic. No scoring. No weights.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ExecutionErrorType(str, Enum):
    """Typed execution failure reasons. All exchange errors are mapped here."""

    NONE = "none"                       # success
    MISSING_STOP = "missing_stop"       # signal has no structural stop_loss
    INVALID_SIGNAL = "invalid_signal"   # signal missing required fields
    INVALID_STOP_LEVEL = "invalid_stop_level"  # stop direction inconsistent with order side
    RISK_BREACH = "risk_breach"          # risk_used exceeds risk_amount
    MIN_NOTIONAL = "min_notional"       # order notional below instrument minimum
    SPREAD_TOO_WIDE = "spread_too_wide" # spread exceeds allowed threshold
    ACCOUNT_FROZEN = "account_frozen"   # is_frozen=True — bot paused
    EXCHANGE_ERROR = "exchange_error"    # raw exchange error
    TIMEOUT = "timeout"                  # exchange request timed out
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"  # client_order_id already on exchange
    IDEMPOTENCY_CHECK_FAILED = "idempotency_check_failed"  # exchange history lookup failed


class OrderRequest(BaseModel):
    """
    Canonical order submission request.

    Produced by ExecutionEngine after all risk checks pass.
    Consumed by ExecutionAdapter for exchange submission.

    All fields must be set before submission — no None defaults
    that would allow partial data to reach the exchange.
    """

    # Identity
    client_order_id: str          # deterministic UUID5 — required, set by IdempotencyManager
    signal_id: str                # originating signal
    bot_id: str                   # alpha_bot | beta_bot | gamma_bot

    # Instrument
    symbol: str                   # e.g. "BTCUSDT"
    side: str                     # "Buy" | "Sell"  (Bybit string format)
    order_type: str               # "Market" | "Limit" | "StopMarket" | "TakeProfitMarket"

    # Price and quantity
    price: float | None = None    # limit price; None for market
    qty: float                   # in instrument qty unit (post-quantization)
    stop_price: float | None = None  # trigger price for stop/conditional orders

    # Attached stop-loss (Bybit native sl_trigger_price on entry order)
    sl_trigger_price: float | None = None   # structural stop from Signal.locked

    # Take-profit targets (set as attached TP on entry, or as separate orders)
    tp_trigger_price_1: float | None = None
    tp_trigger_price_2: float | None = None

    # Risk tracking
    risk_used_usdt: float         # actual risk consumed = qty * |entry - stop|
                                  # pre-fees / pre-slippage unless explicitly adjusted
    entry_price: float           # price used for sizing (for traceability)


class ExecutionResult(BaseModel):
    """
    Result of an exchange submission attempt.

    Either order_id is set (success or partial) or error is set (failure).
    Never both.
    """

    success: bool
    order_id: str | None = None          # exchange-assigned order id
    client_order_id: str | None = None    # our idempotency key (echoed by exchange)
    symbol: str | None = None
    status: str | None = None             # exchange status string
    error: ExecutionErrorType = ExecutionErrorType.NONE
    error_detail: str | None = None      # raw exchange message or internal reason
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def is_submittable(self) -> bool:
        """
        True only when the result is a clean success that can proceed to position tracking.
        """
        return (
            self.success
            and self.order_id is not None
            and self.error == ExecutionErrorType.NONE
        )


class PreOrderRiskCheck(BaseModel):
    """
    Result of pre-submission risk gate.

    all_passed == True: all checks passed, execution may proceed.
    all_passed == False: one or more checks failed, execution MUST NOT submit.
    """

    all_passed: bool
    risk_amount_usdt: float      # the budget we checked against
    risk_used_usdt: float        # actual risk if qty were submitted
    spread_bps: float | None = None
    spread_safe: bool = True
    account_frozen: bool = False
    min_notional_safe: bool = True
    stop_valid: bool = True

    # Human-readable rule that fired (empty if all_passed)
    rule_fired: str = ""
    reason: str | None = None


class RiskLimitState(BaseModel):
    """
    Per-bot drawdown state — tracked by RiskLimitChecker.

    Reset policy:
    - daily: reset at the start of each UTC calendar day
    - weekly: reset at the start of each UTC calendar week (Monday)
    - tracked in BotState/journal, persisted across restarts
    """

    bot_id: str
    daily_pnl_usdt: float = 0.0      # net PnL since last UTC day reset
    weekly_pnl_usdt: float = 0.0     # net PnL since last UTC week reset
    last_daily_reset: datetime = Field(default_factory=datetime.utcnow)
    last_weekly_reset: datetime = Field(default_factory=datetime.utcnow)

    def reset_daily_if_needed(self) -> bool:
        """
        Reset daily counter if UTC calendar day has changed.
        Returns True if reset was performed.
        """
        now = datetime.utcnow()
        if now.date() > self.last_daily_reset.date():
            self.daily_pnl_usdt = 0.0
            self.last_daily_reset = now
            return True
        return False

    def reset_weekly_if_needed(self) -> bool:
        """
        Reset weekly counter if UTC calendar week has changed (Monday boundary).
        Returns True if reset was performed.
        """
        now = datetime.utcnow()
        if now.isocalendar()[1] != self.last_weekly_reset.isocalendar()[1]:
            self.weekly_pnl_usdt = 0.0
            self.last_weekly_reset = now
            return True
        return False

    def apply_trade_result(self, pnl_usdt: float) -> None:
        """Add closed trade PnL to both daily and weekly accumulators."""
        self.daily_pnl_usdt += pnl_usdt
        self.weekly_pnl_usdt += pnl_usdt
