"""
tests/unit/test_v3_pb01_runner.py — V3 Step 10b · T6 the PB-01 place-free would-be runner.

Proves the confirmed candidate → would-be record path: the deterministic PB-01 SL, the
S&R-derived TGT/RR (the Kalyan skip when no resistance exists), the FULL 3-layer score
(Playbook-40 present), NO-LOOKAHEAD (truncation), and the CRITICAL G-NO-ORDER guarantee —
the runner writes ONE JSONL row and has NO order/reserve capability of any kind.
"""
from __future__ import annotations

import inspect
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from core.config_loader import SRDetectorConfig, V3ChainConfig, WatchlistConfig
from sr_detector.models import Candle
from sr_detector.zone_builder import build_scoring_params, build_zone_knobs
from v3_chain.pb01_entry import Pb01Confirmation
from v3_chain.pb01_runner import Pb01WouldBeRunner

AS_OF = datetime(2026, 7, 13, 9, 30)     # the confirmation candle CLOSE (D+1 morning)

# straddling wave: trough ~90 (support), peak ~110 (resistance) around entry 100.
_WAVE = [100, 98, 96, 94, 92, 90, 92, 94, 96, 98, 100, 102, 104, 106, 108, 110,
         108, 106, 104, 102, 100, 99]
_RISING_ONLY = [98, 96, 94, 92, 90, 88, 90, 92, 94, 96, 97]   # trough only, NO high near entry 100.5


def _mk(prices, ts0, step_min, *, rng=1.0, vol=1000):
    return [Candle(ts=ts0 + timedelta(minutes=step_min * i), open=p, high=p + rng / 2.0,
                   low=p - rng / 2.0, close=p, volume=vol) for i, p in enumerate(prices)]


class _StubFetcher:
    def __init__(self, tf):
        self._tf = tf

    def fetch_timeframes(self, symbol, intervals):
        return {tf: list(self._tf.get(tf, [])) for tf in intervals if self._tf.get(tf)}


def _tfset(wave=None, *, extra=None, daily_prices=None):
    base = datetime(2026, 7, 10, 9, 15)          # PRIOR day → all bars close before AS_OF
    w = wave if wave is not None else _WAVE
    dp = daily_prices if daily_prices is not None else [100 + (i % 5) for i in range(20)]
    tf = {"30minute": _mk(w, base, 30, rng=2.0), "60minute": _mk(w, base, 60, rng=2.0),
          "day": _mk(dp, datetime(2026, 6, 15, 0, 0), 24 * 60)}
    if extra:
        for k, cs in extra.items():
            tf[k] = tf.get(k, []) + cs
    return tf


def _conf(entry=100.5, level=100.0, pullback_low=99.5):
    return Pb01Confirmation(
        row_id=1, symbol="TEST", trading_date="2026-07-13", breakout_date="2026-07-10",
        source="pb01_breakout_retest", level=level, sr_zone_json=None,
        entry_price=entry, confirm_open=99.6, confirm_high=100.8, confirm_low=99.4,
        confirm_close=entry, confirm_volume=2000.0, as_of=AS_OF,
        session_open=100.2, session_low=pullback_low, lowest_5m_close=99.7,
        pullback_low=pullback_low, atr30=2.0, baseline_5m_volume=1000.0)


def _runner(tf, *, path):
    cfg = WatchlistConfig(would_be_log_path=str(path))
    sr = SRDetectorConfig()
    return Pb01WouldBeRunner(
        config=cfg, v3_cfg=V3ChainConfig(), fetcher=_StubFetcher(tf),
        zone_knobs=build_zone_knobs(sr), zone_scoring=build_scoring_params(sr),
        logger=None, now_fn=lambda: datetime(2026, 7, 13, 9, 31), regime_runner=None)


def _tmp():
    return Path(tempfile.mkdtemp()) / "pb01_wb.jsonl"


# ── the happy path: pass all gates, full score, deterministic SL ──────────────

