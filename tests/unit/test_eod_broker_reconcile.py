"""P1 (02-Jul): broker-authoritative EOD reconcile — verdict engine + persistence.

Fail-on-old / pass-on-new: today's eod_verify (local-only) VERIFIES over a naked broker
position; P1 emits ISSUES/UNVERIFIED. REQUIRED dims = positions/orders/broker-day-P&L
(unavailable ⇒ UNVERIFIED, no partial false-pass). MARGIN is supplemental + reliability-
gated (NOT_CHECKED at 15:58, never blocks VERIFIED).
"""
from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path

from core.state_store import StateStore
from scripts.eod_broker_reconcile import (
    BrokerState, LocalState, compute_verdict, persist, margin_reliable_now,
    _local_capital_snapshot,
    VERIFIED, ISSUES, UNVERIFIED, NOT_CHECKED,
)

_TOL = dict(pnl_tolerance=100.0, margin_tol_base=50.0, margin_tol_pct=0.10,
            human_order_allowance=5000.0)


def _v(broker, local, *, mode="LIVE", margin_reliable=False):
    return compute_verdict(broker, local, mode=mode, margin_reliable=margin_reliable, **_TOL)


# ── REQUIRED-dimension verdicts ────────────────────────────────────────────────
def test_clean_verified():
    v = _v(BrokerState(positions={}, open_order_count=0, day_realized=250.0, margin_net=None),
           LocalState(open_positions={}, pending_order_count=0, realized=250.0, invariant_ok=True))
    assert v.overall_status == VERIFIED and v.margin_status == NOT_CHECKED


def test_orphan_at_broker_issues():
    # broker holds a position local doesn't know (the naked/A-1 case) → ISSUES.
    v = _v(BrokerState(positions={"RELIANCE": 50}, open_order_count=0, day_realized=0.0),
           LocalState(open_positions={}, realized=0.0))
    assert v.overall_status == ISSUES and v.positions_status == ISSUES


def test_missing_at_broker_issues():
    v = _v(BrokerState(positions={}, open_order_count=0, day_realized=0.0),
           LocalState(open_positions={"INFY": 10}, realized=0.0))
    assert v.overall_status == ISSUES and v.positions_status == ISSUES


def test_pnl_mismatch_issues():
    v = _v(BrokerState(positions={}, open_order_count=0, day_realized=-500.0),
           LocalState(open_positions={}, realized=-100.0))  # |−500 − −100| = 400 > 100
    assert v.overall_status == ISSUES and v.pnl_status == ISSUES


def test_broker_unreachable_unverified():
    v = _v(BrokerState(), LocalState(open_positions={"X": 5}))  # all None → required down
    assert v.overall_status == UNVERIFIED
    assert v.positions_status == UNVERIFIED and v.pnl_status == UNVERIFIED


def test_processdown_is_unverified_via_unreachable():
    # process-down at 15:17: the standalone job's broker query yields no data (BrokerState())
    # → UNVERIFIED (never a local-only false VERIFIED). Same path as unreachable.
    v = _v(BrokerState(), LocalState())
    assert v.overall_status == UNVERIFIED


# ── point 7 (revised) — REQUIRED down ⇒ UNVERIFIED; SUPPLEMENTAL gated ⇒ VERIFIED ──
def test_A_required_pnl_down_is_unverified():
    # positions OK + orders OK + margin N/A + broker DAY-P&L UNAVAILABLE ⇒ UNVERIFIED.
    v = _v(BrokerState(positions={}, open_order_count=0, day_realized=None, margin_net=None),
           LocalState(open_positions={}, pending_order_count=0))
    assert v.overall_status == UNVERIFIED and v.pnl_status == UNVERIFIED


def test_B_margin_unavailable_does_not_block_verified():
    # everything clean + margin UNAVAILABLE at 15:58 ⇒ margin NOT_CHECKED, overall VERIFIED.
    v = _v(BrokerState(positions={}, open_order_count=0, day_realized=0.0, margin_net=None),
           LocalState(open_positions={}, pending_order_count=0, realized=0.0),
           margin_reliable=False)
    assert v.margin_status == NOT_CHECKED and v.overall_status == VERIFIED


# ── contradiction / ledger / margin ────────────────────────────────────────────
def test_contradiction_with_15_45_reconcile_positions():
    # broker matches local, BUT reconcile_positions(15:45) flagged a non-OK row → NOT VERIFIED.
    v = _v(BrokerState(positions={}, open_order_count=0, day_realized=0.0),
           LocalState(open_positions={}, realized=0.0, position_recon_issue=True))
    assert v.overall_status == ISSUES and v.positions_status == ISSUES


def test_ledger_invariant_broken_issues():
    v = _v(BrokerState(positions={}, open_order_count=0, day_realized=0.0),
           LocalState(open_positions={}, realized=0.0, invariant_ok=False))
    assert v.ledger_status == ISSUES and v.overall_status == ISSUES


