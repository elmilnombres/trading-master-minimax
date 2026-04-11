"""
Macro Bias Builder.

Constructs a MacroBias from 5 explicit blocks using deterministic rules.
No scoring. No weights. No EMA. No Asian Range as a rule input.

FROZEN H4 SEMANTICS:
  When market.h4_candle_closed == True:
    H4-dependent blocks (H4 Structure, Draw on Liquidity, Price vs POI) are
    evaluated using market.h4_confirmed_close (not live price).
  When market.h4_candle_closed == False:
    If previous_bias is not None: H4/Draw/POI blocks are carried from previous_bias.
                                  Final state = previous_bias.state.
    If previous_bias is None:     H4/Draw/POI blocks = NEUTRAL.
                                  Final state = NEUTRAL.
  D1 and H1 blocks are always evaluated from latest confirmed candles.

FROZEN POI SELECTION (POI-FIRST, DIRECTION-AGNOSTIC):
  1. Deduplicate by structural identity before ranking.
  2. Sort all POIs by (tier, -source_timestamp, price_low, price_high).
  3. Select the single highest-priority POI.
  4. Evaluate that POI against the H4-derived bias direction.
     Conflict = NEUTRAL. No second attempt. No fallback to opposite direction.
"""

from dataclasses import dataclass
from datetime import datetime

from schemas.candle import Candle
from schemas.poi import (
    OrderBlock,
    FVG,
    iFVG,
    SessionLevel,
    PeriodHighLow,
    POI,
)
from schemas.structure import SwingPoint, BOS, SwingType, BOSDirection
from schemas.bias import (
    BiasState,
    BiasBlockH4,
    BiasBlockDraw,
    BiasBlockPOI,
    BiasBlockD1,
    BiasBlockH1,
    LiquiditySide,
    MacroBias,
)
from core.market_data.types import MarketSnapshot
from core.structure.swing import get_most_recent_swing


# ─── Input container ─────────────────────────────────────────────────────────

@dataclass
class MacroBiasInput:
    """
    Explicit inputs for macro bias construction.
    """

    market: MarketSnapshot
    swings_h4: list[SwingPoint]
    swings_h1: list[SwingPoint]
    bos_list: list[BOS]
    candles_d1: list[Candle]
    candles_h1: list[Candle]
    orderblocks: list[OrderBlock]
    fvgs: list[FVG]
    ifvgs: list[iFVG]
    session_levels: list[SessionLevel]
    period_levels: list[PeriodHighLow]

    # Carry-forward: last confirmed MacroBias.
    # Used only when market.h4_candle_closed == False.
    previous_bias: MacroBias | None = None


# ─── Block evaluators ───────────────────────────────────────────────────────

def _evaluate_h4_structure(
    h4_close: float,
    swings: list[SwingPoint],
) -> BiasBlockH4:
    """Block 1: H4 Structure."""
    swing_high = get_most_recent_swing(swings, SwingType.HIGH)
    swing_low = get_most_recent_swing(swings, SwingType.LOW)

    if swing_high is None and swing_low is None:
        return BiasBlockH4(state=BiasState.NEUTRAL, rule_fired="no swings found")

    if swing_high is not None and h4_close > swing_high.price:
        return BiasBlockH4(
            state=BiasState.BULLISH,
            confirmed_h4_close=h4_close,
            swing_high_price=swing_high.price,
            swing_low_price=swing_low.price if swing_low else None,
            rule_fired="last confirmed H4 close > most recent H4 swing HIGH",
        )

    if swing_low is not None and h4_close < swing_low.price:
        return BiasBlockH4(
            state=BiasState.BEARISH,
            confirmed_h4_close=h4_close,
            swing_high_price=swing_high.price if swing_high else None,
            swing_low_price=swing_low.price,
            rule_fired="last confirmed H4 close < most recent H4 swing LOW",
        )

    return BiasBlockH4(
        state=BiasState.NEUTRAL,
        confirmed_h4_close=h4_close,
        swing_high_price=swing_high.price if swing_high else None,
        swing_low_price=swing_low.price if swing_low else None,
        rule_fired="last confirmed H4 close at or inside swing range",
    )


