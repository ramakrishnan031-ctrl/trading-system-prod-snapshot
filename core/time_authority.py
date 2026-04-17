"""
core/time_authority.py — Trading System v2

Purpose:
    Single source of time for the entire system. Every module that needs
    "now" calls time_authority.now_ist(). Broker clock skew is detected
    by higher layers calling record_broker_skew() with broker timestamps.

Manual Input Required:
    No.

How It Works:
    1. now_ist() returns the VM's current time in IST — the runtime clock.
    2. Higher layers (broker/zerodha_adapter.py) call record_broker_skew()
       after every broker API call, passing the broker's response timestamp.
    3. Skew readings accumulate in a rolling deque(maxlen=10).
    4. Tier checks use the AVERAGE of the deque (all tiers, including halt).
    5. Halt-tier check is SKIPPED if fewer than 3 samples exist.
    6. assert_clock_at_startup() is a single-point check — raises
       ClockSkewTooLarge if skew > 30s. Called by main.py before trading.
    7. An optional on_critical_skew callback is injected at init for
       runtime halt triggers. time_authority never imports kill_switch.

Inputs:
    - Broker timestamps via record_broker_skew() (called by Layer 3)
    - Callback via set_critical_skew_callback() (wired by main.py)
    - Thresholds via configure() or defaults

Outputs:
    - IST datetime via now_ist()
    - Raises ClockSkewTooLarge on startup check failure
    - Fires on_critical_skew callback on runtime halt threshold

Design Refs:
    - G4 (hybrid clock, 4-tier skew thresholds, startup NTP check)
    - Layer 1: imports ONLY stdlib. No broker, no state_store, no events.

What This Module Does NOT Do:
    - Does not fetch broker timestamps (caller's job)
    - Does not halt the system (fires callback; caller decides)
    - Does not import from any layer above core/
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone, timedelta, tzinfo
from typing import Callable, Deque, Optional

from core.exceptions import ClockSkewTooLarge  # noqa: F401 — re-exported for callers


# ─────────────────────────────────────────────────────────────────────────────
# IST timezone (UTC+05:30) — no dependency on zoneinfo for portability
# ─────────────────────────────────────────────────────────────────────────────

_IST_OFFSET = timedelta(hours=5, minutes=30)
_IST = timezone(_IST_OFFSET, name="IST")


# ─────────────────────────────────────────────────────────────────────────────
# Default thresholds (overridable via configure())
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_THRESHOLDS = {
    "warn_sec": 2.0,         # log warning
    "alert_sec": 5.0,        # Telegram alert, continue trading
    "halt_sec": 30.0,        # fire on_critical_skew callback
    "startup_max_sec": 30.0, # refuse to start
    "min_samples_for_halt": 3,
    "window_size": 10,
}


# ─────────────────────────────────────────────────────────────────────────────
# Module state
# ─────────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_skew_window: Deque[float] = deque(maxlen=_DEFAULT_THRESHOLDS["window_size"])
_thresholds: dict = dict(_DEFAULT_THRESHOLDS)

# Callbacks — injected by main.py at startup, never imported from above
_on_critical_skew: Optional[Callable[[float, str], None]] = None
_on_alert_skew: Optional[Callable[[float, str], None]] = None
_on_warn_skew: Optional[Callable[[float, str], None]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Time
# ─────────────────────────────────────────────────────────────────────────────

def now_ist() -> datetime:
    """
    Return the current time in IST. This is the ONLY function any module
    should call to get the current time. No datetime.now() elsewhere.
    """
    return datetime.now(tz=_IST)


def ist_timezone() -> tzinfo:
    """Return the IST timezone object for callers that need it."""
    return _IST


def today_ist() -> str:
    """Return today's date as YYYY-MM-DD string in IST."""
    return now_ist().strftime("%Y-%m-%d")


def now_ist_iso() -> str:
    """Return current IST time as ISO-8601 string."""
    return now_ist().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Configuration
# ─────────────────────────────────────────────────────────────────────────────

