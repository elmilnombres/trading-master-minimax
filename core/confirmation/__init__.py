"""
Confirmation modules.

FROZEN IMPLEMENTATION CONSTANTS (in respective modules):
  sweep.py:       SWEEP_LOOKBACK_CANDLES = 3,  SWEEP_REVERSAL_REQUIRED = True
  reclaim.py:     RECLAIM_LOOKBACK_CANDLES = 3
  inducement.py:  INDUCEMENT_LOOKBACK_CANDLES = 4

Alpha is excluded from this module. See core/confirmation/sequencer.py.
"""

from core.confirmation.sweep import detect_sweep, detect_all_sweeps
from core.confirmation.reclaim import detect_reclaim
from core.confirmation.inducement import detect_inducement
from core.confirmation.sequencer import (
    validate_beta_sequence,
    validate_gamma_sequence,
)
from schemas.confirmation import (
    LiquiditySweep,
    SequenceStep,
    SequenceStepName,
    Direction,
)

__all__ = [
    "detect_sweep",
    "detect_all_sweeps",
    "detect_reclaim",
    "detect_inducement",
    "validate_beta_sequence",
    "validate_gamma_sequence",
    "LiquiditySweep",
    "SequenceStep",
    "SequenceStepName",
    "Direction",
]
