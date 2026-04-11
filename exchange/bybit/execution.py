"""
Bybit execution adapter — all Bybit exchange calls live here.

Owned by exchange/bybit/.
Implements the ExecutionAdapter protocol declared in core/execution/engine.py.

No strategy logic. No cross-exchange abstractions.
"""

import time
from typing import Any

from exchange.bybit.adapter import BybitAdapter
from exchange.bybit.client import BybitClient, BybitAPIError
from exchange.bybit.subaccount import BybitSubaccountClient

from schemas.execution import ExecutionResult, ExecutionErrorType, OrderRequest
from schemas.order import Order, OrderStatus

from core.execution.errors import RetryableExchangeError


# Retry policy for transient Bybit errors.
MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_CODES = {28936, 10002, 10006, 10007, 10008, 10009}  # rate limit + server errors


def _is_retryable(code: int) -> bool:
    return code in RETRY_CODES


def _submit_order_with_retry(
    subaccount: BybitSubaccountClient,
    category: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """
    Submit an order with exponential backoff retry.

    Retries on transient error codes only. Returns the last response
    (success or non-retryable error) without raising.
    """
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            result = subaccount.submit_order(category, params)
            return result
        except BybitAPIError as e:
            if not _is_retryable(e.code):
                raise
            last_error = e
            delay = RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
            time.sleep(delay)

    # All retries exhausted
    if last_error is not None:
        raise last_error
    raise RuntimeError("retry loop exited without result or error")


class BybitExecutionAdapter:
    """
    Implements ExecutionAdapter for Bybit linear USDT perpetuals.

    Responsibilities:
    - Translates OrderRequest to Bybit API params
    - Attaches stop-loss and take-profit via Bybit native parameters
    - Parses exchange responses into ExecutionResult
    - Exposes get_order_by_client_id for reconciliation
    - Never raises to the caller — all errors surface as ExecutionResult.error

    Subaccount scoping:
    - Every call is routed to the bound subaccount via BybitSubaccountClient.
    - The engine is constructed with one adapter per bot, scoped to one subaccount.
    """

    CATEGORY = "linear"  # Bybit linear USDT perpetuals

    def __init__(self, subaccount_client: BybitSubaccountClient):
        self._sa = subaccount_client

    def submit_order(self, req: OrderRequest) -> ExecutionResult:
        """
        Submit an order to Bybit via the subaccount client.

        For market orders, entry_price is None and qty is submitted as-is.
        For limit orders, entry_price is quantized via InstrumentFilterCache
        before being included in params (quantization is done upstream
        in ExecutionEngine via the lot_size check; this method uses the
        already-quantized qty from OrderRequest).

        Stop-loss is attached via Bybit's native sl_trigger_price on the
        entry order (One-Way Mode). The stop is the structural stop from the
        approved Signal — StopManager does not invent a stop.

        Take-profit is attached via tp_trigger_price on the entry order, or
        as separate TakeProfitMarket orders if multiple TP levels are needed.
        Phase 3 uses tp_trigger_price_1 on the entry order only.

        Retry: transient Bybit errors trigger exponential backoff.
        """
        params = self._build_order_params(req)

        try:
            raw = _submit_order_with_retry(self._sa, self.CATEGORY, params)
            parsed = BybitAdapter.parse_order_response(raw)
            return ExecutionResult(
                success=True,
                order_id=parsed.get("order_id"),
                client_order_id=parsed.get("client_order_id"),
                symbol=parsed.get("symbol"),
                status=parsed.get("status"),
                error=ExecutionErrorType.NONE,
                error_detail=None,
            )
        except BybitAPIError as e:
            error_type = self._map_bybit_error(e.code, e.msg)
            return ExecutionResult(
                success=False,
                error=error_type,
                error_detail=f"Bybit {e.code}: {e.msg}",
            )
        except TimeoutError:
            return ExecutionResult(
                success=False,
                error=ExecutionErrorType.TIMEOUT,
                error_detail="request timed out after retries",
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                error=ExecutionErrorType.EXCHANGE_ERROR,
                error_detail=str(e),
            )

    def cancel_order(self, order_id: str, symbol: str) -> ExecutionResult:
        """Cancel an open order by exchange order_id."""
        try:
            raw = self._sa.cancel_order(self.CATEGORY, symbol, order_id=order_id)
            return ExecutionResult(
                success=True,
                order_id=order_id,
                error=ExecutionErrorType.NONE,
            )
        except BybitAPIError as e:
            return ExecutionResult(
                success=False,
                error=self._map_bybit_error(e.code, e.msg),
                error_detail=f"Bybit {e.code}: {e.msg}",
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                error=ExecutionErrorType.EXCHANGE_ERROR,
                error_detail=str(e),
            )

    def get_order_by_client_id(self, client_order_id: str) -> Order | None:
        """
        Fetch order status by client_order_id.

        Searches BOTH open orders AND order history.
        - Not found in open orders → check order history (/v5/order/list)
        - Not found in order history → return None (never existed)
        - History lookup fails → RAISE RetryableExchangeError (fail-closed)

        Order history (/v5/order/list) covers all terminal states:
        Filled, Cancelled, Rejected — the complete source of record for
        terminal orders. Fill history (/v5/order/execution-list) is NOT used
        for idempotency because it does not cover Cancelled/Rejected orders.

        Used by ReconciliationService and ExecutionEngine for restart-safe
        idempotency. The fail-closed policy ensures we never treat an
        unresolvable lookup as "never submitted".
        """
        # Step 1: check open orders
        try:
            raw = self._sa.get_open_orders(self.CATEGORY)
            items = raw.get("list", [])
            for item in items:
                if item.get("clientOrderId") == client_order_id:
                    return self._parse_order_item(item)
        except Exception as e:
            # Unexpected error on open-order query — fail-closed
            raise RetryableExchangeError(
                f"open-order lookup failed for {client_order_id}: {e}"
            ) from e

        # Step 2: not in open orders — check order history (/v5/order/list)
        # Covers Filled, Cancelled, Rejected (full terminal record).
        # FAIL-CLOSED: if history lookup cannot complete, raise, do not return None.
        try:
            raw = self._sa.get_order_history(
                self.CATEGORY, client_order_id=client_order_id
            )
            items = raw.get("list", [])
            for item in items:
                if item.get("clientOrderId") == client_order_id:
                    return self._parse_order_item(item)
            # Not found in either open orders or order history — never existed
            return None
        except Exception as e:
            raise RetryableExchangeError(
                f"order-history lookup failed for {client_order_id}: {e}"
            ) from e

    def get_open_orders(self, symbol: str) -> list[Order]:
        """Get all open orders for symbol."""
        try:
            raw = self._sa.get_open_orders(self.CATEGORY, symbol=symbol)
            items = raw.get("list", [])
            return [self._parse_order_item(i) for i in items]
        except Exception:
            return []

    def get_positions(self, symbol: str) -> list[dict]:
        """Get open positions for symbol from Bybit."""
        try:
            raw_positions = self._sa.get_positions(self.CATEGORY)
            return [p for p in raw_positions if p.get("symbol") == symbol and float(p.get("size", 0)) > 0]
        except Exception:
            return []

    # ---- Internal helpers ----

    def _build_order_params(self, req: OrderRequest) -> dict[str, Any]:
        """
        Build Bybit API params from OrderRequest.

        Quantization of qty is already done by PositionSizer.
        Quantization of price is done here (round to tick_size via adapter).
        """
        params: dict[str, Any] = {
            "symbol": req.symbol,
            "side": req.side,
            "orderType": req.order_type,
            "qty": str(req.qty),
        }

        if req.order_type == "Limit" and req.price is not None:
            params["price"] = str(req.price)
            params["timeInForce"] = "GTC"

        # Attached stop-loss (One-Way Mode)
        if req.sl_trigger_price is not None:
            params["slTriggerPrice"] = str(req.sl_trigger_price)
            params["slTriggerBy"] = "LastPrice"

        # Attached take-profit (Phase 3: one TP only)
        if req.tp_trigger_price_1 is not None:
            params["tpTriggerPrice"] = str(req.tp_trigger_price_1)
            params["tpTriggerBy"] = "LastPrice"

        # Deterministic client_order_id
        params["clientOrderId"] = req.client_order_id

        return params

    def _parse_order_item(self, item: dict[str, Any]) -> Order:
        """Parse a Bybit open-order list item into an Order."""
        return Order(
            order_id=item.get("orderId"),
            client_order_id=item.get("clientOrderId"),
            symbol=item.get("symbol"),
            side=item.get("side", ""),
            order_type=item.get("orderType", ""),
            price=float(item["price"]) if item.get("price") else None,
            stop_price=float(item["triggerPrice"]) if item.get("triggerPrice") else None,
            qty=float(item["qty"]) if item.get("qty") else None,
            avg_price=float(item["avgPrice"]) if item.get("avgPrice") else None,
            filled_qty=float(item["execQty"]) if item.get("execQty") else None,
            status=BybitAdapter._normalize_order_status(item.get("orderStatus", "")),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(item.get("createdTime", 0)) / 1000)),
            bot_id=None,
            subaccount_name=self._sa.subaccount_name,
            signal_id=None,
        )

    def _map_bybit_error(self, code: int, msg: str) -> ExecutionErrorType:
        """Map Bybit retCode to ExecutionErrorType."""
        mapping = {
            10001: ExecutionErrorType.EXCHANGE_ERROR,     # unknown error
            10002: ExecutionErrorType.TIMEOUT,            # request timestamp error
            10003: ExecutionErrorType.EXCHANGE_ERROR,     # signature error
            10006: ExecutionErrorType.TIMEOUT,            # too many requests
            10007: ExecutionErrorType.EXCHANGE_ERROR,     # timestamp type mismatch
            10008: ExecutionErrorType.EXCHANGE_ERROR,     # invalid request
            10009: ExecutionErrorType.TIMEOUT,            # endpoint error
            20001: ExecutionErrorType.EXCHANGE_ERROR,     # not found order
            20002: ExecutionErrorType.EXCHANGE_ERROR,     # qty below minimum
            20003: ExecutionErrorType.EXCHANGE_ERROR,     # qty above maximum
            20004: ExecutionErrorType.MIN_NOTIONAL,      # order value below minimum
            20005: ExecutionErrorType.EXCHANGE_ERROR,    # price below minimum
            20006: ExecutionErrorType.EXCHANGE_ERROR,    # price above maximum
            20007: ExecutionErrorType.EXCHANGE_ERROR,     # qty step error
            20010: ExecutionErrorType.EXCHANGE_ERROR,    # price step error
            20015: ExecutionErrorType.EXCHANGE_ERROR,    # position value exceeds limit
            20019: ExecutionErrorType.EXCHANGE_ERROR,     # balance insufficient
            20020: ExecutionErrorType.EXCHANGE_ERROR,   # order price has too many decimals
            20021: ExecutionErrorType.EXCHANGE_ERROR,    # order qty has too many decimals
            20022: ExecutionErrorType.EXCHANGE_ERROR,     # trigger price has too many decimals
            110006: ExecutionErrorType.EXCHANGE_ERROR,   # stop loss below/above mark price
            110007: ExecutionErrorType.EXCHANGE_ERROR,   # take profit below/above mark price
        }
        return mapping.get(code, ExecutionErrorType.EXCHANGE_ERROR)
