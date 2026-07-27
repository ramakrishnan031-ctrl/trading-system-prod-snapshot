"""tests/unit/test_backfill_closure_source_w8.py -- the W8 backfill, proven before
it is ever pointed at production (27-Jul-2026).

⛔ THE BACKFILL ITSELF IS UNRUN. It rewrites historical rows that reports have
already read, so whether it runs is Rama's decision. What is NOT optional is that
its guards be evidence rather than claims -- "reversible", "idempotent" and "it
refuses on schema 44" are exactly the kind of assurance this project has repeatedly
found to be untrue of code that looked alive and had never executed.

Every test here drives the REAL script against a REAL StateStore on a tmp DB.

⭐ THE ORDERING THAT MATTERS: the refusal tests come first, because a guard that
has never been seen to fire is not a guard.

Run: python -m pytest tests/unit/test_backfill_closure_source_w8.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.state_store import StateStore
from scripts import backfill_closure_source_w8 as bf
from tests.unit.test_order_reconciler import _insert_order, _insert_trade


def _db(tmp_path: Path, monkeypatch, rows) -> Path:
    """Build a tmp DB shaped like production, and point the script at it."""
    p = tmp_path / "trading_system.db"
    store = StateStore(p)
    for tid, symbol, status, legs in rows:
        _insert_trade(store, tid, symbol=symbol, status=status)
        for oid, leg, leg_status in legs:
            _insert_order(store, oid, tid, leg=leg, status=leg_status)
    store.close()
    monkeypatch.setattr(bf, "DB_PATH", p)
    return p


def _sources(p: Path) -> dict:
    store = StateStore(p)
    try:
        return {r["symbol"]: r["closure_source"] for r in store.fetch_all(
            "SELECT symbol, closure_source FROM trades")}
    finally:
        store.close()


def _drop_closure_source(p: Path) -> None:
    """Recreate the schema-44 shape: the column simply is not there."""
    import sqlite3
    conn = sqlite3.connect(str(p))
    try:
        conn.execute("ALTER TABLE trades DROP COLUMN closure_source")
        conn.commit()
    finally:
        conn.close()


def _leg(kind: str, tid: str = "x", status: str = "COMPLETE"):
    """orders.order_id is UNIQUE, so ids must not collide across trades in one DB."""
    return [(f"{tid}_{kind}", kind, status)]


def _no_leg(tid: str):
    return _leg("SL", tid, "CANCELLED") + _leg("TGT", tid, "CANCELLED")


# ── ⭐ the refusals, first ─────────────────────────────────────────────────────

def test_it_refuses_on_schema_44_where_the_column_does_not_exist(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """⭐⭐ THE ONE THAT MATTERS ON TUESDAY MORNING. Production was on 44 and v45
    adds the column by REBUILDING `trades` at the 08:15 boot -- so a write before
    that boot would be silently destroyed by the rebuild. The script must refuse,
    not create the column: migration is main.py's alone."""
    p = _db(tmp_path, monkeypatch, [("t1", "IRFC", "CLOSED_MANUAL", _leg("TGT", "t1"))])
    _drop_closure_source(p)
    with pytest.raises(SystemExit) as e:
        bf.main(["--commit"])
    assert "schema 44" in str(e.value) and "REFUSING" in str(e.value)


def test_a_trade_with_two_completed_legs_refuses_rather_than_picking_one(
        tmp_path: Path, monkeypatch) -> None:
    """⚠️ THE CONTRADICTION GUARD. Two different COMPLETE exit legs is exactly what
    orders/closure_classifier.py resolves to EXTERNAL_UNATTRIBUTED at CRITICAL --
    it does NOT take the higher-precedence one. A backfill that quietly picked one
    would disagree with the live classifier on identical evidence.

    ⭐ Measured 27-Jul: production has ZERO such rows, so this guard cannot fire on
    today's data -- which is precisely why it needs a test. An untestable guard and
    an absent one look the same in production."""
    _db(tmp_path, monkeypatch, [
        ("t1", "IRFC", "CLOSED_MANUAL",
         _leg("TGT", "t1") + _leg("SL", "t1"))])
    with pytest.raises(SystemExit) as e:
        bf.main(["--commit"])
    assert "two different COMPLETE exit legs" in str(e.value)


