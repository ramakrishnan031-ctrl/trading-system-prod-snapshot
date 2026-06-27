"""
sr_detector/zone_cache.py — Trading System v2 · S&R V2 Phase A (SNR-V2)

Purpose:
    In-memory, thread-safe, per-symbol store of the latest confluence-scored
    zones, split into resistance[] and support[] (BOTH cached — Phase A reads
    resistance for the buying-into-resistance divert; Phase B will read support).
    A pre-placement check reads this synchronously (NO fetch on the hot path) —
    a miss / expired entry returns None and the caller falls through to normal
    placement. Pure: stdlib + sr_detector.models only.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from sr_detector.models import ScoredZone


@dataclass(frozen=True)
class ZoneSet:
    resistance: Tuple[ScoredZone, ...]
    support: Tuple[ScoredZone, ...]
    warmed_at: datetime


class ZoneCache:
    def __init__(self, *, ttl_sec: float, now_fn: Callable[[], datetime]) -> None:
        self._ttl_sec = float(ttl_sec)
        self._now_fn = now_fn
        self._lock = threading.RLock()
        self._store: Dict[str, ZoneSet] = {}

    def put(self, symbol: str, resistance: List[ScoredZone], support: List[ScoredZone]) -> None:
        zs = ZoneSet(tuple(resistance), tuple(support), self._now_fn())
        with self._lock:
            self._store[symbol] = zs

    def get(self, symbol: str) -> Optional[ZoneSet]:
        """Return the cached ZoneSet, or None on miss / expiry (treated as miss)."""
        with self._lock:
            zs = self._store.get(symbol)
        if zs is None:
            return None
        if self._age_sec(zs) > self._ttl_sec:
            return None
        return zs

    def symbols_due_for_rewarm(self, margin_sec: float) -> List[str]:
        """Cached symbols whose age exceeds (ttl − margin) — the warmer refreshes
        them before they expire so a hot-path get() keeps hitting."""
        cutoff = self._ttl_sec - float(margin_sec)
        with self._lock:
            return [s for s, zs in self._store.items() if self._age_sec(zs) >= cutoff]

    def evict_expired(self) -> int:
        with self._lock:
            stale = [s for s, zs in self._store.items() if self._age_sec(zs) > self._ttl_sec]
            for s in stale:
                self._store.pop(s, None)
            return len(stale)

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def clear(self) -> int:
        with self._lock:
            n = len(self._store)
            self._store.clear()
            return n

    def _age_sec(self, zs: ZoneSet) -> float:
        return (self._now_fn() - zs.warmed_at).total_seconds()
