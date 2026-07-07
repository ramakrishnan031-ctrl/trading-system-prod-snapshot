"""Wave-5 · Terminal-state write guard (trigger keystone) + close_trade CAS (D-1).

Validates the hybrid built on 2026-07-07 (Audit-B Phase-4 rec #5):
  1. A SQLite BEFORE-UPDATE-OF-status trigger (trg_trades_terminal_status_guard)
     forbids CHANGING trades.status once the row is terminal (CLOSED /
     CLOSED_MANUAL / FAILED / CANCELLED / REJECTED*) — a data-layer keystone that
     catches EVERY writer, including raw module SQL and any future writer.
  2. close_trade() is now an atomic compare-and-swap (WHERE status IN the live
     set + rowcount): exactly one finalizer wins and releases capital once; a
     loser gets the ValueError the caller already maps to "skip release" (D-1).

Groups:
  A  illegal transitions REJECTED (+ a headline RED->GREEN that drops the trigger
     to reproduce the pre-fix reopen vulnerability).
  B  every LEGAL edge still ALLOWED — the over-strictness guard (H-2 EXITING->CLOSED,
     EXITING->OPEN revert, EXITING->CLOSED_MANUAL, the entry/recovery edges, the
     exit-financial backfills onto a terminal row, and idempotent same-status).
  C  the D-1 concurrent-finalizer race: exactly one winner, capital released once.
  D  regression is covered by running the affected suites (state_store, order_*,
     kill_switch, reconciler, eod, structure_exit) — see the build report.

Real collaborators: a real StateStore (schema-backed tmp DB — the ACTUAL trigger
runs in the real SQLite engine), a real OrderManager (real create_trade /
record_entry_fill / close_trade / update_trade_status), and the real guarded DAO
methods (mark_trade_manually_closed / mark_trade_closed_gtt / revert_exiting_to_open
/ adopt_recovery_trade_to_open / fail_recovery_trade / record_*_financials). The
threaded race uses real thread-local connections + the store's real BEGIN IMMEDIATE
serialization. Simulated (not mocked): Group C models only the CALLER'S release
decision with a counter (we don't build order_placer+fund_manager); Group A's raw
writer is a faithful copy of the U4-U9 module statement (the guard is at the DB
layer, so the statement — not the class around it — is what the trigger sees).

Run: python -m pytest tests/unit/test_terminal_state_write_guard.py -v
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from core.state_store import StateStore
from orders.order_manager import OrderManager

_SCHEMA = Path(__file__).parent.parent.parent / "core" / "schema.sql"

TERMINAL = ["CLOSED", "CLOSED_MANUAL", "FAILED", "CANCELLED", "REJECTED_PRICE_DRIFT"]
MODES = ["PAPER", "LIVE"]
_TS = "2026-07-07T10:00:00+05:30"


# ── real-collaborator harness ────────────────────────────────────────────────
def _store(tmp_path: Path, name: str = "tsg.db") -> StateStore:
    return StateStore(tmp_path / name, _SCHEMA)


def _om(store: StateStore) -> OrderManager:
    return OrderManager(state_store=store, logger=logging.getLogger("test_tsg"))


def _seed_signal(store: StateStore, signal_id: str) -> None:
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, 'RELIANCE', 'SCAN', 'vwap_bounce_long', ?, ?, ?,
                       'TRADED', ?, '2026-07-07', 100.0)""",
            (signal_id, _TS, _TS, _TS, f"fp_{signal_id}"),
        )


def _new_pending(store: StateStore, om: OrderManager, mode: str = "LIVE") -> str:
    """A fresh PENDING_FILL trade (with parent signal) tagged with `mode`."""
    sid = f"sig_{uuid.uuid4().hex[:8]}"
    _seed_signal(store, sid)
    return om.create_trade(
        signal_id=sid, symbol="RELIANCE", direction="LONG",
        strategy="vwap_bounce_long", sector=None, qty=10,
        entry_target_price=2500.0, sl_initial=2475.0, tgt_initial=2550.0,
        order_protocol="LIMIT_TRIPLE", margin_reserved=5000.0, risk_amount=250.0,
        mode=mode,
    )


