"""
Structure analysis — swing detection, BOS, and CHoCH.
"""

from core.structure.swing import detect_swings, get_most_recent_swing
from core.structure.bos import detect_bos, detect_choch

__all__ = [
    "detect_swings",
    "get_most_recent_swing",
    "detect_bos",
    "detect_choch",
]
