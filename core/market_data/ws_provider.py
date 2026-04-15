"""
WS-first market data provider.

Wraps MarketDataProvider but reads from WSStreamManager for live data.
Falls back to MarketDataFetcher REST calls for cold start / stale / disconnected.

Owned by core/market_data/.
"""

from datetime import datetime

from core.market_data.provider import MarketDataProvider
from core.market_data.fetcher import MarketDataFetcher
from core.market_data.ws_stream_manager import WSStreamManager
from core.market_data.types import MarketSnapshot
from schemas.candle import Timeframe, Candle


class WSMarketDataProvider:
    """
    WS-first market data provider.

    Uses WSStreamManager for live market data when connected.
    Falls back to MarketDataFetcher REST calls when:
    - WS is disconnected
    - Timeframe cache is stale (no update in > interval × 3)

    Preserves the same interface as MarketDataProvider:
    - get_snapshot(symbol) -> MarketSnapshot
    - get_multi_timeframe_candles(symbol, timeframes, count) -> dict[Timeframe, list[Candle]]
    """

    GRACE_SECONDS_H4 = 60  # frozen constant

    def __init__(
        self,
        stream_manager: WSStreamManager,
        fetcher: MarketDataFetcher,
    ):
        self._stream_manager = stream_manager
        self._fetcher = fetcher
        # Derive the base provider for fallback computation (ATR, H4 close, killzone)
        self._base = MarketDataProvider(fetcher)

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        """
        Build MarketSnapshot using live WS data.
        Fall back to REST for any stale timeframe.
        """
        now_utc = datetime.utcnow()

        # Ticker — from WS cache (no REST fallback for ticker in normal operation)
        ticker = self._stream_manager.get_ticker()
        if ticker:
            last_price = float(ticker.get("lastPrice", 0))
            bid = float(ticker.get("bid1Price", 0))
            ask = float(ticker.get("ask1Price", 0))
            spread_bps = ((ask - bid) / bid * 10_000) if bid > 0 else 0.0
        else:
            last_price, spread_bps = self._fetcher.get_current_price_and_spread(symbol)

        # Candles — WS first, REST fallback per timeframe
        m1_candles = self._get_candles_with_fallback(Timeframe.M1, count=50)
        h4_candles = self._get_candles_with_fallback(Timeframe.H4, count=20)
        h1_candles = self._get_candles_with_fallback(Timeframe.H1, count=20)
        d1_candles = self._get_candles_with_fallback(Timeframe.D1, count=10)

        # ATR14 on M1
        atr_14 = self._calculate_atr(m1_candles, period=14)

        # H4 close confirmation
        h4_closed, h4_close_time = self._check_h4_closed(h4_candles, now_utc)

        # Killzone detection
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

    def get_multi_timeframe_candles(
        self, symbol: str, timeframes: list[Timeframe], count: int = 50
    ) -> dict[Timeframe, list[Candle]]:
        """
        Fetch candles for multiple timeframes.
        WS first — REST fallback per stale timeframe.
        """
        result = {}
        for tf in timeframes:
            result[tf] = self._get_candles_with_fallback(tf, count=count)
        return result

    def _get_candles_with_fallback(self, timeframe: Timeframe, count: int) -> list[Candle]:
        """
        Get candles for a timeframe. Use WS cache if fresh, else REST.
        """
        if not self._stream_manager.is_stale(timeframe):
            candles = self._stream_manager.get_candles(timeframe)
            if candles:
                return candles[-count:]

        # Fall back to REST
        return self._fetcher.fetch_closed_candles(
            self._stream_manager.SYMBOL, timeframe, count=count
        )

    # ── Helpers (mirrored from MarketDataProvider) ─────────────────────────

    def _calculate_atr(self, candles: list[Candle], period: int = 14) -> float | None:
        """Calculate ATR on provided candles (True Range method)."""
        if len(candles) < period + 1:
            return None
        sorted_candles = sorted(candles, key=lambda c: c.timestamp)
        trs = []
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
        return sum(trs[-period:]) / period

    def _check_h4_closed(
        self, h4_candles: list[Candle], now_utc: datetime
    ) -> tuple[bool, datetime | None]:
        """Check if the most recent H4 candle is confirmed closed."""
        if not h4_candles:
            return False, None
        latest = h4_candles[-1]
        close_time = latest.timestamp
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