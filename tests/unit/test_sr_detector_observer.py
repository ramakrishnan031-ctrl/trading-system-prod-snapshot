"""
tests/unit/test_sr_detector_observer.py — SNR-DETECTOR-V1 observer + seam.

Covers (spec K, the ★ proofs):
  • NON-BLOCKING: observe() hands off to the background worker and returns
    immediately even while the fetch is blocked; the worker then writes a row;
    a failing fetch never raises and logs a fetch_failed row.
  • DORMANCY/PARITY: disabled -> no worker, observe() a no-op, zero writes;
    enabled -> rows logged; mode only TAGS the row (paper/live identical analysis).
  • Real StateStore v37: table present, write round-trips, detector_version set.
  • The signal_processor seam: builds the right Candidate; no-op when None.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta

from core.config_loader import SRDetectorConfig
from sr_detector import build_sr_detector
from sr_detector.models import Candidate
from tests.unit.test_order_reconciler import _make_store

_LOG = logging.getLogger("test_sr_observer")
_NOW = datetime(2026, 6, 26, 14, 0)


# ── helpers ───────────────────────────────────────────────────────────────────

class _FakeStore:
    def __init__(self):
        self.rows = []

    def insert_sr_detector_result(self, row):
        self.rows.append(row)


class _Row:
    instrument_token = 555


class _Cache:
    def get_by_symbol(self, sym):
        return _Row()


def _triangle(n, base_ts, step_min, lo=480.0, hi=520.0, period=20):
    """Triangle wave → repeated swing highs at hi, lows at lo (a HIGH zone)."""
    out, half = [], period // 2
    for i in range(n):
        ph = i % period
        price = lo + (hi - lo) * (ph / half) if ph <= half else hi - (hi - lo) * ((ph - half) / half)
        out.append({"date": base_ts + timedelta(minutes=step_min * i), "open": price,
                    "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 1000})
    return out


def _high_fetch(token, frm, to, interval):
    if interval == "day":
        return _triangle(140, _NOW - timedelta(days=140), 1440)
    if interval == "60minute":
        return _triangle(140, _NOW - timedelta(hours=140), 60)
    return _triangle(140, _NOW - timedelta(minutes=30 * 140), 30)


def _build(store, fetch_fn, *, enabled=True, mode="paper"):
    return build_sr_detector(
        config=SRDetectorConfig(enabled=enabled),
        fetch_fn=fetch_fn,
        instrument_cache=_Cache(),
        store=store,
        logger=_LOG,
        mode=mode,
        now_fn=lambda: _NOW,
    )


def _cand(mode="paper", entry=519.0, score=62, direction="LONG"):
    return Candidate(signal_id="SIG1", symbol="TESTSTK", strategy="momentum_long",
                     direction=direction, intended_entry=entry, sl_price=510.0,
                     tgt_price=535.0, qty=10, intent="INTRADAY", mode=mode, ts=_NOW, score=score)


# ── non-blocking ──────────────────────────────────────────────────────────────

def test_observe_returns_immediately_while_worker_blocks():
    started, release = threading.Event(), threading.Event()

    def slow_fetch(token, frm, to, interval):
        started.set()
        release.wait(timeout=5)
        return []   # an empty result still writes one (fetch_failed) row

    store = _FakeStore()
    det = _build(store, slow_fetch)
    det.start()
    try:
        t0 = time.monotonic()
        det.observe(_cand())
        elapsed = time.monotonic() - t0
        assert elapsed < 0.25, f"observe() blocked for {elapsed:.3f}s"
        assert started.wait(2.0), "worker never picked up the candidate"
        release.set()
        deadline = time.monotonic() + 3.0
        while not store.rows and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(store.rows) == 1
    finally:
        det.stop()


def test_failing_fetch_writes_fetch_failed_row_and_never_raises():
    def boom(*a, **k):
        raise RuntimeError("broker down")

    store = _FakeStore()
    det = _build(store, boom)
    det.process_candidate(_cand())   # synchronous; must not raise
    assert store.rows[0]["structure_status"] == "FETCH_FAILED"
    assert json.loads(store.rows[0]["flags"]) == ["NO_CLEAR_STRUCTURE"]


def test_observe_never_raises_even_if_store_write_fails():
    class _BadStore:
        def insert_sr_detector_result(self, row):
            raise RuntimeError("db gone")

    det = _build(_BadStore(), _high_fetch)
    det.process_candidate(_cand())   # guarded write -> no raise


# ── dormancy / parity ─────────────────────────────────────────────────────────

def test_dormant_when_disabled():
    store = _FakeStore()
    det = _build(store, _high_fetch, enabled=False)
    det.start()                      # must not spawn a worker
    assert det.enabled is False
    assert det._worker is None
    det.observe(_cand())             # no-op
    det.process_candidate            # exists but observe gate is off
    assert store.rows == []


def test_parity_mode_only_tags_the_row():
    sp, sl = _FakeStore(), _FakeStore()
    _build(sp, _high_fetch, mode="paper").process_candidate(_cand(mode="paper"))
    _build(sl, _high_fetch, mode="live").process_candidate(_cand(mode="live"))
    rp, rl = sp.rows[0], sl.rows[0]
    assert rp["mode"] == "paper" and rl["mode"] == "live"
    # identical structural verdict regardless of mode
    assert rp["flags"] == rl["flags"]
    assert rp["resistance_confidence"] == rl["resistance_confidence"] == "HIGH"
    assert "BUYING_INTO_RESISTANCE" in json.loads(rp["flags"])


# ── real StateStore (schema v37) ──────────────────────────────────────────────

def _seed_signal(store, signal_id, symbol="TESTSTK"):
    now = "2026-06-26T14:00:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (signal_id, symbol, "SC", "momentum_long", now, now, now, "PROCESSED",
             f"fp_{signal_id}", "2026-06-26"),
        )


def test_real_store_has_sr_table_and_write_roundtrips(tmp_path):
    store = _make_store(tmp_path)
    assert store.get_schema_version() == 41   # W0 bumped 40→41 (+config_snapshots); sr_detector_results still present
    tbl = store.fetch_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sr_detector_results'")
    assert tbl is not None

    _seed_signal(store, "SIG1")
    det = _build(store, _high_fetch)
    det.process_candidate(_cand())

    got = store.fetch_one(
        "SELECT symbol, mode, resistance_confidence, flags, structure_status, "
        "detector_version, would_wait_for_retest, actual_result "
        "FROM sr_detector_results WHERE signal_id = 'SIG1'")
    assert got is not None
    assert got["resistance_confidence"] == "HIGH"
    assert "BUYING_INTO_RESISTANCE" in got["flags"]
    assert got["structure_status"] == "OK"
    assert got["detector_version"] == "snr-v1"
    assert got["would_wait_for_retest"] == 1
    assert got["actual_result"] is None   # EOD-backfilled later


# ── signal_processor seam ─────────────────────────────────────────────────────

def _bare_processor(detector):
    from signals.signal_processor import SignalProcessor
    sp = SignalProcessor.__new__(SignalProcessor)
    sp._sr_detector = detector
    sp._mode = "PAPER"
    sp._log = _LOG
    return sp


def test_seam_noop_when_detector_none():
    sp = _bare_processor(None)
    # must not raise, must do nothing
    sp._sr_observe(symbol="X", strategy_name="s", score=60, entry_price=100.0,
                   sl_price=98.0, tgt_price=103.0, qty=1, direction="LONG",
                   signal_id="S", intent="INTRADAY")


def test_seam_builds_candidate_with_mode_and_score():
    captured = []

    class _Det:
        def observe(self, c):
            captured.append(c)

    sp = _bare_processor(_Det())
    sp._sr_observe(symbol="ACME", strategy_name="momentum_long", score=62,
                   entry_price=100.0, sl_price=98.0, tgt_price=103.0, qty=5,
                   direction="LONG", signal_id="SIGX", intent="INTRADAY")
    assert len(captured) == 1
    c = captured[0]
    assert c.symbol == "ACME" and c.signal_id == "SIGX"
    assert c.mode == "paper"              # derived from self._mode.lower()
    assert c.score == 62 and c.is_long
    assert c.intended_entry == 100.0 and c.qty == 5


def test_seam_gate_path_score_none():
    captured = []

    class _Det:
        def observe(self, c):
            captured.append(c)

    sp = _bare_processor(_Det())
    sp._sr_observe(symbol="ACME", strategy_name="vwap", score=None, entry_price=50.0,
                   sl_price=49.0, tgt_price=52.0, qty=2, direction="SHORT",
                   signal_id="SIGY", intent="INTRADAY")
    assert captured[0].score is None and not captured[0].is_long
