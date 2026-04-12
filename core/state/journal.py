"""
State journal — append-only event log for trade history and state recovery.
Journal policy (frozen Q5): event-driven on state change + 60s periodic backup.

Journal file format: one JSON object per line (.jsonl).
Each entry is a full state snapshot — not a delta.
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.state.store import StateStore, BotState


class StateJournal:
    """
    Manages the append-only journal and periodic snapshots.
    Runs a background thread that:
    1. Takes a snapshot every 60 seconds (frozen Q5)
    2. Provides recovery-from-journal on startup

    One instance per bot.
    """

    PERIODIC_SNAPSHOT_SECONDS = 60  # frozen Q5

    def __init__(self, store: StateStore, journal_file: Path | str | None = None):
        self._store = store
        self._journal_file = Path(journal_file) if journal_file else store._journal_file
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start background periodic snapshot thread."""
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._snapshot_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background thread and take final snapshot."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        # Final snapshot
        self._store.snapshot()

    def _snapshot_loop(self) -> None:
        """Background loop: snapshot every 60 seconds."""
        while self._running and not self._stop_event.is_set():
            self._store.snapshot()
            self._stop_event.wait(timeout=self.PERIODIC_SNAPSHOT_SECONDS)

    @staticmethod
    def recover_from_journal(
        journal_file: Path,
        state_store: StateStore,
    ) -> list[dict]:
        """
        Replay journal file and recover state up to the last valid snapshot.
        Returns list of all state entries in the journal.
        """
        if not journal_file.exists():
            return []

        entries = []
        with open(journal_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue

        if entries:
            # Load last snapshot
            state_store.load_from_snapshot()

        return entries

    def replay_journal(
        self,
        on_entry: Callable[[dict], None] | None = None,
    ) -> list[dict]:
        """
        Replay the journal file and optionally call on_entry for each entry.
        Used for post-trade analysis and recovery.
        """
        return StateJournal.recover_from_journal(self._journal_file, self._store)