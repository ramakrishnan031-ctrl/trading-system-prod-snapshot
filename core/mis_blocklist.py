# core/mis_blocklist.py — Trading System v2
"""
MIS Learned Blocklist — source-free observation of the broker's own truth.

Step 0 (30-Jun-2026) established that NO clean/stable/authoritative machine-readable
MIS-eligibility source exists: Zerodha has no API for it; the list is RMS-dynamic
intraday; the documented Google Sheets are fragile and mutually inconsistent. So
rather than scrape a fragile daily list, we OBSERVE the broker's own HTTP-400 —
"MIS orders are currently blocked for <SYM>. Place a CNC order instead." — and learn:

  * On that rejection, record {SYMBOL: date-blocked} (persisted JSON; NO DB schema).
  * The secondary screener pre-drops a future MIS-intent signal for a symbol that is
    blocked WITHIN a re-test TTL (default 5 calendar days).
  * After the TTL expires the symbol is allowed through to re-test; a fresh 400
    refreshes the date (re-blocks), a successful trade self-corrects (stays unblocked).

Zero false-drop risk: only symbols the broker itself rejected are ever blocked, and
only for a bounded window. The broker-400 handler keeps its existing behaviour; this
module only ADDS a record call and a screener-side lookup.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from core.time_authority import now_ist

# The broker's literal, stable signal that intraday/MIS is disallowed for a symbol
# (Zerodha Kite InputException message). Case-insensitive substring match.
_MIS_BLOCK_PHRASE = "mis orders are currently blocked"


def is_mis_block_rejection(exc: object) -> bool:
    """True iff `exc` is the broker's MIS-blocked rejection.

    Robust to where the reason sits: the exception message (str(exc)) and any
    TradingSystemError.context reason field (rejection_reason / reason / message).
    """
    parts = []
    try:
        parts.append(str(exc))
    except Exception:
        pass
    ctx = getattr(exc, "context", None)
    if isinstance(ctx, dict):
        for k in ("rejection_reason", "reason", "message"):
            v = ctx.get(k)
            if v is not None:
                parts.append(str(v))
    return _MIS_BLOCK_PHRASE in " ".join(parts).lower()


class MisLearnedBlocklist:
    """Persisted {SYMBOL: last_blocked_date_iso} with a re-test TTL. Thread-safe.

    Restart-safe: the JSON store is loaded at construction and rewritten atomically
    on every new block. A missing/corrupt store starts empty and never raises.
    """

    def __init__(
        self,
        path,
        ttl_days: int = 5,
        logger=None,
        today_fn: Optional[Callable[[], date]] = None,
    ) -> None:
        self._path = Path(path)
        self._ttl_days = int(ttl_days)
        self._log = logger
        # Injected clock for testability; default = IST calendar date.
        self._today_fn = today_fn or (lambda: now_ist().date())
        self._lock = threading.Lock()
        self._data: dict[str, str] = {}
        self._load()

    # ── public API ────────────────────────────────────────────────────────────
    def record_block(self, symbol: str) -> None:
        """Record that `symbol` returned the MIS-block 400 today; persist atomically."""
        if not symbol:
            return
        key = symbol.strip().upper()
        today = self._today_fn().isoformat()
        with self._lock:
            self._data[key] = today
            self._persist_locked()
        if self._log is not None:
            self._log.info(
                "mis_blocklist: recorded MIS-block for %s (date=%s, ttl_days=%d)",
                key, today, self._ttl_days,
            )

    def is_blocked(self, symbol: str) -> bool:
        """True iff `symbol` is in the store AND was blocked within the re-test TTL."""
        if not symbol:
            return False
        key = symbol.strip().upper()
        with self._lock:
            last = self._data.get(key)
        if last is None:
            return False
        days = self._days_since(last)
        if days is None:
            return False  # corrupt date -> fail-open (do not block)
        return days < self._ttl_days

    def info(self, symbol: str):
        """(last_blocked_date_iso, days_since) for logging; (None, None) if absent."""
        key = (symbol or "").strip().upper()
        with self._lock:
            last = self._data.get(key)
        if last is None:
            return (None, None)
        return (last, self._days_since(last))

    # ── internals ─────────────────────────────────────────────────────────────
    def _days_since(self, iso: str) -> Optional[int]:
        try:
            d = date.fromisoformat(iso)
        except (ValueError, TypeError):
            return None
        return (self._today_fn() - d).days

    def _load(self) -> None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = {str(k).upper(): str(v) for k, v in raw.items()}
        except Exception as exc:  # corrupt/unreadable -> start empty, never crash
            self._data = {}
            if self._log is not None:
                self._log.warning(
                    "mis_blocklist: could not load %s (%s); starting empty",
                    self._path, exc,
                )

    def _persist_locked(self) -> None:
        # Atomic write (tmp + os.replace), mirrors refresh_instruments / ssh baseline.
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8"
            )
            os.replace(tmp, self._path)
        except Exception as exc:
            if self._log is not None:
                self._log.error(
                    "mis_blocklist: persist to %s failed: %s", self._path, exc
                )