def configure(
    thresholds: Optional[dict] = None,
    on_critical_skew: Optional[Callable[[float, str], None]] = None,
    on_alert_skew: Optional[Callable[[float, str], None]] = None,
    on_warn_skew: Optional[Callable[[float, str], None]] = None,
) -> None:
    """
    Configure thresholds and callbacks. Called once at startup by main.py.

    Args:
        thresholds: dict with keys warn_sec, alert_sec, halt_sec,
                    startup_max_sec, min_samples_for_halt, window_size.
                    Missing keys use defaults.
        on_critical_skew: callback(skew_sec, reason) fired when avg skew > halt_sec
        on_alert_skew:    callback(skew_sec, reason) fired when avg skew > alert_sec
        on_warn_skew:     callback(skew_sec, reason) fired when avg skew > warn_sec
    """
    global _on_critical_skew, _on_alert_skew, _on_warn_skew, _skew_window

    with _lock:
        if thresholds:
            _thresholds.update(thresholds)
            # Rebuild deque if window_size changed
            new_size = _thresholds.get("window_size", 10)
            if new_size != _skew_window.maxlen:
                _skew_window = deque(maxlen=new_size)

        _on_critical_skew = on_critical_skew
        _on_alert_skew = on_alert_skew
        _on_warn_skew = on_warn_skew


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Skew Detection (called by broker adapter, Layer 3)
# ─────────────────────────────────────────────────────────────────────────────

