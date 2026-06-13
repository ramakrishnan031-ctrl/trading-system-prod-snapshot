"""
screening/entry_gate.py -- Trading System v2

Purpose:
    Hold screened signals in a price-watchlist. Poll LTP at fixed interval.
    When price retraces into entry_price +/- tolerance zone: release to
    downstream on_release callback. Also releases on hard timeout (EG6)
    or 3 consecutive quote failures (EG9).

Locked Design Decisions:
    EG1  -- Price-based pullback gate (P11a). Replaces audit-#8 time-tier.
    EG2  -- Constructor: quote_fn, logger, state_store, on_release, intervals.
    EG3  -- WatchEntry frozen dataclass: all signal context for downstream.
    EG4  -- Public API: add(), remove(), watchlist(), size(), start(), stop().
    EG5  -- Price trigger: lower <= ltp <= upper (symmetric tolerance).
    EG6  -- Timeout: hard, per entry.timeout_sec. Default 180s.
    EG7  -- Release protocol: remove -> state_store -> log -> on_release.
    EG8  -- Poll loop: main thread snapshots, workers check entries concurrently.
    EG9  -- Quote failure: skip 1-2; release QUOTE_UNAVAILABLE on 3rd consecutive.
    EG10 -- Thread safety: _watchlist guarded by Lock, snapshot outside lock.
    EG11 -- Idempotency: add() raises on dup; remove() and _release() are no-ops.
    EG12 -- Logging: INFO on add/remove/release; DEBUG on poll cycle; ERROR on exc.
    EG13 -- Layer 5 (screening/). No broker import. quote_fn injected.
    EG14 -- Integration with signal_processor deferred to Module 33.
    EG15 -- NOT in scope: wiring, non-price strategies, priority ordering.

What This Module Does NOT Do:
    - Does not implement sizing, risk, or order placement
    - Does not implement candle-based triggers (LTP only per P11a)
    - Does not wire into signal_processor (Module 33's job)
"""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.time_authority import ist_timezone, now_ist

_MAX_QUOTE_FAILURES = 3


# ---------------------------------------------------------------------------
# WatchEntry (EG3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WatchEntry:
    """
    Immutable snapshot of a signal waiting at the entry gate (EG3).

    added_at must be naive IST (consistent with signal_processor convention).
    extras carries any signal_processor context needed by the on_release
    callback to resume the post-gate pipeline.
    """
    signal_id: str
    symbol: str
    direction: str          # "LONG" | "SHORT"
    trigger_price: float    # scanner-fired price
    entry_price: float      # derived entry from _derive_prices
    sl_price: float
    tgt_price: float
    tolerance_pct: float    # strategy.pullback_wait_tolerance_pct
    timeout_sec: int        # strategy.pullback_wait_timeout_sec
    strategy_name: str
    tier: str               # from screener result
    scanner_name: str
    intent: str
    added_at: datetime      # naive IST -- set when signal enters gate
    extras: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# EntryGate (EG1-EG15)
# ---------------------------------------------------------------------------

