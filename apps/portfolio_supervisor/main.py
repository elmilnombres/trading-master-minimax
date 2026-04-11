"""
Portfolio supervisor — Phase 4 production entrypoint.

Supervisor permissions: READ-ONLY on all subaccounts.
No trade methods. No state writes. No freeze/unfreeze.

Monitoring scope (Phase 4):
  - Heartbeat aliveness: detect stale or stopped bots
  - State snapshot review: detect frozen bots, anomalous risk state
  - Portfolio risk summary: aggregate drawdown per bot

Alert outputs (Phase 4 — log to stdout):
  STALE   — heartbeat file older than 15s
  FROZEN  — bot has is_frozen=True in state snapshot
  DRAWDAY — daily loss ≥ 80% of 2.0% cap
  DRAWWEK — weekly loss ≥ 80% of 5.0% cap
  ANOMALY — consecutive losses ≥ 3, or unreadable snapshot

Alert output extensions (Phase 5): Slack / PagerDuty / webhook.
"""

import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from apps._runtime.bot_config import load_runtime_config
from apps._runtime.supervisor_runtime import SupervisorRuntime


def main() -> None:
    bot_name = "portfolio_supervisor"
    print(f"[{bot_name}] Starting Phase 4 supervisor...")

    runtime_config = load_runtime_config()

    print(f"[{bot_name}] Poll interval: {runtime_config.supervisor_poll_seconds}s")
    print(f"[{bot_name}] State dir: {runtime_config.state_dir}")
    print(f"[{bot_name}] Heartbeat dir: {runtime_config.heartbeat_dir}")
    print(f"[{bot_name}] Supervised bots: alpha_bot, beta_bot, gamma_bot")
    print(f"[{bot_name}] Mode: READ-ONLY — no state mutations, no trade operations")
    print(f"[{bot_name}] Entering poll loop — Ctrl-C to stop")

    supervisor = SupervisorRuntime(runtime_config=runtime_config)

    def _sig_handler(sig, frame):
        print(f"\n[{bot_name}] Received signal {sig} — initiating shutdown")
        supervisor.shutdown()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    supervisor.run()


if __name__ == "__main__":
    main()