def record_broker_skew(broker_timestamp: datetime) -> SkewResult:
    """
    Record a skew observation from a broker API response timestamp.
    Called by broker/zerodha_adapter.py after every broker call.

    Args:
        broker_timestamp: datetime (timezone-aware) from the broker response.
                          If naive, assumed to be IST.

    Returns:
        SkewResult with current skew, average, tier, and whether callback fired.
    """
    # Make broker_timestamp timezone-aware if naive
    if broker_timestamp.tzinfo is None:
        broker_timestamp = broker_timestamp.replace(tzinfo=_IST)

    local_now = now_ist()
    skew_sec = (broker_timestamp - local_now).total_seconds()

    # Callbacks to fire AFTER releasing the lock
    fire_critical = False
    fire_alert = False
    fire_warn = False
    avg = 0.0
    tier = "NORMAL"

    with _lock:
        _skew_window.append(skew_sec)
        sample_count = len(_skew_window)

        if sample_count == 0:
            avg = skew_sec
        else:
            avg = sum(abs(s) for s in _skew_window) / sample_count

        min_for_halt = _thresholds["min_samples_for_halt"]
        halt_sec = _thresholds["halt_sec"]
        alert_sec = _thresholds["alert_sec"]
        warn_sec = _thresholds["warn_sec"]

        if avg >= halt_sec and sample_count >= min_for_halt:
            tier = "HALT"
            fire_critical = True
        elif avg >= alert_sec:
            tier = "ALERT"
            fire_alert = True
        elif avg >= warn_sec:
            tier = "WARN"
            fire_warn = True

    # Fire callbacks outside the lock
    reason = (
        f"Clock skew avg={avg:.1f}s "
        f"(last={abs(skew_sec):.1f}s, samples={sample_count})"
    )

    if fire_critical and _on_critical_skew:
        _on_critical_skew(avg, reason)
    elif fire_alert and _on_alert_skew:
        _on_alert_skew(avg, reason)
    elif fire_warn and _on_warn_skew:
        _on_warn_skew(avg, reason)

    return SkewResult(
        skew_sec=round(skew_sec, 3),
        avg_abs_skew_sec=round(avg, 3),
        sample_count=sample_count,
        tier=tier,
        callback_fired=fire_critical or fire_alert or fire_warn,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Startup Check (called by main.py, Phase 0d)
# ─────────────────────────────────────────────────────────────────────────────

def assert_clock_at_startup(broker_timestamp: datetime) -> float:
    """
    Single-point clock check at startup. NOT windowed — uses a single reading.

    Args:
        broker_timestamp: timezone-aware datetime from broker's first API call.

    Returns:
        The skew in seconds (for logging).

    Raises:
        ClockSkewTooLarge: if abs(skew) > startup_max_sec threshold.
    """
    if broker_timestamp.tzinfo is None:
        broker_timestamp = broker_timestamp.replace(tzinfo=_IST)

    local_now = now_ist()
    skew_sec = (broker_timestamp - local_now).total_seconds()
    abs_skew = abs(skew_sec)

    max_sec = _thresholds["startup_max_sec"]

    if abs_skew > max_sec:
        raise ClockSkewTooLarge(
            f"VM clock off by {skew_sec:+.1f}s vs broker "
            f"(tolerance: ±{max_sec:.0f}s). "
            f"Local: {local_now.isoformat()} "
            f"Broker: {broker_timestamp.isoformat()}",
            skew_seconds=skew_sec,
            threshold_sec=max_sec,
            local_time=local_now.isoformat(),
            broker_time=broker_timestamp.isoformat(),
        )

    # Seed the runtime skew window with the startup reading
    with _lock:
        _skew_window.append(skew_sec)

    return skew_sec


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Introspection (for logging, debugging, daily report)
# ─────────────────────────────────────────────────────────────────────────────

def get_skew_status() -> dict:
    """
    Return current skew state for logging or display.

    Returns dict with keys:
        last_skew_sec, avg_abs_skew_sec, sample_count, current_tier,
        thresholds (copy).
    """
    with _lock:
        if _skew_window:
            last = _skew_window[-1]
            avg = sum(abs(s) for s in _skew_window) / len(_skew_window)
            count = len(_skew_window)
        else:
            last = 0.0
            avg = 0.0
            count = 0

        min_for_halt = _thresholds["min_samples_for_halt"]
        halt_sec = _thresholds["halt_sec"]
        alert_sec = _thresholds["alert_sec"]
        warn_sec = _thresholds["warn_sec"]

    if avg >= halt_sec and count >= min_for_halt:
        tier = "HALT"
    elif avg >= alert_sec:
        tier = "ALERT"
    elif avg >= warn_sec:
        tier = "WARN"
    else:
        tier = "NORMAL"

    return {
        "last_skew_sec": round(last, 3),
        "avg_abs_skew_sec": round(avg, 3),
        "sample_count": count,
        "current_tier": tier,
        "thresholds": dict(_thresholds),
    }


def reset() -> None:
    """
    Reset skew window and callbacks. Used by tests and cold start.
    NOT called during normal operation.
    """
    global _on_critical_skew, _on_alert_skew, _on_warn_skew, _skew_window

    with _lock:
        _skew_window = deque(maxlen=_thresholds.get("window_size", 10))
        _on_critical_skew = None
        _on_alert_skew = None
        _on_warn_skew = None
        _thresholds.update(_DEFAULT_THRESHOLDS)


# ─────────────────────────────────────────────────────────────────────────────
# Data class for record_broker_skew return value
# ─────────────────────────────────────────────────────────────────────────────

class SkewResult:
    """Result of a single skew observation."""

    __slots__ = (
        "skew_sec", "avg_abs_skew_sec", "sample_count", "tier", "callback_fired",
    )

    def __init__(
        self,
        skew_sec: float,
        avg_abs_skew_sec: float,
        sample_count: int,
        tier: str,
        callback_fired: bool,
    ) -> None:
        self.skew_sec = skew_sec
        self.avg_abs_skew_sec = avg_abs_skew_sec
        self.sample_count = sample_count
        self.tier = tier
        self.callback_fired = callback_fired

    def __repr__(self) -> str:
        return (
            f"SkewResult(skew={self.skew_sec:+.3f}s, "
            f"avg={self.avg_abs_skew_sec:.3f}s, "
            f"samples={self.sample_count}, "
            f"tier={self.tier}, fired={self.callback_fired})"
        )