def _evaluate_draw_on_liquidity(
    h4_close: float,
    bos_list: list[BOS],
) -> BiasBlockDraw:
    """
    Draw on Liquidity — evaluated inside core/bias using macro inputs only.

    Inputs (all from MacroBiasInput):
      h4_close  — market.h4_confirmed_close (not live price)
      bos_list  — confirmed BOS list

    No dependency on core/confirmation.

    Draw fires when:
      - The most recent BOS has a swept_level
      - AND the H4 close has reclaimed above (bullish) or below (bearish) that level
        relative to the sweep direction

    Reclaim evaluated against swept_level, NOT sweep_price (wick extreme).
    """
    if not bos_list:
        return BiasBlockDraw(state=BiasState.NEUTRAL, rule_fired="no BOS found")

    most_recent_bos = max(bos_list, key=lambda b: b.timestamp)

    sweep_level = most_recent_bos.swept_level  # structural level, not wick extreme

    if most_recent_bos.direction == BOSDirection.BULLISH:
        liquidity_swept = LiquiditySide.BUY_SIDE   # swept UP through highs → buy-side liquidity
        if h4_close > sweep_level:
            return BiasBlockDraw(
                state=BiasState.BULLISH,
                liquidity_swept=liquidity_swept,
                sweep_level=sweep_level,
                reclaim_price=h4_close,
                rule_fired=f"BUY_SIDE liquidity swept at {sweep_level}, H4 reclaimed above at {h4_close}",
            )
        return BiasBlockDraw(
            state=BiasState.NEUTRAL,
            liquidity_swept=liquidity_swept,
            sweep_level=sweep_level,
            rule_fired=f"BUY_SIDE liquidity swept at {sweep_level} but H4 close {h4_close} not above",
        )

    elif most_recent_bos.direction == BOSDirection.BEARISH:
        liquidity_swept = LiquiditySide.SELL_SIDE  # swept DOWN through lows → sell-side liquidity
        if h4_close < sweep_level:
            return BiasBlockDraw(
                state=BiasState.BEARISH,
                liquidity_swept=liquidity_swept,
                sweep_level=sweep_level,
                reclaim_price=h4_close,
                rule_fired=f"SELL_SIDE liquidity swept at {sweep_level}, H4 reclaimed below at {h4_close}",
            )
        return BiasBlockDraw(
            state=BiasState.NEUTRAL,
            liquidity_swept=liquidity_swept,
            sweep_level=sweep_level,
            rule_fired=f"SELL_SIDE liquidity swept at {sweep_level} but H4 close {h4_close} not below",
        )

    return BiasBlockDraw(
        state=BiasState.NEUTRAL,
        liquidity_swept=None,
        sweep_level=None,
        rule_fired="no valid BOS direction",
    )


# ─── POI-First selection — total ordering ────────────────────────────────────

def _poi_tier(poi: POI) -> int:
    """POI type priority tier. Lower = higher priority."""
    if   isinstance(poi, OrderBlock):                    return 1
    elif isinstance(poi, (FVG, iFVG)):                   return 2
    elif isinstance(poi, SessionLevel):                  return 3
    elif isinstance(poi, PeriodHighLow):                  return 4
    raise ValueError(f"Unknown POI type: {type(poi)}")


def _poi_source_timestamp(poi: POI) -> float:
    """Market-derived timestamp for POI selection. Primary key for recency."""
    if   isinstance(poi, (OrderBlock, FVG, iFVG)):  return poi.created_at.timestamp()
    elif isinstance(poi, SessionLevel):              return poi.timestamp.timestamp()
    elif isinstance(poi, PeriodHighLow):             return poi.period_end.timestamp()


def _poi_identity(poi: POI) -> tuple:
    """
    Structural identity key — deduplication criterion.
    Two POIs with identical identity keys are exact duplicates and one is kept.
    """
    if   isinstance(poi, OrderBlock):
        return (type(poi).__name__, poi.price_low, poi.price_high, poi.side.value, poi.source_candle_time.timestamp())
    elif isinstance(poi, (FVG, iFVG)):
        return (type(poi).__name__, poi.price_low, poi.price_high, poi.created_at.timestamp())
    elif isinstance(poi, SessionLevel):
        return (type(poi).__name__, poi.session.value, poi.level_type.value, poi.price, poi.period_start.timestamp())
    elif isinstance(poi, PeriodHighLow):
        return (type(poi).__name__, poi.period.value, poi.level_type.value, poi.price, poi.period_start.timestamp())


