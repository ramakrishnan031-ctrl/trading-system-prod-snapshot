"""
allocation/window_buffer.py — V3 03.05 thread-safe candidate buffer.

N signal-processor workers append (fire-and-forget); the single admission worker
drains once per window. Its OWN small lock (never the portfolio_lock), so appending
adds no contention to the reservation path.
"""
from __future__ import annotations

import threading
from typing import List

from allocation.models import ScoredCandidate


class WindowBuffer:
    """A simple lock-guarded list with an atomic drain-and-clear."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: List[ScoredCandidate] = []

    def append(self, candidate: ScoredCandidate) -> None:
        with self._lock:
            self._items.append(candidate)

    def drain(self) -> List[ScoredCandidate]:
        """Atomically return everything buffered so far and reset to empty."""
        with self._lock:
            drained = self._items
            self._items = []
        return drained

    def size(self) -> int:
        with self._lock:
            return len(self._items)
