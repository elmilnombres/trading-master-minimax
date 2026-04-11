from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class Candle(BaseModel):
    """Normalized OHLCV candle."""

    symbol: str
    timeframe: Timeframe
    timestamp: datetime  # candle open time
    open: float
    high: float
    low: float
    close: float
    volume: float

    # ATR field — populated by market_data layer
    atr: float | None = None

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def total_range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    def is_closed(self, grace_seconds: int = 60) -> bool:
        """
        Check if candle is confirmed closed.
        H4 policy: wait full candle close + grace_seconds before evaluation.
        """
        now = datetime.utcnow()
        elapsed = (now - self.timestamp).total_seconds()
        timeframe_seconds = {
            Timeframe.M1: 60,
            Timeframe.M5: 300,
            Timeframe.M15: 900,
            Timeframe.H1: 3600,
            Timeframe.H4: 14400,
            Timeframe.D1: 86400,
        }.get(self.timeframe, 60)
        # Candle is closed if we are past its open + timeframe duration + grace
        return elapsed >= (timeframe_seconds + grace_seconds)


class CandleBatch(BaseModel):
    """Collection of candles for a symbol + timeframe."""

    symbol: str
    timeframe: Timeframe
    candles: list[Candle] = Field(default_factory=list)

    @property
    def latest(self) -> Candle | None:
        return self.candles[-1] if self.candles else None

    @property
    def latest_closed(self) -> Candle | None:
        for c in reversed(self.candles):
            if c.is_closed():
                return c
        return None