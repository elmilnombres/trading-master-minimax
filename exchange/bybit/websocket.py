"""
Bybit WebSocket client for public streams (kline + ticker).

Owned by exchange/bybit/.
No auth required — public streams only.

Usage:
    client = BybitWSClient(
        on_kline=lambda topic, data: ...,
        on_ticker=lambda topic, data: ...,
        on_connect=lambda: ...,
        on_disconnect=lambda reason: ...,
    )
    client.connect()
    client.subscribe_ticker("BTCUSDT")
    client.subscribe_kline("BTCUSDT", "1")   # M1
    client.subscribe_kline("BTCUSDT", "5")   # M5
    client.subscribe_kline("BTCUSDT", "15")  # M15
    client.subscribe_kline("BTCUSDT", "60")  # H1
    client.subscribe_kline("BTCUSDT", "240") # H4
    client.subscribe_kline("BTCUSDT", "D")   # D1
"""

import asyncio
import json
import threading
import time
from typing import Callable, Optional

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore


BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
PING_INTERVAL_SECONDS = 20
RECONNECT_DELAYS = (5, 10, 20, 40, 60)  # seconds


class BybitWSClient:
    """
    Raw Bybit WebSocket client for public linear streams.

    Connects to wss://stream.bybit.com/v5/public/linear.
    Handles: connect, subscribe, ping/pong, reconnect with backoff.
    Parses kline and ticker push messages and dispatches to callbacks.

    Thread-safe: all public methods acquire the instance lock.
    """

    def __init__(
        self,
        on_kline: Callable[[str, dict], None],
        on_ticker: Callable[[str, dict], None],
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
    ):
        if websockets is None:
            raise ImportError("websockets library not installed. Run: pip install websockets")

        self._on_kline = on_kline
        self._on_ticker = on_ticker
        self._on_connect = on_connect or (lambda: None)
        self._on_disconnect = on_disconnect or (lambda _: None)

        self._lock = threading.Lock()
        self._connected = False
        self._running = False
        self._active_subscriptions: set[str] = set()
        self._reconnect_attempt = 0

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ping_task: Optional[asyncio.Task] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def connect(self) -> None:
        """Start the WS background thread and connect."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._ws_thread = threading.Thread(target=self._run_ws_loop, daemon=True)
            self._ws_thread.start()

    def disconnect(self) -> None:
        """Stop the WS connection cleanly."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._connected = False
        # Signal the thread to stop via _running flag
        # Next iteration of the loop will exit

    def subscribe_ticker(self, symbol: str) -> None:
        """Subscribe to ticker stream for symbol (e.g. "BTCUSDT")."""
        topic = f"tickers.{symbol}"
        self._subscribe(topic)

    def subscribe_kline(self, symbol: str, interval: str) -> None:
        """
        Subscribe to kline stream for symbol and interval.

        interval: Bybit interval string — "1", "5", "15", "60", "240", "D", etc.
        symbol: e.g. "BTCUSDT"
        """
        topic = f"kline.{interval}.{symbol}"
        self._subscribe(topic)

    def get_subscriptions(self) -> list[str]:
        """Return copy of currently active subscriptions."""
        with self._lock:
            return list(self._active_subscriptions)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _subscribe(self, topic: str) -> None:
        """Send subscribe frame for topic. Idempotent."""
        with self._lock:
            if topic in self._active_subscriptions:
                return
            if self._ws is not None and self._connected:
                asyncio.run_coroutine_threadsafe(
                    self._send_subscribe(topic), self._ws_loop
                )
            self._active_subscriptions.add(topic)

    async def _send_subscribe(self, topic: str) -> None:
        """Coroutine: send subscribe frame."""
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send(json.dumps({
                "op": "subscribe",
                "args": [topic],
            }))
        except Exception:
            pass

    def _resubscribe_all(self) -> None:
        """Re-send all active subscriptions after reconnect."""
        with self._lock:
            topics = list(self._active_subscriptions)
        for topic in topics:
            asyncio.run_coroutine_threadsafe(self._send_subscribe(topic), self._ws_loop)

    async def _ping_loop(self) -> None:
        """Send ping every PING_INTERVAL_SECONDS to keep connection alive."""
        while self._running and self._connected:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            if self._ws is None or self._ws.closed:
                break
            try:
                await self._ws.send(json.dumps({
                    "op": "ping",
                    "req_id": str(int(time.time() * 1000)),
                }))
            except Exception:
                break

    def _run_ws_loop(self) -> None:
        """Run in a dedicated thread: connect → message loop → reconnect."""
        loop = asyncio.new_event_loop()
        self._ws_loop = loop
        asyncio.set_event_loop(loop)

        while self._running:
            try:
                loop.run_until_complete(self._ws_connect_and_read())
            except Exception:
                pass
            if not self._running:
                break
            # Reconnect with backoff
            delay = RECONNECT_DELAYS[min(self._reconnect_attempt, len(RECONNECT_DELAYS) - 1)]
            self._reconnect_attempt += 1
            time.sleep(delay)

        loop.close()
        self._ws_loop = None

    async def _ws_connect_and_read(self) -> None:
        """Connect, wait for hello, resubscribe, then read messages."""
        async with websockets.connect(BYBIT_WS_URL, ping_interval=None) as ws:
            self._ws = ws
            with self._lock:
                self._connected = True
                self._reconnect_attempt = 0

            self._on_connect()
            self._resubscribe_all()

            # Start ping task
            self._ping_task = asyncio.create_task(self._ping_loop())

            try:
                while self._running:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        self._handle_message(raw)
                    except asyncio.TimeoutError:
                        continue
                    except websockets.ConnectionClosed:
                        break
            finally:
                if self._ping_task:
                    self._ping_task.cancel()
                with self._lock:
                    self._connected = False
                    self._ws = None
                self._on_disconnect("connection_closed")

    def _handle_message(self, raw: str | bytes) -> None:
        """Parse and dispatch a WS message."""
        try:
            msg = json.loads(raw)
        except Exception:
            return

        # pong response — ignore
        if msg.get("op") == "pong":
            return

        # Subscribe/unsubscribe ack — ignore (we trust our own state)
        if "op" in msg and msg.get("op") in ("subscribe", "unsubscribe"):
            return

        topic = msg.get("topic", "")
        data = msg.get("data")

        if topic.startswith("tickers."):
            self._on_ticker(topic, data)
        elif topic.startswith("kline."):
            self._on_kline(topic, data)