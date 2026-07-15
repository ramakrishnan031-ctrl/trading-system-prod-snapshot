"""Integration test for the 15-Jul COMBINED DEPLOY (`deploy-15jul-combined`).

The merged generate_screened_stocks_csv.py must carry BOTH overlapping change sets:
  - Branch A (M-SC2b): the reads go through a dedicated READ-ONLY connection
    (db_connect.connect_readonly) — no `transaction(readonly=)` TypeError, and the
    connection cannot write.
  - Branch B (F2): `_cron_main` records a FUNCTIONAL status derived from the CSV
    artifact (OK / EMPTY_NO_DATA / FAILED) via `_csv_functional_status`.

This asserts BOTH SIMULTANEOUSLY on the merged code, so a merge that dropped either
side fails here.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime as _dt
from pathlib import Path

import pytest

import scripts.generate_screened_stocks_csv as gsc
from core import db_connect
from core.state_store import StateStore
from scripts.generate_screened_stocks_csv import (
    generate_csv,
    get_non_traded_symbols,
    get_traded_symbols,
)


def _seed(store: StateStore, date_str: str) -> None:
    ts = f"{date_str}T10:00:00+05:30"
    sig_t, tid, sig_r = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id, symbol, scanner, strategy, triggered_at,
               received_at, expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, 'AAA', 'test', 'test', ?, ?, ?, 'TRADED', ?, ?, 100.0)""",
            (sig_t, ts, ts, ts, f"fp-{sig_t}", date_str))
        cur.execute(
            """INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy,
               qty_planned, qty_filled, entry_target_price, sl_initial, tgt_initial,
               margin_reserved, risk_amount, created_at, status, order_protocol, updated_at)
               VALUES (?, ?, 'AAA', 'LONG', 'test', 10, 10, 100.0, 95.0, 110.0, 2000.0,
                       500.0, ?, 'CLOSED', 'LIMIT_TRIPLE', ?)""",
            (tid, sig_t, ts, ts))
        cur.execute(
            """INSERT INTO signals (signal_id, symbol, scanner, strategy, triggered_at,
               received_at, expires_at, status, rejection_reason, fingerprint,
               fingerprint_date, trigger_price)
               VALUES (?, 'ZZZ', 'test', 'test', ?, ?, ?, 'REJECTED_SCORE_57',
                       'Score too low', ?, ?, 100.0)""",
            (sig_r, ts, ts, ts, f"fp-{sig_r}", date_str))


def test_merged_readonly_read_and_f2_functional_status_coexist(tmp_path, monkeypatch):
    # ── Branch A (M-SC2b): read-only connection reads correctly + cannot write ──
    db = tmp_path / "t.db"
    store = StateStore(db_path=db)
    _seed(store, "2026-05-31")
    store.close()

    conn = db_connect.connect_readonly(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM signals")             # read path cannot write
        traded = get_traded_symbols(conn, "2026-05-31")     # no TypeError (kwarg gone)
        non_traded = get_non_traded_symbols(conn, "2026-05-31")
    finally:
        conn.close()
    assert traded == ["AAA"]
    assert non_traded == [("ZZZ", "Score too low")]

    # ── Branch B (F2): functional_status derived from the CSV artifact ──
    reports = Path(gsc.__file__).parent.parent / "reports" / "daily_review"
    reports.mkdir(parents=True, exist_ok=True)
    test_date = "2099-01-01"   # far-future date → cannot clash with a real artifact

    class _FixedDt:
        @staticmethod
        def now():
            return _dt(2099, 1, 1)

    monkeypatch.setattr(gsc, "datetime", _FixedDt)
    csv_path = reports / f"screened_stocks_{test_date}.csv"
    try:
        generate_csv(test_date, traded, non_traded, reports)       # populated artifact
        assert gsc._csv_functional_status() == "OK"
        generate_csv(test_date, [], [], reports)                   # header-only artifact
        assert gsc._csv_functional_status() == "EMPTY_NO_DATA"
    finally:
        csv_path.unlink(missing_ok=True)
