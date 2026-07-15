"""M-SC2b (15-Jul-2026): generate_screened_stocks_csv read path.

Root cause: get_traded_symbols / get_non_traded_symbols called
`store.transaction(readonly=True)`, but StateStore.transaction() has no
`readonly` param -> TypeError every 15:50 run -> empty header-only CSV. The fix
routes the two reads through db_connect.connect_readonly (mode=ro + query_only),
a connection that is STRUCTURALLY unable to write/migrate and does NOT go through
the migrating StateStore init.

These tests exercise the FIXED path: the reads work (no TypeError), the CSV is
populated, and the connection rejects writes (proving read-only).
"""
from __future__ import annotations

import csv
import sqlite3
import uuid
from pathlib import Path

import pytest

from core import db_connect
from core.state_store import StateStore
from scripts.generate_screened_stocks_csv import (
    generate_csv,
    get_non_traded_symbols,
    get_traded_symbols,
)


def _seed(store: StateStore, date_str: str) -> None:
    ts = f"{date_str}T10:00:00+05:30"
    sig_t = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    sig_r = str(uuid.uuid4())
    with store.transaction() as cur:
        # a TRADED symbol (trades row 'AAA', CLOSED)
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, 'AAA', 'test', 'test', ?, ?, ?, 'TRADED', ?, ?, 100.0)""",
            (sig_t, ts, ts, ts, f"fp-{sig_t}", date_str),
        )
        cur.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, strategy, qty_planned,
                qty_filled, entry_target_price, sl_initial, tgt_initial,
                margin_reserved, risk_amount, created_at, status,
                order_protocol, updated_at)
               VALUES (?, ?, 'AAA', 'LONG', 'test', 10, 10, 100.0, 95.0, 110.0,
                       2000.0, 500.0, ?, 'CLOSED', 'LIMIT_TRIPLE', ?)""",
            (tid, sig_t, ts, ts),
        )
        # a NON-TRADED symbol (rejected signal 'ZZZ')
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, rejection_reason, fingerprint, fingerprint_date,
                trigger_price)
               VALUES (?, 'ZZZ', 'test', 'test', ?, ?, ?, 'REJECTED_SCORE_57',
                       'Score too low', ?, ?, 100.0)""",
            (sig_r, ts, ts, ts, f"fp-{sig_r}", date_str),
        )


def test_connect_readonly_rejects_writes(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    StateStore(db_path=db).close()
    conn = db_connect.connect_readonly(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO signals (signal_id) VALUES ('x')")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM signals")
    finally:
        conn.close()


def test_screened_reads_work_via_readonly(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    store = StateStore(db_path=db)
    _seed(store, "2026-05-31")
    store.close()

    conn = db_connect.connect_readonly(db)
    try:
        traded = get_traded_symbols(conn, "2026-05-31")
        non_traded = get_non_traded_symbols(conn, "2026-05-31")
    finally:
        conn.close()

    assert traded == ["AAA"]
    assert non_traded == [("ZZZ", "Score too low")]


def test_full_read_to_csv_is_populated(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    store = StateStore(db_path=db)
    _seed(store, "2026-05-31")
    store.close()

    conn = db_connect.connect_readonly(db)
    try:
        traded = get_traded_symbols(conn, "2026-05-31")
        non_traded = get_non_traded_symbols(conn, "2026-05-31")
    finally:
        conn.close()

    out = generate_csv("2026-05-31", traded, non_traded, tmp_path / "out")
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    assert rows[0] == ["TRADED", "NON_TRADED", "REJECTION_REASON"]
    # populated (NOT the header-only empty CSV the pre-fix TypeError produced)
    assert rows[1] == ["AAA", "ZZZ", "Score too low"]