def _new_open(store: StateStore, om: OrderManager, mode: str = "LIVE") -> str:
    tid = _new_pending(store, om, mode)
    om.record_entry_fill(trade_id=tid, avg_fill_price=2500.0, qty_filled=10,
                         filled_at=_TS)
    return tid


def _drive_to(store: StateStore, om: OrderManager, status: str,
              mode: str = "LIVE") -> str:
    """Return a trade_id parked in `status`, reached ONLY via legal edges so the
    seeding itself never trips the guard."""
    if status == "CLOSED":
        tid = _new_open(store, om, mode)
        om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                       exit_reason="TGT_HIT", gross_pnl=500.0, charges=25.0)
    elif status == "CLOSED_MANUAL":
        tid = _new_open(store, om, mode)
        store.mark_trade_manually_closed(tid)
    elif status in ("FAILED", "CANCELLED", "REJECTED_PRICE_DRIFT"):
        tid = _new_pending(store, om, mode)          # PENDING_FILL (non-terminal)
        om.update_trade_status(tid, status)          # legal non-terminal -> terminal
    else:
        raise AssertionError(f"unsupported seed status {status!r}")
    assert _status(store, tid) == status
    return tid


def _row(store: StateStore, tid: str):
    return store.fetch_one("SELECT * FROM trades WHERE trade_id = ?", (tid,))


def _status(store: StateStore, tid: str) -> str:
    return _row(store, tid)["status"]


# ═════════════════════════════════════════════════════════════════════════════
# GROUP A — illegal transitions REJECTED (+ RED->GREEN baseline)
# ═════════════════════════════════════════════════════════════════════════════

def test_A_red_green_generic_setter_reopen(tmp_path: Path) -> None:
    """Headline RED->GREEN. With the trigger, a raw generic-setter reopen of a
    CLOSED trade is REJECTED and the row is untouched. Drop the trigger (== the
    pre-fix HEAD) and the SAME write SUCCEEDS — proving the trigger is the
    load-bearing guard, not incidental behaviour."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _drive_to(store, om, "CLOSED")

    # GREEN — trigger present: the illegal reopen is aborted at the DB layer.
    with pytest.raises(sqlite3.IntegrityError):
        om.update_trade_status(tid, "OPEN")
    assert _status(store, tid) == "CLOSED"

    # RED — simulate pre-fix HEAD by dropping the trigger: the identical write now
    # reopens a terminal trade (the exact Audit-B rec #5 vulnerability).
    with store.transaction() as cur:
        cur.execute("DROP TRIGGER trg_trades_terminal_status_guard")
    om.update_trade_status(tid, "OPEN")
    assert _status(store, tid) == "OPEN"
    store.close()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("terminal", TERMINAL)
def test_A_reopen_rejected_all_terminals(tmp_path: Path, terminal: str,
                                         mode: str) -> None:
    """Illegal-rejected matrix: reopening ANY terminal state to OPEN via the raw
    generic setter raises IntegrityError and leaves the row untouched — identical
    in both modes (parity)."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _drive_to(store, om, terminal, mode)
    with pytest.raises(sqlite3.IntegrityError):
        om.update_trade_status(tid, "OPEN")
    assert _status(store, tid) == terminal
    store.close()


@pytest.mark.parametrize("terminal", TERMINAL)
def test_A_raw_module_sql_writer_rejected(tmp_path: Path, terminal: str) -> None:
    """The keystone catches a RAW module-level UPDATE — the exact statement
    kill_switch / structure_exit / order_placer / eod / reconciler issue (U4-U9)
    — on a terminal row, proving it covers EVERY writer including future raw SQL,
    not only the DAO methods."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _drive_to(store, om, terminal)
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as cur:
            cur.execute(
                "UPDATE trades SET status = 'EXITING', updated_at = ? "
                "WHERE trade_id = ?",
                (_TS, tid),
            )
    assert _status(store, tid) == terminal
    store.close()


def test_A_record_entry_fill_on_terminal_rejected(tmp_path: Path) -> None:
    """U2: an entry-fill event arriving on an already-terminal (FAILED) row is
    blocked (record_entry_fill sets status='OPEN')."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _drive_to(store, om, "FAILED")
    with pytest.raises(sqlite3.IntegrityError):
        om.record_entry_fill(trade_id=tid, avg_fill_price=2500.0, qty_filled=10,
                             filled_at=_TS)
    assert _status(store, tid) == "FAILED"
    store.close()


