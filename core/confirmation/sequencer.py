"""
Sequence validation for Beta and Gamma.

FROZEN IMPLEMENTATION CONSTANTS:
  SWEEP_LOOKBACK_CANDLES       = 3
  SWEEP_REVERSAL_REQUIRED      = True
  RECLAIM_LOOKBACK_CANDLES     = 3
  INDUCEMENT_LOOKBACK_CANDLES  = 4

These constants are locked in CLAUDE.md and here.
Do not change without explicit user approval.

Alpha: completely absent from this module.
Alpha uses H4 POI + M15 refinement + MacroBias at the app layer.
No generic validate_sequence function exists here.
"""

from schemas.confirmation import (
    SequenceResult,
    SequenceStep,
    SequenceStepName,
    BotId,
    Direction,
)


# ─── Frozen constants ─────────────────────────────────────────────────────────

SWEEP_LOOKBACK_CANDLES = 3
RECLAIM_LOOKBACK_CANDLES = 3
INDUCEMENT_LOOKBACK_CANDLES = 4


# ─── Beta ───────────────────────────────────────────────────────────────────────

def validate_beta_sequence(
    sweep_step: SequenceStep | None,
    chosh_step: SequenceStep | None,
    inducement_step: SequenceStep | None,
    mitigation_step: SequenceStep | None,
) -> SequenceResult:
    """
    Validate Beta's 4-step confirmation sequence. All 4 steps must trigger in order.

    Step order: SWEEP → CHoCH → INDUCEMENT → MITIGATION

    Parameters
    ----------
    sweep_step : SequenceStep | None
    chosh_step : SequenceStep | None
    inducement_step : SequenceStep | None
    mitigation_step : SequenceStep | None

    Returns
    -------
    SequenceResult
        bot_id = BotId.BETA
        valid = True only if all 4 steps triggered in order
    """
    steps: list[SequenceStep] = []
    failure_reason: str | None = None

    if sweep_step is None or not sweep_step.triggered:
        failure_reason = "Step 1 (SWEEP): not triggered"
    else:
        steps.append(sweep_step)

    if chosh_step is None or not chosh_step.triggered:
        failure_reason = "Step 2 (CHoCH): not triggered"
    elif failure_reason is None:
        steps.append(chosh_step)

    if inducement_step is None or not inducement_step.triggered:
        failure_reason = "Step 3 (INDUCEMENT): not triggered"
    elif failure_reason is None:
        steps.append(inducement_step)

    if mitigation_step is None or not mitigation_step.triggered:
        failure_reason = "Step 4 (MITIGATION): not triggered"
    elif failure_reason is None:
        steps.append(mitigation_step)

    direction = sweep_step.details.get("direction", Direction.BUY) if sweep_step else Direction.BUY
    if isinstance(direction, str):
        direction = Direction.BUY if direction == "buy" else Direction.SELL
    valid = failure_reason is None and len(steps) == 4

    return SequenceResult(
        bot_id=BotId.BETA,
        valid=valid,
        direction=direction,
        steps=steps if valid else [],
        failure_reason=failure_reason,
    )


# ─── Gamma ─────────────────────────────────────────────────────────────────────

def validate_gamma_sequence(
    sweep_step: SequenceStep | None,
    reclaim_step: SequenceStep | None,
) -> SequenceResult:
    """
    Validate Gamma's 2-step confirmation sequence. Both steps must trigger in order.

    Step order: SWEEP → RECLAIM

    Parameters
    ----------
    sweep_step : SequenceStep | None
    reclaim_step : SequenceStep | None

    Returns
    -------
    SequenceResult
        bot_id = BotId.GAMMA
        valid = True only if both steps triggered in order
    """
    steps: list[SequenceStep] = []
    failure_reason: str | None = None

    if sweep_step is None or not sweep_step.triggered:
        failure_reason = "Step 1 (SWEEP): not triggered"
    else:
        steps.append(sweep_step)

    if reclaim_step is None or not reclaim_step.triggered:
        failure_reason = "Step 2 (RECLAIM): not triggered"
    elif failure_reason is None:
        steps.append(reclaim_step)

    direction = sweep_step.details.get("direction", Direction.BUY) if sweep_step else Direction.BUY
    if isinstance(direction, str):
        direction = Direction.BUY if direction == "buy" else Direction.SELL
    valid = failure_reason is None and len(steps) == 2

    return SequenceResult(
        bot_id=BotId.GAMMA,
        valid=valid,
        direction=direction,
        steps=steps if valid else [],
        failure_reason=failure_reason,
    )
