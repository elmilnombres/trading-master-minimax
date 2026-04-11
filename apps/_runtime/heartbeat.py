"""
Heartbeat — file-based aliveness signal between bots and supervisor.

Owned by apps/_runtime/.
Bots write a heartbeat file every tick.
Supervisor reads all heartbeat files every poll cycle.

File format: {heartbeat_dir}/{bot_id}_heartbeat.json

Falls back to project-relative paths in development when /app/ is unavailable
(via BotConfig/RuntimeConfig path resolution).

Rationale: heartbeat files survive process crashes and allow the supervisor
to detect a frozen or dead bot even if the supervisor's own process restarts.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Frozen constant per CLAUDE.md
SUPERVISOR_MAX_LATENCY_SECONDS = 15


# ---- Bot-side writer ----

class HeartbeatWriter:
    """
    Writes a heartbeat file every tick.

    Written by: each bot (alpha, beta, gamma)
    Read by: supervisor

    File is atomically replaced (write-then-rename) to avoid partial reads.
    """

    def __init__(self, bot_id: str, heartbeat_file: Path):
        self._bot_id = bot_id
        self._file = heartbeat_file
        self._file.parent.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        status: str,            # "running" | "frozen" | "stopped"
        is_frozen: bool = False,
        error: str | None = None,
    ) -> None:
        """Write current heartbeat state to disk."""
        payload = {
            "bot_id": self._bot_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "is_frozen": is_frozen,
            "last_error": error,
        }
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, default=str))
        tmp.replace(self._file)


# ---- Supervisor-side reader ----

@dataclass
class BotHeartbeat:
    """Parsed heartbeat file for one bot."""
    bot_id: str
    timestamp: datetime
    status: str           # "running" | "frozen" | "stopped"
    is_frozen: bool
    last_error: str | None = None

    def is_stale(self, max_age_seconds: int = SUPERVISOR_MAX_LATENCY_SECONDS) -> bool:
        """
        True if the heartbeat is older than max_age_seconds.

        A stale heartbeat means the bot has not written in time,
        indicating a frozen process or crash.
        """
        age = (datetime.now(timezone.utc) - self.timestamp).total_seconds()
        return age > max_age_seconds


class HeartbeatReader:
    """
    Reads heartbeat files written by all bots.
    Used by the supervisor to detect stale, frozen, or stopped bots.
    """

    def __init__(self, heartbeat_dir: Path):
        self._dir = heartbeat_dir

    def read(self, bot_id: str) -> BotHeartbeat | None:
        """
        Read the heartbeat file for one bot.

        Returns None if the file does not exist
        (bot has never started, or heartbeat dir is missing).
        """
        path = self._dir / f"{bot_id}_heartbeat.json"
        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text())
            return BotHeartbeat(
                bot_id=raw["bot_id"],
                timestamp=datetime.fromisoformat(raw["timestamp"]),
                status=raw["status"],
                is_frozen=raw.get("is_frozen", False),
                last_error=raw.get("last_error"),
            )
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            return None

    def read_all(self, bot_ids: list[str]) -> dict[str, BotHeartbeat | None]:
        """Read heartbeat files for all known bots."""
        return {bid: self.read(bid) for bid in bot_ids}

    def detect_stale(self, bot_ids: list[str]) -> list[str]:
        """Return list of bot_ids with stale heartbeats."""
        stale = []
        for bid in bot_ids:
            hb = self.read(bid)
            if hb is None or hb.is_stale():
                stale.append(bid)
        return stale
