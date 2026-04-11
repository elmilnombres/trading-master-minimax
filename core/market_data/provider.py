"""
Market data provider.

ROLE (frozen Phase 1 scope):
  Aggregates raw data from MarketDataFetcher and produces a MarketSnapshot.
  Computes only those derived fields that are prerequisites for every bot's
  evaluation loop and cannot be deferred: H4 close confirmation, ATR14 (M1),
  current killzone state, and spread.
  All of these are locked constants — no strategy logic lives here.

WHAT BELONGS HERE (Phase 1 scope):
  - H4 candle close confirmation (enforces Q1: +60s grace)
  - ATR14 on M1 (enforces Q2: period=14)
  - Killzone UTC state (enforces Q3: London 07-09, NY 13:30-16)
  - Spread monitoring (used in execution/filters.py pre-trade check)
  - Multi-timeframe candle fetch helper (delegates to fetcher)

WHAT DOES NOT BELONG HERE (Phase 2+ scope):
  - Bias construction (→ core/bias/builder.py)
  - POI detection (→ core/poi/ order_block.py, fvg.py, levels.py)
  - Structure analysis / swing detection (→ core/structure/)
  - Confirmation sequencing (→ core/confirmation/)
  - Risk sizing or drawdown (→ core/risk/)
  - Order execution (→ core/execution/)

EXPLICIT NO-GO RULES (enforced forever — do not bypass):
  provider.py must NEVER:
  1. Score or rate a setup (quality, confidence, etc.)
  2. Rank or select POIs
  3. Infer or compute a bias state (bullish/bearish/neutral)
  4. Decide trade validity or signal activation

  These are decisions made downstream in strategy-specific modules.
  provider.py only measures primitives. Everything else is out of scope.

As modules are added in Phase 2 and 3, they consume MarketSnapshot
as input but own their own logic. This file grows no further.
"""

from datetime import datetime, timezone

from core.market_data.fetcher import MarketDataFetcher, CATEGORY_LINEAR
from core.market_data.types import MarketSnapshot
from schemas.candle import Timeframe


class MarketDataProvider:
    """
    Provides high-level market data access to strategy engines.
    Handles: multi-timeframe fetch, ATR calculation, H4 close validation,
    killzone detection, spread monitoring.
    """

    GRACE_SECONDS_H4 = 60  # frozen constant Q1

    def __init__(self, fetcher: MarketDataFetcher, timezone_str: str = "UTC"):
        self._fetcher = fetcher
        self._timezone = timezone_str  # for killzone display; internal logic uses UTC

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        """
        Build a comprehensive MarketSnapshot for a symbol.
        Fetches H4, H1, M15, M5, M1 candles + current ticker.
        """
        now_utc = datetime.utcnow()

        # Current price and spread
        last_price, spread_bps = self._fetcher.get_current_price_and_spread(symbol)

        # Fetch required timeframes for macro bias (5 blocks)
        h4_candles = self._fetcher.fetch_closed_candles(symbol, Timeframe.H4, count=20)
        h1_candles = self._fetcher.fetch_closed_candles(symbol, Timeframe.H1, count=20)
        d1_candles = self._fetcher.fetch_closed_candles(symbol, Timeframe.D1, count=10)

        # M1 for ATR buffer
        m1_candles = self._fetcher.fetch_closed_candles(symbol, Timeframe.M1, count=50)

        # ATR14 on M1
        atr_14 = self._calculate_atr(m1_candles, period=14)

        # H4 close policy validation
        h4_closed, h4_close_time = self._check_h4_closed(h4_candles, now_utc)

        # Killzone detection (UTC)
        is_london = self._is_in_killzone_utc(now_utc, "07:00", "09:00")
        is_ny = self._is_in_killzone_utc(now_utc, "13:30", "16:00")

        return MarketSnapshot(
            symbol=symbol,
            timestamp=now_utc,
            last_price=last_price,
            spread_bps=spread_bps,
            atr_14_m1=atr_14,
            is_killzone_london=is_london,
            is_killzone_ny=is_ny,
            h4_candle_closed=h4_closed,
            h4_close_time=h4_close_time,
        )

    def _calculate_atr(self, candles: list, period: int = 14) -> float | None:
        """Calculate ATR on provided candles (True Range method)."""
        if len(candles) < period + 1:
            return None

        # True Range = max(H - L, |H - prev_C|, |L - prev_C|)
        trs = []
        sorted_candles = sorted(candles, key=lambda c: c.timestamp)
        for i in range(1, len(sorted_candles)):
            cur = sorted_candles[i]
            prev = sorted_candles[i - 1]
            tr = max(
                cur.high - cur.low,
                abs(cur.high - prev.close),
                abs(cur.low - prev.close),
            )
            trs.append(tr)

        if len(trs) < period:
            return None

        # Simple moving average ( Wilder smoothing comes later in Phase B)
        return sum(trs[-period:]) / period

    def _check_h4_closed(
        self, h4_candles: list, now_utc: datetime
    ) -> tuple[bool, datetime | None]:
        """
        Check if the most recent H4 candle is confirmed closed.
        H4 policy: candle is closed if we are past open_time + 4h + 60s grace.
        """
        if not h4_candles:
            return False, None

        latest = h4_candles[-1]
        close_time = latest.timestamp
        # H4 duration = 4 * 3600 = 14400 seconds
        elapsed = (now_utc - close_time).total_seconds()
        is_closed = elapsed >= (14400 + self.GRACE_SECONDS_H4)
        return is_closed, close_time

    def _is_in_killzone_utc(
        self, dt: datetime, start_str: str, end_str: str
    ) -> bool:
        """Check if current UTC time is within killzone window."""
        try:
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
        except ValueError:
            return False

        current_minutes = dt.hour * 60 + dt.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        return start_minutes <= current_minutes < end_minutes

    def get_multi_timeframe_candles(
        self, symbol: str, timeframes: list[Timeframe], count: int = 50
    ) -> dict[Timeframe, list]:
        """
        Fetch candles for multiple timeframes in one call.
        Returns dict of {timeframe: list_of_candles}.
        """
        result = {}
        for tf in timeframes:
            candles = self._fetcher.fetch_closed_candles(symbol, tf, count=count)
            result[tf] = candles
        return result