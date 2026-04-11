"""
Position sizing — risk amount to instrument qty unit.

Owned by core/risk/.
No strategy logic. No exchange calls.
"""

from dataclasses import dataclass

from core.bot_id import BotId


@dataclass
class SizingInput:
    """
    Input to PositionSizer.size_position.

    risk_amount_usdt is the only risk input — all other parameters
    are instrument-specific or signal-specific.
    """

    bot_id: BotId
    risk_amount_usdt: float          # capital_usdt * risk_per_trade_pct (derived upstream)
    entry_price: float               # price used for sizing (from Signal.locked)
    stop_loss_price: float           # structural stop from Signal.locked (required)
    lot_size: float                 # from InstrumentFilterCache (instrument qty unit)
    min_order_qty: float            # from InstrumentFilterCache (instrument qty unit)


@dataclass
class SizingResult:
    """
    Output of PositionSizer.size_position.

    risk_used_usdt is pre-fees / pre-slippage unless explicitly adjusted elsewhere.

    rejected == True: the order MUST NOT proceed to exchange submission.
    qty is still populated for diagnostic purposes but is not submit-ready.
    """

    qty: float                      # quantized in instrument qty unit
    risk_used_usdt: float           # qty * |entry_price - stop_loss_price| (pre-fees/slippage)
    risk_amount_usdt: float         # the budget this result was checked against
    within_risk_limit: bool        # risk_used_usdt <= risk_amount_usdt
    rejected: bool = False          # True when min lot causes budget breach


def round_to_lot(qty: float, lot_size: float) -> float:
    """
    Round qty DOWN to nearest multiple of lot_size.
    This is the only rounding direction that preserves the risk budget.
    """
    steps = int(qty / lot_size)
    return steps * lot_size


class PositionSizer:
    """
    Converts a risk_amount_usdt budget into a exchange-ready qty.

    Formula (Bybit linear USDT perpetuals):
        risk_used_usdt = qty * |entry_price - stop_loss_price|

    qty, lot_size, and min_order_qty are all in instrument qty units.

    Quantization:
        raw_qty  = risk_amount_usdt / |entry_price - stop_loss_price|
        qty      = floor(raw_qty / lot_size) * lot_size
        if qty < min_order_qty:
            qty = min_order_qty
            risk_used_usdt = qty * |entry_price - stop_loss_price|
            within_risk_limit = (risk_used_usdt <= risk_amount_usdt)
            rejected = not within_risk_limit

    Rejection path:
        If the minimum order quantity causes the actual risk to exceed
        the budget, rejected = True and execution MUST NOT submit.
        The qty field is populated for diagnostic/debugging purposes.
    """

    def size_position(self, inp: SizingInput) -> SizingResult:
        price_diff = abs(inp.entry_price - inp.stop_loss_price)

        if price_diff == 0:
            return SizingResult(
                qty=0.0,
                risk_used_usdt=0.0,
                risk_amount_usdt=inp.risk_amount_usdt,
                within_risk_limit=False,
                rejected=True,
            )

        raw_qty = inp.risk_amount_usdt / price_diff
        qty = round_to_lot(raw_qty, inp.lot_size)

        if qty < inp.min_order_qty:
            # min_order_qty would require upsizing beyond raw_qty
            qty = inp.min_order_qty
            risk_used_usdt = qty * price_diff
            within_risk_limit = risk_used_usdt <= inp.risk_amount_usdt
            return SizingResult(
                qty=qty,
                risk_used_usdt=risk_used_usdt,
                risk_amount_usdt=inp.risk_amount_usdt,
                within_risk_limit=within_risk_limit,
                rejected=not within_risk_limit,
            )

        risk_used_usdt = qty * price_diff
        within_risk_limit = risk_used_usdt <= inp.risk_amount_usdt

        # No upsizing beyond raw_qty — if rounding causes breach, it's a rejection
        return SizingResult(
            qty=qty,
            risk_used_usdt=risk_used_usdt,
            risk_amount_usdt=inp.risk_amount_usdt,
            within_risk_limit=within_risk_limit,
            rejected=not within_risk_limit,
        )
