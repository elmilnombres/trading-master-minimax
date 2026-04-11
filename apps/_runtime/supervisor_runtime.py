"""
Supervisor runtime — read-only monitoring and alerting for all bots.

Owned by apps/_runtime/.
Supervisor permissions: READ-ONLY on all subaccounts (no trade methods).
No state mutations. No freeze/unfreeze. No order operations.

Path resolution: falls back to project-relative paths in development when
/app/ directories are unavailable (handled by RuntimeConfig).

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

from __future__ import annotations

import json
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps._runtime.bot_config import load_runtime_config, RuntimeConfig
from apps._runtime.heartbeat import HeartbeatReader, SUPERVISOR_MAX_LATENCY_SECONDS

# Frozen constants from CLAUDE.md
MAX_DAILY_LOSS_PCT = 0.02     # 2.0%
MAX_WEEKLY_LOSS_PCT = 0.05    # 5.0%
DRAWDOWN_WARNING_PCT = 0.80   # alert when 80% of limit reached

SUPERVISED_BOTS = ["alpha_bot", "beta_bot", "gamma_bot"]


@dataclass
class BotAlert:
    """One alert from the supervisor for one bot."""
    bot_id: str
    level: str       # "STALE" | "FROZEN" | "DRAWDAY" | "DRAWWEK" | "ANOMALY"
    message: str
    timestamp: datetime


class SupervisorRuntime:
    """
    Read-only supervisor — monitors bot health via heartbeat and state files.

    Design contract (frozen Phase 4):
    - Reads ONLY: heartbeat files, state snapshot files
    - Writes NOTHING: no state, no orders, no API calls that mutate state
    - Alert output: log lines (Phase 5: Slack/webhook)

    Poll interval: SUPERVISOR_POLL_SECONDS = 5s (frozen CLAUDE.md)
    """

    def __init__(self, runtime_config: RuntimeConfig):
        self._rt = runtime_config
        self._heartbeat_reader = HeartbeatReader(
            heartbeat_dir=runtime_config.heartbeat_dir,
        )
        self._running = False

    def run(self) -> None:
        """Start the supervisor poll loop. Runs until SIGINT/SIGTERM."""
        self._running = True
        print(f"[supervisor] Poll loop started — {self._rt.supervisor_poll_seconds}s interval")
        print(f"[supervisor] Supervising: {SUPERVISED_BOTS}")

        while self._running:
            try:
                self._poll()
            except Exception as e:
                print(f"[supervisor] Poll error: {e}")
            finally:
                time.sleep(self._rt.supervisor_poll_seconds)

        self._shutdown()

    def _poll(self) -> None:
        """One supervisor poll cycle."""
        alerts = self._check_heartbeats()
        alerts.extend(self._check_state_snapshots())

        for alert in alerts:
            self._emit_alert(alert)

        if not alerts:
            self._log_summary()

    # ---- Heartbeat checks ----

    def _check_heartbeats(self) -> list[BotAlert]:
        """Check all bot heartbeats for staleness or stopped status."""
        alerts = []
        for bot_id in SUPERVISED_BOTS:
            hb = self._heartbeat_reader.read(bot_id)

            if hb is None:
                continue

            if hb.status == "stopped":
                alerts.append(BotAlert(
                    bot_id=bot_id,
                    level="STALE",
                    message="Bot has stopped",
                    timestamp=datetime.now(timezone.utc),
                ))
                continue

            if hb.is_stale():
                alerts.append(BotAlert(
                    bot_id=bot_id,
                    level="STALE",
                    message=f"Heartbeat stale: last write {hb.timestamp.isoformat()} "
                            f"(max latency: {SUPERVISOR_MAX_LATENCY_SECONDS}s)",
                    timestamp=datetime.now(timezone.utc),
                ))

        return alerts

    # ---- State snapshot checks ----

    def _check_state_snapshots(self) -> list[BotAlert]:
        """Read and analyse state snapshots for all supervised bots."""
        alerts = []
        for bot_id in SUPERVISED_BOTS:
            snapshot_path = self._state_snapshot_path(bot_id)
            if not snapshot_path.exists():
                continue

            try:
                data = json.loads(snapshot_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                alerts.append(BotAlert(
                    bot_id=bot_id,
                    level="ANOMALY",
                    message=f"Could not read state snapshot: {e}",
                    timestamp=datetime.now(timezone.utc),
                ))
                continue

            alerts.extend(self._analyse_snapshot(bot_id, data))

        return alerts

    def _analyse_snapshot(self, bot_id: str, data: dict[str, Any]) -> list[BotAlert]:
        """Check one bot's state snapshot for alert-worthy conditions."""
        alerts = []

        if data.get("is_frozen", False):
            alerts.append(BotAlert(
                bot_id=bot_id,
                level="FROZEN",
                message="Bot is frozen — trading paused",
                timestamp=datetime.now(timezone.utc),
            ))

        daily_loss_pct = abs(data.get("daily_loss_pct", 0.0))
        daily_warning = MAX_DAILY_LOSS_PCT * DRAWDOWN_WARNING_PCT
        if daily_loss_pct >= daily_warning:
            alerts.append(BotAlert(
                bot_id=bot_id,
                level="DRAWDAY",
                message=f"Daily loss {daily_loss_pct:.2%} ≥ {daily_warning:.2%} warning threshold",
                timestamp=datetime.now(timezone.utc),
            ))

        weekly_loss_pct = abs(data.get("weekly_loss_pct", 0.0))
        weekly_warning = MAX_WEEKLY_LOSS_PCT * DRAWDOWN_WARNING_PCT
        if weekly_loss_pct >= weekly_warning:
            alerts.append(BotAlert(
                bot_id=bot_id,
                level="DRAWWEK",
                message=f"Weekly loss {weekly_loss_pct:.2%} ≥ {weekly_warning:.2%} warning threshold",
                timestamp=datetime.now(timezone.utc),
            ))

        consecutive_losses = data.get("consecutive_losses", 0)
        if consecutive_losses >= 3:
            alerts.append(BotAlert(
                bot_id=bot_id,
                level="ANOMALY",
                message=f"Consecutive losses: {consecutive_losses} (review strategy)",
                timestamp=datetime.now(timezone.utc),
            ))

        return alerts

    def _state_snapshot_path(self, bot_id: str) -> Path:
        """Path to a bot's state snapshot file."""
        return self._rt.state_dir / bot_id / f"{bot_id}_snapshot.json"

    # ---- Output ----

    def _emit_alert(self, alert: BotAlert) -> None:
        """Emit one alert. Phase 4: log to stdout."""
        ts = alert.timestamp.isoformat()
        print(f"[supervisor ALERT] [{alert.level}] [{alert.bot_id}] {alert.message} at {ts}")

    def _log_summary(self) -> None:
        """Log a brief summary of bot states — informational, not an alert."""
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        summaries: list[str] = []

        for bot_id in SUPERVISED_BOTS:
            hb = self._heartbeat_reader.read(bot_id)
            if hb is None:
                summaries.append(f"{bot_id}=no_hb")
            elif hb.is_stale():
                summaries.append(f"{bot_id}=stale")
            elif hb.status == "stopped":
                summaries.append(f"{bot_id}=stopped")
            elif hb.is_frozen:
                summaries.append(f"{bot_id}=frozen")
            else:
                summaries.append(f"{bot_id}=ok")

        print(f"[supervisor {now}] {' | '.join(summaries)}")

    # ---- Shutdown ----

    def shutdown(self) -> None:
        """Initiate graceful shutdown."""
        print("[supervisor] Shutdown requested")
        self._running = False

    def _shutdown(self) -> None:
        print("[supervisor] Shutdown complete.")
        sys.exit(0)
