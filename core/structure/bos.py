"""
BOS and CHoCH detection.

FROZEN IMPLEMENTATION CONSTANTS:
  BOS_LOOKBACK_CANDLES = 2   (number of recent candles checked for a confirmed BOS)

This constant is locked in CLAUDE.md and here.
Do not change without explicit user approval.
"""

from schemas.candle import Candle
from schemas.structure import BOS, CHoCH, SwingPoint, SwingType, BOSDirection

# ─── Frozen constant ─────────────────────────────────────────────────────────

BOS_LOOKBACK_CANDLES = 2


def detect_bos(
    candles: list[Candle],
    swings: list[SwingPoint],
    require_sweep: bool = True,
) -> BOS | None:
    """
    Detect the most recent confirmed Break of Structure.

    Bullish BOS: candle body closes above the most recent swing HIGH.
    Bearish BOS: candle body closes below the most recent swing LOW.

    A wick-only breach does NOT confirm a BOS — the body must close
    beyond the swing level.

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.
    swings : list[SwingPoint]
        Detected swing points for the same timeframe.
    require_sweep : bool
        If True, records the wick sweep price when the wick breaches
        but the body does not yet confirm. Default True.

    Returns
    -------
    BOS | None
        The most recent confirmed BOS, or None.
    """
    if not candles or not swings:
        return None

    recent_high = max(
        (s for s in swings if s.type == SwingType.HIGH),
        key=lambda s: (s.timestamp, s.index),
        default=None,
    )
    recent_low = max(
        (s for s in swings if s.type == SwingType.LOW),
        key=lambda s: (s.timestamp, s.index),
        default=None,
    )

    if recent_high is None or recent_low is None:
        return None

    lookback = candles[-BOS_LOOKBACK_CANDLES:]

    for candle in lookback:
        # Bullish BOS: body closes above swing high
        if candle.close > recent_high.price and candle.open <= recent_high.price:
            sweep_price = None
            if require_sweep and candle.high > recent_high.price:
                sweep_price = candle.high

            return BOS(
                timeframe=candles[0].timeframe.value,
                direction=BOSDirection.BULLISH,
                broken_at=candle.close,
                timestamp=candle.timestamp,
                swept_level=recent_high.price,
                sweep_price=sweep_price,
            )

        # Bearish BOS: body closes below swing low
        if candle.close < recent_low.price and candle.open >= recent_low.price:
            sweep_price = None
            if require_sweep and candle.low < recent_low.price:
                sweep_price = candle.low

            return BOS(
                timeframe=candles[0].timeframe.value,
                direction=BOSDirection.BEARISH,
                broken_at=candle.close,
                timestamp=candle.timestamp,
                swept_level=recent_low.price,
                sweep_price=sweep_price,
            )

    return None


def detect_choch(
    candles: list[Candle],
    prior_bos: BOS,
) -> CHoCH | None:
    """
    Detect a Change of Character following a confirmed BOS.

    CHoCH direction is always opposite to the prior BOS direction.

    Bullish CHoCH (after bearish BOS): candle body closes above the
    bearish BOS broken_at level.

    Bearish CHoCH (after bullish BOS): candle body closes below the
    bullish BOS broken_at level.

    A wick-only breach does NOT confirm CHoCH — the body must close
    beyond the BOS level.

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.
    prior_bos : BOS
        The confirmed BOS that preceded the potential CHoCH.

    Returns
    -------
    CHoCH | None
        The most recent CHoCH, or None if not confirmed.
    """
    if not candles:
        return None

    if prior_bos.direction == BOSDirection.BEARISH:
        for candle in candles:
            if candle.close > prior_bos.broken_at and candle.open <= prior_bos.broken_at:
                return CHoCH(
                    timeframe=candle.timeframe.value,
                    direction=BOSDirection.BULLISH,
                    triggered_at=candle.close,
                    timestamp=candle.timestamp,
                    body_close_above=prior_bos.broken_at,
                )
    elif prior_bos.direction == BOSDirection.BULLISH:
        for candle in candles:
            if candle.close < prior_bos.broken_at and candle.open >= prior_bos.broken_at:
                return CHoCH(
                    timeframe=candle.timeframe.value,
                    direction=BOSDirection.BEARISH,
                    triggered_at=candle.close,
                    timestamp=candle.timestamp,
                    body_close_below=prior_bos.broken_at,
                )

    return None
