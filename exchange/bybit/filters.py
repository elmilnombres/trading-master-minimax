"""
Instrument filter cache — fetches and caches Bybit instrument filters.
Used by the execution engine to validate orders before submission.
Critical for $50 capital — prevents orders that violate minNotional.

Filter cache refresh: every 5 minutes + before every order if stale > 60s.
"""

import time
from dataclasses import dataclass
from typing import Any

from exchange.bybit.client import BybitClient


@dataclass
class InstrumentFilter:
    """Normalized instrument filter for a symbol."""

    symbol: str
    min_order_qty: float
    qty_step: float
    tick_size: float
    min_notional: float
    price_precision: int
    qty_precision: int
    fetched_at: float  # unix timestamp

    @property
    def lot_size(self) -> float:
        """Alias for qty_step — used throughout ExecutionEngine."""
        return self.qty_step


class InstrumentFilterCache:
    """
    Caches Bybit instrument filters per symbol.
    Stale after 5 minutes or if last fetch > 60s before an order.
    Thread-safe for concurrent access from multiple bots on the same VPS.
    """

    DEFAULT_TTL_SECONDS = 300  # 5 minutes
    STALE_BEFORE_ORDER_SECONDS = 60

    def __init__(self, client: BybitClient, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._client = client
        self._ttl = ttl_seconds
        self._cache: dict[str, InstrumentFilter] = {}
        self._category_map: dict[str, str] = {
            "BTCUSDT": "linear",
            "ETHUSDT": "linear",
            "SOLUSDT": "linear",
            "XRPUSDT": "linear",
            "ADAUSDT": "linear",
            "DOGEUSDT": "linear",
            "LINKUSDT": "linear",
            "AVAXUSDT": "linear",
            "DOTUSDT": "linear",
            "MATICUSDT": "linear",
            "INJUSDT": "linear",
            "APTUSDT": "linear",
            "SUIUSDT": "linear",
            "ARBUSDT": "linear",
            "OPUSDT": "linear",
            "RNDRUSDT": "linear",
            "FETUSDT": "linear",
            "TIAUSDT": "linear",
            "NEARUSDT": "linear",
        }  # extend as needed; default to "linear"

    def _category_for_symbol(self, symbol: str) -> str:
        return self._category_map.get(symbol, "linear")

    def fetch_filter(self, symbol: str, force: bool = False) -> InstrumentFilter:
        """
        Fetch instrument info from Bybit and cache it.
        Returns cached value if still valid.
        """
        now = time.time()
        cached = self._cache.get(symbol)

        if not force and cached and (now - cached.fetched_at) < self._ttl:
            return cached

        category = self._category_for_symbol(symbol)
        raw = self._client.get_instrument_info(category, symbol)
        items = raw.get("list", [])

        if not items:
            raise ValueError(f"No instrument info for {symbol} in category {category}")

        info = items[0]
        lot_size_filter = info.get("lotSizeFilter", {})
        price_filter = info.get("priceFilter", {})

        filter_obj = InstrumentFilter(
            symbol=symbol,
            min_order_qty=float(lot_size_filter.get("minOrderQty", 0)),
            qty_step=float(lot_size_filter.get("qtyStep", 1)),
            tick_size=float(price_filter.get("tickSize", 0.01)),
            min_notional=float(info.get("minNotional", 0)),
            price_precision=int(info.get("pricePrecision", 2)),
            qty_precision=int(lot_size_filter.get("qtyPrecision", 3)),
            fetched_at=now,
        )

        self._cache[symbol] = filter_obj
        return filter_obj

    def get_filter(self, symbol: str) -> InstrumentFilter:
        """Get cached filter, fetching if stale or missing."""
        return self.fetch_filter(symbol, force=False)

    def is_stale(self, symbol: str) -> bool:
        """Check if cached filter for symbol is stale (older than STALE_BEFORE_ORDER_SECONDS)."""
        cached = self._cache.get(symbol)
        if not cached:
            return True
        return (time.time() - cached.fetched_at) > self.STALE_BEFORE_ORDER_SECONDS

    def quantize_qty(self, symbol: str, qty: float) -> float:
        """
        Round qty down to nearest qtyStep.
        Critical for avoiding 'qtyStep violation' rejections from Bybit.
        """
        f = self.get_filter(symbol)
        steps = int(qty / f.qty_step)
        return max(steps * f.qty_step, f.min_order_qty)

    def quantize_price(self, symbol: str, price: float) -> float:
        """
        Round price to nearest tickSize.
        Critical for avoiding 'price precision' rejections from Bybit.
        """
        f = self.get_filter(symbol)
        ticks = round(price / f.tick_size)
        return ticks * f.tick_size

    def validate_order(self, symbol: str, price: float, qty: float) -> tuple[bool, str]:
        """
        Full validation of an order against exchange filters.
        Returns (is_valid, reason).
        """
        f = self.get_filter(symbol)

        # Quantize to check
        qty_rounded = self.quantize_qty(symbol, qty)
        price_rounded = self.quantize_price(symbol, price)
        notional = price_rounded * qty_rounded

        if qty_rounded < f.min_order_qty:
            return False, f"qty {qty_rounded} below min {f.min_order_qty}"

        if notional < f.min_notional:
            return False, f"notional {notional:.4f} below min {f.min_notional:.4f}"

        # Check if qty was significantly changed by quantization
        if abs(qty_rounded - qty) / qty > 0.01:
            return False, f"qty changed too much after quantization: {qty} -> {qty_rounded}"

        return True, "ok"