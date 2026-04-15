"""
WS Stream Manager — wires BybitWSClient to WSMarketCache and manages lifecycle.

Owned by core/market_data/.
Exposes a clean synchronous interface to WSMarketDataProvider.

Responsibilities:
- Owns BybitWSClient instance
- Owns WSMarketCache instance
- Connects, subscribes to all required topics, reconnects on disconnect
- Provides get_ticker() and get_candles() proxies to cache
- Tracks subscriptions for resubscribe on reconnect
"""

import threading
import time
from typing import Callable, Optional

from exchange.bybit.websocket import BybitWSClient
from core.market_data.ws_market_cache import WSMarketCache
from schemas.candle import Timeframe


class WSStreamManager:
    """
    Manages WS lifecycle for a single symbol.

    Call start() to connect and subscribe. Call stop() to disconnect.
    Use get_ticker() and get_candles() to read cached WS data.
    """

    SYMBOL = "BTCUSDT"  # hardcoded — single-symbol bot

    # All timeframes Alpha needs for its strategy evaluation
    TIMEFRAMES = [
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    ]

    # Map Timeframe to Bybit interval string
    TF_TO_INTERVAL: dict[Timeframe, str] = {
        Timeframe.M1: "1",
        Timeframe.M5: "5",
        Timeframe.M15: "15",
        Timeframe.H1: "60",
        Timeframe.H4: "240",
        Timeframe.D1: "D",
    }

    def __init__(
        self,
        symbol: str,
        on_stale_callback: Callable[[list[Timeframe]], None] | None = None,
    ):
        """
        Args:
            symbol: trading symbol, e.g. "BTCUSDT"
            on_stale_callback: called when any timeframe goes stale.
                              Receiver triggers REST fallback for stale timeframes.
        """
        self._symbol = symbol
        self._cache = WSMarketCache()
        self._ws_client: Optional[BybitWSClient] = None
        self._on_stale_callback = on_stale_callback or (lambda _: None)
        self._running = False
        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Connect WS, subscribe to ticker + all kline streams."""
        with self._lock:
            if self._running:
                return
            self._running = True

        self._ws_client = BybitWSClient(
            on_kline=self._on_kline,
            on_ticker=self._on_ticker,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
        )
        self._ws_client.connect()

    def stop(self) -> None:
        """Disconnect WS gracefully."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._ws_client:
            self._ws_client.disconnect()
            self._ws_client = None

    def is_connected(self) -> bool:
        """True iff WS client is connected."""
        with self._lock:
            if self._ws_client is None:
                return False
            return self._ws_client.is_connected()

    def get_ticker(self) -> dict | None:
        """Return latest ticker from cache."""
        return self._cache.get_ticker()

    def get_candles(self, timeframe: Timeframe) -> list:
        """Return candles for given timeframe from cache."""
        return self._cache.get_candles(timeframe)

    def is_stale(self, timeframe: Timeframe) -> bool:
        """Return True if timeframe cache is stale (> interval × 3 since last WS update)."""
        return self._cache.is_stale(timeframe)

    def get_subscriptions(self) -> list[str]:
        """Return list of active subscription topics."""
        if self._ws_client:
            return self._ws_client.get_subscriptions()
        return []

    # ── Internal callbacks from BybitWSClient ────────────────────────────────

    def _on_kline(self, topic: str, data: list[dict]) -> None:
        """Handle incoming kline push message — forward to cache."""
        self._cache.update_kline(data)

    def _on_ticker(self, topic: str, data: dict) -> None:
        """Handle incoming ticker push message — forward to cache."""
        self._cache.update_ticker(data)

    def _on_connect(self) -> None:
        """WS connected — subscriptions happen in BybitWSClient._resubscribe_all()"""
        pass

    def _on_disconnect(self, reason: str) -> None:
        """
        WS disconnected.

        Check for stale timeframes and trigger REST fallback
        if any timeframe has not received updates.
        """
        stale_timeframes = [tf for tf in self.TIMEFRAMES if self._cache.is_stale(tf)]
        if stale_timeframes:
            self._on_stale_callback(stale_timeframes)

    def _on_stale(self, stale_timeframes: list[Timeframe]) -> None:
        """Called by _on_disconnect — triggers REST fallback for stale timeframes."""
        self._on_stale_callback(stale_timeframes)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @property
    def cache(self) -> WSMarketCache:
        return self._cache