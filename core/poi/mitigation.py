"""
Mitigation Zone detection and state tracking.

A MitigationZone is a defined price range that price has entered and triggered.
Once triggered, the zone is considered mitigated (price passed through it).

A zone is mitigated when a candle's body closes inside the zone.
Wick touching alone does NOT constitute mitigation.
"""

import uuid
from datetime import datetime

from schemas.candle import Candle
from schemas.poi import MitigationZone


def detect_mitigation(
    candles: list[Candle],
    zones: list[tuple[float, float]],  # list of (price_high, price_low)
    lookback: int = 1,
) -> list[MitigationZone]:
    """
    Detect which POI zones have been mitigated by recent candles.

    Parameters
    ----------
    candles : list[Candle]
        Candle sequence ordered oldest → newest.
    zones : list[tuple[float, float]]
        List of (price_high, price_low) zone boundaries.
    lookback : int
        Number of recent candles to check. Default 1 (last candle only).

    Returns
    -------
    list[MitigationZone]
        All zones that have been mitigated, most recent first.
    """
    if not candles or not zones:
        return []

    triggered: list[MitigationZone] = []
    recent_candles = candles[-lookback:] if lookback > 0 else candles

    for candle in recent_candles:
        for zone_high, zone_low in zones:
            if zone_low <= candle.close <= zone_high:
                triggered.append(
                    MitigationZone(
                        id=str(uuid.uuid4())[:8],
                        price_high=zone_high,
                        price_low=zone_low,
                        triggered=True,
                        triggered_at=candle.timestamp,
                    )
                )

    triggered.sort(key=lambda z: z.triggered_at or datetime.utcnow(), reverse=True)
    return triggered


def update_mitigation_state(
    zones: list[MitigationZone],
    candles: list[Candle],
) -> list[MitigationZone]:
    """
    Update the triggered state of an existing list of MitigationZones
    given new candle data.

    If a zone was already triggered, it stays triggered.
    If a zone was not triggered and a new candle body closes inside it,
    it becomes triggered.

    Parameters
    ----------
    zones : list[MitigationZone]
        Existing zones with their current state.
    candles : list[Candle]
        New candles since last check.

    Returns
    -------
    list[MitigationZone]
        Zones with updated triggered state.
    """
    for candle in candles:
        for zone in zones:
            if zone.triggered:
                continue
            if zone.price_low <= candle.close <= zone.price_high:
                zone.triggered = True
                zone.triggered_at = candle.timestamp

    return zones