def test_dry_run_is_the_default_and_writes_nothing(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """No flag, no write. The safe thing must be what happens when you type the
    command without thinking."""
    p = _db(tmp_path, monkeypatch, [("t1", "IRFC", "CLOSED_MANUAL", _leg("TGT", "t1"))])
    assert bf.main([]) == 0
    assert _sources(p) == {"IRFC": None}
    assert "DRY RUN" in capsys.readouterr().out


# ── the write, and what it deliberately leaves alone ──────────────────────────

def test_it_writes_the_leg_the_orders_table_records(
        tmp_path: Path, monkeypatch) -> None:
    p = _db(tmp_path, monkeypatch, [
        ("t1", "AAA", "CLOSED_MANUAL", _leg("TGT", "t1")),
        ("t2", "BBB", "CLOSED_MANUAL", _leg("SL", "t2")),
        ("t3", "CCC", "CLOSED_MANUAL", _leg("EOD", "t3"))])
    assert bf.main(["--commit"]) == 0
    assert _sources(p) == {"AAA": "OWN_TGT", "BBB": "OWN_SL", "CCC": "OWN_EOD"}


def test_a_row_with_no_own_leg_is_left_NULL_not_EXTERNAL(
        tmp_path: Path, monkeypatch) -> None:
    """⭐⭐ THE POINT OF §C4 -- the SULA / AGARIND / EVEREADY / AEROENTER / RCF /
    GICRE shape. NULL is "we do not know"; EXTERNAL_UNATTRIBUTED is a POSITIVE
    claim that nothing of ours closed it, and for the two era-artifact rows the
    record is simply silent. A backfill that cleaned all 41 would be manufacturing
    answers where there is no evidence -- the same failure as the false CRITICAL,
    pointed the other way."""
    p = _db(tmp_path, monkeypatch, [
        ("t1", "EVEREADY", "CLOSED_MANUAL", _no_leg("t1")),
        ("t2", "SULA", "CLOSED_MANUAL", []),          # predates leg recording
        ("t3", "AAA", "CLOSED_MANUAL", _leg("TGT", "t3"))])
    assert bf.main(["--commit"]) == 0
    got = _sources(p)
    assert got["EVEREADY"] is None, "a cancelled leg is not evidence"
    assert got["SULA"] is None, "silence must not be converted into a verdict"
    assert got["AAA"] == "OWN_TGT"


def test_an_ENTRY_leg_never_counts_as_the_thing_that_closed_it(
        tmp_path: Path, monkeypatch) -> None:
    """SULA's ENTRY leg IS COMPLETE. Only SL/TGT/EOD close a position."""
    p = _db(tmp_path, monkeypatch, [
        ("t1", "SULA", "CLOSED_MANUAL", [("o1", "ENTRY", "COMPLETE")])])
    assert bf.main(["--commit"]) == 0
    assert _sources(p) == {"SULA": None}


def test_it_only_touches_CLOSED_MANUAL(tmp_path: Path, monkeypatch) -> None:
    """A normally-closed trade is the live exit path's business, not this script's."""
    p = _db(tmp_path, monkeypatch, [
        ("t1", "AAA", "CLOSED", _leg("TGT", "t1")),
        ("t2", "BBB", "CLOSED_MANUAL", _leg("TGT", "t2"))])
    assert bf.main(["--commit"]) == 0
    assert _sources(p) == {"AAA": None, "BBB": "OWN_TGT"}


def test_exit_mechanism_is_left_NULL(tmp_path: Path, monkeypatch) -> None:
    """⚠️ No canonical order_type -> exit_mechanism mapping exists anywhere. Writing
    one here would create the second classifier W8 exists to retire."""
    p = _db(tmp_path, monkeypatch, [("t1", "AAA", "CLOSED_MANUAL", _leg("TGT", "t1"))])
    bf.main(["--commit"])
    store = StateStore(p)
    try:
        assert store.fetch_one(
            "SELECT exit_mechanism FROM trades WHERE trade_id='t1'"
        )["exit_mechanism"] is None
    finally:
        store.close()


# ── ⭐ reversible and idempotent -- as evidence, not as adjectives ─────────────

def test_the_backup_is_taken_before_the_write_and_is_real(
        tmp_path: Path, monkeypatch) -> None:
    """⭐ Structural, not procedural: a runbook step gets skipped, this cannot be.
    And it is checked -- a copy that opens but reports 0 trades is a FAILED copy."""
    p = _db(tmp_path, monkeypatch, [("t1", "AAA", "CLOSED_MANUAL", _leg("TGT", "t1"))])
    bf.main(["--commit"])
    backups = list((p.parent / "backups").glob("pre_w8_backfill_*.db"))
    assert len(backups) == 1
    pre = StateStore(backups[0])
    try:
        assert pre.fetch_one(
            "SELECT closure_source FROM trades WHERE trade_id='t1'"
        )["closure_source"] is None, "the backup must predate the write"
    finally:
        pre.close()


def test_rerunning_writes_nothing_more(tmp_path: Path, monkeypatch) -> None:
    """Idempotent by the COALESCE guard: the setter can only fill a NULL."""
    p = _db(tmp_path, monkeypatch, [("t1", "AAA", "CLOSED_MANUAL", _leg("TGT", "t1"))])
    bf.main(["--commit"])
    bf.main(["--commit"])
    assert _sources(p) == {"AAA": "OWN_TGT"}


def test_it_cannot_clobber_a_value_the_live_path_already_wrote(
        tmp_path: Path, monkeypatch) -> None:
    """⚠️ The live CHECK1 writes closure_source too. If it has since recorded
    EXTERNAL_UNATTRIBUTED on a row, this script must not overwrite that with its
    own reading -- the live path saw the broker; the backfill only sees history."""
    p = _db(tmp_path, monkeypatch, [("t1", "AAA", "CLOSED_MANUAL", _leg("TGT", "t1"))])
    store = StateStore(p)
    store.set_trade_closure_axes("t1", "EXTERNAL_UNATTRIBUTED")
    store.close()
    bf.main(["--commit"])
    assert _sources(p) == {"AAA": "EXTERNAL_UNATTRIBUTED"}


def test_revert_restores_exactly_the_rows_the_run_wrote(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """⭐ REVERSIBLE, DEMONSTRATED. The receipt drives the undo, so it touches the
    rows this run wrote and no others -- in particular not a row that already
    carried a value before it, which a blanket `SET closure_source = NULL` would
    have destroyed."""
    p = _db(tmp_path, monkeypatch, [
        ("t1", "AAA", "CLOSED_MANUAL", _leg("TGT", "t1")),
        ("t2", "BBB", "CLOSED_MANUAL", _leg("SL", "t2"))])
    store = StateStore(p)
    store.set_trade_closure_axes("t2", "EXTERNAL_UNATTRIBUTED")  # pre-existing
    store.close()

    bf.main(["--commit"])
    assert _sources(p) == {"AAA": "OWN_TGT", "BBB": "EXTERNAL_UNATTRIBUTED"}

    receipt = next(p.parent.glob("w8_backfill_receipt_*.json"))
    assert bf.main(["--revert-file", str(receipt)]) == 0
    assert _sources(p) == {"AAA": None, "BBB": "EXTERNAL_UNATTRIBUTED"}, (
        "the undo must not touch a row it did not write")


def test_the_receipt_records_what_was_written(tmp_path: Path, monkeypatch) -> None:
    p = _db(tmp_path, monkeypatch, [("t1", "AAA", "CLOSED_MANUAL", _leg("EOD", "t1"))])
    bf.main(["--commit"])
    receipt = next(p.parent.glob("w8_backfill_receipt_*.json"))
    data = json.loads(receipt.read_text(encoding="utf-8"))
    assert data["rows"] == [
        {"trade_id": "t1", "symbol": "AAA", "leg": "EOD",
         "wrote": "OWN_EOD", "was": None}]
