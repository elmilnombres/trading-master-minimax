"""
Structure schemas — owned by core/structure/.

Defines SwingPoint, BOS, and CHoCH as concrete types.
No base class. No unions. No vague fields.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class SwingType(str, Enum):
    HIGH = "high"
    LOW = "low"


class BOSDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class SwingPoint(BaseModel):
    """
    A local swing high or swing low on a given timeframe.

    Detected by core/structure/swing.py.
    Consumed by core/bias/builder.py and core/poi/ modules.
    """

    index: int  # position in the candle sequence (0 = oldest)
    type: SwingType
    price: float
    timestamp: datetime  # open time of the candle that formed this swing


class BOS(BaseModel):
    """
    Break of Structure — price has displaced beyond a prior swing high/low
    on a given timeframe. Confirmed on the candle that closes beyond the level.

    Consumed by core/bias/builder.py (draw on liquidity block)
    and core/confirmation/ modules.
    """

    timeframe: str
    direction: BOSDirection
    broken_at: float  # price level at which the BOS was confirmed (body close)
    timestamp: datetime  # time of the candle that confirmed the BOS
    swept_level: float  # structural level that was displaced (swing high for bullish, swing low for bearish)
    sweep_price: float | None = None  # optional: wick extreme that swept past swept_level


class CHoCH(BaseModel):
    """
    Change of Character — the first candle that closes beyond the opposite
    side of the most recent BOS, confirming a structural shift.
    Requires body close on the correct side (not just wick).

    Consumed by core/confirmation/inducement.py (Beta sequence step 2).
    """

    timeframe: str
    direction: BOSDirection  # direction of the CHoCH (same as the break that triggered it)
    triggered_at: float  # price at the candle close that confirmed CHoCH
    timestamp: datetime  # time of the triggering candle
    body_close_above: float | None = None  # for bullish CHoCH: body closed above this level
    body_close_below: float | None = None  # for bearish CHoCH: body closed below this level
