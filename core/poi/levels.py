"""
Session Level and Period High/Low extraction.

FROZEN IMPLEMENTATION CONSTANTS (all UTC):
  ASIAN_SESSION   = (22, 0, 0, 0)   (start_h, start_m, end_h, end_m)
  LONDON_SESSION  = (7, 0, 9, 0)
  NY_SESSION     = (13, 30, 16, 0)
  WEEK_START_DOW = 0   (Monday)
  WEEK_END_DOW   = 6   (Sunday)

Period high/low:
  PDH / PDL = previous UTC trading day (max/min of actual period high)
  PWH / PWL = previous UTC trading week (max/min of actual period high)

These constants are locked in CLAUDE.md and here.
Do not change without explicit user approval.
"""

import uuid
from datetime import datetime, timedelta

from schemas.candle import Candle
from schemas.poi import (
    SessionLevel,
    PeriodHighLow,
    SessionName,
    SessionLevelType,
    PeriodName,
    PeriodLevelType,
)

# ─── Frozen constants ─────────────────────────────────────────────────────────

ASIAN_SESSION = (22, 0, 0, 0)   # 22:00 prior day → 00:00 UTC
LONDON_SESSION = (7, 0, 9, 0)    # 07:00 → 09:00 UTC
NY_SESSION = (13, 30, 16, 0)     # 13:30 → 16:00 UTC

WEEK_START_DOW = 0  # Monday
WEEK_END_DOW = 6    # Sunday


def _in_session_window(dt: datetime, session: tuple[int, int, int, int]) -> bool:
    """Check if a datetime falls within a session window (start_h, start_m, end_h, end_m)."""
    sh, sm, eh, em = session
    total_mins = dt.hour * 60 + dt.minute
    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em
    return start_mins <= total_mins < end_mins


def _session_window_candles(
    candles: list[Candle],
    session: tuple[int, int, int, int],
) -> list[Candle]:
    """Return all candles that fall within a session window."""
    return [c for c in candles if _in_session_window(c.timestamp, session)]


def _prior_day_boundaries(dt: datetime) -> tuple[datetime, datetime]:
    """Return (period_start, period_end) for the previous UTC day."""
    prior = dt - timedelta(days=1)
    start = prior.replace(hour=0, minute=0, second=0, microsecond=0)
    end = prior.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def _prior_week_boundaries(dt: datetime) -> tuple[datetime, datetime]:
    """
    Return (period_start, period_end) for the previous UTC week.

    Weeks run Monday 00:00 UTC to Sunday 23:59:59 UTC.
    """
    today = dt.date()
    days_since_monday = today.weekday()
    last_monday = today - timedelta(days=days_since_monday)
    prior_sunday = last_monday - timedelta(days=1)

    start = datetime.combine(last_monday, datetime.min.time()).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = datetime.combine(prior_sunday, datetime.min.time()).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )
    return start, end


