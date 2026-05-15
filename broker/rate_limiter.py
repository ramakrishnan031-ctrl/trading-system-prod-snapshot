"""
broker/rate_limiter.py — Trading System v2

Purpose:
    Client-side token bucket rate limiter for Zerodha API endpoint categories.
    Every broker call must go through acquire() before hitting the network.
    Enforces burst + sustained-rate limits independently per category.

Locked Design Decisions:
    RL1  — Token bucket algorithm; one independent bucket per category.
    RL2  — Four categories: order, quote, historical, margins.
           Unknown category → ValueError immediately.
    RL3  — acquire(category, n=1) blocks until n tokens available or timeout.
           Raises BrokerRateLimitError if n > capacity or max_wait_sec exceeded.
    RL4  — max_wait_sec global (default 30s), passed at construction.
    RL5  — try_acquire(category, n=1) non-blocking; returns bool.
    RL6  — Backoff is the adapter's responsibility. penalize(category, sleep_sec)
           freezes the bucket for that duration (acquire/try_acquire both respect it).
    RL7  — One threading.Lock per bucket; no global lock.
    RL8  — time.monotonic() for all refill arithmetic (NTP-immune).
    RL9  — Raises BrokerRateLimitError(SEVERITY="WARN") on timeout.
    RL10 — Layer 2. Imports: threading, time, core.exceptions, core.config_loader,
            core.logger. Does NOT import core.time_authority (RL8).

What This Module Does NOT Do:
    - Does not make HTTP calls or catch 429 responses (adapter's job, RL6)
    - Does not implement retry logic or backoff sequences
    - Does not read config files directly — caller injects BrokerLimitsConfig
    - Does not use wall-clock time (time_authority) for refill math (RL8)
"""
from __future__ import annotations

import threading
import time

from core.exceptions import BrokerRateLimitError, RateLimitAbortedError
from core.logger import get_logger

# Import only for type annotation; caller constructs and injects the config.
from core.config_loader import BrokerLimitsConfig

_log = get_logger(__name__)

# Valid categories (RL2) — used for validation and for iterating at construction.
_VALID_CATEGORIES: frozenset[str] = frozenset({"order", "quote", "historical", "margins"})


# ─────────────────────────────────────────────────────────────────────────────
# Internal: _Bucket
# ─────────────────────────────────────────────────────────────────────────────

