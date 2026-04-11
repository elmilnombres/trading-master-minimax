"""
Inducement detection.

FROZEN IMPLEMENTATION CONSTANTS:
  INDUCEMENT_LOOKBACK_CANDLES = 4

INDUCEMENT = an internal liquidity sweep that occurs AFTER CHoCH has been
confirmed, sweeping internal stop liquidity on the same side that was just
broken, before the legitimate move resumes.

CRITICAL: This module requires a confirmed CHoCH as input.
If CHoCH.triggered_at is None, inducement CANNOT be evaluated.

Requirements (all must be met):
  1. CHoCH has been confirmed (triggered_at is not None)
  2. Inducement candle sweeps internal liquidity PAST the CHoCH level
     but NOT beyond the original external sweep level
  3. Inducement direction must match CHoCH direction

Distinct from mitigation:
  - inducement = internal liquidity sweep post-CHoCH (this module)
  - mitigation = price closing inside the valid FVG zone on the retracement
    (handled in core/poi/fvg.py — MitigationZone tracking)

These constants are locked in CLAUDE.md and here.
Do not change without explicit user approval.
"""

from datetime import datetime

from schemas.candle import Candle
from schemas.confirmation import InducementResult, Direction
from schemas.structure import CHoCH, BOSDirection

# ─── Frozen constant ─────────────────────────────────────────────────────────

INDUCEMENT_LOOKBACK_CANDLES = 4


def detect_inducement(
    candles: list[Candle],
    chosh: CHoCH,
    external_sweep_price: float,
    chosh_level: float | None = None,
    lookback: int = INDUCEMENT_LOOKBACK_CANDLES,
) -> InducementResult:
    """
    Detect an inducement following a confirmed CHoCH.

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.
    chosh : CHoCH
        The confirmed CHoCH. Inducement CANNOT fire if chosh.triggered_at is None.
    external_sweep_price : float
        The price of the original external liquidity sweep.
        Inducement must NOT sweep past this level.
    chosh_level : float | None
        The level that CHoCH broke. Defaults to chosh.triggered_at.
    lookback : int
        Number of recent candles to check.
        Default INDUCEMENT_LOOKBACK_CANDLES (frozen).

    Returns
    -------
    InducementResult
        is_valid == True only if all inducement requirements are met.
        is_valid == False if CHoCH is not confirmed or rules fail.
    """
    if chosh.triggered_at is None:
        return InducementResult(
            direction=Direction.SELL if chosh.direction == BOSDirection.BULLISH else Direction.BUY,
            inducement_price=0.0,
            sweep_price=external_sweep_price,
            choch_confirmed_at=datetime.utcnow(),
            triggered_at=datetime.utcnow(),
            is_valid=False,
        )

    inducement_level = chosh_level if chosh_level is not None else chosh.triggered_at

    if chosh.direction == BOSDirection.BULLISH:
        # Bullish CHoCH: looking for a sell-side inducement
        # Price drops back to grab stops below the CHoCH level, then resumes upward.
        for candle in candles[-(lookback + 1):]:
            if candle.low < inducement_level:
                if candle.low >= external_sweep_price:
                    return InducementResult(
                        direction=Direction.SELL,
                        inducement_price=candle.low,
                        sweep_price=external_sweep_price,
                        choch_confirmed_at=chosh.timestamp,
                        triggered_at=candle.timestamp,
                        is_valid=True,
                    )
                else:
                    # Went below external sweep — too deep, not an inducement
                    return InducementResult(
                        direction=Direction.SELL,
                        inducement_price=candle.low,
                        sweep_price=external_sweep_price,
                        choch_confirmed_at=chosh.timestamp,
                        triggered_at=candle.timestamp,
                        is_valid=False,
                    )

    elif chosh.direction == BOSDirection.BEARISH:
        # Bearish CHoCH: looking for a buy-side inducement
        # Price rises back to grab stops above the CHoCH level, then resumes downward.
        for candle in candles[-(lookback + 1):]:
            if candle.high > inducement_level:
                if candle.high <= external_sweep_price:
                    return InducementResult(
                        direction=Direction.BUY,
                        inducement_price=candle.high,
                        sweep_price=external_sweep_price,
                        choch_confirmed_at=chosh.timestamp,
                        triggered_at=candle.timestamp,
                        is_valid=True,
                    )
                else:
                    return InducementResult(
                        direction=Direction.BUY,
                        inducement_price=candle.high,
                        sweep_price=external_sweep_price,
                        choch_confirmed_at=chosh.timestamp,
                        triggered_at=candle.timestamp,
                        is_valid=False,
                    )

    return InducementResult(
        direction=Direction.SELL if chosh.direction == BOSDirection.BULLISH else Direction.BUY,
        inducement_price=0.0,
        sweep_price=external_sweep_price,
        choch_confirmed_at=chosh.timestamp,
        triggered_at=datetime.utcnow(),
        is_valid=False,
    )
