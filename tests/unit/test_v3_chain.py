"""
tests/unit/test_v3_chain.py — V3 Step 10a · the decision-chain shadow enrichment.

Covers the mandated acceptance tests:
  • G-CONFLUENCE  — grouped correlated factors cannot double-count (spec §4).
  • ANTI-LOOKAHEAD (V2) — a "future" candle does NOT alter the verdict; every input is
    truncated to signal time (Step-10a addendum, MANDATORY).
plus the gates (G-RR / G-HTF / G-EXTREME), the truncation helper, the score composer,
and an end-to-end record build.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.candle_math import atr
from core.config_loader import SRDetectorConfig, V3ChainConfig
from screening.hard_gate import (
    GATE_RR, gate_extreme, gate_htf, gate_rr,
)
from sr_detector.models import Candle
from sr_detector.zone_builder import (
    build_scoring_params, build_zone_knobs, scored_zones_from_candles, split_zones,
)
from v3_chain.models import V3Signal
from v3_chain.runner import V3ChainRunner
from v3_chain.score import combine, compose_score
from v3_chain.truncate import close_ts, last_close_ts, truncate_to_asof


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mk(prices, ts0, step_min, *, rng=1.0, vol=1000):
    """Build candles from a close-price list; high/low span ±rng/2 around the price."""
    out = []
    for i, p in enumerate(prices):
        ts = ts0 + timedelta(minutes=step_min * i)
        out.append(Candle(ts=ts, open=p, high=p + rng / 2.0, low=p - rng / 2.0,
                          close=p, volume=vol))
    return out


# A peak at ~110 (resistance) and a trough at ~90 (support) straddling entry=100.
_WAVE = [100, 98, 96, 94, 92, 90, 92, 94, 96, 98, 100, 102, 104, 106, 108, 110,
         108, 106, 104, 102, 100, 99]


class _StubFetcher:
    """Returns a fixed per-interval candle set (stands in for the rate-limited
    OhlcFetcher). fetch_timeframes is the only method the runner calls."""
    def __init__(self, tf_candles):
        self._tf = tf_candles

    def fetch_timeframes(self, symbol, intervals):
        return {tf: list(self._tf.get(tf, [])) for tf in intervals if self._tf.get(tf)}


def _runner(fetcher, *, now):
    cfg = V3ChainConfig(v3_chain_mode="shadow")
    sr = SRDetectorConfig()
    return V3ChainRunner(
        config=cfg, zone_knobs=build_zone_knobs(sr), zone_scoring=build_scoring_params(sr),
        fetcher=fetcher, logger=None, now_fn=lambda: now, regime_runner=None)


def _signal(as_of, side="BUY", entry=100.0):
    return V3Signal(
        signal_id="sig1", symbol="TEST", scanner_name="scn", strategy_name="strat",
        side=side, intent="INTRADAY", entry_price=entry, live_sl_price=entry * 0.99,
        live_tgt_price=entry * 1.02, trigger_price=entry, score=62.0, tier="MEDIUM",
        step_results={"volume_surge": 1.0, "vwap_position": 1.0, "atr_filter": 1.0,
                      "rsi_range": 1.0, "price_action": 1.0, "sector_strength": 1.0,
                      "time_of_day": 1.0, "spread_check": 1.0},
        market_data={}, sector="IT", as_of=as_of, triggered_at=as_of)


# ─────────────────────────────────────────────────────────────────────────────
# TRUNCATION (no-lookahead primitive)
# ─────────────────────────────────────────────────────────────────────────────

class TestTruncate:
    def test_intraday_drops_forming_bar(self):
        base = datetime(2026, 7, 10, 9, 15)
        c = _mk([1, 1, 1, 1, 1], base, 5)          # 09:15..09:35 starts
        # signal at 09:33: bar 09:25 (closes 09:30<=09:33) kept; bar 09:30 (closes 09:35) dropped.
        kept = truncate_to_asof(c, "5minute", datetime(2026, 7, 10, 9, 33))
        assert [k.ts.minute for k in kept] == [15, 20, 25]

    def test_boundary_inclusive_at_exact_close(self):
        base = datetime(2026, 7, 10, 9, 15)
        c = _mk([1, 1], base, 5)                    # closes 09:20, 09:25
        kept = truncate_to_asof(c, "5minute", datetime(2026, 7, 10, 9, 20))
        assert len(kept) == 1                       # 09:15 bar closed exactly at 09:20 → kept

    def test_daily_keeps_prior_dates_only(self):
        c = [Candle(ts=datetime(2026, 7, d, 0, 0), open=1, high=1, low=1, close=1, volume=1)
             for d in (8, 9, 10)]
        kept = truncate_to_asof(c, "day", datetime(2026, 7, 10, 9, 30))
        assert [k.ts.day for k in kept] == [8, 9]   # today's forming daily bar excluded

    def test_close_ts_and_last_close(self):
        base = datetime(2026, 7, 10, 9, 15)
        c = _mk([1, 1], base, 30)
        assert close_ts(c[0], "30minute") == datetime(2026, 7, 10, 9, 45)
        assert last_close_ts(c, "30minute") == datetime(2026, 7, 10, 10, 15).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# GATES
# ─────────────────────────────────────────────────────────────────────────────

class TestGates:
    def test_rr_long_pass_and_fail(self):
        ok = gate_rr("BUY", 100.0, sl_zone_edge=95.0, tgt_zone_edge=115.0, atr30=2.0,
                     rr_floor=2.0, sl_buffer_atr_mult=0.20)
        assert ok.passed and ok.evidence["v3_rr"] > 2.0
        bad = gate_rr("BUY", 100.0, sl_zone_edge=95.0, tgt_zone_edge=104.0, atr30=2.0,
                      rr_floor=2.0, sl_buffer_atr_mult=0.20)
        assert not bad.passed and bad.reason == GATE_RR

    def test_rr_short(self):
        ok = gate_rr("SELL", 100.0, sl_zone_edge=105.0, tgt_zone_edge=85.0, atr30=2.0,
                     rr_floor=2.0, sl_buffer_atr_mult=0.20)
        assert ok.passed and ok.evidence["v3_rr"] > 2.0

    def test_rr_missing_zone_fails(self):
        v = gate_rr("BUY", 100.0, sl_zone_edge=None, tgt_zone_edge=115.0, atr30=2.0,
                    rr_floor=2.0, sl_buffer_atr_mult=0.20)
        assert not v.passed                          # missing S&R → FAIL, never guess

    def test_rr_invalid_geometry_fails(self):
        # support edge ABOVE entry → risk<=0 → invalid
        v = gate_rr("BUY", 100.0, sl_zone_edge=101.0, tgt_zone_edge=115.0, atr30=0.0,
                    rr_floor=2.0, sl_buffer_atr_mult=0.20)
        assert not v.passed

    def test_htf_fail_open_on_missing(self):
        assert gate_htf("BUY", htf_close=None, htf_ema=None).passed

    def test_htf_only_strong_contradiction_fails(self):
        assert not gate_htf("BUY", htf_close=95, htf_ema=100, htf_swing_highs=[110, 105]).passed
        assert gate_htf("BUY", htf_close=99, htf_ema=100, htf_swing_highs=[100, 110]).passed  # mild pullback OK
        assert gate_htf("BUY", htf_close=95, htf_ema=100, htf_swing_highs=[100, 110]).passed  # below EMA but HH → OK

    def test_extreme_gate(self):
        class R:
            extreme_flag = True
        assert not gate_extreme(R()).passed
        assert gate_extreme(None).passed             # regime OFF/missing → pass


# ─────────────────────────────────────────────────────────────────────────────
# SCORE + CONFLUENCE (the anti-inflation acceptance test)
# ─────────────────────────────────────────────────────────────────────────────

class TestScore:
    def test_confluence_group_cannot_double_count(self):
        """G-CONFLUENCE: a grouped correlated pair (rsi_range + vwap_position, both 1.0)
        contributes ONE weight (8), NOT two (16)."""
        cfg = V3ChainConfig()
        steps = {"rsi_range": 1.0, "vwap_position": 1.0}
        sc = compose_score(steps, regime_fraction=None, sr_target_fraction=0.0,
                           htf_ema_fraction=None, htf_swing_fraction=None, cfg=cfg)
        assert sc["context"]["momentum_position"] == pytest.approx(cfg.w_momentum_position)
        assert sc["context"]["momentum_position"] < 2 * cfg.w_momentum_position  # not double

    def test_combine_mean_vs_max(self):
        assert combine([1.0, 0.0], "mean") == 0.5
        assert combine([1.0, 0.0], "max") == 1.0
        assert combine([None, None], "mean") == 0.0

    def test_execution_layer_sums_to_budget(self):
        cfg = V3ChainConfig()
        steps = {k: 1.0 for k in ("volume_surge", "atr_filter", "time_of_day", "spread_check")}
        sc = compose_score(steps, regime_fraction=None, sr_target_fraction=None,
                           htf_ema_fraction=None, htf_swing_fraction=None, cfg=cfg)
        assert sc["execution"]["total"] == pytest.approx(cfg.exec_budget)

    def test_playbook_is_null_not_renormalized(self):
        cfg = V3ChainConfig()
        sc = compose_score({}, regime_fraction=None, sr_target_fraction=None,
                           htf_ema_fraction=None, htf_swing_fraction=None, cfg=cfg)
        assert sc["playbook"] is None
        # partial_total is out of 60 (Context 40 + Execution 20), NEVER scaled to 100.
        assert sc["partial_total"] <= 60.0


# ─────────────────────────────────────────────────────────────────────────────
# ANTI-LOOKAHEAD (V2) — the mandated correctness test
# ─────────────────────────────────────────────────────────────────────────────

class TestAntiLookahead:
    def _tfset(self, extra=None):
        base = datetime(2026, 7, 10, 9, 15)
        c30 = _mk(_WAVE, base, 30, rng=2.0)
        c60 = _mk(_WAVE, base, 60, rng=2.0)
        day = _mk([100 + (i % 5) for i in range(20)], datetime(2026, 6, 15, 0, 0), 24 * 60)
        tf = {"30minute": list(c30), "60minute": list(c60), "day": list(day)}
        if extra:
            for k, cs in extra.items():
                tf[k] = tf.get(k, []) + cs
        return tf

    def test_future_candle_does_not_alter_verdict(self):
        # as_of AFTER the wave's last 30m/60m bar so the whole wave is usable.
        base = datetime(2026, 7, 10, 9, 15)
        as_of = base + timedelta(minutes=30 * (len(_WAVE)) + 5)   # just after the last 30m close
        now = as_of + timedelta(seconds=30)

        # FUTURE bars (closed AFTER as_of) with a dramatically larger range + a new high.
        fut30 = _mk([200, 210, 220, 205, 195, 130], as_of + timedelta(minutes=1), 30, rng=40.0)
        fut60 = _mk([200, 210, 220, 205, 195, 130], as_of + timedelta(minutes=1), 60, rng=40.0)

        base_tf = self._tfset()
        with_future = self._tfset(extra={"30minute": fut30, "60minute": fut60})

        # The future bars ARE material: ATR(30m) differs if they are NOT truncated.
        atr_base = atr(base_tf["30minute"], 14)
        atr_fut = atr(with_future["30minute"], 14)
        assert atr_base is not None and atr_fut is not None and atr_base != pytest.approx(atr_fut)

        rec_base = _runner(_StubFetcher(base_tf), now=now)._build_record(_signal(as_of))
        rec_fut = _runner(_StubFetcher(with_future), now=now)._build_record(_signal(as_of))

        # Every input pre-dates the signal: last close of each TF <= as_of.
        for tf, iso in rec_base.last_candle_close_ts.items():
            if iso:
                assert datetime.fromisoformat(iso) <= as_of, f"{tf} used a post-signal bar"

        # THE ASSERTION: the future bars are neutralised by truncation → identical verdict.
        a, b = rec_base.to_json_dict(), rec_fut.to_json_dict()
        for k in ("v3_sl", "v3_tgt", "v3_rr", "gates", "score",
                  "nearest_support", "nearest_resistance", "last_candle_close_ts"):
            assert a[k] == b[k], f"lookahead leak via {k}: {a[k]} != {b[k]}"

    def test_record_has_real_rr_from_zones(self):
        """Sanity: with the straddling wave, the worker DOES derive an S&R R:R (so the
        anti-lookahead test above is exercising the real gate path, not an all-None one)."""
        base = datetime(2026, 7, 10, 9, 15)
        as_of = base + timedelta(minutes=30 * len(_WAVE) + 5)
        now = as_of + timedelta(seconds=30)
        rec = _runner(_StubFetcher(self._tfset()), now=now)._build_record(_signal(as_of))
        assert rec.v3_rr is not None or rec.gates[GATE_RR]["reason"] == GATE_RR
        # the zones straddle entry → we expect both edges resolved:
        assert rec.nearest_support is not None and rec.nearest_resistance is not None


# ─────────────────────────────────────────────────────────────────────────────
# observe() — hot-path guarantees
# ─────────────────────────────────────────────────────────────────────────────

class TestObserve:
    def test_off_mode_is_noop(self):
        r = V3ChainRunner(config=V3ChainConfig(v3_chain_mode="off"), zone_knobs=None,
                          zone_scoring=None, fetcher=None, logger=None,
                          now_fn=lambda: datetime(2026, 7, 10), regime_runner=None)
        r.observe(_signal(datetime(2026, 7, 10)))     # must not enqueue / raise
        assert r._q.qsize() == 0

    def test_observe_never_raises_on_full_queue(self):
        r = _runner(_StubFetcher({}), now=datetime(2026, 7, 10))
        # fill the queue past capacity; observe must swallow queue.Full (never raise).
        for _ in range(r._q.maxsize + 5):
            r.observe(_signal(datetime(2026, 7, 10)))   # no exception = pass


# ─────────────────────────────────────────────────────────────────────────────
# G-NO-INTERFERENCE — the shadow chain NEVER alters the live outcome (even if it
# would reject, even if observe() RAISES). Reuses the signal_processor harness.
# ─────────────────────────────────────────────────────────────────────────────

class _RaisingChain:
    """A v3_chain stub whose observe() records the call and then RAISES — proving the
    hot-path hook swallows everything and the live path is untouched."""
    def __init__(self):
        self.calls = []

    def observe(self, sig):
        self.calls.append(sig.signal_id)
        raise RuntimeError("shadow chain blew up — must NOT reach the live pipeline")


class TestNoInterference:
    def _status(self, v3_chain):
        from tests.unit.test_signal_processor import _make_proc, _now_tup, _run_one
        proc, sq, store = _make_proc()
        if v3_chain is not None:
            proc.set_v3_chain(v3_chain)
        row = _run_one(proc, _now_tup(signal_id="v3ni"), store=store)
        return (row["status"] if row else None)

    def test_shadow_chain_does_not_change_live_outcome(self):
        baseline = self._status(None)                 # v3_chain OFF (byte-identical path)
        chain = _RaisingChain()
        with_chain = self._status(chain)              # shadow chain that RAISES on observe
        assert with_chain == baseline, (
            f"v3_chain interfered: baseline={baseline} with_chain={with_chain}")
        assert chain.calls == ["v3ni"], "hook did not fire (or fired for the wrong signal)"
