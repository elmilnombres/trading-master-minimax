"""
Bybit subaccount-aware client.
Wraps BybitClient and enforces that every call is routed to the correct subaccount.
Bot permissions: READ + TRADE on their own subaccount.
Supervisor permissions: READ-ONLY (no trade methods exposed here).
"""

from exchange.bybit.client import BybitClient, BybitAPIError
from exchange.bybit.adapter import BybitAdapter
from core.rate_limiter.governor import RateLimitGovernor


class BybitSubaccountClient:
    """
    Bot-facing client for a specific Bybit subaccount.
    Enforces subaccount on every call.
    Permissions: READ + TRADE on the bound subaccount.
    """

    def __init__(self, api_key: str, api_secret: str, subaccount_name: str):
        self._client = BybitClient(api_key=api_key, api_secret=api_secret)
        self.subaccount_name = subaccount_name

    @property
    def client(self) -> BybitClient:
        return self._client

    @property
    def governor(self) -> RateLimitGovernor:
        return self._client._governor

    # ---- Market Data (no subaccount needed) ----

    def get_klines(self, category: str, symbol: str, interval: str, start: int | None = None, end: int | None = None, limit: int = 200) -> dict:
        return self._client.get_klines(category, symbol, interval, start, end, limit)

    def get_ticker(self, category: str, symbol: str) -> dict:
        return self._client.get_ticker(category, symbol)

    def get_instrument_info(self, category: str, symbol: str) -> dict:
        return self._client.get_instrument_info(category, symbol)

    def get_orderbook(self, category: str, symbol: str, limit: int = 50) -> dict:
        return self._client.get_orderbook(category, symbol, limit)

    # ---- Account (subaccount-scoped) ----

    def get_positions(self, category: str = "linear") -> list[dict]:
        result = self._client.get_positions(category, subaccount=self.subaccount_name)
        return result.get("list", [])

    def get_account_balance(self) -> dict:
        return self._client.get_account_balance(subaccount=self.subaccount_name)

    # ---- Orders (subaccount-scoped — TRADE permission) ----

    def set_leverage(self, category: str, symbol: str, leverage: int = 1) -> dict:
        return self._client.set_leverage(
            category, symbol,
            buy_leverage=leverage, sell_leverage=leverage,
            subaccount=self.subaccount_name,
        )

    def submit_market_order(
        self, category: str, symbol: str, side: str, qty: float, subaccount: str | None = None
    ) -> dict:
        params = {
            "side": side,
            "qty": str(qty),
            "orderType": "Market",
        }
        return self._client.submit_order(
            category, params,
            subaccount=subaccount or self.subaccount_name,
        )

    def submit_limit_order(
        self, category: str, symbol: str, side: str, price: float, qty: float
    ) -> dict:
        params = {
            "side": side,
            "price": str(price),
            "qty": str(qty),
            "orderType": "Limit",
            "timeInForce": "GTC",
        }
        return self._client.submit_order(
            category, params,
            subaccount=self.subaccount_name,
        )

    def cancel_order(
        self, category: str, symbol: str, order_id: str | None = None,
        client_order_id: str | None = None
    ) -> dict:
        return self._client.cancel_order(
            category, symbol, order_id=order_id,
            client_order_id=client_order_id,
            subaccount=self.subaccount_name,
        )

    def get_open_orders(self, category: str, symbol: str | None = None) -> dict:
        return self._client.get_open_orders(
            category, symbol=symbol,
            subaccount=self.subaccount_name,
        )

    def get_order_history(
        self,
        category: str,
        symbol: str | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        """Fetch order history — all orders including terminal states (Filled, Cancelled, Rejected)."""
        return self._client.get_order_history(
            category,
            symbol=symbol,
            client_order_id=client_order_id,
            subaccount=self.subaccount_name,
        )