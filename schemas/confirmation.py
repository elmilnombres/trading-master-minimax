"""
Confirmation schemas — owned by core/confirmation/.

Defines SequenceStep, LiquiditySweep, ReclaimResult, InducementResult,
and SequenceResult.
Alpha is excluded from this module entirely.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SequenceStepName(str, Enum):
    SWEEP = "sweep"
    CHoCH = "choch"
    INDUCEMENT = "inducement"
    MITIGATION = "mitigation"
    RECLAIM = "reclaim"


class Direction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class BotId(str, Enum):
    BETA = "beta_bot"
    GAMMA = "gamma_bot"


class SequenceStep(BaseModel):
    """
    A single step in a bot-specific confirmation sequence.

    Beta:  sweep → choch → inducement → mitigation
    Gamma: sweep → reclaim
    Alpha: not applicable — does not use this module.
    """

    name: SequenceStepName           # ← enum, not raw string
    triggered: bool
    triggered_at: datetime | None = None
    price_at_trigger: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class LiquiditySweep(BaseModel):
    """
    Result of liquidity sweep detection on M5/M1.

    Consumed by core/confirmation/reclaim.py and
    core/confirmation/inducement.py as the first step in their sequences.
    """

    direction: Direction             # ← enum, not raw string
    sweep_price: float             # wick price at the moment of the sweep
    triggered_at: datetime
    is_valid: bool = True


class ReclaimResult(BaseModel):
    """
    Result of reclaim detection — price has returned above or below
    the swept level after a liquidity sweep.

    Consumed by core/confirmation/sequencer.py as the final step in Gamma sequence.
    """

    direction: Direction          # ← enum, not raw string
    reclaim_price: float
    held_above: bool | None = None
    is_valid: bool = False


class InducementResult(BaseModel):
    """
    Result of inducement detection — an internal liquidity sweep that occurs
    AFTER CHoCH has been confirmed, sweeping internal stop liquidity on the
    same side that was just broken, before the legitimate move resumes.

    Distinct from mitigation:
      - inducement = internal liquidity sweep post-CHoCH
      - mitigation = price closing inside the valid FVG zone on the retracement

    Consumed by core/confirmation/sequencer.py as Beta sequence step 3.
    """

    direction: Direction                     # ← enum, not raw string
    inducement_price: float                   # wick price at the moment of the inducement sweep
    sweep_price: float                        # price of the original external liquidity sweep
    choch_confirmed_at: datetime              # ← typo fixed: was chosh_confirmed_at
    triggered_at: datetime
    is_valid: bool = False


class SequenceResult(BaseModel):
    """
    Final sequence validation result for Beta or Gamma.

    Alpha: excluded. Alpha uses H4 POI + M15 refinement + MacroBias
           at the app layer. This module is not consulted for Alpha.
    """

    bot_id: BotId                            # ← enum, not raw string
    valid: bool
    direction: Direction                     # ← enum, not raw string
    steps: list[SequenceStep] = Field(default_factory=list)
    failure_reason: str | None = None         # None if valid
