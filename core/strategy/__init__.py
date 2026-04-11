"""
core/strategy/ — bot-specific strategy implementations.

Each module here owns one bot's signal construction logic per the approved
Signal Construction Contract (Phase 4).

No exchange calls. No state mutations. Pure input → Signal | None.
"""

from core.strategy.adapter import (
    StrategyAdapter,
    AlphaStrategyAdapter,
    BetaStrategyAdapter,
    GammaStrategyAdapter,
)

__all__ = [
    "StrategyAdapter",
    "AlphaStrategyAdapter",
    "BetaStrategyAdapter",
    "GammaStrategyAdapter",
]
