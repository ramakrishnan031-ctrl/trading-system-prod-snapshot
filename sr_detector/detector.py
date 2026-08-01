"""
sr_detector/detector.py — Trading System v2 · S&R Detector V1 (SNR-DETECTOR-V1)

Purpose:
    The async, NON-GATING observer. signal_processor hands it a placed Candidate
    AFTER the order reached placement; observe() enqueues and returns immediately.
    A single serialized background worker does the slow work (3 historical
    fetches → pivots → zones → confluence → flags → shadow-log row). It NEVER
    blocks/delays placement and NEVER raises into the pipeline (spec A, J, K-★).

    SHADOW only: no reject, no veto, no entry/SL/TGT change, no STM. V1 answers
    one question — "can we reliably detect buying-into-resistance before the
    outcome is known?" — by logging evidence for offline validation.

What This Module Does NOT Do:
    - No order/STM/sizing interaction; no EventBus dependency.
    - No outcome simulation (retest is a proposal only; outcomes are an EOD step).
    - No long-term candle storage (fetch → analyse → discard + the session cache).
"""
from __future__ import annotations

import json
import queue
import threading
from datetime import datetime

from core.effect_telemetry import handle as _effect_handle
from statistics import mean
from typing import Dict, List, Optional

from sr_detector.flags import BreakoutContext, compute_flags_and_retest
from sr_detector.models import (
    CONF_ANCHOR_ONLY,
    STRUCT_FETCH_FAILED,
    STRUCT_NONE,
    STRUCT_OK,
    Candidate,
    SRAnalysis,
)
from sr_detector.zone_builder import (
    build_anchor_payload,
    build_flag_params,
    build_scoring_params,
    build_swings_payload,
    build_zone_knobs,
    intraday_anchors,
    scored_zones_from_candles,
)

_SENTINEL = object()
DETECTOR_VERSION = "snr-v1"