def _poi_sort_key(poi: POI) -> tuple:
    """
    Total ordering sort key for POI selection.

    Levels:
      1. tier             — POI type priority (1=OB, 2=FVG/iFVG, 3=SessionLevel, 4=PeriodHighLow)
      2. -source_ts       — most recent by market-derived timestamp (negated = descending)
      3. price_low        — zone lower boundary (ascending)
      4. price_high       — zone upper boundary (ascending)

    All fields are market-derived, stable, and direction-neutral.
    """
    tier = _poi_tier(poi)
    ts   = _poi_source_timestamp(poi)

    if isinstance(poi, (OrderBlock, FVG, iFVG)):
        return (tier, -ts, poi.price_low, poi.price_high)
    elif isinstance(poi, (SessionLevel, PeriodHighLow)):
        return (tier, -ts, poi.price, poi.price)


def _deduplicate_pois(all_pois: list[POI]) -> list[POI]:
    """
    Deduplicate by structural identity.
    When duplicates exist, the one with the earliest source timestamp is kept
    (earliest in market time = the one that occurred first).
    """
    seen: dict[tuple, POI] = {}
    for poi in all_pois:
        key = _poi_identity(poi)
        if key not in seen:
            # On first encounter, store directly; subsequent duplicates are skipped
            seen[key] = poi
        else:
            # Duplicate found — keep the one with earlier source timestamp
            existing_ts = _poi_source_timestamp(seen[key])
            new_ts = _poi_source_timestamp(poi)
            if new_ts < existing_ts:
                seen[key] = poi
    return list(seen.values())


def _select_relevant_poi(
    orderblocks: list[OrderBlock],
    fvgs: list[FVG],
    ifvgs: list[iFVG],
    session_levels: list[SessionLevel],
    period_levels: list[PeriodHighLow],
) -> POI | None:
    """
    POI-FIRST selection: select one POI using explicit precedence + total ordering.

    Selection is DIRECTION-AGNOSTIC. No bias_direction is accepted.
    The selected POI is determined solely by priority tier + recency + structural tie-break.
    """
    all_pois: list[POI] = list(orderblocks) + list(fvgs) + list(ifvgs) + list(session_levels) + list(period_levels)
    if not all_pois:
        return None

    unique = _deduplicate_pois(all_pois)
    unique.sort(key=_poi_sort_key)
    return unique[0] if unique else None


