"""
Reclaim detection.

FROZEN IMPLEMENTATION CONSTANTS:
  RECLAIM_LOOKBACK_CANDLES = 3

RECLAIM ABOVE = price closes above the sweep level on the next M5 candle.
RECLAIM BELOW = price closes below the sweep level on the next M5 candle.

These constants are locked in CLAUDE.md and here.
Do not change without explicit user approval.
"""

from schemas.candle import Candle
from schemas.confirmation import ReclaimResult, Direction

# ─── Frozen constant ─────────────────────────────────────────────────────────

RECLAIM_LOOKBACK_CANDLES = 3


def detect_reclaim(
    candles: list[Candle],
    sweep_price: float,
    sweep_direction: Direction,
    lookback: int = RECLAIM_LOOKBACK_CANDLES,
) -> ReclaimResult:
    """
    Detect a reclaim above or below the sweep level.

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.
        Should include the sweep candle and at least 1 subsequent candle.
    sweep_price : float
        The price level from the LiquiditySweep.
    sweep_direction : Direction
        BUY  = looking for bullish reclaim (price above sweep level)
        SELL = looking for bearish reclaim (price below sweep level)
    lookback : int
        Number of recent candles to check.
        Default RECLAIM_LOOKBACK_CANDLES (frozen).

    Returns
    -------
    ReclaimResult
        is_valid == True only if a reclaim candle was found after the sweep.
    """
    if len(candles) < 2:
        return ReclaimResult(
            direction=sweep_direction,
            reclaim_price=sweep_price,
            held_above=None,
            is_valid=False,
        )

    if sweep_direction == Direction.BUY:
        for candle in candles[-(lookback + 1):]:
            if candle.close > sweep_price:
                return ReclaimResult(
                    direction=Direction.BUY,
                    reclaim_price=candle.close,
                    held_above=True,
                    is_valid=True,
                )

    elif sweep_direction == Direction.SELL:
        for candle in candles[-(lookback + 1):]:
            if candle.close < sweep_price:
                return ReclaimResult(
                    direction=Direction.SELL,
                    reclaim_price=candle.close,
                    held_above=False,
                    is_valid=True,
                )

    return ReclaimResult(
        direction=sweep_direction,
        reclaim_price=sweep_price,
        held_above=None,
        is_valid=False,
    )
