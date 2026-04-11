"""
Order Block detection.

FROZEN IMPLEMENTATION CONSTANTS:
  OB_MIN_BARS = 2   (minimum consecutive candles in the OB run)
  OB_MAX_BARS = 3   (maximum consecutive candles in the OB run)

These constants are locked in CLAUDE.md and here.
Do not change without explicit user approval.
"""

import uuid
from datetime import datetime

from schemas.candle import Candle
from schemas.poi import OrderBlock, POISide

# ─── Frozen constants ─────────────────────────────────────────────────────────

OB_MIN_BARS = 2
OB_MAX_BARS = 3


def detect_order_blocks(
    candles: list[Candle],
    min_bars: int = OB_MIN_BARS,
    max_bars: int = OB_MAX_BARS,
) -> list[OrderBlock]:
    """
    Detect Order Blocks in a candle sequence.

    BUY OB:  a run of 2–3 consecutive bearish candles followed by
             a bullish candle that closes above the OB zone high.
             OB zone high = highest high in the bearish run.
    SELL OB: a run of 2–3 consecutive bullish candles followed by
             a bearish candle that closes below the OB zone low.
             OB zone low = lowest low in the bullish run.

    "Most recent" selection: sort by created_at descending.
    No significance field. No ranking.

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.
    min_bars : int
        Minimum consecutive candles in the OB run.
        Default OB_MIN_BARS (frozen).
    max_bars : int
        Maximum consecutive candles in the OB run.
        Default OB_MAX_BARS (frozen).

    Returns
    -------
    list[OrderBlock]
        All detected Order Blocks, most recent first.
    """
    if len(candles) < min_bars + 1:
        return []

    order_blocks: list[OrderBlock] = []

    for i in range(len(candles) - min_bars):
        for run_length in range(min_bars, min(max_bars + 1, len(candles) - i)):
            run = candles[i : i + run_length]
            next_candle = candles[i + run_length]

            ob = _try_build_ob(run, next_candle, candles[0].timeframe.value)
            if ob is not None:
                order_blocks.append(ob)
                break

    order_blocks.sort(key=lambda ob: ob.created_at, reverse=True)
    return order_blocks


def _try_build_ob(
    run: list[Candle],
    next_candle: Candle,
    timeframe: str,
) -> OrderBlock | None:
    """
    Attempt to build an OrderBlock from a consecutive candle run.

    BUY OB:  run is bearish (close < open), next_candle is bullish,
             next_candle closes above the run's high.
    SELL OB: run is bullish (close > open), next_candle is bearish,
             next_candle closes below the run's low.
    """
    if len(run) < 2:
        return None

    run_is_bearish = all(c.close < c.open for c in run)
    run_is_bullish = all(c.close > c.open for c in run)

    if run_is_bearish and next_candle.close > next_candle.open:
        zone_high = max(c.high for c in run)
        if next_candle.close <= zone_high:
            return None
        zone_low = min(c.low for c in run)
        return OrderBlock(
            id=str(uuid.uuid4())[:8],
            price_high=zone_high,
            price_low=zone_low,
            side=POISide.BUY,
            is_unmitigated=True,
            created_at=datetime.utcnow(),
            source_candle_time=run[0].timestamp,
        )

    if run_is_bullish and next_candle.close < next_candle.open:
        zone_low = min(c.low for c in run)
        if next_candle.close >= zone_low:
            return None
        zone_high = max(c.high for c in run)
        return OrderBlock(
            id=str(uuid.uuid4())[:8],
            price_high=zone_high,
            price_low=zone_low,
            side=POISide.SELL,
            is_unmitigated=True,
            created_at=datetime.utcnow(),
            source_candle_time=run[0].timestamp,
        )

    return None