def _evaluate_poi(
    poi: POI | None,
    bias_direction: BiasState,
    h4_close: float,
) -> BiasBlockPOI:
    """
    Evaluate the already-selected POI against the H4-derived bias direction.

    This is called AFTER _select_relevant_poi has returned one POI or None.
    No secondary POI is attempted. Conflict = immediate NEUTRAL.

    Rules:
      - OrderBlock: inherent direction from side field
        * BUY + bullish bias → BULLISH
        * SELL + bearish bias → BEARISH
        * All other combinations → NEUTRAL
      - FVG / iFVG: inherent direction from direction field
        * BULLISH + bullish bias → BULLISH
        * BEARISH + bearish bias → BEARISH
        * All other combinations → NEUTRAL
      - SessionLevel / PeriodHighLow: price-relative
        * h4_close above price + bullish bias → BULLISH
        * h4_close below price + bearish bias → BEARISH
        * At or inside zone / NEUTRAL bias → NEUTRAL
    """
    if poi is None:
        return BiasBlockPOI(
            state=BiasState.NEUTRAL,
            relevant_poi_type=None,
            relevant_poi_id=None,
            reference_price=None,
            rule_fired="no POI found at any tier",
        )

    if bias_direction == BiasState.NEUTRAL:
        return BiasBlockPOI(
            state=BiasState.NEUTRAL,
            relevant_poi_type=type(poi).__name__,
            relevant_poi_id=poi.id,
            reference_price=_poi_reference_price(poi),
            rule_fired="H4 bias NEUTRAL — POI evaluation skipped",
        )

    # ── OrderBlock ──────────────────────────────────────────────────────────
    if isinstance(poi, OrderBlock):
        if poi.side.value == "buy" and bias_direction == BiasState.BULLISH:
            return BiasBlockPOI(
                state=BiasState.BULLISH,
                relevant_poi_type="OrderBlock",
                relevant_poi_id=poi.id,
                reference_price=poi.price_high,
                rule_fired=f"TIER1: BUY OB with bullish H4 bias → BULLISH (H4 close {h4_close} vs OB high {poi.price_high})",
            )
        if poi.side.value == "sell" and bias_direction == BiasState.BEARISH:
            return BiasBlockPOI(
                state=BiasState.BEARISH,
                relevant_poi_type="OrderBlock",
                relevant_poi_id=poi.id,
                reference_price=poi.price_low,
                rule_fired=f"TIER1: SELL OB with bearish H4 bias → BEARISH (H4 close {h4_close} vs OB low {poi.price_low})",
            )
        # Conflict: direction mismatch → NEUTRAL
        return BiasBlockPOI(
            state=BiasState.NEUTRAL,
            relevant_poi_type="OrderBlock",
            relevant_poi_id=poi.id,
            reference_price=poi.price_high,
            rule_fired=f"TIER1: OB side '{poi.side.value}' conflicts with H4 bias '{bias_direction.value}' → NEUTRAL",
        )

    # ── FVG / iFVG ────────────────────────────────────────────────────────────
    if isinstance(poi, (FVG, iFVG)):
        poi_type = type(poi).__name__
        if poi.direction.value == "bullish" and bias_direction == BiasState.BULLISH:
            return BiasBlockPOI(
                state=BiasState.BULLISH,
                relevant_poi_type=poi_type,
                relevant_poi_id=poi.id,
                reference_price=poi.price_high,
                rule_fired=f"TIER2: {poi_type} bullish with bullish H4 bias → BULLISH (H4 close {h4_close} vs {poi_type} high {poi.price_high})",
            )
        if poi.direction.value == "bearish" and bias_direction == BiasState.BEARISH:
            return BiasBlockPOI(
                state=BiasState.BEARISH,
                relevant_poi_type=poi_type,
                relevant_poi_id=poi.id,
                reference_price=poi.price_low,
                rule_fired=f"TIER2: {poi_type} bearish with bearish H4 bias → BEARISH (H4 close {h4_close} vs {poi_type} low {poi.price_low})",
            )
        # Conflict: direction mismatch → NEUTRAL
        return BiasBlockPOI(
            state=BiasState.NEUTRAL,
            relevant_poi_type=poi_type,
            relevant_poi_id=poi.id,
            reference_price=poi.price_high,
            rule_fired=f"TIER2: {poi_type} direction '{poi.direction.value}' conflicts with H4 bias '{bias_direction.value}' → NEUTRAL",
        )

    # ── SessionLevel / PeriodHighLow ─────────────────────────────────────────
    if isinstance(poi, (SessionLevel, PeriodHighLow)):
        poi_type = type(poi).__name__
        price = poi.price
        if bias_direction == BiasState.BULLISH and h4_close > price:
            return BiasBlockPOI(
                state=BiasState.BULLISH,
                relevant_poi_type=poi_type,
                relevant_poi_id=poi.id,
                reference_price=price,
                rule_fired=f"TIER{_poi_tier(poi)}: {poi_type} with bullish H4 bias → BULLISH (H4 close {h4_close} > level {price})",
            )
        if bias_direction == BiasState.BEARISH and h4_close < price:
            return BiasBlockPOI(
                state=BiasState.BEARISH,
                relevant_poi_type=poi_type,
                relevant_poi_id=poi.id,
                reference_price=price,
                rule_fired=f"TIER{_poi_tier(poi)}: {poi_type} with bearish H4 bias → BEARISH (H4 close {h4_close} < level {price})",
            )
        # At or inside level, or direction mismatch → NEUTRAL
        return BiasBlockPOI(
            state=BiasState.NEUTRAL,
            relevant_poi_type=poi_type,
            relevant_poi_id=poi.id,
            reference_price=price,
            rule_fired=f"TIER{_poi_tier(poi)}: {poi_type} at or inside level {price} with H4 bias '{bias_direction.value}' → NEUTRAL",
        )

    # Fallback — should not reach here
    return BiasBlockPOI(
        state=BiasState.NEUTRAL,
        relevant_poi_type=None,
        relevant_poi_id=None,
        reference_price=None,
        rule_fired=f"unknown POI type: {type(poi)}",
    )


def _poi_reference_price(poi: POI) -> float | None:
    """Extract the reference price from any POI type."""
    if isinstance(poi, (OrderBlock, FVG, iFVG)):
        return poi.price_high
    if isinstance(poi, (SessionLevel, PeriodHighLow)):
        return poi.price
    return None


