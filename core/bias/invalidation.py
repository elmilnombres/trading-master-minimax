"""
Bias Invalidation — checks whether a live MacroBias has been invalidated
by current market conditions.

A bias is invalidated when its H4-coupled blocks (H4 Structure, Draw on Liquidity,
Price vs POI) no longer reflect the current confirmed market state.
D1 and H1 blocks are not checked for invalidation — they are always fresh.

Inputs:  MacroBias (prior), MarketSnapshot, all POI lists
Output: BiasInvalidationResult
"""

from core.bias.builder import build_macro_bias, MacroBiasInput
from core.bias.invalidation_rules import (
    should_invalidate_h4,
    should_invalidate_draw,
    should_invalidate_poi,
)
from schemas.bias import BiasInvalidationResult, MacroBias


def check_bias_invalidation(
    current_bias: MacroBias,
    market_snapshot,  # MarketSnapshot
    swings_h4,  # list[SwingPoint]
    swings_h1,  # list[SwingPoint]
    bos_list,  # list[BOS]
    candles_d1,  # list[Candle]
    candles_h1,  # list[Candle]
    orderblocks,  # list[OrderBlock]
    fvgs,  # list[FVG]
    ifvgs,  # list[iFVG]
    session_levels,  # list[SessionLevel]
    period_levels,  # list[PeriodHighLow]
) -> BiasInvalidationResult:
    """
    Check if the current confirmed MacroBias has been invalidated.

    Invalidation fires when any H4-coupled block's state has changed between
    the previously confirmed bias and the newly confirmed bias.

    H4-coupled blocks: H4 Structure, Draw on Liquidity, Price vs POI.
    D1 and H1 blocks: not checked (always fresh per timeframe).

    Parameters
    ----------
    current_bias : MacroBias
        The most recently confirmed MacroBias.
    market_snapshot : MarketSnapshot
    swings_h4, swings_h1 : list[SwingPoint]
    bos_list : list[BOS]
    candles_d1, candles_h1 : list[Candle]
    orderblocks, fvgs, ifvgs : POI lists
    session_levels : list[SessionLevel]
    period_levels : list[PeriodHighLow]

    Returns
    -------
    BiasInvalidationResult
    """
    # Build fresh bias using carry-forward semantics
    # If H4 is now confirmed: fresh evaluation
    # If H4 is still not confirmed: carry forward (no invalidation possible)
    # If H4 was confirmed before and is confirmed now: compare blocks
    new_bias = build_macro_bias(
        MacroBiasInput(
            market=market_snapshot,
            swings_h4=swings_h4,
            swings_h1=swings_h1,
            bos_list=bos_list,
            candles_d1=candles_d1,
            candles_h1=candles_h1,
            orderblocks=orderblocks,
            fvgs=fvgs,
            ifvgs=ifvgs,
            session_levels=session_levels,
            period_levels=period_levels,
            previous_bias=current_bias,  # carry forward if H4 not yet confirmed
        )
    )

    triggered_blocks: list[str] = []

    if should_invalidate_h4(current_bias.h4, new_bias.h4):
        triggered_blocks.append("H4_structure")

    if should_invalidate_draw(current_bias.draw, new_bias.draw):
        triggered_blocks.append("draw_on_liquidity")

    if should_invalidate_poi(current_bias.poi, new_bias.poi):
        triggered_blocks.append("price_vs_poi")

    if triggered_blocks:
        return BiasInvalidationResult(
            valid=False,
            reason=f"blocks invalidated: {', '.join(triggered_blocks)}",
            triggered_blocks=triggered_blocks,
        )

    return BiasInvalidationResult(
        valid=True,
        reason=None,
        triggered_blocks=[],
    )
