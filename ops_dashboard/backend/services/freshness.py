"""
ops_dashboard/backend/services/freshness.py

Market-clock awareness: the authority on whether silence is NORMAL right now.
Drives (a) GRAY-vs-RED on pipeline cards, (b) the htmx poll cadence
(5s active / 60s off), and (c) the summary window badge.

IST is a fixed +05:30 offset (India has no DST), so we derive it from UTC by
value — no zoneinfo, no production import (works on the MSYS/Windows dev box
where TZ='Asia/Kolkata' would wrongly return UTC).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

_IST = timezone(timedelta(hours=5, minutes=30))

# Pipeline stages whose activity is only expected inside the ENTRY window.
_ENTRY_STAGES = {"orders_created", "orders_placed", "orders_filled"}
# "processing" freshness window (seconds) — a stage active within this is YELLOW.
PROCESSING_WINDOW_SEC = 90


def ist_now() -> datetime:
    """Timezone-aware current time in IST (fixed +05:30)."""
    return datetime.now(timezone.utc).astimezone(_IST)


def ist_today_iso(now: Optional[datetime] = None) -> str:
    return (now or ist_now()).strftime("%Y-%m-%d")


def _parse_hhmm(s: str) -> tuple:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _minutes(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _cfg_minutes(cfg: dict, key: str, default: str) -> int:
    mc = cfg.get("market_clock", {})
    hh, mm = _parse_hhmm(mc.get(key, default))
    return hh * 60 + mm


def is_weekday(cfg: dict, now: Optional[datetime] = None) -> bool:
    now = now or ist_now()
    active = cfg.get("market_clock", {}).get("active_weekdays", [0, 1, 2, 3, 4])
    return now.weekday() in active


def market_is_open(cfg: dict, now: Optional[datetime] = None) -> bool:
    now = now or ist_now()
    if not is_weekday(cfg, now):
        return False
    t = _minutes(now)
    return _cfg_minutes(cfg, "market_open", "09:15") <= t <= _cfg_minutes(cfg, "market_close", "15:30")


def in_entry_window(cfg: dict, now: Optional[datetime] = None) -> bool:
    now = now or ist_now()
    if not is_weekday(cfg, now):
        return False
    t = _minutes(now)
    return _cfg_minutes(cfg, "entry_start", "10:00") <= t <= _cfg_minutes(cfg, "eod_squareoff", "15:17")


def phase(cfg: dict, now: Optional[datetime] = None) -> str:
    """Human-readable market phase for the summary badge."""
    now = now or ist_now()
    if not is_weekday(cfg, now):
        return "WEEKEND"
    t = _minutes(now)
    mo = _cfg_minutes(cfg, "market_open", "09:15")
    es = _cfg_minutes(cfg, "entry_start", "10:00")
    ee = _cfg_minutes(cfg, "entry_end", "15:00")
    sq = _cfg_minutes(cfg, "eod_squareoff", "15:17")
    mc = _cfg_minutes(cfg, "market_close", "15:30")
    if t < mo:
        return "PRE_OPEN"
    if t < es:
        return "OPEN_PRE_ENTRY"
    if t < ee:
        return "ENTRY_WINDOW"
    if t < sq:
        return "POST_ENTRY"
    if t < mc:
        return "SQUAREOFF"
    return "POST_CLOSE"


def is_active_now(cfg: dict, now: Optional[datetime] = None) -> bool:
    """Whether trading activity is expected now (drives 5s vs 60s poll)."""
    return market_is_open(cfg, now)


def poll_interval_ms(cfg: dict, now: Optional[datetime] = None) -> int:
    poll = cfg.get("poll", {})
    return int(poll.get("market_ms", 5000)) if is_active_now(cfg, now) else int(poll.get("off_ms", 60000))


def expected_activity(cfg: dict, stage_key: str, now: Optional[datetime] = None) -> bool:
    """Is activity expected for this pipeline stage right now?

    Entry stages: only inside the entry window. All other stages: any time the
    market is open (signals arrive / exits fire across the whole session).
    """
    now = now or ist_now()
    if stage_key in _ENTRY_STAGES:
        return in_entry_window(cfg, now)
    return market_is_open(cfg, now)


def parse_ist(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-ish IST timestamp to an aware datetime; None on failure."""
    if not ts:
        return None
    s = ts.strip().replace(" ", "T", 1) if " " in ts and "T" not in ts else ts
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Fall back to a date-only or minute-precision string.
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_IST)
    return dt


def age_seconds(ts: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """Seconds between an event timestamp and now (IST). None if unparseable."""
    dt = parse_ist(ts)
    if dt is None:
        return None
    now = now or ist_now()
    return max(0.0, (now - dt).total_seconds())


def resolve_period(period: Optional[str], from_date: Optional[str] = None,
                   to_date: Optional[str] = None, now: Optional[datetime] = None) -> tuple:
    """G5c multi-period resolver → (from_date, to_date) as YYYY-MM-DD in IST.

    today  → (today, today)
    week   → trailing 7 days incl. today  (today-6 .. today)
    month  → trailing 30 days incl. today (today-29 .. today)
    custom → (from_date, to_date) if both valid YYYY-MM-DD, else today

    Trailing windows (not calendar week/month) are used so the boundary is
    deterministic and IST-anchored (reuses ist_now; no zoneinfo). Reported for
    Rama/Web-Claude review. Read-only; no mode branch (parity).
    """
    now = now or ist_now()
    today = now.strftime("%Y-%m-%d")

    def _valid(d):
        return bool(d) and len(d) == 10 and d[4] == "-" and d[7] == "-"

    p = (period or "today").lower()
    if p == "custom" and _valid(from_date) and _valid(to_date):
        return (from_date, to_date) if from_date <= to_date else (to_date, from_date)
    if p == "week":
        return (now - timedelta(days=6)).strftime("%Y-%m-%d"), today
    if p == "month":
        return (now - timedelta(days=29)).strftime("%Y-%m-%d"), today
    return today, today


def freshness_state(cfg: dict, now: Optional[datetime] = None) -> dict:
    """Bundle for the frontend: phase, active flag, poll interval."""
    now = now or ist_now()
    return {
        "phase": phase(cfg, now),
        "market_open": market_is_open(cfg, now),
        "entry_window": in_entry_window(cfg, now),
        "active": is_active_now(cfg, now),
        "poll_ms": poll_interval_ms(cfg, now),
        "ist_now": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
