"""
Per-bot risk limit checker — daily and weekly drawdown gates.

Owned by core/risk/.
No strategy logic. No exchange calls.

Portfolio-level risk (1.0% cap) is informational only in Phase 3 —
produced as a read-only PortfolioRiskSnapshot, not used for gating.
"""

from dataclasses import dataclass, field
from datetime import datetime

from core.bot_id import BotId
from schemas.execution import RiskLimitState


# Frozen per CLAUDE.md — do not change without explicit user approval.
MAX_DAILY_LOSS_PCT = 0.02    # 2.0%
MAX_WEEKLY_LOSS_PCT = 0.05   # 5.0%


@dataclass
class PortfolioRiskSnapshot:
    """
    Read-only snapshot of current portfolio-level risk exposure.

    Produced by RiskLimitChecker on every check call.
    Consumed by the supervisor (Phase 4) for display, alerts, or
    cross-bot enforcement — not enforced in Phase 3.
    """

    total_active_risk_usdt: float
    max_portfolio_risk_pct: float  # frozen = 1.0%  (from CLAUDE.md, not from YAML)
    portfolio_cap_usdt: float      # portfolio total capital
    portfolio_risk_pct: float      # total_active_risk_usdt / portfolio_cap_usdt


class RiskLimitChecker:
    """
    Enforces per-bot daily and weekly drawdown limits.

    Limits (frozen):
    - Daily loss cap: 2.0% of capital_usdt
    - Weekly loss cap: 5.0% of capital_usdt

    Policy:
    - On any closed trade, accumulate pnl into RiskLimitState.
    - On every check, call reset_daily_if_needed() and reset_weekly_if_needed().
    - If daily_pnl_usdt < -MAX_DAILY_LOSS_PCT * capital_usdt: block new entries.
    - If weekly_pnl_usdt < -MAX_WEEKLY_LOSS_PCT * capital_usdt: block new entries.

    Portfolio-level risk (1.0% max_portfolio_risk_pct):
    - Phase 3: informational only — computed as PortfolioRiskSnapshot,
      not used to block orders.

    Reset policy:
    - Daily: UTC calendar day boundary
    - Weekly: UTC calendar week boundary (Monday 00:00 UTC)
    """

    def __init__(self, bot_id: BotId, capital_usdt: float):
        self.bot_id = bot_id
        self.capital_usdt = capital_usdt
        self._state = RiskLimitState(bot_id=bot_id.value)

    @property
    def state(self) -> RiskLimitState:
        """Return current state (for persistence into BotState)."""
        return self._state

    def apply_trade_result(self, pnl_usdt: float) -> None:
        """Record a closed trade PnL into daily and weekly accumulators."""
        self._state.apply_trade_result(pnl_usdt)

    def check(self) -> tuple[bool, str]:
        """
        Check daily and weekly limits.

        Returns (allowed, reason).
        allowed == True: no limit breached.
        allowed == False: one or both limits breached; reason explains which.
        """
        self._state.reset_daily_if_needed()
        self._state.reset_weekly_if_needed()

        daily_limit = -self.capital_usdt * MAX_DAILY_LOSS_PCT
        weekly_limit = -self.capital_usdt * MAX_WEEKLY_LOSS_PCT

        reasons: list[str] = []

        if self._state.daily_pnl_usdt < daily_limit:
            reasons.append(
                f"daily loss {self._state.daily_pnl_usdt:.4f} < limit {daily_limit:.4f}"
            )

        if self._state.weekly_pnl_usdt < weekly_limit:
            reasons.append(
                f"weekly loss {self._state.weekly_pnl_usdt:.4f} < limit {weekly_limit:.4f}"
            )

        if reasons:
            return False, "; ".join(reasons)

        return True, ""

    def portfolio_snapshot(
        self, total_active_risk_usdt: float, portfolio_cap_usdt: float
    ) -> PortfolioRiskSnapshot:
        """
        Produce a read-only portfolio risk snapshot.

        Phase 3: informational only. This method does not gate orders.
        """
        return PortfolioRiskSnapshot(
            total_active_risk_usdt=total_active_risk_usdt,
            max_portfolio_risk_pct=0.01,   # frozen 1.0% from CLAUDE.md
            portfolio_cap_usdt=portfolio_cap_usdt,
            portfolio_risk_pct=(
                total_active_risk_usdt / portfolio_cap_usdt
                if portfolio_cap_usdt > 0 else 0.0
            ),
        )

    def load_state(self, state: RiskLimitState) -> None:
        """Rehydrate from persisted RiskLimitState (on startup from BotState)."""
        self._state = state
        # Always run a reset check on load — we may have crossed a day/week boundary
        self._state.reset_daily_if_needed()
        self._state.reset_weekly_if_needed()
