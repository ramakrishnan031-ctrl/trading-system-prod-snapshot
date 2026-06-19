"""
signals/entry_throttle.py — Bug G (full integration): entry-rate limiter.

Prevents entry bursts (the 19-Jun 10:00:24-28 incident: 5 entries in ~5s). Three
independent gates, evaluated global-first (global takes precedence):

  1. min_gap        — minimum seconds between any two PLACED entries.
  2. burst          — at most `burst_max` placed entries per rolling `burst_window`.
  3. per_symbol     — per-symbol cooldown: no re-entry of the SAME symbol within
                      `per_symbol_cooldown` seconds (stops rapid same-symbol churn,
                      e.g. enter X 10:00 → exit 10:02 → re-fire 10:03 → wait).

Design: `admit(symbol)` does an ATOMIC check-AND-record under one lock — it records
the placement before returning allowed=True. This is deliberately NOT a split
check()/record() pair: a split would leave a TOCTOU window where a burst of
concurrent worker threads all pass check() before any records (the exact race that
let entries pile up). Each gate also counts its rejections for /metrics.

Parity: pure in-memory rate logic, identical in paper and live.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ThrottleResult:
    allowed: bool
    reason: str = "ok"
    category: str = ""   # "" | "min_gap" | "burst" | "per_symbol"


class EntryThrottle:
    """Thread-safe entry-rate limiter. See module docstring."""

    def __init__(
        self,
        *,
        min_gap_sec: float = 0.0,
        burst_window_sec: float = 60.0,
        burst_max: int = 0,
        per_symbol_cooldown_sec: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_gap = max(0.0, float(min_gap_sec or 0.0))
        self._burst_window = max(0.0, float(burst_window_sec or 60.0))
        self._burst_max = max(0, int(burst_max or 0))
        self._per_symbol = max(0.0, float(per_symbol_cooldown_sec or 0.0))
        self._clock = clock

        self._lock = threading.Lock()
        self._last_entry: Optional[float] = None
        self._recent: deque[float] = deque()           # placement timestamps (burst window)
        self._per_symbol_last: dict[str, float] = {}    # symbol -> last placement ts
        self._counts = {"min_gap": 0, "burst": 0, "per_symbol": 0, "admitted": 0}

    @property
    def enabled(self) -> bool:
        return self._min_gap > 0 or self._burst_max > 0 or self._per_symbol > 0

    def admit(self, symbol: Optional[str] = None) -> ThrottleResult:
        """Atomic check-and-record. Returns allowed=True (and records the
        placement) iff all enabled gates pass; otherwise allowed=False with the
        blocking gate's category/reason (and increments that gate's counter)."""
        if not self.enabled:
            with self._lock:
                self._counts["admitted"] += 1
            return ThrottleResult(True, "ok", "")

        with self._lock:
            now = self._clock()

            # 1. global min gap (precedence)
            if self._min_gap > 0 and self._last_entry is not None:
                gap = now - self._last_entry
                if gap < self._min_gap:
                    self._counts["min_gap"] += 1
                    return ThrottleResult(
                        False, f"min_gap {gap:.1f}s < {self._min_gap:.0f}s", "min_gap")

            # 2. global burst (rolling window)
            if self._burst_max > 0:
                cutoff = now - self._burst_window
                while self._recent and self._recent[0] < cutoff:
                    self._recent.popleft()
                if len(self._recent) >= self._burst_max:
                    self._counts["burst"] += 1
                    return ThrottleResult(
                        False,
                        f"burst {len(self._recent)} >= {self._burst_max}/"
                        f"{self._burst_window:.0f}s", "burst")

            # 3. per-symbol cooldown
            if self._per_symbol > 0 and symbol:
                last = self._per_symbol_last.get(symbol)
                if last is not None:
                    elapsed = now - last
                    if elapsed < self._per_symbol:
                        self._counts["per_symbol"] += 1
                        return ThrottleResult(
                            False,
                            f"per_symbol {symbol} {elapsed:.0f}s < "
                            f"{self._per_symbol:.0f}s", "per_symbol")

            # admit — record the placement
            self._last_entry = now
            self._recent.append(now)
            if symbol:
                self._per_symbol_last[symbol] = now
            self._counts["admitted"] += 1
            return ThrottleResult(True, "ok", "")

    def metrics(self) -> dict:
        """Per-reason throttle counters for /metrics observability."""
        with self._lock:
            return {
                "entries_throttled_min_gap": self._counts["min_gap"],
                "entries_throttled_burst": self._counts["burst"],
                "entries_throttled_per_symbol": self._counts["per_symbol"],
                "entries_admitted": self._counts["admitted"],
            }
