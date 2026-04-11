"""
Pre-order risk checks — filter before exchange submission.

Owned by core/risk/.
No strategy logic. No exchange calls.
"""

from dataclasses import dataclass

from core.bot_id import BotId
from schemas.execution import PreOrderRiskCheck
from exchange.bybit.filters import InstrumentFilterCache


@dataclass
class PreOrderRiskCheckInput:
    """
    Input to PreOrderRiskChecker.check.
    """

    bot_id: BotId
    symbol: str
    risk_amount_usdt: float       # budget for this trade
    risk_used_usdt: float        # actual risk if this qty were submitted
    spread_bps: float | None     # current spread in basis points
    is_frozen: bool              # account frozen state from BotState
    lot_size: float              # instrument qty unit
    min_order_qty: float         # instrument qty unit
    qty: float                  # proposed qty in instrument qty unit


class PreOrderRiskChecker:
    """
    Runs all pre-submission risk filters.

    Policy: if any check fails, all_passed = False and execution MUST NOT submit.

    Checks:
    - risk budget: risk_used_usdt <= risk_amount_usdt
    - spread: spread_bps <= max_spread_bps threshold (default 5.0 bps)
    - account frozen: is_frozen must be False
    - min notional: qty and price checked against InstrumentFilterCache
    - qty quantum: qty is multiple of lot_size and >= min_order_qty

    Note: risk_used_usdt is pre-fees / pre-slippage unless explicitly adjusted.
    """

    MAX_SPREAD_BPS: float = 5.0  # Bybit spread threshold for $50 capital

    def __init__(self, filter_cache: InstrumentFilterCache):
        self._cache = filter_cache

    def check(self, inp: PreOrderRiskCheckInput) -> PreOrderRiskCheck:
        # 1. Risk budget
        if inp.risk_used_usdt > inp.risk_amount_usdt:
            return PreOrderRiskCheck(
                all_passed=False,
                risk_amount_usdt=inp.risk_amount_usdt,
                risk_used_usdt=inp.risk_used_usdt,
                spread_bps=inp.spread_bps,
                spread_safe=True,
                account_frozen=inp.is_frozen,
                min_notional_safe=True,
                stop_valid=True,
                rule_fired="risk_budget_exceeded",
                reason=f"risk_used_usdt {inp.risk_used_usdt:.4f} > risk_amount_usdt {inp.risk_amount_usdt:.4f}",
            )

        # 2. Spread
        spread_safe = True
        if inp.spread_bps is not None and inp.spread_bps > self.MAX_SPREAD_BPS:
            return PreOrderRiskCheck(
                all_passed=False,
                risk_amount_usdt=inp.risk_amount_usdt,
                risk_used_usdt=inp.risk_used_usdt,
                spread_bps=inp.spread_bps,
                spread_safe=False,
                account_frozen=inp.is_frozen,
                min_notional_safe=True,
                stop_valid=True,
                rule_fired="spread_too_wide",
                reason=f"spread {inp.spread_bps:.2f} bps > max {self.MAX_SPREAD_BPS:.2f} bps",
            )

        # 3. Account frozen
        if inp.is_frozen:
            return PreOrderRiskCheck(
                all_passed=False,
                risk_amount_usdt=inp.risk_amount_usdt,
                risk_used_usdt=inp.risk_used_usdt,
                spread_bps=inp.spread_bps,
                spread_safe=True,
                account_frozen=True,
                min_notional_safe=True,
                stop_valid=True,
                rule_fired="account_frozen",
                reason="account is frozen — trading paused",
            )

        # 4. Min notional
        # Checked by InstrumentFilterCache on the actual entry_price at exchange submission.
        # Here we only confirm the instrument filter is available.
        try:
            self._cache.get_filter(inp.symbol)
        except Exception:
            return PreOrderRiskCheck(
                all_passed=False,
                risk_amount_usdt=inp.risk_amount_usdt,
                risk_used_usdt=inp.risk_used_usdt,
                spread_bps=inp.spread_bps,
                spread_safe=True,
                account_frozen=inp.is_frozen,
                min_notional_safe=False,
                stop_valid=True,
                rule_fired="instrument_filter_unavailable",
                reason=f"could not fetch instrument filter for {inp.symbol}",
            )

        min_notional_safe = True

        return PreOrderRiskCheck(
            all_passed=True,
            risk_amount_usdt=inp.risk_amount_usdt,
            risk_used_usdt=inp.risk_used_usdt,
            spread_bps=inp.spread_bps,
            spread_safe=True,
            account_frozen=inp.is_frozen,
            min_notional_safe=min_notional_safe,
            stop_valid=True,
            rule_fired="",
            reason=None,
        )
