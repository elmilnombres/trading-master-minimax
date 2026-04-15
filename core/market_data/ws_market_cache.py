"""
Thread-safe in-memory cache for WS market data.

Populated exclusively by Bybit WebSocket push messages.
Read by WSMarketDataProvider to serve market data without REST calls.

Owned by core/market_data/.
"""

import threading
import time
from datetime import datetime
from typing import Optional

from schemas.candle import Candle, Timeframe


# Map Bybit interval strings to Timeframe enums
BYBIT_INTERVAL_MAP: dict[str, Timeframe] = {
    "1": Timeframe.M1,
    "5": Timeframe.M5,
    "15": Timeframe.M15,
    "60": Timeframe.H1,
    "240": Timeframe.H4,
    "D": Timeframe.D1,
    "d": Timeframe.D1,
    "1m": Timeframe.M1,
    "5m": Timeframe.M5,
    "15m": Timeframe.M15,
    "1h": Timeframe.H1,
    "4h": Timeframe.H4,
    "1d": Timeframe.D1,
}

INTERVAL_SECONDS: dict[Timeframe, float] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
}


class WSMarketCache:
    """
    Thread-safe cache for WS-derived market data.

    Stores:
    - Latest ticker (lastPrice, bid, ask, etc.)
    - Candle buffers per timeframe (list of Candle objects)
    - Per-timeframe last_update_timestamp for stale detection

    Key methods:
    - update_ticker(data): call from WS ticker callback
    - update_kline(data): call from WS kline callback
    - get_ticker() -> dict | None
    - get_candles(timeframe) -> list[Candle]
    - is_stale(timeframe) -> bool  (> interval × 3 since last update)
    """

    MAX_CANDLES_PER_TIMEFRAME = 200

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ticker: dict | None = None
        self._candles: dict[Timeframe, list[Candle]] = {
            tf: [] for tf in Timeframe
        }
        self._last_update: dict[Timeframe, float] = {
            tf: 0.0 for tf in Timeframe
        }
        self._ticker_update: float = 0.0

    # ── Writers (called from WS callbacks) ─────────────────────────────────

    def update_ticker(self, data: dict) -> None:
        """Store latest ticker data dict."""
        with self._lock:
            self._ticker = data
            self._ticker_update = time.monotonic()

    def update_kline(self, data: list[dict]) -> None:
        """
        Process a Bybit kline push message (list of candle dicts).

        Bybit kline push format:
        {
            "start": 1672324800000,   # open time ms
            "end": 1672325099999,     # close time ms
            "interval": "5",
            "open": "16649.5",
            "close": "16677",
            "high": "16677",
            "low": "16608",
            "volume": "2.081",
            "turnover": "34666.4005",
            "confirm": false,
            "timestamp": 1672324988882
        }

        If confirm=True, the candle is closed and should be appended/finalized.
        If confirm=False, the candle is updating — update the last candle in buffer.
        """
        with self._lock:
            for candle_data in data:
                self._process_kline_candle(candle_data)

    def _process_kline_candle(self, c: dict) -> None:
        """Process a single Bybit kline dict into the cache."""
        interval_str = c.get("interval", "")
        timeframe = BYBIT_INTERVAL_MAP.get(interval_str)
        if timeframe is None:
            return

        symbol = "BTCUSDT"  # hardcoded for now (single-symbol bot)
        ts = datetime.utcfromtimestamp(int(c["start"]) / 1000)

        candle = Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=ts,
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=float(c["volume"]) if c.get("volume") else 0.0,
        )

        buffer = self._candles[timeframe]

        if c.get("confirm"):
            # Candle closed — append if not already present
            if not buffer or buffer[-1].timestamp != ts:
                buffer.append(candle)
            # Trim to MAX_CANDLES
            if len(buffer) > self.MAX_CANDLES_PER_TIMEFRAME:
                buffer[:] = buffer[-self.MAX_CANDLES_PER_TIMEFRAME:]
        else:
            # Candle still updating — update the last entry
            if buffer and buffer[-1].timestamp == ts:
                buffer[-1] = candle
            else:
                # New candle started but not confirmed yet — append optimistically
                buffer.append(candle)
                if len(buffer) > self.MAX_CANDLES_PER_TIMEFRAME:
                    buffer[:] = buffer[-self.MAX_CANDLES_PER_TIMEFRAME:]

        self._last_update[timeframe] = time.monotonic()

    # ── Readers (called from WSMarketDataProvider) ─────────────────────────

    def get_ticker(self) -> dict | None:
        """Return latest ticker data dict or None."""
        with self._lock:
            return self._ticker

    def get_candles(self, timeframe: Timeframe) -> list[Candle]:
        """Return a copy of the candle list for the given timeframe."""
        with self._lock:
            return list(self._candles.get(timeframe, []))

    def is_stale(self, timeframe: Timeframe) -> bool:
        """
        True iff no WS update received for this timeframe in
        interval_seconds × 3.

        Used to trigger REST fallback for cold/stale data.
        """
        interval_sec = INTERVAL_SECONDS[timeframe]
        stale_threshold = interval_sec * 3

        with self._lock:
            elapsed = time.monotonic() - self._last_update[timeframe]
            return elapsed > stale_threshold

    def get_last_update(self, timeframe: Timeframe) -> float:
        """Return monotonic time of last update for this timeframe."""
        with self._lock:
            return self._last_update[timeframe]

    def get_ticker_age(self) -> float:
        """Return seconds since last ticker update."""
        with self._lock:
            return time.monotonic() - self._ticker_update