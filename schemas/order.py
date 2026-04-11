from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    BUY = "Buy"
    SELL = "Sell"


class OrderType(str, Enum):
    MARKET = "Market"
    LIMIT = "Limit"
    STOP_MARKET = "StopMarket"
    TAKE_PROFIT_MARKET = "TakeProfitMarket"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Order(BaseModel):
    """Normalized order record."""

    order_id: str | None = None  # set by exchange on submission
    client_order_id: str | None = None  # internal idempotency key

    symbol: str
    side: OrderSide
    order_type: OrderType

    price: float | None = None  # for limit orders
    stop_price: float | None = None  # for stop orders

    # Quantity and notional
    qty: float | None = None  # exchange-quantity (after qtyStep quantization)
    estimated_notional: float | None = None

    # Execution results
    avg_price: float | None = None  # filled avg price
    filled_qty: float | None = None
    fee: float | None = None
    fee_currency: str | None = None

    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Metadata
    bot_id: str | None = None
    subaccount_name: str | None = None
    signal_id: str | None = None  # link back to originating signal

    def is_final(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        )

    def is_active(self) -> bool:
        return self.status in (
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        )