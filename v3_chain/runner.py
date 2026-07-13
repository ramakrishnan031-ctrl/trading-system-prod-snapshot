"""
v3_chain/runner.py — Trading System v2 · V3 Step 10a · the async enrichment worker.

Runs the V3 decision chain in SHADOW over live signals. It is:
  • LOG-ONLY   — the verdict never rejects/delays/alters a live signal or order.
  • ASYNC      — observe() is a fast, guarded, fire-and-forget enqueue on the hot path;
                 ALL fetching + computation happens on a BACKGROUND worker (TASK 0:
                 no warm S&R cache exists today, so a sync read would cold-miss and
                 fail-closed on nearly every signal — async is the honest design).
  • NO-LOOKAHEAD — every candle series is truncated to bars CLOSED STRICTLY BEFORE the
                 signal's decision instant (`as_of`) before any S&R/indicator is
                 computed, so the verdict carries NO hindsight (Step-10a addendum).
  • OFF byte-identical — default-OFF: not constructed → signal_processor gets
                 v3_chain=None → the hot-path hook is one skipped flag check.

Reuses (R2 — no duplication): the sr_detector OhlcFetcher + zone_builder + pivots, the
core.candle_math indicators, and the regime runner's `.latest` snapshot.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from typing import Any, List, Optional

from core.candle_math import atr, ema
from screening.hard_gate import (
    GATE_EXTREME, GATE_HTF, GATE_RR, gate_extreme, gate_htf, gate_rr,
)
from sr_detector.models import CONF_ANCHOR_ONLY
from sr_detector.pivots import find_swing_pivots
from sr_detector.zone_builder import scored_zones_from_candles, split_zones
from v3_chain.models import V3Signal, WouldBeRecord
from v3_chain.score import compose_score
from v3_chain.truncate import last_close_ts, truncate_to_asof

_SENTINEL = object()
_TF_30 = "30minute"
_TF_60 = "60minute"
_TF_DAY = "day"


class V3ChainRunner:
    def __init__(
        self,
        *,
        config,                 # V3ChainConfig
        zone_knobs,             # sr_detector.zone_builder.ZoneKnobs (reuse)
        zone_scoring,           # sr_detector.confluence.ScoringParams (reuse)
        fetcher,                # sr_detector.fetch.OhlcFetcher (own instance over the shared closure)
        logger,
        now_fn,
        regime_runner=None,     # regime.runner.MarketRegimeShadowRunner | None (for .latest)
    ) -> None:
        self._cfg = config
        self._knobs = zone_knobs
        self._scoring = zone_scoring
        self._fetcher = fetcher
        self._log = logger
        self._now = now_fn
        self._regime_runner = regime_runner
        self._q: queue.Queue = queue.Queue(maxsize=int(getattr(config, "max_queue", 512)))
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # sr_sync_hit tracking: symbol → last as_of we enriched it at (repeat-within-TTL locality).
        self._last_seen: dict = {}
        self._seen_lock = threading.Lock()

    @property
    def mode(self) -> str:
        return getattr(self._cfg, "v3_chain_mode", "off")

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._worker is not None or self.mode == "off":
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="v3-chain", daemon=True)
        self._worker.start()
        self._safe_log("info", "v3_chain: shadow enrichment worker started")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        w = self._worker
        if w is not None:
            w.join(timeout=timeout)
        self._worker = None

    # ── hot-path entry (fast, guarded, fire-and-forget) ───────────────────────
    def observe(self, sig: V3Signal) -> None:
        """Capture the regime snapshot AS OF signal time (O(1) read, on the hot path)
        and enqueue for the background worker. NEVER blocks/raises into the pipeline;
        a full queue drops the copy (logged) — the LIVE path is untouched either way."""
        if self.mode != "shadow":
            return
        try:
            # regime AS OF signal time — the latest snapshot, captured now (pre-dates
            # nothing later). None when regime is OFF (10a). NOT re-read in the worker.
            if self._regime_runner is not None:
                try:
                    sig.regime_state = self._regime_runner.latest
                except Exception:
                    sig.regime_state = None
            self._q.put_nowait(sig)
        except queue.Full:
            self._safe_log("warning", "v3_chain: queue full, dropping %s/%s",
                           sig.symbol, sig.signal_id)
        except Exception as exc:   # observability must NEVER break admission
            self._safe_log("error", "v3_chain.observe error (ignored): %s", exc)

    # ── background worker ─────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if item is _SENTINEL:
                    continue
                self._enrich(item)
            except Exception as exc:   # a broken job must NEVER kill the worker
                self._safe_log("error", "v3_chain: enrich error (ignored): %s", exc)
            finally:
                self._q.task_done()

    def _sr_sync_hit(self, symbol: str, as_of) -> bool:
        """Would a warm S&R cache have HIT at signal time? Proxy: had we already
        enriched this symbol within the fetch cache TTL before this signal. First
        signal per symbol → miss (expected ~0% today; a future SYNC/enforce path
        REQUIRES pre-warming — reported plainly, not engineered around)."""
        ttl = float(getattr(self._cfg, "fetch_cache_ttl_sec", 1800.0))
        hit = False
        with self._seen_lock:
            prev = self._last_seen.get(symbol)
            if prev is not None:
                try:
                    hit = (as_of - prev).total_seconds() <= ttl
                except Exception:
                    hit = False
            self._last_seen[symbol] = as_of
        return hit

    def _enrich(self, sig: V3Signal) -> None:
        """Compute + persist the would-be record (background-worker entry)."""
        self._persist(self._build_record(sig))

    def _build_record(self, sig: V3Signal) -> WouldBeRecord:
        """Compute the V3 verdict for one signal. PURE w.r.t. the injected fetcher +
        the captured snapshot (so the anti-lookahead test can drive it directly). Every
        candle series is truncated to signal time BEFORE any S&R/indicator is computed."""
        as_of = sig.as_of
        long = str(sig.side).strip().upper() in ("BUY", "LONG")
        sync_hit = self._sr_sync_hit(sig.symbol, as_of)

        # 1. Fetch structure TFs and TRUNCATE each to signal time (NO-LOOKAHEAD).
        intervals = list(getattr(self._cfg, "structure_intervals", [_TF_DAY, _TF_60, _TF_30]))
        fetched = self._fetcher.fetch_timeframes(sig.symbol, intervals) or {}
        truncated = {tf: truncate_to_asof(fetched.get(tf) or [], tf, as_of) for tf in intervals}
        last_close = {tf: last_close_ts(truncated.get(tf) or [], tf) for tf in intervals}

        c30 = truncated.get(_TF_30) or []
        c60 = truncated.get(_TF_60) or []

        # 2. S&R zones from the TRUNCATED structure set (reuse the shared builder).
        supports: List[Any] = []
        resistances: List[Any] = []
        try:
            nonempty = {tf: cs for tf, cs in truncated.items() if cs}
            if nonempty:
                scored = scored_zones_from_candles(
                    nonempty, knobs=self._knobs, scoring=self._scoring, now=as_of)
                resistances, supports = split_zones(scored)
        except Exception as exc:
            self._safe_log("warning", "v3_chain: zone build failed for %s: %s", sig.symbol, exc)

        # 3. Select the nearest zones for SL / TGT (side-aware).
        near_sup = _nearest_support_below(supports, sig.entry_price)
        near_res = _nearest_resistance_above(resistances, sig.entry_price)
        if long:
            sl_edge = near_sup.band_low if near_sup is not None else None      # SL below support
            tgt_edge = near_res.band_low if near_res is not None else None     # TGT = near edge of resistance above
            tgt_zone = near_res
        else:
            sl_edge = near_res.band_high if near_res is not None else None     # SL above resistance
            tgt_edge = near_sup.band_high if near_sup is not None else None    # TGT = near edge of support below
            tgt_zone = near_sup

        # 4. Indicators from the truncated series.
        atr30 = _safe(lambda: atr(c30, int(self._cfg.atr30_period))) if len(c30) > int(self._cfg.atr30_period) else None
        htf_close = c60[-1].close if c60 else None
        htf_ema = _safe(lambda: ema([c.close for c in c60], int(self._cfg.htf_ema_period))) if c60 else None
        n = int(self._cfg.htf_swing_pivot_n)
        pivots = _safe(lambda: find_swing_pivots(c60, left=n, right=n)) or [] if c60 else []
        htf_highs = [p.price for p in pivots if p.kind == "HIGH"]
        htf_lows = [p.price for p in pivots if p.kind == "LOW"]

        # 5. Gates (LOG-ONLY — recorded, never enforced).
        v_rr = gate_rr(
            sig.side, sig.entry_price, sl_zone_edge=sl_edge, tgt_zone_edge=tgt_edge,
            atr30=atr30, rr_floor=float(self._cfg.rr_floor),
            sl_buffer_atr_mult=float(self._cfg.sl_buffer_atr_mult))
        v_htf = gate_htf(
            sig.side, htf_close=htf_close, htf_ema=htf_ema,
            htf_swing_highs=htf_highs, htf_swing_lows=htf_lows)
        v_ext = gate_extreme(sig.regime_state)
        gates = {
            GATE_RR: {"passed": v_rr.passed, "reason": v_rr.reason, "evidence": v_rr.evidence},
            GATE_HTF: {"passed": v_htf.passed, "reason": v_htf.reason, "evidence": v_htf.evidence},
            GATE_EXTREME: {"passed": v_ext.passed, "reason": v_ext.reason, "evidence": v_ext.evidence},
        }
        # verdict: first failing gate in [EXTREME(blocker), HTF(blocker), RR(must-have)] order.
        verdict = "WOULD_PASS_GATES"
        for name, v in ((GATE_EXTREME, v_ext), (GATE_HTF, v_htf), (GATE_RR, v_rr)):
            if not v.passed:
                verdict = f"WOULD_REJECT_{name}"
                break

        # 6. Score (Context 40 + Execution 20; Playbook null).
        regime_frac, regime_unavail = self._regime_fraction(sig.regime_state, as_of)
        sr_target_frac = self._sr_target_fraction(tgt_zone)
        htf_ema_frac, htf_swing_frac = _htf_alignment_fractions(long, htf_close, htf_ema, htf_highs, htf_lows)
        score = compose_score(
            sig.step_results,
            regime_fraction=regime_frac, sr_target_fraction=sr_target_frac,
            htf_ema_fraction=htf_ema_frac, htf_swing_fraction=htf_swing_frac, cfg=self._cfg)

        # 7. V3 vs LIVE placement comparison.
        v3_sl = v_rr.evidence.get("v3_sl")
        v3_tgt = v_rr.evidence.get("v3_tgt")
        v3_rr = v_rr.evidence.get("v3_rr")
        live_rr = _live_rr(long, sig.entry_price, sig.live_sl_price, sig.live_tgt_price)

        rec = WouldBeRecord(
            signal_id=sig.signal_id, symbol=sig.symbol, strategy=sig.strategy_name,
            scanner=sig.scanner_name, side=sig.side, intent=sig.intent,
            signal_ts=_iso(as_of), enrichment_ts=_iso(self._safe_now()),
            last_candle_close_ts=last_close,
            regime=(sig.regime_state.to_dict() if sig.regime_state is not None else None),
            regime_asof_unavailable=regime_unavail,
            nearest_support=(near_sup.to_dict() if near_sup is not None else None),
            nearest_resistance=(near_res.to_dict() if near_res is not None else None),
            sr_confidence_class=CONF_ANCHOR_ONLY,   # swing-derived zones are NOT-YET-VALIDATED (promotion gate P2)
            sr_sync_hit=sync_hit,
            v3_sl=v3_sl, v3_tgt=v3_tgt, v3_rr=v3_rr,
            live_entry=sig.entry_price, live_sl=sig.live_sl_price, live_tgt=sig.live_tgt_price,
            live_rr=live_rr,
            gates=gates, score=score, live_score=sig.score, live_tier=sig.tier,
            v3_verdict=verdict, live_outcome_link=sig.signal_id,
        )
        return rec

    # ── score-factor helpers (delegate to the shared pure functions below so the
    #    PB-01 would-be runner reuses ONE implementation — no duplication) ─────────
    def _regime_fraction(self, regime_state, as_of):
        return regime_fraction(self._cfg, regime_state, as_of)

    def _sr_target_fraction(self, tgt_zone):
        return sr_target_fraction(self._cfg, tgt_zone)

    # ── persistence (best-effort; never blocks/raises) ────────────────────────
    def _persist(self, rec: WouldBeRecord) -> None:
        path = getattr(self._cfg, "would_be_log_path", "data_store/v3/would_be.jsonl")
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_json_dict()) + "\n")
            self._safe_log(
                "info", "v3_chain[%s/%s]: verdict=%s v3_rr=%s live_rr=%s score=%.1f sync_hit=%s",
                rec.symbol, rec.signal_id, rec.v3_verdict, rec.v3_rr, rec.live_rr,
                rec.score.get("partial_total", 0.0), rec.sr_sync_hit)
        except Exception as exc:
            self._safe_log("error", "v3_chain persist failed (ignored): %s", exc)

    def _safe_now(self):
        try:
            return self._now()
        except Exception:
            return None

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# shared score-factor functions (cfg-parametrized; reused by the PB-01 would-be
# runner so the Context factors have ONE implementation — no duplicate scorer).
# ─────────────────────────────────────────────────────────────────────────────

def regime_fraction(cfg, regime_state, as_of):
    """(fraction in [0,1] or None, regime_asof_unavailable). None/UNKNOWN → 0
    contribution + unavailable flag. The snapshot was captured AS OF the decision
    instant, so it is as-of by construction; a `ts` present and AFTER as_of is refused
    (no lookahead). Pure — parametrized by cfg so both runners share it."""
    if regime_state is None:
        return None, True
    try:
        ts = getattr(regime_state, "ts", None)
        if ts and _parse_iso(ts) is not None and as_of is not None:
            if _naive(_parse_iso(ts)) > _naive(as_of):
                return None, True   # post-signal snapshot → refuse (no lookahead)
    except Exception:
        pass
    try:
        d = regime_state.direction
        dt = regime_state.day_type
        vol = regime_state.volatility
        dir_c = float(cfg.regime_pref_direction.get(d.value, 0.5)) * float(d.multiplier)
        day_c = float(cfg.regime_pref_day_type.get(dt.value, 0.5)) * float(dt.multiplier)
        vol_c = float(cfg.regime_pref_volatility.get(vol.value, 1.0)) * float(vol.multiplier)
        frac = (dir_c + day_c + vol_c) / 3.0
        return max(0.0, min(1.0, frac)), False
    except Exception:
        return None, True


def sr_target_fraction(cfg, tgt_zone):
    """Target-zone confidence → fraction (a fuzzy target = an unreliable TGT). Pure —
    shared by V3ChainRunner and the PB-01 would-be runner."""
    if tgt_zone is None:
        return 0.0
    conf = str(getattr(tgt_zone, "confidence", "LOW")).upper()
    if conf == "HIGH":
        return float(cfg.sr_target_quality_high)
    if conf == "MEDIUM":
        return float(cfg.sr_target_quality_medium)
    return float(cfg.sr_target_quality_low)


# ─────────────────────────────────────────────────────────────────────────────
# pure helpers
# ─────────────────────────────────────────────────────────────────────────────

def _nearest_support_below(supports, entry):
    """The support zone nearest below entry (highest band_high strictly < entry)."""
    cands = [z for z in supports if z.band_high < entry]
    return max(cands, key=lambda z: z.band_high) if cands else None


def _nearest_resistance_above(resistances, entry):
    """The resistance zone nearest above entry (lowest band_low strictly > entry)."""
    cands = [z for z in resistances if z.band_low > entry]
    return min(cands, key=lambda z: z.band_low) if cands else None


def _htf_alignment_fractions(long, htf_close, htf_ema, highs, lows):
    """(ema_position_fraction, swing_structure_fraction), each in [0,1] or None.
    Positive-alignment DEGREE for the Context htf_alignment confluence group — distinct
    from the G-HTF gate (which only rejects a STRONG contradiction). Ordinal + bounded."""
    ema_frac = None
    if htf_close is not None and htf_ema is not None:
        if long:
            ema_frac = 1.0 if htf_close >= htf_ema else 0.0
        else:
            ema_frac = 1.0 if htf_close <= htf_ema else 0.0
    swing_frac = None
    seq = highs if long else lows
    if len(seq) >= 2:
        if long:
            swing_frac = 1.0 if seq[-1] > seq[-2] else 0.0   # higher-high = uptrend
        else:
            swing_frac = 1.0 if seq[-1] < seq[-2] else 0.0   # lower-low = downtrend
    return ema_frac, swing_frac


def _live_rr(long, entry, sl, tgt):
    """The live FIXED_PCT-derived R:R (for the comparison). None if unavailable."""
    try:
        if entry is None or sl is None or tgt is None:
            return None
        if long:
            risk, reward = entry - sl, tgt - entry
        else:
            risk, reward = sl - entry, entry - tgt
        if risk <= 0 or reward <= 0:
            return None
        return round(reward / risk, 4)
    except Exception:
        return None


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _iso(dt) -> str:
    try:
        return dt.isoformat()
    except Exception:
        return ""


def _parse_iso(s):
    from datetime import datetime as _dt
    try:
        return _dt.fromisoformat(str(s))
    except Exception:
        return None


def _naive(dt):
    """Drop tzinfo for a like-for-like IST comparison (single-tz codebase)."""
    try:
        return dt.replace(tzinfo=None)
    except Exception:
        return dt
