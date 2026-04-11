"""
Liquidity Sweep detection on M5/M1.

FROZEN IMPLEMENTATION CONSTANTS:
  SWEEP_LOOKBACK_CANDLES  = 3
  SWEEP_REVERSAL_REQUIRED = True

BUY-SIDE LIQUIDITY  = resting stop liquidity above relevant highs.
                      Swept when price trades UP through those highs.
SELL-SIDE LIQUIDITY = resting stop liquidity below relevant lows.
                      Swept when price trades DOWN through those lows.

BUY sweep:  price wick rises above level, then candle closes below it.
SELL sweep: price wick drops below level, then candle closes above it.

These constants are locked in CLAUDE.md and here.
Do not change without explicit user approval.
"""

from schemas.candle import Candle
from schemas.confirmation import LiquiditySweep, Direction

# ─── Frozen constants ─────────────────────────────────────────────────────────

SWEEP_LOOKBACK_CANDLES = 3
SWEEP_REVERSAL_REQUIRED = True


def detect_sweep(
    candles: list[Candle],
    level: float,
    direction: Direction,
    require_reversal: bool = SWEEP_REVERSAL_REQUIRED,
    lookback: int = SWEEP_LOOKBACK_CANDLES,
) -> LiquiditySweep | None:
    """
    Detect a liquidity sweep at the given level on M5/M1 candles.

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.
    level : float
        The liquidity level to check for a sweep.
    direction : Direction
        BUY  = looking for buy-side liquidity sweep (stops above level)
        SELL = looking for sell-side liquidity sweep (stops below level)
    require_reversal : bool
        If True, the candle must close back through the level after the wick breach.
        Default SWEEP_REVERSAL_REQUIRED (frozen).
    lookback : int
        Number of recent candles to check.
        Default SWEEP_LOOKBACK_CANDLES (frozen).

    Returns
    -------
    LiquiditySweep | None
        The most recent confirmed sweep, or None.
    """
    if len(candles) < 2:
        return None

    for i in range(len(candles) - 1, max(len(candles) - lookback - 1, -1), -1):
        candle = candles[i]

        if direction == Direction.BUY:
            # Buy-side sweep: wick goes above level, body closes back below
            if candle.low < level <= candle.high:
                if not require_reversal or candle.close < level:
                    return LiquiditySweep(
                        direction=Direction.BUY,
                        sweep_price=candle.high,
                        triggered_at=candle.timestamp,
                        is_valid=True,
                    )

        elif direction == Direction.SELL:
            # Sell-side sweep: wick goes below level, body closes back above
            if candle.low <= level <= candle.high:
                if not require_reversal or candle.close > level:
                    return LiquiditySweep(
                        direction=Direction.SELL,
                        sweep_price=candle.low,
                        triggered_at=candle.timestamp,
                        is_valid=True,
                    )

    return None


def detect_all_sweeps(
    candles: list[Candle],
    levels: list[tuple[float, Direction]],
    require_reversal: bool = SWEEP_REVERSAL_REQUIRED,
    lookback: int = SWEEP_LOOKBACK_CANDLES,
) -> list[LiquiditySweep]:
    """
    Scan multiple levels for liquidity sweeps.

    Parameters
    ----------
    candles : list[Candle]
    levels : list[tuple[float, Direction]]
        List of (level_price, direction) to check.
    require_reversal : bool
    lookback : int

    Returns
    -------
    list[LiquiditySweep]
        All detected sweeps, most recent first.
    """
    sweeps: list[LiquiditySweep] = []
    for level, direction in levels:
        sweep = detect_sweep(candles, level, direction, require_reversal, lookback)
        if sweep is not None:
            sweeps.append(sweep)

    sweeps.sort(key=lambda s: s.triggered_at, reverse=True)
    return sweeps
