"""
Bybit adapter — normalizes Bybit API responses into internal schema types.
All business logic stays in core/. This module maps external format to internal types.
"""

from datetime import datetime
from typing import Any

from schemas.candle import Candle, Timeframe, CandleBatch
from schemas.order import Order, OrderSide, OrderType, OrderStatus
from schemas.position import Position, PositionSide
from schemas.execution import ExecutionResult, ExecutionErrorType

# Bybit interval string → Timeframe enum
BYBIT_INTERVAL_MAP: dict[str, Timeframe] = {
    "1": Timeframe.M1,
    "5": Timeframe.M5,
    "15": Timeframe.M15,
    "30": Timeframe.H1,
    "60": Timeframe.H1,
    "240": Timeframe.H4,
    "D": Timeframe.D1,
    "1d": Timeframe.D1,
    "W": Timeframe.D1,
    "M": Timeframe.D1,
}


class BybitAdapter:
    """
    Converts Bybit API v5 responses to internal schema types.
    Does NOT make API calls — only transforms data.
    """

    @staticmethod
    def parse_candle(raw: list | dict[str, Any], symbol: str, timeframe: Timeframe) -> Candle:
        """
        Parse one Bybit kline entry into a Candle.

        Bybit v5 kline list entry: [timestamp, open, high, low, close, volume, turnover]
        Each element is a string in the raw response.
        """
        if isinstance(raw, list):
            # Bybit returns [t, o, h, l, c, v, ...]
            ts_str, o_str, h_str, l_str, c_str, v_str = (
                raw[0], raw[1], raw[2], raw[3], raw[4], raw[5]
            )
            return Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.utcfromtimestamp(int(ts_str) / 1000),
                open=float(o_str),
                high=float(h_str),
                low=float(l_str),
                close=float(c_str),
                volume=float(v_str),
            )
        # Fallback for dict-style (if ever needed)
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.utcfromtimestamp(int(raw["t"]) / 1000),
            open=float(raw["o"]),
            high=float(raw["h"]),
            low=float(raw["l"]),
            close=float(raw["c"]),
            volume=float(raw["v"]),
        )

    @staticmethod
    def parse_candle_batch(
        raw_list: list[dict[str, Any]], symbol: str, timeframe: Timeframe
    ) -> CandleBatch:
        candles = [BybitAdapter.parse_candle(r, symbol, timeframe) for r in raw_list]
        return CandleBatch(symbol=symbol, timeframe=timeframe, candles=candles)

    @staticmethod
    def parse_order_response(raw: dict[str, Any]) -> dict[str, Any]:
        """Extract normalized order fields from Bybit order creation response."""
        return {
            "order_id": raw.get("orderId"),
            "client_order_id": raw.get("clientOrderId"),
            "symbol": raw.get("symbol"),
            "side": raw.get("side"),
            "order_type": raw.get("orderType"),
            "price": float(raw["price"]) if raw.get("price") else None,
            "qty": float(raw["qty"]) if raw.get("qty") else None,
            "status": BybitAdapter._normalize_order_status(raw.get("orderStatus", "")),
        }

    @staticmethod
    def parse_position(raw: dict[str, Any]) -> Position | None:
        """Parse Bybit position entry into internal Position schema."""
        size = float(raw.get("size", 0))
        if size == 0:
            return None  # no open position

        side = PositionSide.LONG if raw.get("side", "").lower() == "buy" else PositionSide.SHORT

        return Position(
            symbol=raw.get("symbol", ""),
            side=side,
            entry_price=float(raw["avgPrice"]) if raw.get("avgPrice") else None,
            qty=size,
            stop_loss=float(raw["stopLoss"]) if raw.get("stopLoss") else None,
            take_profit_1=float(raw["takeProfit"]) if raw.get("takeProfit") else None,
            unrealized_pnl=float(raw["unrealisedPnl"]) if raw.get("unrealisedPnl") else None,
            realized_pnl=float(raw["closedPnl"]) if raw.get("closedPnl") else None,
            opened_at=datetime.utcfromtimestamp(int(raw["updatedTime"]) / 1000)
                if raw.get("updatedTime") else None,
        )

    @staticmethod
    def _normalize_order_status(bybit_status: str) -> OrderStatus:
        mapping = {
            "Created": OrderStatus.PENDING,
            "New": OrderStatus.SUBMITTED,
            "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
            "Filled": OrderStatus.FILLED,
            "Cancelled": OrderStatus.CANCELLED,
            "Rejected": OrderStatus.REJECTED,
        }
        return mapping.get(bybit_status, OrderStatus.PENDING)

    @staticmethod
    def parse_execution_result(raw: dict[str, Any]) -> ExecutionResult:
        """
        Parse Bybit order creation response into ExecutionResult.

        Maps Bybit retCode/retMsg to ExecutionErrorType.
        Returns ExecutionResult with success=True only if the order was accepted.
        """
        if raw.get("retCode") != 0:
            code = raw.get("retCode", -1)
            msg = raw.get("retMsg", "Unknown error")
            return ExecutionResult(
                success=False,
                error=BybitAdapter._map_retcode_to_error_type(code),
                error_detail=f"Bybit {code}: {msg}",
            )

        result = raw.get("result", {})
        return ExecutionResult(
            success=True,
            order_id=result.get("orderId"),
            client_order_id=result.get("clientOrderId"),
            symbol=result.get("symbol"),
            status=result.get("orderStatus"),
            error=ExecutionErrorType.NONE,
            error_detail=None,
        )

    @staticmethod
    def _map_retcode_to_error_type(code: int) -> ExecutionErrorType:
        """Map Bybit retCode to ExecutionErrorType."""
        mapping = {
            10001: ExecutionErrorType.EXCHANGE_ERROR,
            10002: ExecutionErrorType.TIMEOUT,
            10003: ExecutionErrorType.EXCHANGE_ERROR,
            10006: ExecutionErrorType.TIMEOUT,
            10007: ExecutionErrorType.EXCHANGE_ERROR,
            10008: ExecutionErrorType.EXCHANGE_ERROR,
            10009: ExecutionErrorType.TIMEOUT,
            20001: ExecutionErrorType.EXCHANGE_ERROR,
            20002: ExecutionErrorType.EXCHANGE_ERROR,
            20003: ExecutionErrorType.EXCHANGE_ERROR,
            20004: ExecutionErrorType.MIN_NOTIONAL,
            20005: ExecutionErrorType.EXCHANGE_ERROR,
            20006: ExecutionErrorType.EXCHANGE_ERROR,
            20007: ExecutionErrorType.EXCHANGE_ERROR,
            20010: ExecutionErrorType.EXCHANGE_ERROR,
            20015: ExecutionErrorType.EXCHANGE_ERROR,
            20019: ExecutionErrorType.EXCHANGE_ERROR,
            20020: ExecutionErrorType.EXCHANGE_ERROR,
            20021: ExecutionErrorType.EXCHANGE_ERROR,
            20022: ExecutionErrorType.EXCHANGE_ERROR,
            110006: ExecutionErrorType.EXCHANGE_ERROR,
            110007: ExecutionErrorType.EXCHANGE_ERROR,
        }
        return mapping.get(code, ExecutionErrorType.EXCHANGE_ERROR)