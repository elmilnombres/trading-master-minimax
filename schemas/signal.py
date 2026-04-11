"""
Trade signal schema.

setup_quality was removed — not frozen in the contract.
The entry plan fields (entry, stop, target, side) are the only locked signal fields.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from schemas.bias import BiasState, MacroBias
from schemas.order import OrderSide


class POIType(str, Enum):
    ORDER_BLOCK_H4 = "order_block_h4"
    ORDER_BLOCK_H1 = "order_block_h1"
    MITIGATION_ZONE = "mitigation_zone"
    EQUILIBRIUM_50 = "equilibrium_50"
    FVG = "fvg"
    IFVG = "ifvg"
    PDH = "pdh"
    PDL = "pdl"
    PWH = "pwh"
    PWL = "pwl"
    ASIAN_HIGH = "asian_high"
    ASIAN_LOW = "asian_low"
    LONDON_HIGH = "london_high"
    LONDON_LOW = "london_low"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    INDUCEMENT_ZONE = "inducement_zone"


class TriggerType(str, Enum):
    TOUCH = "touch"
    SWEEP = "sweep"
    DISPLACEMENT = "displacement"
    RECLAIM = "reclaim"
    RETEST = "retest"
    FVG_MITIGATION = "fvg_mitigation"
    BODY_CLOSE = "body_close"
    BOS = "bos"
    CHOCH = "choch"


class SignalState(str, Enum):
    PENDING = "pending"      # detected, awaiting trigger
    ACTIVE = "active"        # trigger confirmed, entry in progress
    ENTERED = "entered"      # position opened
    EXPIRED = "expired"      # trigger timed out, signal dead
    INVALIDATED = "invalidated"
    CLOSED = "closed"        # trade completed


class Signal(BaseModel):
    """
    Trade signal — the core unit of the trading system.

    Locked fields: entry_price, stop_loss, take_profit_1, take_profit_2,
    atr_buffer_at_entry, position_side.
    All other fields are Phase 4 bot-app concerns.
    """

    signal_id: str
    bot_id: str  # alpha_bot | beta_bot | gamma_bot
    symbol: str

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None

    # Macro context
    macro_bias: MacroBias | None = None
    bias_state: BiasState | None = None

    # POI
    poi_type: POIType
    poi_price_min: float
    poi_price_max: float

    # Trigger
    trigger_type: TriggerType | None = None
    trigger_price: float | None = None
    trigger_timestamp: datetime | None = None

    # Entry plan
    direction: OrderSide | None = None  # BUY | SELL — required for execution
    entry_price: float | None = None
    entry_type: str = "limit"  # limit | market

    # Risk plan
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None
    atr_buffer_at_entry: float | None = None

    # Sizing
    risk_amount_usdt: float | None = None
    position_size: float | None = None
    estimated_notional: float | None = None

    # State
    state: SignalState = SignalState.PENDING

    # Execution tracking
    client_order_id: str | None = None  # set by ExecutionEngine; persisted for restart recovery
    order_id: str | None = None
    filled_price: float | None = None
    filled_at: datetime | None = None

    # Outcome
    exit_price: float | None = None
    exit_reason: str | None = None
    realized_pnl: float | None = None
    result: str | None = None  # win | loss | breakeven | null

    # Invalidation
    invalidation_reason: str | None = None

    def mark_expired(self, reason: str) -> None:
        self.state = SignalState.EXPIRED
        self.invalidation_reason = reason

    def mark_invalidated(self, reason: str) -> None:
        self.state = SignalState.INVALIDATED
        self.invalidation_reason = reason

    def mark_entered(self, order_id: str, filled_price: float) -> None:
        self.state = SignalState.ENTERED
        self.order_id = order_id
        self.filled_price = filled_price
        self.filled_at = datetime.utcnow()

    def mark_closed(self, exit_price: float, exit_reason: str, realized_pnl: float, result: str) -> None:
        self.state = SignalState.CLOSED
        self.exit_price = exit_price
        self.exit_reason = exit_reason
        self.realized_pnl = realized_pnl
        self.result = result
