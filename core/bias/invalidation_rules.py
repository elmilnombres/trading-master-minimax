"""
Individual block invalidation rules.

Each function checks whether an H4-coupled bias block has been invalidated
by comparing the stored state against the newly evaluated state.

A block is invalidated when its state has demonstrably changed —
not when price temporarily moves against it.
"""

from schemas.bias import BiasBlockH4, BiasBlockDraw, BiasBlockPOI, BiasState


def should_invalidate_h4(
    stored: BiasBlockH4,
    fresh: BiasBlockH4,
) -> bool:
    """
    H4 Structure block is invalidated when stored and fresh states
    are in direct opposition (BULLISH ↔ BEARISH).
    Transitions from/to NEUTRAL are not invalidations.
    """
    if stored.state == BiasState.NEUTRAL or fresh.state == BiasState.NEUTRAL:
        return False
    return stored.state != fresh.state


def should_invalidate_draw(
    stored: BiasBlockDraw,
    fresh: BiasBlockDraw,
) -> bool:
    """
    Draw on Liquidity block is invalidated when:
      1. The liquidity side that was swept has changed (buy_side → sell_side)
         AND the reclaim direction has reversed.
      2. A previously bullish draw has flipped to bearish or neutral.
      3. A previously bearish draw has flipped to bullish or neutral.
    """
    if stored.state == BiasState.NEUTRAL or fresh.state == BiasState.NEUTRAL:
        return False
    if stored.liquidity_swept != fresh.liquidity_swept:
        return True
    return stored.state != fresh.state


def should_invalidate_poi(
    stored: BiasBlockPOI,
    fresh: BiasBlockPOI,
) -> bool:
    """
    Price vs POI block is invalidated when:
      1. The referenced POI has changed (different id or type).
      2. The evaluated state has changed (BULLISH ↔ BEARISH ↔ NEUTRAL).
    """
    if stored.state == BiasState.NEUTRAL or fresh.state == BiasState.NEUTRAL:
        return False
    if stored.relevant_poi_id != fresh.relevant_poi_id:
        return True
    if stored.relevant_poi_type != fresh.relevant_poi_type:
        return True
    return stored.state != fresh.state