# ─── Remaining blocks ────────────────────────────────────────────────────────

def _evaluate_d1_context(candles: list[Candle]) -> BiasBlockD1:
    """Block 4: D1 Context — 2 consecutive directional closes."""
    if len(candles) < 3:
        return BiasBlockD1(
            state=BiasState.NEUTRAL,
            rule_fired=f"insufficient D1 candles ({len(candles)} < 3)",
        )

    c_recent = candles[-1]
    c_prior = candles[-2]
    c_before = candles[-3]

    if c_recent.close > c_prior.close and c_prior.close > c_before.close:
        return BiasBlockD1(
            state=BiasState.BULLISH,
            d1_close=c_recent.close,
            prior_d1_close=c_prior.close,
            consecutive_directional_count=2,
            rule_fired="2 consecutive D1 upward closes",
        )

    if c_recent.close < c_prior.close and c_prior.close < c_before.close:
        return BiasBlockD1(
            state=BiasState.BEARISH,
            d1_close=c_recent.close,
            prior_d1_close=c_prior.close,
            consecutive_directional_count=2,
            rule_fired="2 consecutive D1 downward closes",
        )

    return BiasBlockD1(
        state=BiasState.NEUTRAL,
        d1_close=c_recent.close,
        prior_d1_close=c_prior.close,
        consecutive_directional_count=0,
        rule_fired="no consecutive directional closes on D1",
    )


def _evaluate_h1_internal(candles: list[Candle]) -> BiasBlockH1:
    """Block 5: H1 Internal State — 2 consecutive directional closes."""
    if len(candles) < 3:
        return BiasBlockH1(
            state=BiasState.NEUTRAL,
            rule_fired=f"insufficient H1 candles ({len(candles)} < 3)",
        )

    c_recent = candles[-1]
    c_prior = candles[-2]
    c_before = candles[-3]

    if c_recent.close > c_prior.close and c_prior.close > c_before.close:
        return BiasBlockH1(
            state=BiasState.BULLISH,
            h1_close=c_recent.close,
            prior_h1_close=c_prior.close,
            consecutive_directional_count=2,
            rule_fired="2 consecutive H1 upward closes",
        )

    if c_recent.close < c_prior.close and c_prior.close < c_before.close:
        return BiasBlockH1(
            state=BiasState.BEARISH,
            h1_close=c_recent.close,
            prior_h1_close=c_prior.close,
            consecutive_directional_count=2,
            rule_fired="2 consecutive H1 downward closes",
        )

    return BiasBlockH1(
        state=BiasState.NEUTRAL,
        h1_close=c_recent.close,
        prior_h1_close=c_prior.close,
        consecutive_directional_count=0,
        rule_fired="no consecutive directional closes on H1",
    )


def _derive_final_state(h4: BiasBlockH4, poi: BiasBlockPOI) -> BiasState:
    """Final MacroBias state from H4 structure and POI blocks."""
    if h4.state == BiasState.NEUTRAL:
        return BiasState.NEUTRAL
    if h4.state == BiasState.BULLISH and poi.state == BiasState.BULLISH:
        return BiasState.BULLISH
    if h4.state == BiasState.BEARISH and poi.state == BiasState.BEARISH:
        return BiasState.BEARISH
    return BiasState.NEUTRAL


# ─── Carry-forward helpers ──────────────────────────────────────────────────

_CARRY_RULE_FIRED = "H4 not confirmed — carried from previous confirmed close"


def _carry_h4(prev: MacroBias) -> BiasBlockH4:
    return BiasBlockH4(
        state=prev.h4.state,
        confirmed_h4_close=prev.h4.confirmed_h4_close,
        swing_high_price=prev.h4.swing_high_price,
        swing_low_price=prev.h4.swing_low_price,
        rule_fired=_CARRY_RULE_FIRED,
    )


def _carry_draw(prev: MacroBias) -> BiasBlockDraw:
    return BiasBlockDraw(
        state=prev.draw.state,
        liquidity_swept=prev.draw.liquidity_swept,
        sweep_level=prev.draw.sweep_level,
        reclaim_price=prev.draw.reclaim_price,
        rule_fired=_CARRY_RULE_FIRED,
    )


