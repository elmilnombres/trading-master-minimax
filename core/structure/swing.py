"""
Swing detection — identifies local swing highs and swing lows on a given timeframe.

FROZEN IMPLEMENTATION CONSTANTS:
  SWING_LOOKBACK_LEFT  = 3   (number of candles to the left that must be lower)
  SWING_LOOKBACK_RIGHT = 1   (number of candles to the right that must be lower)

These constants are locked in CLAUDE.md and here.
Do not change without explicit user approval.
"""

from schemas.candle import Candle
from schemas.structure import SwingPoint, SwingType

# ─── Frozen constants ─────────────────────────────────────────────────────────

SWING_LOOKBACK_LEFT = 3
SWING_LOOKBACK_RIGHT = 1


def detect_swings(
    candles: list[Candle],
    left_n: int = SWING_LOOKBACK_LEFT,
    right_n: int = SWING_LOOKBACK_RIGHT,
) -> list[SwingPoint]:
    """
    Detect local swing highs and swing lows in a candle sequence.

    A swing high at index i requires:
      - All candles in [i-left_n, i) have high < candles[i].high
      - All candles in (i, i+right_n] have high < candles[i].high

    A swing low at index i requires the inverse.

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.
    left_n : int
        Number of candles to the left that must be lower.
        Default SWING_LOOKBACK_LEFT (frozen).
    right_n : int
        Number of candles to the right that must be lower.
        Default SWING_LOOKBACK_RIGHT (frozen).

    Returns
    -------
    list[SwingPoint]
        All detected swing points, ordered oldest → newest.
    """
    if len(candles) < left_n + right_n + 1:
        return []

    swings: list[SwingPoint] = []

    for i in range(left_n, len(candles) - right_n):
        candle = candles[i]

        left_slice = candles[i - left_n : i]
        right_slice = candles[i + 1 : i + right_n + 1]

        is_swing_high = (
            all(c.high < candle.high for c in left_slice)
            and all(c.high < candle.high for c in right_slice)
        )

        is_swing_low = (
            all(c.low > candle.low for c in left_slice)
            and all(c.low > candle.low for c in right_slice)
        )

        if is_swing_high and not is_swing_low:
            swings.append(
                SwingPoint(
                    index=i,
                    type=SwingType.HIGH,
                    price=candle.high,
                    timestamp=candle.timestamp,
                )
            )
        elif is_swing_low and not is_swing_high:
            swings.append(
                SwingPoint(
                    index=i,
                    type=SwingType.LOW,
                    price=candle.low,
                    timestamp=candle.timestamp,
                )
            )

    return swings


def get_most_recent_swing(
    swings: list[SwingPoint],
    swing_type: SwingType,
) -> SwingPoint | None:
    """
    Return the swing point with the latest timestamp of the given type.

    "Most recent" selection rule: max by (timestamp DESC, index DESC).
    Secondary key (index) ensures total ordering when timestamps coincide.
    A later index means the swing occurred later in the candle sequence.
    """
    matching = [s for s in swings if s.type == swing_type]
    if not matching:
        return None
    return max(matching, key=lambda s: (s.timestamp, s.index))
