"""
Market data types — shared across all bots.
No strategy logic here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from schemas.candle import Timeframe


@dataclass
class MarketSnapshot:
    """
    Point-in-time snapshot of market data for a symbol.
    Produced by MarketDataFetcher and consumed by strategy engines.
    """

    symbol: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Price context
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    spread_bps: float | None = None  # basis points

    # ATR (populated by provider)
    atr_14_m1: float | None = None

    # Session state
    is_killzone_london: bool = False
    is_killzone_ny: bool = False

    # H4 candle state
    h4_candle_closed: bool = False  # True only if confirmed closed + 60s grace
    h4_close_time: datetime | None = None
    h4_confirmed_close: float | None = None   # close price of last fully closed H4 candle

    def is_spread_safe(self, max_spread_bps: float = 5.0) -> bool:
        if self.spread_bps is None:
            return True
        return self.spread_bps <= max_spread_bps

    def is_killzone_active(self) -> bool:
        return self.is_killzone_london or self.is_killzone_ny