def _carry_poi(prev: MacroBias) -> BiasBlockPOI:
    return BiasBlockPOI(
        state=prev.poi.state,
        relevant_poi_type=prev.poi.relevant_poi_type,
        relevant_poi_id=prev.poi.relevant_poi_id,
        reference_price=prev.poi.reference_price,
        rule_fired=_CARRY_RULE_FIRED,
    )


_NEUTRAL_RULE = "H4 not confirmed and no previous bias available — NEUTRAL"


def _neutral_h4() -> BiasBlockH4:
    return BiasBlockH4(
        state=BiasState.NEUTRAL,
        confirmed_h4_close=None,
        swing_high_price=None,
        swing_low_price=None,
        rule_fired=_NEUTRAL_RULE,
    )


def _neutral_draw() -> BiasBlockDraw:
    return BiasBlockDraw(
        state=BiasState.NEUTRAL,
        liquidity_swept=None,
        sweep_level=None,
        reclaim_price=None,
        rule_fired=_NEUTRAL_RULE,
    )


def _neutral_poi() -> BiasBlockPOI:
    return BiasBlockPOI(
        state=BiasState.NEUTRAL,
        relevant_poi_type=None,
        relevant_poi_id=None,
        reference_price=None,
        rule_fired=_NEUTRAL_RULE,
    )


# ─── Public API ─────────────────────────────────────────────────────────────

def build_macro_bias(inp: MacroBiasInput) -> MacroBias:
    """
    Build a complete MacroBias.

    H4-dependent blocks (H4 Structure, Draw on Liquidity, Price vs POI):
      - When market.h4_candle_closed == True:  evaluated with market.h4_confirmed_close.
      - When market.h4_candle_closed == False AND previous_bias is not None:
          carried from previous_bias. Final state = previous_bias.state.
      - When market.h4_candle_closed == False AND previous_bias is None:
          all three = NEUTRAL. Final state = NEUTRAL.

    D1 and H1 blocks: always evaluated from latest confirmed candles.

    POI selection: POI-first, direction-agnostic total ordering.
      1. Deduplicate all POIs by structural identity.
      2. Sort by (tier, -source_timestamp, price_low, price_high).
      3. Select top-ranked POI.
      4. Evaluate selected POI against H4-derived bias direction.
         Conflict = NEUTRAL. No fallback.

    Parameters
    ----------
    inp : MacroBiasInput

    Returns
    -------
    MacroBias
    """
    if inp.market.h4_candle_closed:
        h4_close = inp.market.h4_confirmed_close
        if h4_close is None:
            raise ValueError("market.h4_confirmed_close is required when H4 is confirmed.")

        h4_block = _evaluate_h4_structure(h4_close, inp.swings_h4)
        draw_block = _evaluate_draw_on_liquidity(h4_close, inp.bos_list)
        d1_block = _evaluate_d1_context(inp.candles_d1)
        h1_block = _evaluate_h1_internal(inp.candles_h1)

        # POI-first selection — direction-agnostic
        selected_poi = _select_relevant_poi(
            inp.orderblocks,
            inp.fvgs,
            inp.ifvgs,
            inp.session_levels,
            inp.period_levels,
        )
        poi_block = _evaluate_poi(selected_poi, h4_block.state, h4_close)
        final_state = _derive_final_state(h4_block, poi_block)

    elif inp.previous_bias is not None:
        h4_block = _carry_h4(inp.previous_bias)
        draw_block = _carry_draw(inp.previous_bias)
        poi_block = _carry_poi(inp.previous_bias)
        final_state = inp.previous_bias.state
        d1_block = _evaluate_d1_context(inp.candles_d1)
        h1_block = _evaluate_h1_internal(inp.candles_h1)

    else:
        h4_block = _neutral_h4()
        draw_block = _neutral_draw()
        poi_block = _neutral_poi()
        final_state = BiasState.NEUTRAL
        d1_block = _evaluate_d1_context(inp.candles_d1)
        h1_block = _evaluate_h1_internal(inp.candles_h1)

    return MacroBias(
        state=final_state,
        h4_close_time=inp.market.h4_close_time,
        d1_close_time=inp.candles_d1[-1].timestamp if inp.candles_d1 else None,
        h1_close_time=inp.candles_h1[-1].timestamp if inp.candles_h1 else None,
        h4=h4_block,
        draw=draw_block,
        poi=poi_block,
        d1=d1_block,
        h1=h1_block,
        built_at=datetime.utcnow(),
    )
