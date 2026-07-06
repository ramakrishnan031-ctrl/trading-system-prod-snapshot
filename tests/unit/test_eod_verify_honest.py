"""
tests/unit/test_eod_verify_honest.py

Audit-B Phase-8 (eod_verify false-verify) + M-SC1 (exit 0 on ISSUES) — HONEST-FIX.

The P&L leg queried NON-EXISTENT columns (system_net_pnl/broker_net_pnl; the real
pnl_reconciliation columns are broker_pnl/system_pnl/variance) and swallowed the
OperationalError into pnl_variance=0.0 → a spurious "no variance" → a FALSE
"VERIFIED" that never actually checked P&L. And main() always exited 0, so cron
couldn't catch ISSUES_FOUND.

These tests drive the REAL eod_verify against a REAL StateStore (core/schema.sql),
seeding real trades/orders/pnl_reconciliation rows — no mocks in the assertion path.

Real collaborators:  scripts.eod_verify.run_eod_verification / _evaluate_pnl / main,
                     the REAL StateStore (trades/orders/pnl_reconciliation/
                     eod_verification against core/schema.sql).
Simulated:           none in the assertion path (telegram/heartbeat are best-effort
                     and skipped without a notifier env).

RED/GREEN: test_no_false_verified_live_no_feeder + test_real_columns_divergence_detected
+ test_exit_code_nonzero_on_issues FAIL against the current facade (VERIFIED / exit 0)
and PASS after the honest-fix. Paper-N/A + genuine-VERIFIED pass.

Run: python -m pytest tests/unit/test_eod_verify_honest.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore
from scripts.eod_verify import run_eod_verification, main

_DATE = "2026-05-31"

_TRADE_OPEN_SQL = (
    "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, qty_planned, "
    "entry_target_price, sl_initial, tgt_initial, order_protocol, margin_reserved, "
    "risk_amount, status, created_at, updated_at) VALUES "
    "('t1', 's1', 'RELIANCE', 'LONG', 'fp', 100, 2500, 2450, 2600, 'LIMIT_TRIPLE', 10000, 5000, "
    "'OPEN', '2026-05-31T10:00:00', '2026-05-31T10:00:00')"
)


def _store(tmp_path) -> StateStore:
    s = StateStore(db_path=tmp_path / "eodv.db")
    s._get_conn().execute("PRAGMA foreign_keys = OFF")  # seed without full FK parents
    return s


def _seed_pnl(store: StateStore, *, broker_pnl, system_pnl, variance) -> None:
    store.upsert_pnl_reconciliation(
        date=_DATE, broker_pnl=broker_pnl, system_pnl=system_pnl, variance=variance,
        status=("OK" if (variance or 0) <= 100 else "VARIANCE"), notes=None,
        created_at="2026-05-31T15:58:00",
    )


# ── Test 1 — NO false VERIFIED when the live P&L feeder never ran (core) ──────

def test_no_false_verified_live_no_feeder(tmp_path) -> None:
    store = _store(tmp_path)
    r = run_eod_verification(store, _DATE, is_paper=False)   # LIVE, no pnl_reconciliation row
    # Old facade: swallowed 0.0 -> "VERIFIED". Honest: P&L NOT CHECKED -> PENDING.
    assert r["status"] == "PENDING"
    assert r["pnl_status"] == "NOT_CHECKED"
    assert r["status"] != "VERIFIED"
    print("  OK eod-verify T1: live + no feeder -> PENDING (P&L NOT CHECKED), no false VERIFIED")


# ── Test 2 — real columns: a genuine divergence is DETECTED (not swallowed) ───

def test_real_columns_divergence_detected(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_pnl(store, broker_pnl=1250.0, system_pnl=1000.0, variance=250.0)  # > Rs100 tol
    r = run_eod_verification(store, _DATE, is_paper=False)
    # Old wrong-column query -> OperationalError -> swallowed 0.0 -> missed -> VERIFIED.
    # Honest: reads `variance`=250 -> DIVERGENCE -> ISSUES_FOUND.
    assert r["status"] == "ISSUES_FOUND"
    assert r["pnl_status"] == "DIVERGENCE"
    assert r["pnl_variance"] == pytest.approx(250.0)
    print("  OK eod-verify T2: real columns -> Rs250 divergence DETECTED (ISSUES_FOUND)")


# ── Test 3 — paper: P&L N/A (never a false FAIL, never a broker-VERIFIED) ─────

def test_paper_pnl_na(tmp_path) -> None:
    store = _store(tmp_path)
    r = run_eod_verification(store, _DATE, is_paper=True)   # PAPER, clean
    assert r["status"] == "VERIFIED"       # positions/orders clean; P&L N/A is complete for paper
    assert r["pnl_status"] == "N/A"
    assert r["mode"] == "PAPER"
    print("  OK eod-verify T3: paper -> P&L N/A (verified, not a false FAIL/broker-VERIFIED)")


# ── Test 4 — exit code (M-SC1): ISSUES_FOUND exits NON-ZERO ───────────────────

def test_exit_code_nonzero_on_issues(tmp_path) -> None:
    store = _store(tmp_path)
    with store.transaction() as cur:
        cur.execute(_TRADE_OPEN_SQL)       # an OPEN position at EOD = a real issue
    store.close()
    db = tmp_path / "eodv.db"
    rc = main(["--date", _DATE, "--db", str(db)])
    assert rc == 2, "ISSUES_FOUND must exit non-zero (M-SC1); was always 0"
    print("  OK eod-verify T4: ISSUES_FOUND -> exit 2 (M-SC1) [RED: was 0]")


def test_exit_code_zero_on_pending(tmp_path) -> None:
    store = _store(tmp_path)   # clean live day, no feeder -> PENDING
    store.close()
    rc = main(["--date", _DATE, "--db", str(tmp_path / "eodv.db")])
    assert rc == 0            # PENDING (known gap) is exit 0; honesty is in the status
    print("  OK eod-verify T4b: PENDING -> exit 0 (known gap, not a job failure)")


# ── Test 5 — genuine VERIFIED preserved (matched broker P&L) ──────────────────

def test_genuine_verified_preserved(tmp_path) -> None:
    store = _store(tmp_path)
    _seed_pnl(store, broker_pnl=1000.0, system_pnl=1000.0, variance=0.0)  # matched
    r = run_eod_verification(store, _DATE, is_paper=False)
    assert r["status"] == "VERIFIED"
    assert r["pnl_status"] == "VERIFIED"
    assert r["pnl_variance"] == pytest.approx(0.0)
    print("  OK eod-verify T5: live + matched broker P&L -> genuine VERIFIED preserved")


if __name__ == "__main__":
    import tempfile
    for fn in (test_no_false_verified_live_no_feeder, test_real_columns_divergence_detected,
               test_paper_pnl_na, test_exit_code_nonzero_on_issues,
               test_exit_code_zero_on_pending, test_genuine_verified_preserved):
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
    print("\nAll eod_verify honest-fix tests passed.")