def test_would_be_record_passes_and_scores():
    p = _tmp()
    rec = _runner(_tfset(), path=p).record(_conf())
    assert rec is not None
    # deterministic PB-01 SL = min(pullback_low 99.5, level 100) − 0.20×ATR30(2.0) = 99.1
    assert rec.v3_sl == 99.1
    assert rec.v3_rr is not None and rec.v3_rr > 2.0          # resistance ~110 above → good R:R
    assert rec.v3_verdict == "WOULD_PASS_GATES"
    # FULL 3-layer score present (Playbook-40 computed for a v3_playbook candidate).
    assert rec.score["playbook"] is not None
    assert "total" in rec.score and rec.score["total"] <= 100.0 + 1e-9
    # retest_quality: dist=(99.5-100)/2=-0.25 → 1-0.25/0.75 = 0.667 → ×15 = 10.0
    assert abs(rec.score["playbook"]["retest_quality"] - 10.0) < 0.05
    # exactly ONE JSONL row was written (and it round-trips).
    lines = p.read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["symbol"] == "TEST" and row["strategy"] == "pb01_breakout_retest"
    assert row["v3_sl"] == 99.1 and row["side"] == "BUY"


def test_no_resistance_is_kalyan_reject_rr():
    # no resistance ABOVE entry (all TFs incl. daily below entry) → G-RR FAILS (no safe
    # target → skip; the Kalyan rule).
    p = _tmp()
    tf = _tfset(_RISING_ONLY, daily_prices=[90 + (i % 5) for i in range(20)])
    rec = _runner(tf, path=p).record(_conf())
    assert rec.v3_verdict == "WOULD_REJECT_RR"
    assert rec.v3_tgt is None and rec.v3_rr is None
    assert rec.v3_sl == 99.1                                  # SL is still deterministic
    assert rec.gates["RR"]["passed"] is False


# ── NO-LOOKAHEAD: a future bar cannot change the verdict ──────────────────────

def test_future_bar_does_not_alter_verdict():
    fut = _mk([200, 210, 220], AS_OF + timedelta(minutes=1), 30, rng=40.0)
    base = _runner(_tfset(), path=_tmp()).record(_conf())
    withf = _runner(_tfset(extra={"30minute": fut}), path=_tmp()).record(_conf())
    a, b = base.to_json_dict(), withf.to_json_dict()
    for k in ("v3_sl", "v3_tgt", "v3_rr", "gates", "score"):
        assert a[k] == b[k], f"lookahead leak via {k}"
    # every input pre-dates the confirmation instant.
    for tf, iso in base.last_candle_close_ts.items():
        if iso:
            assert datetime.fromisoformat(iso) <= AS_OF


# ── G-NO-ORDER: the runner CANNOT place an order (structural + behavioural) ────

def test_g_no_order_structural():
    # 1. No order/reserve/broker/fund dependency is injectable → cannot place, ever.
    params = set(inspect.signature(Pb01WouldBeRunner.__init__).parameters)
    banned = {"fund_manager", "order_placer", "placer", "broker", "reserve", "queue",
              "signal_queue", "risk", "kite", "adapter"}
    assert params & banned == set(), f"runner exposes an order path: {params & banned}"
    # 2. No method body references a placement/reservation API.
    src = inspect.getsource(Pb01WouldBeRunner)
    for needle in ("reserve", "place_order", ".place(", "submit_order", "fund_manager"):
        assert needle not in src, f"runner references an order API: {needle}"


def test_g_no_order_behavioural_only_a_record_is_written():
    # Even a fully-admitted candidate produces ONLY a JSONL record — no other side effect
    # is possible (the runner has no other collaborators to call).
    p = _tmp()
    rec = _runner(_tfset(), path=p).record(_conf())
    assert rec.v3_verdict == "WOULD_PASS_GATES"
    assert p.exists() and len(p.read_text().splitlines()) == 1


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    for name in sorted(dir(mod)):
        if name.startswith("test_"):
            getattr(mod, name)()
            print(f"  OK {name}")
    print("\nPB-01 would-be runner: all checks passed.")