@pytest.mark.parametrize("terminal", TERMINAL)
def test_A_close_trade_on_terminal_is_valueerror_not_trigger(tmp_path: Path,
                                                             terminal: str) -> None:
    """U3/D-1: close_trade on a terminal row is handled IN-BAND by the CAS
    (rowcount 0 -> ValueError, the caller's 'already closed -> skip release'
    signal) and NEVER trips the trigger — its WHERE excludes terminal rows before
    the trigger could fire."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _drive_to(store, om, terminal)
    with pytest.raises(ValueError, match="closeable state"):
        om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                       exit_reason="SL_HIT", gross_pnl=-100.0, charges=10.0)
    assert _status(store, tid) == terminal
    store.close()


# ═════════════════════════════════════════════════════════════════════════════
# GROUP B — every LEGAL edge still ALLOWED (the over-strictness guard)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mode", MODES)
def test_B_entry_lifecycle_allowed(tmp_path: Path, mode: str) -> None:
    """PENDING_FILL -> PENDING -> UNKNOWN_IN_FLIGHT -> OPEN all pass."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _new_pending(store, om, mode)
    assert _status(store, tid) == "PENDING_FILL"
    om.update_trade_status(tid, "PENDING")
    assert _status(store, tid) == "PENDING"
    om.update_trade_status(tid, "UNKNOWN_IN_FLIGHT")
    assert _status(store, tid) == "UNKNOWN_IN_FLIGHT"
    om.record_entry_fill(trade_id=tid, avg_fill_price=2500.0, qty_filled=10,
                         filled_at=_TS)
    assert _status(store, tid) == "OPEN"
    store.close()


def test_B_recovery_adopt_and_fail_allowed(tmp_path: Path) -> None:
    """A-1/E-1 recovery edges: UNKNOWN_IN_FLIGHT -> OPEN (adopt) and
    PENDING -> FAILED (fail_recovery_trade), both guarded DAO methods."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _new_pending(store, om)
    om.update_trade_status(tid, "UNKNOWN_IN_FLIGHT")
    assert store.adopt_recovery_trade_to_open(
        tid, avg_fill_price=2500.0, qty_filled=10, filled_at=_TS) is True
    assert _status(store, tid) == "OPEN"

    tid2 = _new_pending(store, om)
    om.update_trade_status(tid2, "PENDING")
    assert store.fail_recovery_trade(tid2) is True
    assert _status(store, tid2) == "FAILED"
    store.close()


@pytest.mark.parametrize("mode", MODES)
def test_B_open_to_exiting_allowed(tmp_path: Path, mode: str) -> None:
    """OPEN -> EXITING (kill_switch / structure_exit / emergency exit path)."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _new_open(store, om, mode)
    om.update_trade_status(tid, "EXITING")
    assert _status(store, tid) == "EXITING"
    store.close()


@pytest.mark.parametrize("mode", MODES)
def test_B_H2_exiting_to_closed_allowed(tmp_path: Path, mode: str) -> None:
    """*H-2*: an EXITING trade (emergency/HARD_KILL flatten in flight) is closed
    by its exit fill with REAL costs — must remain allowed."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _new_open(store, om, mode)
    om.update_trade_status(tid, "EXITING")
    row = om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                         exit_reason="SL_HIT", gross_pnl=-250.0, charges=25.0)
    assert row["status"] == "CLOSED"
    assert abs(row["net_pnl"] - (-275.0)) < 1e-6
    store.close()


def test_B_exiting_to_open_revert_allowed(tmp_path: Path) -> None:
    """EXITING -> OPEN (revert_exiting_to_open, Task-4 still-has-position)."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _new_open(store, om)
    om.update_trade_status(tid, "EXITING")
    assert store.revert_exiting_to_open(tid) is True
    assert _status(store, tid) == "OPEN"
    store.close()


