"""
Beta bot — Phase 4 production entrypoint.

Bot permissions: READ + TRADE on BYBIT_BETA subaccount.
Strategy: 4-step confirmation sequence → M15 mitigation FVG entry.

Unlike Alpha, Beta does NOT gate on H4 candle close.

Signal construction (Phase 4):
  Direction: validate_beta_sequence().direction
  Entry: M15 mitigation FVG midpoint
  Stop: FVG zone edge
  TP1 = 2R, TP2 = 3R
  Sweep level: PDH/PDL primary, session high/low secondary

Bot-owned freeze/unfreeze:
  StateStore.set_frozen() is called by the bot based on risk limit state.
  Supervisor reads is_frozen from the heartbeat file only.
"""

import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from apps._runtime.bot_config import load_bot_config, load_runtime_config
from apps._runtime.bot_runtime import BotRuntime
from core.strategy.adapter import BetaStrategyAdapter


def main() -> None:
    bot_name = "beta_bot"
    print(f"[{bot_name}] Starting Phase 4 runtime...")

    bot_config = load_bot_config(bot_name)
    runtime_config = load_runtime_config()

    print(f"[{bot_name}] Subaccount: {bot_config.subaccount_name}")
    print(f"[{bot_name}] Symbol: {bot_config.symbol}")
    print(f"[{bot_name}] Capital: {bot_config.capital_usdt} USDT")
    print(f"[{bot_name}] Risk per trade: {bot_config.risk_per_trade_pct:.2%} "
          f"({bot_config.risk_amount_usdt:.4f} USDT)")

    strategy = BetaStrategyAdapter()

    runtime = BotRuntime(
        bot_config=bot_config,
        runtime_config=runtime_config,
        strategy_adapter=strategy,
    )

    def _sig_handler(sig, frame):
        print(f"\n[{bot_name}] Received signal {sig} — initiating shutdown")
        runtime.shutdown()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    print(f"[{bot_name}] Tick loop: {runtime_config.tick_interval_seconds}s interval")
    print(f"[{bot_name}] Entering tick loop — Ctrl-C to stop")

    runtime.run()


if __name__ == "__main__":
    main()