def test_margin_reliable_drift_issues():
    # Δ = |100k − 130k| = 30k > tol = max(50, 130k*10%=13k) → ISSUES.
    v = _v(BrokerState(positions={}, open_order_count=0, day_realized=0.0, margin_net=100_000.0),
           LocalState(open_positions={}, realized=0.0, total=130_000.0),
           margin_reliable=True)
    assert v.margin_status == ISSUES and v.overall_status == ISSUES


def test_margin_reliable_within_tolerance_verified():
    v = _v(BrokerState(positions={}, open_order_count=0, day_realized=0.0, margin_net=100_000.0),
           LocalState(open_positions={}, realized=0.0, total=105_000.0),  # Δ5k < 100k*10%
           margin_reliable=True)
    assert v.margin_status == VERIFIED and v.overall_status == VERIFIED


# ── parity — paper SELF_CONSISTENCY ────────────────────────────────────────────
def test_paper_self_consistency_labeled():
    local = LocalState(open_positions={"TCS": 10}, pending_order_count=1, realized=42.0)
    broker = BrokerState(positions=dict(local.open_positions),
                         open_order_count=local.pending_order_count, day_realized=local.realized)
    v = _v(broker, local, mode="PAPER")
    assert v.self_consistency is True and v.mode == "PAPER" and v.overall_status == VERIFIED


def test_margin_reliable_now_false_at_1558():
    assert margin_reliable_now(datetime(2026, 7, 2, 15, 58)) is False
    assert margin_reliable_now(datetime(2026, 7, 2, 11, 0)) is True


# ── persistence: column-bug-fixed + shadow mismatch ────────────────────────────
def test_persist_writes_verdict_and_pnl_reconciliation_correct_columns():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(Path(tmp) / "t.db")
        local = LocalState(open_positions={}, realized=-100.0, total=0.0)
        broker = BrokerState(positions={}, open_order_count=0, day_realized=-500.0, margin_net=None)
        v = _v(broker, local)   # pnl mismatch → ISSUES
        # eod_verify said VERIFIED (local-only, over the divergence) → mismatch=1
        persist(store, "2026-07-02", v, authoritative=False,
                eod_verify_status="VERIFIED", local=local, broker=broker)
        row = store.get_eod_broker_reconciliation("2026-07-02")
        assert row["overall_status"] == ISSUES
        assert row["mismatch"] == 1                     # P1 disagrees with eod_verify's VERIFIED
        assert row["margin_status"] == NOT_CHECKED
        # pnl_reconciliation written with the CORRECT columns → variance actually computes
        pr = store.get_pnl_reconciliation("2026-07-02")
        assert pr is not None
        assert abs(pr["broker_pnl"] - (-500.0)) < 1e-6 and abs(pr["system_pnl"] - (-100.0)) < 1e-6
        assert abs(pr["variance"] - 400.0) < 1e-6       # fail-on-old: eod_verify's query = dead 0.0
        store.close()


class TestLocalCapitalSnapshot:
    """Fix 3 (15-Jul): _local_capital_snapshot queried `ORDER BY id`, but fm_ledger's PK is
    `ledger_id` -> "no such column: id" -> caught -> a SILENT 0.0 total (wrong capital
    snapshot). Harmless in shadow (margin NOT_CHECKED) but wrong once margin-checking is
    authoritative. Fails on old code (returns 0.0), passes on the fix (real latest balance)."""

    def test_returns_latest_ledger_balance(self, tmp_path):
        store = StateStore(db_path=tmp_path / "t.db")
        try:
            with store.transaction() as cur:
                cur.execute(
                    "INSERT INTO fm_ledger (ts, entry_type, amount, bucket, balance_before, "
                    "balance_after) VALUES (?, 'INIT', 0.0, 'intraday', 0.0, 10000.0)",
                    ("2026-05-31T10:00:00+05:30",))
                cur.execute(
                    "INSERT INTO fm_ledger (ts, entry_type, amount, bucket, balance_before, "
                    "balance_after) VALUES (?, 'RELEASE_USED', -100.0, 'intraday', 10000.0, 9876.5)",
                    ("2026-05-31T15:00:00+05:30",))
            ok, total = _local_capital_snapshot(store, logging.getLogger("test"))
        finally:
            store.close()
        assert ok is True
        # latest row (ledger_id DESC) balance_after — NOT the silent 0.0 the old `id` query gave
        assert abs(total - 9876.5) < 1e-6

    def test_empty_ledger_returns_zero(self, tmp_path):
        store = StateStore(db_path=tmp_path / "t.db")
        try:
            ok, total = _local_capital_snapshot(store, logging.getLogger("test"))
        finally:
            store.close()
        assert ok is True and total == 0.0