def _session_period_boundaries(
    session: tuple[int, int, int, int],
    reference_dt: datetime,
) -> tuple[datetime, datetime]:
    """
    Return (period_start, period_end) for a session on the day of reference_dt.

    period_start = 00:00 UTC of the calendar day the session runs on.
    period_end   = 23:59:59 UTC of the same calendar day.

    The session window itself (e.g. 22:00–00:00 for ASIAN) may span midnight,
    but the structural identity uses the calendar day as the period boundary.
    """
    day = reference_dt.date()
    start = datetime.combine(day, datetime.min.time())
    end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def extract_session_levels(candles: list[Candle]) -> list[SessionLevel]:
    """
    Extract ASIAN, LONDON, and NY session HIGH and LOW from M15 candles.

    HIGH = highest high in the session window.
    LOW  = lowest low in the session window.
    OPEN = not extracted (not in approved contract).

    Parameters
    ----------
    candles : list[Candle]
        M15 candles ordered oldest → newest.

    Returns
    -------
    list[SessionLevel]
        All session levels found, sorted by timestamp descending.
    """
    levels: list[SessionLevel] = []
    session_configs = [
        (SessionName.ASIAN, ASIAN_SESSION),
        (SessionName.LONDON, LONDON_SESSION),
        (SessionName.NY, NY_SESSION),
    ]

    for session_name, session_window in session_configs:
        session_candles = _session_window_candles(candles, session_window)
        if not session_candles:
            continue

        first_candle = session_candles[0]
        high_price = max(c.high for c in session_candles)
        low_price = min(c.low for c in session_candles)
        period_start, period_end = _session_period_boundaries(session_window, first_candle.timestamp)

        for level_type, price in [
            (SessionLevelType.HIGH, high_price),
            (SessionLevelType.LOW, low_price),
        ]:
            levels.append(
                SessionLevel(
                    id=f"{session_name.value}_{level_type.value}_{first_candle.timestamp.strftime('%Y%m%d%H%M')}",
                    session=session_name,
                    level_type=level_type,
                    price=price,
                    timestamp=first_candle.timestamp,
                    period_start=period_start,
                    period_end=period_end,
                )
            )

    levels.sort(key=lambda l: l.timestamp, reverse=True)
    return levels


def extract_period_high_low(
    candles: list[Candle],
    now: datetime | None = None,
) -> list[PeriodHighLow]:
    """
    Extract PDH / PDL (previous UTC day) and PWH / PWL (previous UTC week).

    PDH = highest price reached during the previous complete UTC trading day.
    PDL = lowest price reached during the previous complete UTC trading day.
    PWH = highest price reached during the previous complete UTC trading week.
    PWL = lowest price reached during the previous complete UTC trading week.

    Computed by scanning all candles in the period, taking max(high) and min(low).
    NOT derived from H1 closes.

    Parameters
    ----------
    candles : list[Candle]
        Candles spanning the required prior periods. M15 or H1 acceptable.
    now : datetime | None
        Reference datetime for period boundaries. Defaults to utcnow.

    Returns
    -------
    list[PeriodHighLow]
        PDH, PDL, PWH, PWL for the previous complete periods.
    """
    now = now or datetime.utcnow()
    levels: list[PeriodHighLow] = []

    # Daily — previous day
    d_start, d_end = _prior_day_boundaries(now)
    daily_candles = [c for c in candles if d_start <= c.timestamp <= d_end]
    if daily_candles:
        levels.append(
            PeriodHighLow(
                id=f"pdh_{d_start.strftime('%Y%m%d')}",
                period=PeriodName.DAILY,
                level_type=PeriodLevelType.HIGH,
                price=max(c.high for c in daily_candles),
                period_start=d_start,
                period_end=d_end,
            )
        )
        levels.append(
            PeriodHighLow(
                id=f"pdl_{d_start.strftime('%Y%m%d')}",
                period=PeriodName.DAILY,
                level_type=PeriodLevelType.LOW,
                price=min(c.low for c in daily_candles),
                period_start=d_start,
                period_end=d_end,
            )
        )

    # Weekly — previous week
    w_start, w_end = _prior_week_boundaries(now)
    weekly_candles = [c for c in candles if w_start <= c.timestamp <= w_end]
    if weekly_candles:
        levels.append(
            PeriodHighLow(
                id=f"pwh_{w_start.strftime('%Y%m%d')}",
                period=PeriodName.WEEKLY,
                level_type=PeriodLevelType.HIGH,
                price=max(c.high for c in weekly_candles),
                period_start=w_start,
                period_end=w_end,
            )
        )
        levels.append(
            PeriodHighLow(
                id=f"pwl_{w_start.strftime('%Y%m%d')}",
                period=PeriodName.WEEKLY,
                level_type=PeriodLevelType.LOW,
                price=min(c.low for c in weekly_candles),
                period_start=w_start,
                period_end=w_end,
            )
        )

    return levels