class SRDetector:
    def __init__(
        self,
        *,
        config,
        fetcher,
        store,
        logger,
        mode: str,
        now_fn,
        detector_version: str = DETECTOR_VERSION,
        session_open=None,
        session_close=None,
    ) -> None:
        self._cfg = config
        self._fetcher = fetcher
        self._store = store
        self._log = logger
        # effect-telemetry (ledger #1, frozen contract A2.1): one handle,
        # resolved once — an sr_detector_results row written.
        self._fx_write = _effect_handle("sr_detector")
        self._mode = mode
        self._now_fn = now_fn
        self._version = detector_version
        self._enabled = bool(_attr(config, "enabled", False))

        # V3 03.01 Layer-A intraday anchors (VWAP/ORB) — default-OFF gate. When
        # off, analyze() adds no anchor/swing enrichment and does NO extra fetch,
        # so the shadow row is byte-identical to before. Session bounds are
        # injected (from MarketWindows in main.py) — never hardcoded here.
        self._anchors_on = bool(_attr(config, "intraday_anchors_enabled", False))
        self._anchor_interval = str(_attr(config, "anchor_intraday_interval", "5minute"))
        self._anchor_lookback_days = int(_attr(config, "anchor_lookback_days", 1))
        self._orb_window_minutes = int(_attr(config, "orb_window_minutes", 15))
        self._session_open = session_open      # datetime.time | None
        self._session_close = session_close    # datetime.time | None

        # SNR-V2: the zone-building params + the pivots→zones→confluence sequence
        # are shared with the ZoneWarmer via sr_detector.zone_builder (one path).
        self._knobs = build_zone_knobs(config)
        self._scoring = build_scoring_params(config)
        self._flagp = build_flag_params(config)
        self._intervals: List[str] = list(self._knobs.intervals)
        self._breakout_avg_window = int(_attr(config, "breakout_avg_window", 20))
        self._max_zones_logged = int(_attr(config, "max_zones_logged", 12))

        self._q: queue.Queue = queue.Queue(maxsize=int(_attr(config, "max_queue", 256)))
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ── lifecycle ───────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        if not self._enabled or self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="sr-detector", daemon=True)
        self._worker.start()
        self._safe_log("info", "sr_detector: worker started (mode=%s, version=%s)", self._mode, self._version)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        w = self._worker
        if w is not None:
            w.join(timeout=timeout)
        self._worker = None

    # ── the non-gating seam (spec A) ──────────────────────────────────────────

    def observe(self, candidate: Candidate) -> None:
        """Enqueue a placed candidate and return immediately. Never raises."""
        if not self._enabled:
            return
        try:
            self._q.put_nowait(candidate)
        except queue.Full:
            self._safe_log("warning", "sr_detector: queue full, dropping %s", candidate.symbol)
        except Exception as exc:  # belt-and-suspenders; the pipeline must not see this
            self._safe_log("error", "sr_detector: observe() failed: %s", exc)

    # ── worker ────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if item is _SENTINEL:
                    continue
                self.process_candidate(item)
            except Exception as exc:
                self._safe_log("error", "sr_detector: worker error for %s: %s",
                               getattr(item, "symbol", "?"), exc)
            finally:
                self._q.task_done()

    # Public + synchronous so tests can drive it deterministically.
    def process_candidate(self, candidate: Candidate) -> None:
        analysis = self.analyze(candidate)
        self._write(candidate, analysis)

    # ── analysis (fetch → pivots → zones → confluence → flags) ───────────────

    def analyze(self, candidate: Candidate) -> SRAnalysis:
        tf_candles = self._fetcher.fetch_timeframes(candidate.symbol, self._intervals)
        if not tf_candles:
            return SRAnalysis(
                structure_status=STRUCT_FETCH_FAILED,
                flags=("NO_CLEAR_STRUCTURE",),
                note="fetch_failed",
            )

        scored = scored_zones_from_candles(
            tf_candles, knobs=self._knobs, scoring=self._scoring, now=self._safe_now())
        breakout = self._breakout_context(tf_candles)
        fr = compute_flags_and_retest(candidate, scored, breakout, self._flagp)

        status = STRUCT_OK if scored else STRUCT_NONE
        flags = fr.flags if fr.flags else (("NO_CLEAR_STRUCTURE",) if status == STRUCT_NONE else ())

        # V3 03.01 (gated, default-OFF): Layer-A anchor levels + Layer-B swing
        # labelling for the manual-marking validation. confidence_class stays
        # ANCHOR_ONLY — swings are computed + emitted but NOT-YET-VALIDATED, so
        # they never drive the later R:R gate until Rama's validation flips it.
        anchors: dict = {}
        swings: dict = {}
        if self._anchors_on:
            anchors = self._build_anchors(candidate, tf_candles)
            swings = build_swings_payload(scored)

        return SRAnalysis(
            structure_status=status,
            nearest_resistance=fr.nearest_resistance,
            nearest_support=fr.nearest_support,
            dist_to_resistance_pct=fr.dist_to_resistance_pct,
            dist_to_support_pct=fr.dist_to_support_pct,
            breakout_volume=(breakout.last_volume if breakout else None),
            flags=flags,
            retest=fr.retest,
            evidence=self._evidence(scored),
            anchors=anchors,
            swings=swings,
            confidence_class=CONF_ANCHOR_ONLY,
        )

    def _build_anchors(self, candidate: Candidate, tf_candles: Dict[str, list]) -> dict:
        """Layer-A anchors: prior-day (PDH/PDL/PDC) + round numbers (always) plus
        the intraday VWAP/ORB block (a today-only fine fetch; only when session
        bounds are injected). Fail-safe — a fine-fetch miss yields None fields,
        never raises."""
        daily = tf_candles.get("day") or []
        reference = float(candidate.intended_entry) if candidate.intended_entry else None
        intraday = None
        if self._session_open is not None and self._session_close is not None:
            on_date = candidate.ts.date() if candidate.ts is not None else None
            if on_date is not None:
                try:
                    fine = self._fetcher.fetch_interval(
                        candidate.symbol, self._anchor_interval, self._anchor_lookback_days
                    ) or []
                    intraday = intraday_anchors(
                        fine,
                        on_date=on_date,
                        session_open=self._session_open,
                        session_close=self._session_close,
                        orb_window_minutes=self._orb_window_minutes,
                    )
                except Exception as exc:   # fail-safe: anchors are shadow-only
                    self._safe_log("warning", "sr_detector: intraday anchors failed for %s: %s",
                                   candidate.symbol, exc)
                    intraday = None
        return build_anchor_payload(daily, reference, intraday)

    def _breakout_context(self, tf_candles: Dict[str, list]) -> Optional[BreakoutContext]:
        intr = tf_candles.get("30minute") or tf_candles.get("60minute") or tf_candles.get("day")
        if not intr:
            return None
        last = intr[-1]
        window = intr[-(self._breakout_avg_window + 1):-1]
        avg_vol = mean([c.volume for c in window]) if window else float(last.volume)
        return BreakoutContext(
            last_close=last.close,
            last_high=last.high,
            last_low=last.low,
            last_volume=float(last.volume),
            avg_volume=float(avg_vol),
        )

    def _evidence(self, scored) -> dict:
        res = [z.to_dict() for z in scored if z.kind == "RESISTANCE"][: self._max_zones_logged]
        sup = [z.to_dict() for z in scored if z.kind == "SUPPORT"][: self._max_zones_logged]
        return {"resistance_zones": res, "support_zones": sup}

    # ── persistence (imitates the screener_results write: txn + never-raise) ──

    def _write(self, candidate: Candidate, a: SRAnalysis) -> None:
        try:
            # V3 03.01: enrich the evidence JSON with anchors/swings/confidence_class
            # ONLY when the anchor layer is on — default-OFF keeps the persisted
            # row byte-identical to the pre-V3 shadow write.
            evidence_payload = dict(a.evidence)
            if self._anchors_on:
                evidence_payload["anchors"] = a.anchors
                evidence_payload["swings"] = a.swings
                evidence_payload["confidence_class"] = a.confidence_class
            row = {
                "signal_id": candidate.signal_id,
                "symbol": candidate.symbol,
                "ts": _iso(candidate.ts),
                "mode": candidate.mode,
                "strategy": candidate.strategy,
                "direction": candidate.direction,
                "score": candidate.score,
                "intended_entry": candidate.intended_entry,
                "actual_fill": None,
                "nearest_resistance_zone": _json(a.nearest_resistance.to_dict()) if a.nearest_resistance else None,
                "nearest_support_zone": _json(a.nearest_support.to_dict()) if a.nearest_support else None,
                "dist_to_resistance_pct": a.dist_to_resistance_pct,
                "dist_to_support_pct": a.dist_to_support_pct,
                "resistance_confidence": a.nearest_resistance.confidence if a.nearest_resistance else "NONE",
                "support_confidence": a.nearest_support.confidence if a.nearest_support else "NONE",
                "confluence_evidence": _json(evidence_payload),
                "breakout_volume": a.breakout_volume,
                "flags": _json(list(a.flags)),
                "would_wait_for_retest": 1 if a.retest.would_wait else 0,
                "proposed_retest_entry": a.retest.entry,
                "proposed_retest_sl": a.retest.sl,
                "structure_status": a.structure_status,
                "detector_version": self._version,
                "created_at": _iso(self._safe_now()),
            }
            self._store.insert_sr_detector_result(row)
            # effect-telemetry (frozen A2.1): an sr_detector_results row
            # written — counted only after the insert succeeded.
            self._fx_write.inc()
        except Exception as exc:
            # Shadow logging must never raise (parity with screener_results write).
            self._safe_log("error", "sr_detector: write failed for %s: %s", candidate.symbol, exc)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _safe_now(self) -> Optional[datetime]:
        try:
            return self._now_fn()
        except Exception:
            return None

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass


def _attr(config, name, default):
    return getattr(config, name, default)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _json(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)
