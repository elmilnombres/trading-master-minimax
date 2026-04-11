"""
FVG and iFVG detection.

FVG:  Fair Value Gap — a zone formed by three candles where the middle
      candle's body creates a gap against the outer candles' bodies on M15/M5.

FVG bullish:  candle N high < candle N+2 low  (gap to the upside)
FVG bearish:  candle N low > candle N+2 high  (gap to the downside)

iFVG: Inverse FVG — a FVG that forms in the direction of the trend,
      typically filled quickly as the move continues.
      Same zone definition as FVG; tracked separately for audit clarity.

Inputs:  CandleBatch (M15 or M5)
Output: list[FVG], list[iFVG]
"""

import uuid
from datetime import datetime

from schemas.candle import Candle
from schemas.poi import FVG, iFVG, FVGDirection


def detect_fvgs(candles: list[Candle]) -> list[FVG]:
    """
    Detect Fair Value Gaps in a candle sequence.

    An FVG requires three consecutive candles where the middle candle
    creates a body gap against the outer two.

    Bullish FVG: candles[N].high < candles[N+2].low
    Bearish FVG: candles[N].low > candles[N+2].high

    The FVG zone:
      price_high = candles[N+2].low
      price_low  = candles[N].high

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.

    Returns
    -------
    list[FVG]
        All detected FVGs, most recent first.
    """
    if len(candles) < 3:
        return []

    fvgs: list[FVG] = []

    for i in range(len(candles) - 2):
        c1 = candles[i]
        c2 = candles[i + 1]
        c3 = candles[i + 2]

        if c1.high < c3.low:
            fvgs.append(
                FVG(
                    id=str(uuid.uuid4())[:8],
                    direction=FVGDirection.BULLISH,
                    price_high=c3.low,
                    price_low=c1.high,
                    mitigated=False,
                    created_at=c2.timestamp,
                )
            )
        elif c1.low > c3.high:
            fvgs.append(
                FVG(
                    id=str(uuid.uuid4())[:8],
                    direction=FVGDirection.BEARISH,
                    price_high=c1.low,
                    price_low=c3.high,
                    mitigated=False,
                    created_at=c2.timestamp,
                )
            )

    fvgs.sort(key=lambda f: f.created_at, reverse=True)
    return fvgs


def detect_ifvgs(candles: list[Candle]) -> list[iFVG]:
    """
    Detect Inverse Fair Value Gaps.

    An iFVG is a gap that forms in the direction of the prevailing trend,
    typically on lower timeframes as a continuation pattern.

    Bullish iFVG:  c1.high < c3.low  AND c1 and c3 are bullish candles.
    Bearish iFVG:  c1.low > c3.high AND c1 and c3 are bearish candles.

    Zone definition is identical to FVG; separation is for audit clarity.

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.

    Returns
    -------
    list[iFVG]
        All detected iFVGs, most recent first.
    """
    if len(candles) < 3:
        return []

    ifvgs: list[iFVG] = []

    for i in range(len(candles) - 2):
        c1 = candles[i]
        c2 = candles[i + 1]
        c3 = candles[i + 2]

        if c1.high < c3.low and c1.is_bullish and c3.is_bullish:
            ifvgs.append(
                iFVG(
                    id=str(uuid.uuid4())[:8],
                    direction=FVGDirection.BULLISH,
                    price_high=c3.low,
                    price_low=c1.high,
                    mitigated=False,
                    created_at=c2.timestamp,
                )
            )
        elif c1.low > c3.high and not c1.is_bullish and not c3.is_bullish:
            ifvgs.append(
                iFVG(
                    id=str(uuid.uuid4())[:8],
                    direction=FVGDirection.BEARISH,
                    price_high=c1.low,
                    price_low=c3.high,
                    mitigated=False,
                    created_at=c2.timestamp,
                )
            )

    ifvgs.sort(key=lambda f: f.created_at, reverse=True)
    return ifvgs
