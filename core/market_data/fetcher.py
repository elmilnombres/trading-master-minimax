"""
Market data fetcher — pulls raw data from Bybit via the exchange adapter.
Single responsibility: fetch and normalize. No caching, no analysis.
"""

from datetime import datetime

from exchange.bybit.subaccount import BybitSubaccountClient
from exchange.bybit.adapter import BybitAdapter
from schemas.candle import Candle, Timeframe, CandleBatch


# Bybit category for USDT perpetual linear margined contracts
CATEGORY_LINEAR = "linear"

# Interval string for Bybit v5 klines endpoint
BYBIT_INTERVAL: dict[Timeframe, str] = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
    Timeframe.D1: "D",
}


class MarketDataFetcher:
    """
    Fetches market data from Bybit.
    Used by MarketDataProvider (which does the analysis/aggregation).
    """

    def __init__(self, subaccount_client: BybitSubaccountClient):
        self._client = subaccount_client
        self._adapter = BybitAdapter()

    def fetch_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int = 200,
        end_time_ms: int | None = None,
    ) -> CandleBatch:
        """
        Fetch `count` candles of `timeframe` for `symbol`.
        Returns CandleBatch (list of normalized Candle objects).
        """
        interval_str = BYBIT_INTERVAL[timeframe]
        raw = self._client.get_klines(
            category=CATEGORY_LINEAR,
            symbol=symbol,
            interval=interval_str,
            end=end_time_ms,
            limit=count,
        )

        klines = raw.get("list", [])
        # Bybit returns newest first — reverse to chronological
        klines_asc = list(reversed(klines))

        return self._adapter.parse_candle_batch(klines_asc, symbol, timeframe)

    def fetch_latest_candle(
        self, symbol: str, timeframe: Timeframe
    ) -> Candle | None:
        """Fetch just the last candle for a given timeframe."""
        batch = self.fetch_candles(symbol, timeframe, count=1)
        return batch.latest

    def fetch_closed_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int = 200,
    ) -> list[Candle]:
        """Fetch only confirmed-closed candles (uses is_closed())."""
        batch = self.fetch_candles(symbol, timeframe, count=count)
        return [c for c in batch.candles if c.is_closed()]

    def fetch_ticker(self, symbol: str) -> dict:
        """Fetch current ticker (bid/ask/last)."""
        return self._client.get_ticker(category=CATEGORY_LINEAR, symbol=symbol)

    def fetch_orderbook(self, symbol: str, depth: int = 50) -> dict:
        """Fetch orderbook (bids/asks)."""
        return self._client.get_orderbook(category=CATEGORY_LINEAR, symbol=symbol, limit=depth)

    def get_current_price_and_spread(self, symbol: str) -> tuple[float, float]:
        """
        Get last price and spread in bps.
        Returns (last_price, spread_bps).
        """
        raw = self.fetch_ticker(symbol)
        items = raw.get("list", [])
        if not items:
            raise ValueError(f"No ticker data for {symbol}")

        ticker = items[0]
        last = float(ticker.get("lastPrice", 0))
        bid = float(ticker.get("bid1Price", 0))
        ask = float(ticker.get("ask1Price", 0))

        spread_bps = 0.0
        if bid > 0:
            spread_bps = ((ask - bid) / bid) * 10_000

        return last, spread_bps