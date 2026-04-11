"""
POI schemas — owned by core/poi/.

Defines zone-based POIs (OrderBlock, FVG, iFVG, MitigationZone)
and point-based levels (SessionLevel, PeriodHighLow).
No base class. No significance field. No generic POI type.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────────────────

class POISide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class FVGDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class SessionName(str, Enum):
    ASIAN = "asian"
    LONDON = "london"
    NY = "ny"


class SessionLevelType(str, Enum):
    HIGH = "high"
    LOW = "low"


class PeriodName(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


class PeriodLevelType(str, Enum):
    HIGH = "high"
    LOW = "low"


# ─── Zone-based POIs ─────────────────────────────────────────────────────────

class OrderBlock(BaseModel):
    """
    A zone of 2–3 consecutive bearish (for BUY) or bullish (for SELL) candles
    on H4 or H1, where institutions are expected to defend price.

    Consumed by core/bias/builder.py (price vs POI block).
    """

    id: str
    price_high: float
    price_low: float
    side: POISide
    is_unmitigated: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    source_candle_time: datetime  # open time of the first candle in the OB zone


class FVG(BaseModel):
    """
    Fair Value Gap — a directional zone formed by three candles where the
    middle candle's body creates a gap against the outer candles' bodies.

    Consumed by core/bias/builder.py (price vs POI block)
    and core/confirmation/inducement.py (Beta sequence step 3).
    """

    id: str
    direction: FVGDirection
    price_high: float
    price_low: float
    mitigated: bool = False
    mitigated_at: datetime | None = None
    created_at: datetime  # required — set by detection code from c2.timestamp; no runtime default


class iFVG(BaseModel):
    """
    Inverse Fair Value Gap — a FVG that forms in the direction of the trend,
    typically filled quickly as the move continues.
    Tracked separately from FVG for audit clarity.
    """

    id: str
    direction: FVGDirection
    price_high: float
    price_low: float
    mitigated: bool = False
    mitigated_at: datetime | None = None
    created_at: datetime  # required — set by detection code from c2.timestamp; no runtime default


class MitigationZone(BaseModel):
    """
    A defined price range that price has entered and triggered.

    Consumed by core/bias/builder.py (price vs POI block).
    """

    id: str
    price_high: float
    price_low: float
    triggered: bool = False
    triggered_at: datetime | None = None


# ─── Point-based POIs ────────────────────────────────────────────────────────

class SessionLevel(BaseModel):
    """
    High or low of a named trading session (ASIAN / LONDON / NY),
    computed from M15 candles within that session's time window.

    Consumed by core/bias/builder.py (price vs POI block, tier 3).
    """

    id: str
    session: SessionName
    level_type: SessionLevelType
    price: float  # single point — the high or low of the session
    timestamp: datetime  # open time of the first candle in the session
    period_start: datetime  # UTC calendar start of the session's trading day (for structural identity)
    period_end: datetime   # UTC calendar end of the session's trading day


class PeriodHighLow(BaseModel):
    """
    Previous Day High (PDH), Previous Day Low (PDL),
    Previous Week High (PWH), or Previous Week Low (PWL).

    Computed from actual period OHLC data (highest high / lowest low in period),
    NOT derived from H1 closes.

    Consumed by core/bias/builder.py (price vs POI block, tier 4).
    """

    id: str
    period: PeriodName  # DAILY → PDH/PDL; WEEKLY → PWH/PWL
    level_type: PeriodLevelType
    price: float  # max(high) or min(low) of the previous complete period
    period_start: datetime  # 00:00 UTC of the PREVIOUS period
    period_end: datetime   # 23:59:59 UTC of the PREVIOUS period


# ─── Type alias — defined AFTER all concrete types ────────────────────────────
POI = OrderBlock | FVG | iFVG | SessionLevel | PeriodHighLow
