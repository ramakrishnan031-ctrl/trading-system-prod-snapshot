"""
screening/retest_monitor.py — Trading System v2 · S&R V2 Phase A (SNR-V2)

Purpose:
    The WAIT_FOR_RETEST monitor. Holds candidates DIVERTED by the pre-placement
    check (a LONG entry inside a HIGH resistance zone) and, on a daemon poll
    thread, fetches fresh 1m candles per parked symbol and advances the pure
    retest_confirm state machine:
      • CONFIRMED → hand to signal_processor.continue_from_retest (reserve+MARKET).
      • REJECT (timeout / break-down) → clear (no capital was held).
    Restart-safe like EntryGate: persists to retest_state, rehydrates on boot,
    deletes the row atomically on release. Uniform worker pattern (daemon thread
    + stop_event.wait). NO capital is held while parked.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from sr_detector.retest_confirm import RetestParams, evaluate


@dataclass(frozen=True)
class ParkedCandidate:
    signal_id: str
    symbol: str
    direction: str
    zone_band_low: float
    zone_band_high: float
    entry_price: float          # original derived entry (pre-divert)
    sl_price: float             # original derived SL (pre-divert)
    strategy: str
    intent: str
    tier: str
    trigger_price: float
    sizing_inputs: dict
    added_at: datetime          # naive IST (divert time = retest clock start)
    state: str = "WAIT_BREAKOUT"


class RetestMonitor:
    def __init__(
        self,
        *,
        onem_fetcher,                       # OhlcFetcher (short 1m window, no cache)
        state_store,
        params: RetestParams,
        on_confirm: Callable[[ParkedCandidate], None],   # signal_processor.continue_from_retest
        logger,
        now_fn: Callable[[], datetime],
        poll_interval_sec: float = 20.0,
        onem_interval: str = "minute",
        enabled: bool = True,
    ) -> None:
        self._fetcher = onem_fetcher
        self._store = state_store
        self._params = params
        self._on_confirm = on_confirm
        self._log = logger
        self._now_fn = now_fn
        self._poll_interval = float(poll_interval_sec)
        self._onem_interval = onem_interval
        self._enabled = bool(enabled)

        self._parked: Dict[str, ParkedCandidate] = {}
        self._lock = threading.RLock()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._enabled or self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="sr-retest-monitor", daemon=True)
        self._worker.start()
        self._safe_log("info", "retest_monitor: started")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        w = self._worker
        if w is not None:
            w.join(timeout=timeout)
        self._worker = None

    # ── register / clear (called by signal_processor + EOD) ──────────────────

    def register(self, parked: ParkedCandidate) -> None:
        """Park a diverted candidate + persist for restart-safety. Never raises."""
        try:
            with self._lock:
                self._parked[parked.signal_id] = parked
            self._store.insert_retest_state(self._to_row(parked))
            self._safe_log(
                "info", "retest_monitor: parked %s (%s) zone=[%.2f,%.2f] state=%s",
                parked.symbol, parked.signal_id, parked.zone_band_low,
                parked.zone_band_high, parked.state,
            )
        except Exception as exc:
            self._safe_log("error", "retest_monitor: register failed for %s: %s",
                           parked.signal_id, exc)

    def clear_all(self) -> int:
        """EOD: drop every parked candidate (in-memory + persisted). Returns count."""
        with self._lock:
            n = len(self._parked)
            self._parked.clear()
        try:
            self._store.clear_all_retest_state()
        except Exception as exc:
            self._safe_log("error", "retest_monitor: clear_all_retest_state failed: %s", exc)
        return n

    def rehydrate(self) -> int:
        """Boot: rebuild the parked set from retest_state. Returns count restored."""
        restored = 0
        try:
            rows = self._store.get_all_retest_state()
        except Exception as exc:
            self._safe_log("error", "retest_monitor: get_all_retest_state failed: %s", exc)
            return 0
        for row in rows:
            try:
                parked = self._from_row(row)
                with self._lock:
                    self._parked[parked.signal_id] = parked
                restored += 1
            except Exception as exc:
                self._safe_log("error", "retest_monitor: skipping malformed retest_state row: %s", exc)
        if restored:
            self._safe_log("info", "retest_monitor: rehydrated %d parked candidate(s)", restored)
        return restored

    def parked_count(self) -> int:
        with self._lock:
            return len(self._parked)

    def has_symbol(self, symbol: str) -> bool:
        """True if a candidate for this symbol is already parked (dedup guard)."""
        with self._lock:
            return any(p.symbol == symbol for p in self._parked.values())

    # ── worker ────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(timeout=self._poll_interval):
                break
            try:
                self.poll_once()
            except Exception as exc:
                self._safe_log("error", "retest_monitor: poll error: %s", exc)

    # Public + synchronous so tests can drive it deterministically.
    def poll_once(self) -> None:
        with self._lock:
            snapshot = list(self._parked.values())
        for parked in snapshot:
            try:
                self._evaluate(parked)
            except Exception as exc:
                self._safe_log("error", "retest_monitor: evaluate failed for %s: %s",
                               parked.signal_id, exc)

    def _evaluate(self, parked: ParkedCandidate) -> None:
        candles = self._fetch_recent_1m(parked.symbol, since=parked.added_at)
        elapsed = (self._now_fn() - parked.added_at).total_seconds()
        result = evaluate(
            band_low=parked.zone_band_low,
            band_high=parked.zone_band_high,
            candles=candles,
            elapsed_sec=elapsed,
            params=self._params,
            direction=parked.direction,    # SNR-V2: LONG above resistance / SHORT below support
        )
        if result.confirmed:
            # Release the parking row first (so it can't be re-evaluated), then
            # resume the pipeline. No capital was held → nothing to release.
            self._release(parked, "RETEST_CONFIRMED")
            self._safe_log("info", "retest_monitor: CONFIRMED %s (%s) — resuming to MARKET entry",
                           parked.symbol, parked.signal_id)
            try:
                self._on_confirm(parked)
            except Exception as exc:
                self._safe_log("error", "retest_monitor: continue_from_retest raised for %s: %s",
                               parked.signal_id, exc)
        elif result.rejected:
            self._release(parked, f"RETEST_REJECTED_{result.reason.upper()}")
            self._safe_log("info", "retest_monitor: REJECTED %s (%s) — %s",
                           parked.symbol, parked.signal_id, result.reason)
        elif result.state != parked.state:
            updated = replace(parked, state=result.state)
            with self._lock:
                if parked.signal_id in self._parked:
                    self._parked[parked.signal_id] = updated
            try:
                self._store.update_retest_state(parked.signal_id, result.state)
            except Exception as exc:
                self._safe_log("error", "retest_monitor: update_retest_state failed: %s", exc)

    def _release(self, parked: ParkedCandidate, status: str) -> None:
        with self._lock:
            self._parked.pop(parked.signal_id, None)
        try:
            self._store.release_retest_state(parked.signal_id, status)
        except Exception as exc:
            self._safe_log("error", "retest_monitor: release_retest_state failed for %s: %s",
                           parked.signal_id, exc)

    # ── 1m fetch (short window, fresh each poll) ──────────────────────────────

    def _fetch_recent_1m(self, symbol: str, *, since: datetime) -> List:
        tf = self._fetcher.fetch_timeframes(symbol, [self._onem_interval])
        candles = tf.get(self._onem_interval) or []
        since_naive = since.replace(tzinfo=None)
        out = []
        for c in candles:
            ts = c.ts.replace(tzinfo=None) if c.ts.tzinfo else c.ts
            if ts >= since_naive:
                out.append(c)
        return out

    # ── persistence (de)serialisation ────────────────────────────────────────

    def _to_row(self, p: ParkedCandidate) -> dict:
        now_iso = self._now_fn().replace(tzinfo=None).isoformat()
        timeout_at = (p.added_at + timedelta(seconds=self._params.timeout_sec)).isoformat()
        return {
            "signal_id": p.signal_id, "symbol": p.symbol, "direction": p.direction,
            "zone_band_low": p.zone_band_low, "zone_band_high": p.zone_band_high,
            "entry_price": p.entry_price, "sl_price": p.sl_price,
            "strategy": p.strategy, "intent": p.intent, "tier": p.tier,
            "state": p.state, "trigger_price": p.trigger_price,
            "sizing_inputs": json.dumps(p.sizing_inputs, default=str),
            "timeout_at": timeout_at, "added_at": p.added_at.replace(tzinfo=None).isoformat(),
            "created_at": now_iso,
        }

    def _from_row(self, row: dict) -> ParkedCandidate:
        return ParkedCandidate(
            signal_id=row["signal_id"], symbol=row["symbol"], direction=row["direction"],
            zone_band_low=float(row["zone_band_low"]), zone_band_high=float(row["zone_band_high"]),
            entry_price=float(row["entry_price"]), sl_price=float(row["sl_price"]),
            strategy=row["strategy"], intent=row["intent"], tier=row.get("tier") or "",
            trigger_price=float(row["trigger_price"] or 0.0),
            sizing_inputs=json.loads(row["sizing_inputs"]) if row.get("sizing_inputs") else {},
            added_at=datetime.fromisoformat(row["added_at"]),
            state=row.get("state") or "WAIT_BREAKOUT",
        )

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass


class RetestDiverter:
    """
    The pre-placement divert decision (SNR-V2 STEP 3), SYMMETRIC by direction.
    Reads the ZoneCache SYNCHRONOUSLY (no fetch on the hot path):
      • a LONG entry sitting inside a HIGH-confidence RESISTANCE zone, or
      • a SHORT entry sitting inside a HIGH-confidence SUPPORT zone,
    is parked into the RetestMonitor BEFORE any sizing/reservation (so it holds no
    capital). A miss / unknown side / no-HIGH-zone returns False → the caller
    continues normal placement. Never raises.
    """

    def __init__(
        self,
        *,
        zone_cache,
        monitor: RetestMonitor,
        state_store,
        config,
        logger,
        now_fn: Callable[[], datetime],
        mode: str,
        detector_version: str = "snr-v2",
    ) -> None:
        self._cache = zone_cache
        self._monitor = monitor
        self._store = state_store
        self._log = logger
        self._now_fn = now_fn
        self._mode = mode
        self._version = detector_version
        self._enabled = bool(getattr(config, "wait_for_retest_enabled", False))
        self._near_buffer = float(getattr(config, "near_zone_buffer_pct", 0.3))
        self._require_conf = str(getattr(config, "require_confidence", "HIGH"))
        self._sl_buffer_pct = float(getattr(config, "sl_buffer_pct", 0.2))

    @property
    def enabled(self) -> bool:
        return self._enabled

    def maybe_divert(
        self,
        *,
        signal_id: str,
        symbol: str,
        side: str,
        direction: str,
        entry_price: float,
        sl_price: float,
        strategy_name: str,
        intent: str,
        tier: str,
        trigger_price: float,
        score=None,
    ) -> bool:
        """Return True iff the candidate was diverted into WAIT_FOR_RETEST."""
        if not self._enabled:
            return False
        is_long = side == "BUY"
        is_short = side == "SELL"
        if not (is_long or is_short):              # unknown side → normal placement
            return False
        # M-S6: `divert_committed` flips True the moment the caller MUST NOT place —
        # either the symbol is already parked (dedup) or we have registered the candidate.
        # register() adds to the monitor BEFORE it persists and never raises, so once it
        # runs the candidate WILL fire on retest; a later bookkeeping exception must NOT
        # fall through to placement (that is the double-order path). Concurrent same-symbol
        # signals are already serialized upstream by the receiver's atomic _claim_in_flight
        # (M-1/FIX-011), so this method never runs twice for one symbol in parallel.
        divert_committed = False
        try:
            zs = self._cache.get(symbol)            # synchronous; miss → None
            if zs is None:
                return False
            # LONG diverts into a resistance zone (sells the rip); SHORT into a
            # support zone (buys the dip back). Mirror, same matcher.
            zones = zs.resistance if is_long else zs.support
            zone = self._matching_zone(zones, entry_price)
            if zone is None:
                return False

            # Dedup: a symbol already parked must not spawn a 2nd MARKET entry
            # (overlap invariant). Drop the duplicate signal rather than placing
            # it into the zone.
            if self._monitor.has_symbol(symbol):
                divert_committed = True          # already parked → caller must NOT place
                self._store.update_signal_status(
                    signal_id, "REJECTED_RETEST_DUP",
                    "symbol already parked in WAIT_FOR_RETEST")
                return True

            # Structure SL: LONG below the reclaimed level, SHORT above the rejected one.
            if is_long:
                structure_sl = zone.band_low * (1.0 - self._sl_buffer_pct / 100.0)
            else:
                structure_sl = zone.band_high * (1.0 + self._sl_buffer_pct / 100.0)
            parked = ParkedCandidate(
                signal_id=signal_id, symbol=symbol, direction=direction,
                zone_band_low=zone.band_low, zone_band_high=zone.band_high,
                entry_price=entry_price, sl_price=sl_price, strategy=strategy_name,
                intent=intent, tier=tier or "", trigger_price=float(trigger_price or 0.0),
                sizing_inputs={
                    "side": side, "intent": intent, "tier": tier,
                    "orig_entry": entry_price, "orig_sl": sl_price,
                },
                added_at=self._now_fn().replace(tzinfo=None),
                state="WAIT_BREAKOUT",
            )
            self._monitor.register(parked)
            divert_committed = True              # M-S6: register() is authoritative from here
            self._store.update_signal_status(signal_id, "RETEST_WAITING")
            self._log_divert_audit(parked, zone, structure_sl, score)
            return True
        except Exception as exc:
            self._safe_log("error", "retest_diverter: maybe_divert failed for %s: %s", symbol, exc)
            # M-S6: a post-commit bookkeeping failure must NOT fall through to placement.
            return divert_committed

    def _matching_zone(self, zones, entry: float):
        """Nearest required-confidence zone (resistance for LONG / support for
        SHORT) whose band contains the entry. Side-agnostic."""
        candidates = [
            z for z in zones
            if z.confidence == self._require_conf and z.contains(entry, self._near_buffer)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda z: abs(z.center - entry))

    def _log_divert_audit(self, parked: ParkedCandidate, zone, structure_sl: float, score) -> None:
        """Best-effort sr_detector_results row so the V2 divert is auditable.
        Mirrored by direction: LONG fills the resistance columns + reclaim level
        (band_high); SHORT fills the support columns + rejection level (band_low)."""
        try:
            import json
            now_iso = self._now_fn().replace(tzinfo=None).isoformat()
            is_long = parked.direction in ("LONG", "BUY")
            zone_json = json.dumps(zone.to_dict())
            if is_long:
                res_zone, sup_zone = zone_json, None
                res_conf, sup_conf = zone.confidence, "NONE"
                evidence = {"resistance_zones": [zone.to_dict()]}
                flags = ["BUYING_INTO_RESISTANCE", "WAIT_FOR_RETEST_DIVERTED"]
                proposed_entry = zone.band_high          # reclaim level
            else:
                res_zone, sup_zone = None, zone_json
                res_conf, sup_conf = "NONE", zone.confidence
                evidence = {"support_zones": [zone.to_dict()]}
                flags = ["SELLING_INTO_SUPPORT", "WAIT_FOR_RETEST_DIVERTED"]
                proposed_entry = zone.band_low           # rejection level
            self._store.insert_sr_detector_result({
                "signal_id": parked.signal_id, "symbol": parked.symbol, "ts": now_iso,
                "mode": str(self._mode).lower(), "strategy": parked.strategy,
                "direction": parked.direction,
                "score": (int(score) if score is not None else None),
                "intended_entry": parked.entry_price, "actual_fill": None,
                "nearest_resistance_zone": res_zone,
                "nearest_support_zone": sup_zone,
                "dist_to_resistance_pct": None, "dist_to_support_pct": None,
                "resistance_confidence": res_conf, "support_confidence": sup_conf,
                "confluence_evidence": json.dumps(evidence),
                "breakout_volume": None,
                "flags": json.dumps(flags),
                "would_wait_for_retest": 1,
                "proposed_retest_entry": proposed_entry,
                "proposed_retest_sl": structure_sl,
                "structure_status": "OK", "detector_version": self._version,
                "created_at": now_iso,
            })
        except Exception as exc:
            self._safe_log("error", "retest_diverter: audit-log write failed: %s", exc)

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass
