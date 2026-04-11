"""
Phase 3 bot identity — owned by core/risk/ and core/execution/.

Import rule: core/risk/ and core/execution/ MUST import BotId from here.
Do NOT import from schemas/confirmation.py (Alpha is excluded from that module).

This enum is separate from schemas/confirmation.py BotId because:
- schemas/confirmation.py BotId is for confirmation-sequence bots (Beta, Gamma)
- Phase 3 risk and execution apply to ALL bots including Alpha
"""

from enum import Enum


class BotId(str, Enum):
    """Phase 3 bot identity — includes Alpha, Beta, Gamma."""

    ALPHA = "alpha_bot"
    BETA = "beta_bot"
    GAMMA = "gamma_bot"
