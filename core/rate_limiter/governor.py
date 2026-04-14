"""
Rate-limit governor for Bybit REST API.

Tracks rate-limit headers from Bybit responses and enforces cooldown/backoff
when the API signals overload (retCode 10006).

Thread-safe using threading.Lock.

Usage:
    governor = RateLimitGovernor()
    # Before every REST call:
    if governor.should_throttle():
        time.sleep(governor.get_cooldown_seconds())
    # After every REST response:
    governor.record_response(response_headers, ret_code)
    # On 10006 specifically:
    governor.on_10006()
"""

import random
import threading
import time
from typing import Optional


class RateLimitGovernor:
    """
    Centralized rate-limit tracking for Bybit REST API calls.

    Tracks:
    - Bybit X-BAPI-Limit-* headers from responses
    - 10006 error occurrences (overload)

    Enforces:
    - Pre-request throttle check (should_throttle)
    - Post-response cooldown on 10006 (on_10006)
    - Exponential backoff with ±2s jitter, cap at 60s
    - Automatic reset on successful response (retCode == 0)
    """

    COOLDOWN_BASE_SECONDS: float = 5.0
    COOLDOWN_MAX_SECONDS: float = 60.0
    JITTER_RANGE_SECONDS: float = 2.0

    # Bybit rate-limit header names
    HEADER_LIMIT = "X-BAPI-LIMIT"
    HEADER_USED = "X-BAPI-LIMIT-USED"
    HEADER_REMAINING = "X-BAPI-LIMIT-REMAINING"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self._remaining: int = 100  # optimistic default
        self._backoff_multiplier: float = 0.0
        self._last_10006_at: float = 0.0
        self._cooldown_until: float = 0.0

    def record_response(
        self,
        headers: Optional[dict[str, str]],
        ret_code: Optional[int],
    ) -> None:
        """
        Record response headers and retCode after every REST call.

        On success (ret_code == 0): reset backoff state.
        On 10006: triggers backoff escalation.
        On other errors: no backoff change (fail-fast).
        """
        with self._lock:
            if ret_code == 0:
                # Success — reset backoff
                self._backoff_multiplier = 0.0
                self._cooldown_until = 0.0
                # Still track remaining from headers
                if headers:
                    remaining = headers.get(self.HEADER_REMAINING)
                    if remaining is not None:
                        try:
                            self._remaining = int(remaining)
                        except ValueError:
                            pass
                return

            if ret_code == 10006:
                self.on_10006()

    def should_throttle(self) -> bool:
        """
        Returns True if the next REST request should wait before sending.

        Triggers if:
        - A 10006 backoff is active (cooldown_until > now)
        - Remaining quota is very low (≤ 2)
        """
        with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                return True
            if self._remaining <= 2:
                return True
            return False

    def get_cooldown_seconds(self) -> float:
        """
        Returns seconds to sleep before the next request.

        Exponential backoff: base × 2^multiplier, capped at MAX.
        ± jitter randomized each call.
        """
        with self._lock:
            base = self.COOLDOWN_BASE_SECONDS * (2 ** self._backoff_multiplier)
            jitter = random.uniform(-self.JITTER_RANGE_SECONDS, self.JITTER_RANGE_SECONDS)
            cooldown = min(base + jitter, self.COOLDOWN_MAX_SECONDS)
            return max(cooldown, 1.0)  # never sleep less than 1s

    def on_10006(self) -> None:
        """
        Called when a 10006 (too many requests) retCode is received.

        Escalates backoff multiplier by 1. Sets cooldown_until.
        Does NOT sleep — caller is responsible for calling sleep(get_cooldown_seconds()).
        """
        with self._lock:
            self._backoff_multiplier = min(self._backoff_multiplier + 1.0, 8.0)
            self._last_10006_at = time.monotonic()
            self._cooldown_until = time.monotonic() + self.get_cooldown_seconds_unlocked()

    def get_cooldown_seconds_unlocked(self) -> float:
        """Internal: get cooldown seconds without acquiring lock. Caller must hold lock."""
        base = self.COOLDOWN_BASE_SECONDS * (2 ** self._backoff_multiplier)
        jitter = random.uniform(-self.JITTER_RANGE_SECONDS, self.JITTER_RANGE_SECONDS)
        cooldown = min(base + jitter, self.COOLDOWN_MAX_SECONDS)
        return max(cooldown, 1.0)

    def reset(self) -> None:
        """Full reset — clears all backoff state. Call on startup or major state change."""
        with self._lock:
            self._reset()