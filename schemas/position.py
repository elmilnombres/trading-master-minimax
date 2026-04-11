from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Position(BaseModel):
    """Normalized position record."""

    symbol: str
    side: PositionSide

    # Entry
    entry_price: float | None = None
    qty: float | None = None

    # Stops and targets
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None

    # PnL
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None

    # Timestamps
    opened_at: datetime | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Metadata
    bot_id: str | None = None
    subaccount_name: str | None = None
    signal_id: str | None = None

    def is_open(self) -> bool:
        return self.side != PositionSide.FLAT and self.qty is not None and self.qty > 0

    def notional(self) -> float | None:
        if self.entry_price is None or self.qty is None:
            return None
        return self.entry_price * self.qty