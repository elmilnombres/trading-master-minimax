"""
Bybit raw API client — HTTP wrapper only.
No business logic. No order sizing. No strategy.
"""

import hashlib
import hmac
import time
from typing import Any

import httpx

from core.rate_limiter.governor import RateLimitGovernor


class BybitClient:
    """
    Raw Bybit API v5 client.
    Handles signature generation and request dispatch.
    Does NOT handle subaccount routing — use BybitSubaccountClient for that.
    """

    BASE_URL = "https://api.bybit.com"

    def __init__(self, api_key: str, api_secret: str, recv_window: int = 5000):
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window = recv_window
        self._governor = RateLimitGovernor()

    def _sign(self, params: dict[str, Any], timestamp: int) -> str:
        """
        Generate HMAC SHA256 signature per Bybit API spec.
        String to sign = timestamp + api_key + recv_window + JSON(params).
        """
        param_str = f"{timestamp}{self.api_key}{self.recv_window}"
        for k in sorted(params.keys()):
            param_str += f"{k}={params[k]}"
        return hmac.new(
            self.api_secret.encode(),
            param_str.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        subaccount: str | None = None,
    ) -> dict[str, Any]:
        """Send signed request to Bybit API v5."""
        # Rate-limit throttle check before sending
        if self._governor.should_throttle():
            time.sleep(self._governor.get_cooldown_seconds())

        timestamp = int(time.time() * 1000)
        signed_params = (params or {}).copy()
        signature = self._sign(signed_params, timestamp)

        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": str(timestamp),
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
            "Content-Type": "application/json",
        }
        # Subaccount header if operating on behalf of a subaccount
        if subaccount:
            headers["X-BAPI-TRADE-VERSION"] = "2"  # subaccount mode
            headers["X-BAPI-SUB-ACCOUNT"] = subaccount

        url = f"{self.BASE_URL}{endpoint}"
        with httpx.Client(timeout=30.0) as client:
            if method == "GET":
                response = client.get(url, headers=headers, params=signed_params)
            elif method == "POST":
                response = client.post(url, headers=headers, json=signed_params)
            elif method == "PUT":
                response = client.put(url, headers=headers, json=signed_params)
            elif method == "DELETE":
                response = client.delete(url, headers=headers, json=signed_params)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()
        data = response.json()

        # Record response headers in governor (reset on success, backoff on 10006)
        self._governor.record_response(dict(response.headers), data.get("retCode"))

        # Handle 10006: single owner aborts tick and raises immediately — no retry
        if data.get("retCode") == 10006:
            self._governor.on_10006_abort()
            raise BybitAPIError(
                code=10006,
                msg="Rate limit exceeded",
            )

        # Bybit error structure
        if data.get("retCode") != 0:
            raise BybitAPIError(
                code=data.get("retCode", -1),
                msg=data.get("retMsg", "Unknown error"),
            )

        return data.get("result", {})

    # ---- Market Data ----

    def get_klines(
        self,
        category: str,  # "spot" | "linear" | "inverse"
        symbol: str,
        interval: str,  # "1" "3" "5" "15" "30" "60" "240" "D" "W" "M"
        start: int | None = None,  # ms timestamp
        end: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        return self._request("GET", "/v5/market/kline", params)

    def get_ticker(self, category: str, symbol: str) -> dict[str, Any]:
        params = {"category": category, "symbol": symbol}
        return self._request("GET", "/v5/market/tickers", params)

    def get_instrument_info(
        self, category: str, symbol: str | None = None
    ) -> dict[str, Any]:
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/v5/market/instruments-info", params)

    def get_orderbook(self, category: str, symbol: str, limit: int = 50) -> dict[str, Any]:
        params = {"category": category, "symbol": symbol, "limit": limit}
        return self._request("GET", "/v5/market/orderbook", params)

    # ---- Account ----

    def get_positions(self, category: str, subaccount: str | None = None) -> dict[str, Any]:
        params = {"category": category}
        return self._request("GET", "/v5/position/list", params, subaccount=subaccount)

    def get_account_balance(
        self, account_type: str = "UNIFIED", subaccount: str | None = None
    ) -> dict[str, Any]:
        params = {"accountType": account_type}
        return self._request("GET", "/v5/account/wallet-balance", params, subaccount=subaccount)

    # ---- Orders ----

    def set_leverage(self, category: str, symbol: str, buy_leverage: int, sell_leverage: int, subaccount: str | None = None) -> dict[str, Any]:
        params = {
            "category": category,
            "symbol": symbol,
            "buyLeverage": str(buy_leverage),
            "sellLeverage": str(sell_leverage),
        }
        return self._request("POST", "/v5/position/set-leverage", params, subaccount=subaccount)

    def place_order(
        self, category: str, params: dict[str, Any], subaccount: str | None = None
    ) -> dict[str, Any]:
        full_params = {"category": category, **params}
        return self._request("POST", "/v5/order/replace", full_params, subaccount=subaccount)

    def submit_order(
        self, category: str, params: dict[str, Any], subaccount: str | None = None
    ) -> dict[str, Any]:
        full_params = {"category": category, **params}
        return self._request("POST", "/v5/order/create", full_params, subaccount=subaccount)

    def cancel_order(
        self, category: str, symbol: str, order_id: str | None = None,
        client_order_id: str | None = None, subaccount: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["clientOrderId"] = client_order_id
        return self._request("POST", "/v5/order/cancel", params, subaccount=subaccount)

    def get_open_orders(
        self, category: str, symbol: str | None = None, subaccount: str | None = None
    ) -> dict[str, Any]:
        params = {"category": category, "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/v5/order/realtime", params, subaccount=subaccount)

    def get_order_history(
        self,
        category: str,
        symbol: str | None = None,
        client_order_id: str | None = None,
        subaccount: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch order history — all orders including terminal states.

        Bybit endpoint: GET /v5/order/list
        Returns orders in all states: Created, New, PartiallyFilled,
        Filled, Cancelled, Rejected.

        This is the authoritative source for terminal-order detection.
        Used by get_order_by_client_id for restart-safe idempotency.
        """
        params: dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        if client_order_id:
            params["clientOrderId"] = client_order_id
        return self._request("GET", "/v5/order/list", params, subaccount=subaccount)


class BybitAPIError(Exception):
    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"Bybit API error {code}: {msg}")