class EntryGate:
    """
    Price-based pullback gate (P11a, EG1-EG15).

    Holds WatchEntry objects and polls LTP every poll_interval_sec.
    Releases entries when:
      - ltp enters entry_price +/- tolerance (PRICE_HIT)
      - elapsed >= timeout_sec (TIMEOUT)
      - 3 consecutive quote failures (QUOTE_UNAVAILABLE)

    Usage::
        gate = EntryGate(
            quote_fn=quote_fn,
            logger=log,
            state_store=store,
            on_release=signal_processor.on_gate_release,
            poll_interval_sec=5.0,
            worker_count=2,
        )
        gate.start()
        gate.add(WatchEntry(...))
        # ...on_release callback fires when entry is released...
        gate.stop()
    """

    def __init__(
        self,
        quote_fn: Callable,
        logger,
        state_store,
        on_release: Callable,           # (WatchEntry, str) -> None
        poll_interval_sec: float = 5.0,
        worker_count: int = 2,
    ) -> None:
        if poll_interval_sec <= 0:
            raise ValueError("poll_interval_sec must be > 0")
        self._quote_fn = quote_fn
        self._log = logger
        self._state_store = state_store
        self._on_release = on_release
        self._poll_interval_sec = poll_interval_sec
        self._worker_count = max(1, worker_count)

        # Watchlist guarded by lock (EG10)
        self._watchlist: Dict[str, WatchEntry] = {}
        self._lock = threading.Lock()
        self._quote_failures: Dict[str, int] = {}  # signal_id -> consecutive fail count

        # Lifecycle
        self._running = False
        self._stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch poll thread and worker pool.

        Audit 4.4: rehydrates the watchlist from gate_state BEFORE the poll
        loop spins up. A mid-session restart therefore resumes watching the
        same signals at the same prices/timeouts they had before the crash.
        """
        if self._running:
            self._log.warning("EntryGate.start() called while already running")
            return

        # Audit 4.4: rehydrate before starting the poll loop.
        rehydrated = self.rehydrate_from_gate_state()
        if rehydrated > 0:
            self._log.info(
                f"EntryGate.rehydrate: {rehydrated} entries restored from gate_state"
            )

        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="eg-worker",
        )
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="eg-poll",
            daemon=True,
        )
        self._running = True
        self._poll_thread.start()
        self._log.info(
            f"EntryGate started: poll_interval={self._poll_interval_sec}s "
            f"workers={self._worker_count}"
        )

    def rehydrate_from_gate_state(self) -> int:
        """
        Audit 4.4: read every persisted gate_state row, reconstruct WatchEntry,
        and add to in-memory _watchlist. Returns the number of entries
        restored. Mirrors FundManager.rehydrate_from_open_trades.

        Skips rows whose signal_id is already in _watchlist (idempotent).
        Skips rows that fail to parse (logged WARNING).
        """
        import json
        try:
            rows = self._state_store.get_all_gate_state()
        except Exception as exc:
            self._log.error(f"EntryGate.rehydrate: get_all_gate_state failed: {exc}")
            return 0

        restored = 0
        for row in rows:
            signal_id = row.get("signal_id")
            try:
                added_at_str = row["added_at"]
                # added_at is stored naive IST per signal_processor convention;
                # accept either naive or aware ISO strings.
                added_at = datetime.fromisoformat(added_at_str)
                if added_at.tzinfo is not None:
                    added_at = added_at.astimezone(ist_timezone()).replace(tzinfo=None)

                extras_json = row.get("extras_json")
                extras = json.loads(extras_json) if extras_json else {}

                entry = WatchEntry(
                    signal_id=signal_id,
                    symbol=row["symbol"],
                    direction=row["direction"],
                    trigger_price=float(row["trigger_price"]),
                    entry_price=float(row["entry_price"]),
                    sl_price=float(row["sl_price"]),
                    tgt_price=float(row["tgt_price"]),
                    tolerance_pct=float(row["tolerance_pct"]),
                    timeout_sec=int(row["timeout_sec"]),
                    strategy_name=row["strategy_name"],
                    tier=row["tier"],
                    scanner_name=row["scanner_name"],
                    intent=row["intent"],
                    added_at=added_at,
                    extras=extras,
                )
            except Exception as exc:
                self._log.warning(
                    f"EntryGate.rehydrate: skipping malformed row "
                    f"signal_id={signal_id}: {exc}"
                )
                continue

            with self._lock:
                if entry.signal_id in self._watchlist:
                    continue   # idempotent
                self._watchlist[entry.signal_id] = entry
            restored += 1

        return restored

    def stop(self) -> None:
        """Signal shutdown and join within 5s (poll thread + 3s bounded wait for workers)."""
        if not self._running:
            return
        self._stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)
        # FIX-060 Part B: shutdown(wait=False) + bounded 3s wait per thread.
        # With Part A (shutdown_event in rate limiter), workers blocked in
        # rate_limiter.acquire() will abort immediately via RateLimitAbortedError.
        # We give them 3s to wrap up before continuing regardless.
        if self._executor is not None:
            import time
            self._executor.shutdown(wait=False, cancel_futures=False)
            time.sleep(3.0)  # FIX-060: bounded wait; threads finish or orphaned
            self._executor = None
        self._running = False
        self._log.info("EntryGate stopped")

    def clear_all(self) -> int:
        """
        FIX-046: Clear all gate entries (in-memory watchlist + persisted gate_state).
        Called by EOD squareoff to prevent stale signal rehydration next morning.
        Returns count of cleared entries.
        """
        count = 0
        with self._lock:
            count = len(self._watchlist)
            self._watchlist.clear()
            self._quote_failures.clear()

        # Clear persisted state
        db_count = self._state_store.clear_all_gate_state()

        self._log.info(
            f"EntryGate.clear_all: cleared {count} in-memory entries, "
            f"{db_count} DB rows from gate_state"
        )
        return count

    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Public API (EG4)
    # ------------------------------------------------------------------

    def add(self, entry: WatchEntry) -> None:
        """
        Add WatchEntry to watchlist.
        Raises ValueError if signal_id already present (EG11).
        Works regardless of running state (gate need not be started yet).

        Audit 4.4: also persists to gate_state and updates signals.status to
        GATE_WAITING so a mid-session restart can rehydrate the watchlist.
        Persistence errors are logged but do not abort the add (the in-memory
        watchlist remains the source of truth for the running session).
        """
        with self._lock:
            if entry.signal_id in self._watchlist:
                raise ValueError(
                    f"signal_id {entry.signal_id!r} already in watchlist"
                )
            self._watchlist[entry.signal_id] = entry

        # Audit 4.4: persist for rehydrate. Best-effort; errors don't abort.
        try:
            self._persist_gate_state(entry)
            self._state_store.update_signal_status(entry.signal_id, "GATE_WAITING")
        except Exception as exc:
            self._log.error(
                f"EntryGate: gate_state persist failed for {entry.signal_id} "
                f"(in-memory add still applies): {exc}"
            )

        self._log.info(
            f"EntryGate.add: {entry.signal_id} ({entry.symbol}) "
            f"dir={entry.direction} entry={entry.entry_price} "
            f"tol={entry.tolerance_pct:.4f} timeout={entry.timeout_sec}s"
        )

    def _persist_gate_state(self, entry: WatchEntry) -> None:
        """Audit 4.4: persist a WatchEntry to gate_state for rehydrate."""
        import json
        added_iso = (
            entry.added_at.isoformat()
            if entry.added_at is not None else now_ist().isoformat()
        )
        self._state_store.insert_gate_state({
            "signal_id":     entry.signal_id,
            "symbol":        entry.symbol,
            "direction":     entry.direction,
            "trigger_price": entry.trigger_price,
            "entry_price":   entry.entry_price,
            "sl_price":      entry.sl_price,
            "tgt_price":     entry.tgt_price,
            "tolerance_pct": entry.tolerance_pct,
            "timeout_sec":   entry.timeout_sec,
            "strategy_name": entry.strategy_name,
            "tier":          entry.tier,
            "scanner_name":  entry.scanner_name,
            "intent":        entry.intent,
            "added_at":      added_iso,
            "extras_json":   json.dumps(entry.extras) if entry.extras else None,
        })

    def remove(self, signal_id: str) -> bool:
        """
        Remove by signal_id. Returns True if removed, False if not found (EG4).
        Idempotent: calling again returns False without error (EG11).
        """
        with self._lock:
            if signal_id not in self._watchlist:
                return False
            del self._watchlist[signal_id]
            self._quote_failures.pop(signal_id, None)
        self._log.info(f"EntryGate.remove: {signal_id} removed externally")
        return True

    def watchlist(self) -> List[WatchEntry]:
        """Return a snapshot copy of the current watchlist (EG4)."""
        with self._lock:
            return list(self._watchlist.values())

    def size(self) -> int:
        """Return current watchlist size (EG4)."""
        with self._lock:
            return len(self._watchlist)

    # ------------------------------------------------------------------
    # Poll loop (EG8)
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """
        Main poll loop. Snapshots watchlist, submits each entry to executor,
        waits for completion, then sleeps until next cycle (EG8).
        """
        while not self._stop_event.is_set():
            t0 = time.monotonic()

            with self._lock:
                snapshot = list(self._watchlist.values())

            if snapshot:
                futures = []
                for entry in snapshot:
                    if self._executor is not None:
                        futures.append(self._executor.submit(self._check_one, entry))
                for fut in futures:
                    try:
                        fut.result(timeout=self._poll_interval_sec + 2.0)
                    except Exception:
                        pass

            elapsed = time.monotonic() - t0
            self._log.debug(
                f"EntryGate poll: {len(snapshot)} entries checked in {elapsed*1000:.1f}ms"
            )

            sleep_sec = max(0.0, self._poll_interval_sec - (time.monotonic() - t0))
            self._stop_event.wait(timeout=sleep_sec)

    # ------------------------------------------------------------------
    # Per-entry check (EG5, EG6, EG9)
    # ------------------------------------------------------------------

    def _check_one(self, entry: WatchEntry) -> None:
        """
        Evaluate one WatchEntry. Called by executor workers.
        Never raises -- all exceptions caught and logged (EG12).
        """
        signal_id = entry.signal_id

        # EG11: skip if already removed (handles races)
        with self._lock:
            if signal_id not in self._watchlist:
                return

        # EG6: timeout check (hard, per-entry)
        now = now_ist()
        added_aware = (
            entry.added_at.replace(tzinfo=ist_timezone())
            if entry.added_at.tzinfo is None
            else entry.added_at
        )
        elapsed_sec = (now - added_aware).total_seconds()
        if elapsed_sec >= entry.timeout_sec:
            self._release(entry, "TIMEOUT")
            return

        # EG9: fetch quote
        try:
            quotes = self._quote_fn([entry.symbol])
            quote = quotes.get(entry.symbol)
            if quote is None:
                raise KeyError(f"No quote returned for {entry.symbol}")
            ltp = float(quote.last_price)
            # Reset consecutive failure count on success
            with self._lock:
                self._quote_failures.pop(signal_id, None)
        except Exception as exc:
            with self._lock:
                count = self._quote_failures.get(signal_id, 0) + 1
                self._quote_failures[signal_id] = count
            if count >= _MAX_QUOTE_FAILURES:
                self._log.error(
                    f"EntryGate: {_MAX_QUOTE_FAILURES} consecutive quote failures "
                    f"for {signal_id} ({entry.symbol}): {exc}"
                )
                self._release(entry, "QUOTE_UNAVAILABLE")
            else:
                self._log.warning(
                    f"EntryGate: quote failure ({count}/{_MAX_QUOTE_FAILURES}) "
                    f"for {signal_id} ({entry.symbol}): {exc}"
                )
            return

        # EG5: price trigger
        tolerance = entry.entry_price * entry.tolerance_pct
        lower = entry.entry_price - tolerance
        upper = entry.entry_price + tolerance

        if lower <= ltp <= upper:
            self._release(entry, "PRICE_HIT", release_ltp=ltp)

    # ------------------------------------------------------------------
    # Release (EG7)
    # ------------------------------------------------------------------

    def _release(self, entry: WatchEntry, reason: str, release_ltp: Optional[float] = None) -> None:
        """
        Atomically remove entry, update state_store, call on_release (EG7).
        Idempotent: if entry was already removed, silently returns.
        Never raises: errors in on_release are caught and logged.

        FIX-025: release_ltp is captured at PRICE_HIT time and passed to
        on_release callback for slippage protection.
        """
        signal_id = entry.signal_id

        # Atomic removal -- idempotent (EG11)
        with self._lock:
            if signal_id not in self._watchlist:
                return
            del self._watchlist[signal_id]
            self._quote_failures.pop(signal_id, None)

        # Compute elapsed for logging
        now = now_ist()
        added_aware = (
            entry.added_at.replace(tzinfo=ist_timezone())
            if entry.added_at.tzinfo is None
            else entry.added_at
        )
        elapsed_sec = (now - added_aware).total_seconds()

        _REASON_STATUS = {
            "PRICE_HIT":         "GATE_RELEASED_PRICE_HIT",
            "TIMEOUT":           "GATE_RELEASED_TIMEOUT",
            "QUOTE_UNAVAILABLE": "GATE_RELEASED_QUOTE_UNAVAILABLE",
        }
        status = _REASON_STATUS.get(reason, f"GATE_RELEASED_{reason}")

        # FIX-010: atomically update signal status AND delete gate_state row
        # in one transaction so a crash between the two cannot leave a zombie
        # gate_state row with a stale signal status (Audit 4.4).
        try:
            self._state_store.release_gate_state(signal_id, status)
        except Exception as exc:
            self._log.error(
                f"EntryGate: release_gate_state failed "
                f"for {signal_id} (reason={reason}): {exc}\n{traceback.format_exc()}"
            )

        # EG7 step 4: log
        ltp_str = f" ltp={release_ltp}" if release_ltp is not None else ""
        self._log.info(
            f"EntryGate: released {signal_id} ({entry.symbol}) "
            f"reason={reason} elapsed={elapsed_sec:.1f}s{ltp_str}"
        )

        # FIX-169 F38: create new entry instead of mutating frozen dataclass interior
        if release_ltp is not None:
            entry = replace(entry, extras={**entry.extras, "release_ltp": release_ltp})

        # EG7 step 3: on_release callback (after store update, errors caught)
        try:
            self._on_release(entry, reason)
        except Exception as exc:
            self._log.error(
                f"EntryGate: on_release callback raised for {signal_id}: "
                f"{exc}\n{traceback.format_exc()}"
            )