def test_B_exiting_to_closed_manual_allowed(tmp_path: Path) -> None:
    """EXITING -> CLOSED_MANUAL (stuck-EXITING finalize via CHECK1)."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _new_open(store, om)
    om.update_trade_status(tid, "EXITING")
    assert store.mark_trade_manually_closed(tid) is True
    assert _status(store, tid) == "CLOSED_MANUAL"
    store.close()


def test_B_open_to_closed_gtt_allowed(tmp_path: Path) -> None:
    """OPEN -> CLOSED (GTT exit, mark_trade_closed_gtt)."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _new_open(store, om)
    assert store.mark_trade_closed_gtt(tid, "GTT_EXIT") is True
    assert _status(store, tid) == "CLOSED"
    store.close()


@pytest.mark.parametrize("mode", MODES)
def test_B_financial_backfill_on_terminal_allowed(tmp_path: Path, mode: str) -> None:
    """*The crux*: the guard keys on the status CHANGING, not on any write to a
    terminal row. The exit-financial backfills (which do NOT touch status) MUST
    succeed on a CLOSED_MANUAL / CLOSED(GTT) row."""
    store = _store(tmp_path)
    om = _om(store)

    tid = _new_open(store, om, mode)
    store.mark_trade_manually_closed(tid)
    assert store.record_manual_close_financials(
        tid, exit_price=2540.0, net_pnl=400.0) is True
    r = _row(store, tid)
    assert r["status"] == "CLOSED_MANUAL"
    assert r["exit_price"] == 2540.0 and r["net_pnl"] == 400.0

    tid2 = _new_open(store, om, mode)
    store.mark_trade_closed_gtt(tid2, "GTT_EXIT")
    assert store.record_gtt_close_financials(
        tid2, exit_price=2530.0, net_pnl=300.0) is True
    r2 = _row(store, tid2)
    assert r2["status"] == "CLOSED" and r2["exit_price"] == 2530.0
    store.close()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("terminal", TERMINAL)
def test_B_idempotent_same_status_write_allowed(tmp_path: Path, terminal: str,
                                                mode: str) -> None:
    """A same-status write to a terminal row (NEW == OLD) passes: the trigger's
    CASE is NULL when NEW.status == OLD.status, so it does not over-fire."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _drive_to(store, om, terminal, mode)
    om.update_trade_status(tid, terminal)   # must NOT raise
    assert _status(store, tid) == terminal
    store.close()


# ═════════════════════════════════════════════════════════════════════════════
# GROUP C — D-1 concurrent-finalizer race (exactly one winner, release once)
# ═════════════════════════════════════════════════════════════════════════════

def test_C_sequential_double_finalize_single_release(tmp_path: Path) -> None:
    """D-1 (deterministic): two finalizers on one OPEN trade — the first wins
    (returns a CLOSED row -> caller releases), the second gets ValueError ->
    caller skips release. Modelled capital-release count == 1."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _new_open(store, om)

    releases = {"n": 0}

    def _finalize() -> None:
        try:
            om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                           exit_reason="TGT_HIT", gross_pnl=500.0, charges=25.0)
            releases["n"] += 1          # caller releases ONLY on a clean return
        except ValueError:
            pass                        # "already closed -> skip release"

    _finalize()
    _finalize()
    assert releases["n"] == 1
    assert _status(store, tid) == "CLOSED"
    store.close()


@pytest.mark.parametrize("mode", MODES)
def test_C_threaded_race_exactly_one_winner(tmp_path: Path, mode: str) -> None:
    """D-1 (real race): two barrier-synchronised threads finalize the SAME OPEN
    trade. BEGIN IMMEDIATE serialises the CAS so EXACTLY one wins (rowcount 1)
    and one loses (ValueError). Each thread uses its own thread-local
    connection (check_same_thread=True)."""
    store = _store(tmp_path)
    om = _om(store)
    tid = _new_open(store, om, mode)

    barrier = threading.Barrier(2)
    outcomes: list = [None, None]

    def _worker(i: int) -> None:
        barrier.wait()                  # release both threads together
        try:
            om.close_trade(trade_id=tid, exit_price=2550.0, exit_qty=10,
                           exit_reason="TGT_HIT", gross_pnl=500.0, charges=25.0)
            outcomes[i] = "won"
        except ValueError:
            outcomes[i] = "lost"
        except Exception as exc:        # noqa: BLE001 — surface anything unexpected
            outcomes[i] = f"error:{type(exc).__name__}"

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["lost", "won"], outcomes
    assert _status(store, tid) == "CLOSED"
    store.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