class _Bucket:
    """
    A single token bucket. Thread-safe via self._lock (RL7).
    Uses time.monotonic() for refill arithmetic (RL8).
    """

    def __init__(self, capacity: int, rate_per_sec: float) -> None:
        self._capacity: int     = capacity
        self._rate_per_sec: float = rate_per_sec
        self._tokens: float     = float(capacity)   # start full
        self._last_refill: float = time.monotonic()
        self._frozen_until: float = 0.0             # monotonic timestamp; 0 = not frozen
        self._was_frozen: bool  = False             # set True by freeze(); cleared on thaw
        self._lock: threading.Lock = threading.Lock()

    # ── Internal helpers (must be called with _lock held) ────────────────────

    def _refill(self) -> None:
        """Add tokens proportional to elapsed time. Caps at capacity."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(float(self._capacity), self._tokens + elapsed * self._rate_per_sec)
        self._last_refill = now

    def _seconds_until_ready(self, n: int) -> float:
        """
        Estimate seconds until n tokens will be available.
        Accounts for freeze duration. Lock must be held by caller.
        """
        now = time.monotonic()
        freeze_rem = max(0.0, self._frozen_until - now)
        self._refill()
        needed = max(0.0, n - self._tokens)
        time_to_tokens = (needed / self._rate_per_sec) if needed > 0 else 0.0
        return max(freeze_rem, time_to_tokens)

    # ── Public bucket operations ─────────────────────────────────────────────

    def try_acquire(self, n: int) -> bool:
        """Non-blocking. Return True and consume n tokens if available, else False."""
        with self._lock:
            if time.monotonic() < self._frozen_until:
                return False
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def freeze(self, sleep_sec: float) -> None:
        """Freeze bucket for sleep_sec seconds (RL6: penalize support)."""
        with self._lock:
            self._frozen_until = time.monotonic() + sleep_sec
            self._was_frozen = True

    def pop_thaw_notice(self) -> bool:
        """Return True once after a freeze expires (transitions frozen→thawed).
        Resets the flag so only the first successful acquire after a freeze logs.
        Lock must NOT be held by caller."""
        with self._lock:
            if self._was_frozen and time.monotonic() >= self._frozen_until:
                self._was_frozen = False
                return True
            return False

    def available_tokens(self) -> float:
        """Read current token count (after refill). Used for error context."""
        with self._lock:
            self._refill()
            return self._tokens


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Token bucket rate limiter for the four Zerodha API endpoint categories.

    Constructed once at startup with the loaded BrokerLimitsConfig and an
    optional max_wait_sec ceiling (default 30s, RL4).

    Usage::
        limiter = RateLimiter(cfg.broker_limits)
        limiter.acquire("order")          # blocks up to 30s
        if not limiter.try_acquire("quote"):
            raise ...                     # fail fast
        # on broker 429:
        limiter.penalize("order", backoff_sec)
    """

    def __init__(
        self,
        limits: BrokerLimitsConfig,
        *,
        max_wait_sec: float = 30.0,
        shutdown_event: threading.Event | None = None,  # FIX-060
    ) -> None:
        self._max_wait_sec = max_wait_sec
        self._shutdown_event = shutdown_event  # FIX-060
        self._buckets: dict[str, _Bucket] = {
            "order":      _Bucket(limits.order.burst,      limits.order.rate_per_sec),
            "quote":      _Bucket(limits.quote.burst,      limits.quote.rate_per_sec),
            "historical": _Bucket(limits.historical.burst, limits.historical.rate_per_sec),
            "margins":    _Bucket(limits.margins.burst,    limits.margins.rate_per_sec),
        }

    # ── Private ──────────────────────────────────────────────────────────────

    def _get_bucket(self, category: str) -> _Bucket:
        try:
            return self._buckets[category]
        except KeyError:
            raise ValueError(
                f"Unknown rate-limit category: {category!r}. "
                f"Valid categories: {sorted(self._buckets)}"
            )

    # ── Public ───────────────────────────────────────────────────────────────

    def acquire(self, category: str, n: int = 1) -> None:
        """
        Block until n tokens are available in category's bucket, then consume them.

        Raises:
            ValueError: category is not one of the four valid categories (RL2).
            BrokerRateLimitError: n > bucket capacity (impossible to satisfy), or
                                  max_wait_sec elapsed without acquiring n tokens (RL3).
        """
        bucket = self._get_bucket(category)

        # Immediate rejection: n can never be satisfied by this bucket.
        # _capacity is read-only; safe to access without lock
        if n > bucket._capacity:
            raise BrokerRateLimitError(
                f"Requested {n} token(s) exceeds bucket capacity {bucket._capacity} "
                f"for category '{category}' — cannot be satisfied",
                category=category,
                requested_tokens=n,
                available_tokens=bucket._capacity,
                waited_sec=0.0,
                max_wait_sec=self._max_wait_sec,
            )

        start = time.monotonic()
        deadline = start + self._max_wait_sec

        while True:
            if bucket.try_acquire(n):
                if bucket.pop_thaw_notice():
                    _log.info(
                        "Rate limiter: bucket '%s' thawed (freeze expired)",
                        category,
                    )
                return

            now = time.monotonic()
            if now >= deadline:
                raise BrokerRateLimitError(
                    f"Rate limit timeout after {self._max_wait_sec}s waiting for "
                    f"{n} token(s) in category '{category}'",
                    category=category,
                    requested_tokens=n,
                    available_tokens=bucket.available_tokens(),
                    waited_sec=round(now - start, 3),
                    max_wait_sec=self._max_wait_sec,
                )

            # Estimate how long to sleep before retrying.
            with bucket._lock:
                sleep_for = bucket._seconds_until_ready(n)

            # FIX-060: Cap sleep interval to 0.1s so shutdown polls every 100ms.
            # Also cap to remaining deadline; min 1ms for responsiveness.
            remaining = deadline - time.monotonic()
            sleep_for = min(sleep_for, remaining, 0.1)  # FIX-060: 0.1s poll interval
            sleep_for = max(sleep_for, 0.001)

            # FIX-060: Poll shutdown_event if available, else fall back to time.sleep
            if self._shutdown_event is not None:
                if self._shutdown_event.wait(timeout=sleep_for):
                    # Shutdown event was set
                    raise RateLimitAbortedError(
                        f"Rate limiter acquire aborted by shutdown during wait for "
                        f"{n} token(s) in category '{category}'",
                        category=category,
                        waited_sec=round(time.monotonic() - start, 3),
                    )
            else:
                time.sleep(sleep_for)

    def try_acquire(self, category: str, n: int = 1) -> bool:
        """
        Non-blocking token acquisition (RL5).

        Returns True and consumes n tokens if available immediately.
        Returns False if insufficient tokens (or bucket is frozen).
        Raises ValueError for unknown category.
        """
        return self._get_bucket(category).try_acquire(n)

    def penalize(self, category: str, sleep_sec: float) -> None:
        """
        Freeze category's bucket for sleep_sec seconds (RL6).

        During the freeze, try_acquire returns False and acquire blocks.
        Called by the broker adapter after receiving a 429 response.

        Raises ValueError for unknown category.
        """
        bucket = self._get_bucket(category)
        bucket.freeze(sleep_sec)
        _log.warning(
            "Rate limiter: bucket '%s' frozen for %.1fs (broker 429 backoff)",
            category, sleep_sec,
        )
