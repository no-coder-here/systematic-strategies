"""Proactive rate limiting, shared by both venue clients.

Fixes a self-reported QR-DATA-001 v1.0 gap: `HyperliquidClient` previously
only handled rate-limit-triggered FAILURES via retry/backoff; it never
proactively paced requests. That gap becomes material for AMENDMENT B's
Binance bulk download (thousands of monthly-file requests). `RateLimiter`
enforces a minimum wall-clock interval between successive calls and is
thread-safe (required for the Binance bulk download's concurrent fetch pool).

D§2.1: MUST NOT import `src/data/hyperliquid/**` or `src/data/binance/**`.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

__all__ = ["RateLimiter"]


class RateLimiter:
    """Enforces `min_interval_seconds` between successive `wait()` calls,
    across however many threads share this instance.

    `time_fn`/`sleep_fn` are injectable seams for deterministic, offline,
    non-sleeping unit tests (D§11.1) — production code leaves them as the
    real `time.monotonic`/`time.sleep`.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        time_fn: Optional[Callable[[], float]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ):
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be >= 0")
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call: Optional[float] = None
        self._time_fn = time_fn or time.monotonic
        self._sleep_fn = sleep_fn or time.sleep

    @property
    def min_interval_seconds(self) -> float:
        return self._min_interval

    def wait(self) -> float:
        """Blocks (if needed) until at least `min_interval_seconds` has
        elapsed since the previous call on THIS instance. Returns the number
        of seconds slept (0.0 if no wait was required).
        """
        with self._lock:
            now = self._time_fn()
            if self._last_call is None:
                slept = 0.0
            else:
                elapsed = now - self._last_call
                remaining = self._min_interval - elapsed
                slept = remaining if remaining > 0 else 0.0
                if slept > 0:
                    self._sleep_fn(slept)
                    now = self._time_fn()
            self._last_call = now
            return slept
