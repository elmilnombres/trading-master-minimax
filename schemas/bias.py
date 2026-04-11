"""
Bias schemas — owned by core/bias/.

Replaces the Phase 1 MacroBias class with explicit block-based types.
No composite scores. No weights. No vague fields.
Each block is a deterministic evaluation with a named rule_fired string.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class BiasState(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class LiquiditySide(str, Enum):
    """
    BUY-SIDE LIQUIDITY  = resting stop liquidity above relevant highs.
                          Swept when price moves UP through those highs.
    SELL-SIDE LIQUIDITY = resting stop liquidity below relevant lows.
                          Swept when price moves DOWN through those lows.
    """

    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


# ─── Individual bias blocks ───────────────────────────────────────────────────

class BiasBlockH4(BaseModel):
    """
    H4 Structure block.

    BULLISH: last confirmed H4 close > most recent H4 swing HIGH.
    BEARISH: last confirmed H4 close < most recent H4 swing LOW.
    NEUTRAL: otherwise.

    Input: last confirmed H4 close, list[SwingPoint].
    """

    state: BiasState
    confirmed_h4_close: float | None = None
    swing_high_price: float | None = None
    swing_low_price: float | None = None
    rule_fired: str = ""


class BiasBlockDraw(BaseModel):
    """
    Draw on Liquidity block.

    BUY-SIDE LIQUIDITY  = resting stop liquidity above relevant highs.
                          Swept when price trades UP through those highs.
    SELL-SIDE LIQUIDITY = resting stop liquidity below relevant lows.
                          Swept when price trades DOWN through those lows.

    BULLISH: buy-side liquidity swept AND last confirmed H4 close > sweep level.
    BEARISH: sell-side liquidity swept AND last confirmed H4 close < sweep level.
    NEUTRAL: otherwise.

    Input: list[BOS], last confirmed H4 close, sweep level.
    """

    state: BiasState
    liquidity_swept: LiquiditySide | None  # BUY_SIDE or SELL_SIDE; None if no sweep
    sweep_level: float | None
    reclaim_price: float | None
    rule_fired: str = ""


class BiasBlockPOI(BaseModel):
    """
    Price vs POI block — 4-tier precedence using concrete POI types.

    Tier 1: OrderBlock (most specific — zone with confirmed directional origin)
    Tier 2: FVG / iFVG (zone-based, directional)
    Tier 3: SessionLevel ASIAN/LONDON/NY HIGH/LOW (point-based)
    Tier 4: PeriodHighLow PDH/PDL/PWH/PWL (point-based)

    BULLISH: last confirmed H4 close > reference price of highest-priority unmitigated bullish POI.
    BEARISH: last confirmed H4 close < reference price of highest-priority unmitigated bearish POI.
    NEUTRAL: at or inside the reference zone, OR no POI exists in any tier.

    Input: last confirmed H4 close, orderblocks, fvgs, ifvgs, session_levels, period_levels.
    """

    state: BiasState
    relevant_poi_type: str | None = None  # e.g. "OrderBlock", "FVG", "SessionLevel", "PeriodHighLow"
    relevant_poi_id: str | None = None
    reference_price: float | None = None
    rule_fired: str = ""


class BiasBlockD1(BaseModel):
    """
    D1 Context block.

    BULLISH: 2 consecutive D1 upward closes.
    BEARISH: 2 consecutive D1 downward closes.
    NEUTRAL: otherwise.

    Input: CandleBatch (D1, 3+ candles).
    """

    state: BiasState
    d1_close: float | None = None
    prior_d1_close: float | None = None
    consecutive_directional_count: int = 0
    rule_fired: str = ""


class BiasBlockH1(BaseModel):
    """
    H1 Internal State block.

    BULLISH: 2 consecutive H1 upward closes.
    BEARISH: 2 consecutive H1 downward closes.
    NEUTRAL: otherwise.

    Input: CandleBatch (H1, 3+ candles).
    """

    state: BiasState
    h1_close: float | None = None
    prior_h1_close: float | None = None
    consecutive_directional_count: int = 0
    rule_fired: str = ""


# ─── Assembly ────────────────────────────────────────────────────────────────

class MacroBias(BaseModel):
    """
    Macro bias assembled from 5 explicit blocks.
    Final state is derived by deterministic decision table, not by scoring.

    Decision table:
      - If H4_structure == NEUTRAL → NEUTRAL
      - If H4_structure == BULLISH AND POI == BULLISH → BULLISH
      - If H4_structure == BEARISH AND POI == BEARISH → BEARISH
      - If H4_structure and POI disagree → NEUTRAL
      - Otherwise → NEUTRAL
    """

    state: BiasState

    # Source candle timestamps for traceability
    h4_close_time: datetime | None = None
    d1_close_time: datetime | None = None
    h1_close_time: datetime | None = None

    # Five blocks
    h4: BiasBlockH4
    draw: BiasBlockDraw
    poi: BiasBlockPOI
    d1: BiasBlockD1
    h1: BiasBlockH1

    built_at: datetime


class BiasInvalidationResult(BaseModel):
    """
    Result of a bias invalidation check.

    valid == True: bias is still consistent with current market state.
    valid == False: one or more blocks have been invalidated.
    triggered_blocks names which blocks fired the invalidation.
    """

    valid: bool
    reason: str | None = None
    triggered_blocks: list[str] = []
