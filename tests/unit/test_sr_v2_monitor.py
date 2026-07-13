"""
tests/unit/test_sr_v2_monitor.py — SNR-V2 RetestMonitor + retest_state DAO + schema.

Real StateStore (v38): schema/table, DAO insert/rehydrate/release/clear, the
monitor poll → CONFIRMED/REJECT, restart rehydration, and the EOD clear.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from screening.retest_monitor import ParkedCandidate, RetestMonitor
from sr_detector.retest_confirm import RetestParams
from tests.unit.test_order_reconciler import _make_store

NOW = datetime(2026, 6, 26, 14, 30)
ADDED = NOW - timedelta(minutes=5)
P = RetestParams(timeout_sec=1800.0, max_away_pct=1.0, confirm_strong_close_frac=0.6)


def _seed_signal(store, signal_id, symbol="ACME"):
    iso = ADDED.isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (signal_id, symbol, "SC", "strat", iso, iso, iso, "RETEST_WAITING",
             f"fp_{signal_id}", "2026-06-26"),
        )


def _parked(signal_id="SIG1", symbol="ACME"):
    return ParkedCandidate(
        signal_id=signal_id, symbol=symbol, direction="LONG",
        zone_band_low=100.0, zone_band_high=101.0, entry_price=100.5, sl_price=98.0,
        strategy="strat", intent="INTRADAY", tier="A", trigger_price=100.4,
        sizing_inputs={"tier": "A"}, added_at=ADDED, state="WAIT_BREAKOUT")


def _candle(ts, high, low, close):
    return SimpleNamespace(ts=ts, open=close, high=high, low=low, close=close, volume=1000)


class _Fetcher:
    """1m fetcher returning a fixed candle script (already post-divert)."""
    def __init__(self, candles):
        self._candles = candles

    def fetch_timeframes(self, symbol, intervals):
        return {"minute": list(self._candles)}


def _confirm_script():
    t = ADDED
    return [
        _candle(t + timedelta(minutes=1), 102.2, 101.0, 102.0),   # breakout
        _candle(t + timedelta(minutes=2), 102.0, 100.5, 100.8),   # retest touch
        _candle(t + timedelta(minutes=3), 103.2, 101.0, 103.0),   # reclaim strong → CONFIRMED
    ]


def _breakdown_script():
    return [_candle(ADDED + timedelta(minutes=1), 99.5, 97.0, 98.0)]   # close < 99 → reject


def _parked_short(signal_id="SIG1", symbol="ACME"):
    return ParkedCandidate(
        signal_id=signal_id, symbol=symbol, direction="SHORT",
        zone_band_low=100.0, zone_band_high=101.0, entry_price=100.5, sl_price=102.0,
        strategy="strat", intent="INTRADAY", tier="A", trigger_price=100.6,
        sizing_inputs={"tier": "A"}, added_at=ADDED, state="WAIT_BREAKOUT")


def _short_confirm_script():
    t = ADDED
    return [
        _candle(t + timedelta(minutes=1), 99.5, 97.8, 98.0),     # breakdown (close < 100)
        _candle(t + timedelta(minutes=2), 100.8, 99.5, 100.5),   # retest touch
        _candle(t + timedelta(minutes=3), 100.0, 97.0, 97.3),    # rejection strong → CONFIRMED
    ]


def _short_reclaim_script():
    return [_candle(ADDED + timedelta(minutes=1), 103.0, 101.5, 102.5)]  # close > 102.01 → reject


def _monitor(store, candles, on_confirm=None):
    return RetestMonitor(
        onem_fetcher=_Fetcher(candles), state_store=store, params=P,
        on_confirm=on_confirm or (lambda p: None), logger=None,
        now_fn=lambda: NOW, poll_interval_sec=20.0)


# ── schema / DAO ──────────────────────────────────────────────────────────────

def test_schema_v38_table_exists(tmp_path: Path):
    store = _make_store(tmp_path)
    assert store.get_schema_version() == 44   # v44 (M-S4: +daily_symbol_stats); retest_state still present
    row = store.fetch_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='retest_state'")
    assert row is not None


def test_dao_insert_get_release(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "SIG1")
    m = _monitor(store, [])
    m.register(_parked())
    rows = store.get_all_retest_state()
    assert len(rows) == 1 and rows[0]["state"] == "WAIT_BREAKOUT"
    store.update_retest_state("SIG1", "WAIT_RETEST")
    assert store.get_all_retest_state()[0]["state"] == "WAIT_RETEST"
    store.release_retest_state("SIG1", "RETEST_CONFIRMED")
    assert store.get_all_retest_state() == []
    sig = store.fetch_one("SELECT status FROM signals WHERE signal_id='SIG1'")
    assert sig["status"] == "RETEST_CONFIRMED"


# ── monitor poll ──────────────────────────────────────────────────────────────

def test_poll_confirms_and_resumes(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "SIG1")
    confirmed = []
    m = _monitor(store, _confirm_script(), on_confirm=confirmed.append)
    m.register(_parked())
    assert m.parked_count() == 1
    m.poll_once()
    assert len(confirmed) == 1 and confirmed[0].signal_id == "SIG1"
    assert m.parked_count() == 0
    assert store.get_all_retest_state() == []          # released


def test_poll_rejects_on_break_down(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "SIG1")
    confirmed = []
    m = _monitor(store, _breakdown_script(), on_confirm=confirmed.append)
    m.register(_parked())
    m.poll_once()
    assert confirmed == []
    assert m.parked_count() == 0
    sig = store.fetch_one("SELECT status FROM signals WHERE signal_id='SIG1'")
    assert sig["status"] == "RETEST_REJECTED_BREAK_DOWN"


def test_short_poll_confirms_and_resumes(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "SIG1")
    confirmed = []
    m = _monitor(store, _short_confirm_script(), on_confirm=confirmed.append)
    m.register(_parked_short())
    assert m.parked_count() == 1
    m.poll_once()
    assert len(confirmed) == 1 and confirmed[0].direction == "SHORT"
    assert m.parked_count() == 0
    assert store.get_all_retest_state() == []          # released


def test_short_poll_rejects_on_reclaim_up(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "SIG1")
    confirmed = []
    m = _monitor(store, _short_reclaim_script(), on_confirm=confirmed.append)
    m.register(_parked_short())
    m.poll_once()
    assert confirmed == []
    assert m.parked_count() == 0
    sig = store.fetch_one("SELECT status FROM signals WHERE signal_id='SIG1'")
    assert sig["status"] == "RETEST_REJECTED_RECLAIM_UP"


def test_in_progress_persists_state(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "SIG1")
    breakout_only = [_candle(ADDED + timedelta(minutes=1), 102.2, 101.0, 102.0)]
    m = _monitor(store, breakout_only)
    m.register(_parked())
    m.poll_once()
    assert m.parked_count() == 1                        # still waiting
    assert store.get_all_retest_state()[0]["state"] == "WAIT_RETEST"


# ── restart rehydration + EOD ─────────────────────────────────────────────────

def test_rehydrate_from_retest_state(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "SIG1")
    _monitor(store, []).register(_parked())            # persisted by monitor A
    fresh = _monitor(store, [])                          # monitor B (a "restart")
    assert fresh.rehydrate() == 1 and fresh.parked_count() == 1
    assert fresh.has_symbol("ACME")


def test_clear_all_for_eod(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_signal(store, "SIG1")
    _seed_signal(store, "SIG2", symbol="BCME")
    m = _monitor(store, [])
    m.register(_parked("SIG1", "ACME"))
    m.register(_parked("SIG2", "BCME"))
    assert m.clear_all() == 2
    assert m.parked_count() == 0
    assert store.get_all_retest_state() == []
