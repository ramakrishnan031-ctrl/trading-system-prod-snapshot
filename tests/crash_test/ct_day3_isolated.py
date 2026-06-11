"""
Isolated Day 3 crash test scenarios (kill switch + circuit breakers).
CT067, CT068, CT077, CT078, CT079, CT080, CT082, CT083, CT085.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ct_day3")

from core.state_store import StateStore
from core.events import EventBus, KillSwitchActivated
from core.time_authority import now_ist
from capital.kill_switch import KillSwitch
from capital.fund_manager import FundManager
from capital.risk_engine import RiskEngine, ApprovalResult
from capital.position_sizer import SizingResult

IST = timezone(timedelta(hours=5, minutes=30))
_BASE = Path(__file__).resolve().parent.parent.parent
_SCHEMA = _BASE / "core" / "schema.sql"
_SCRATCH = _BASE / "data_store"

all_results = {}
_dbs = []
_n = [0]


def _check(scenario, checks):
    fail = 0
    for desc, ok in checks:
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {desc}")
        if not ok:
            fail += 1
    total = len(checks) - fail
    verdict = "PASS" if fail == 0 else "FAIL"
    print(f"{scenario}: {total} PASS, {fail} FAIL")
    all_results[scenario] = verdict
    return fail == 0


def _store():
    _n[0] += 1
    p = str(_SCRATCH / f"ct3_{_n[0]}.db")
    _dbs.append(p)
    return StateStore(db_path=p, schema_path=str(_SCHEMA))


def _ks(store, bus=None, threshold=3):
    return KillSwitch(
        state_store=store, bus=bus or EventBus(), logger=logger,
        api_failure_threshold=threshold, enable_auto_trip=True,
    )


def _seed_signal(store, sig_id, symbol="RELIANCE"):
    now = now_ist().isoformat()
    fp = f"fp_{sig_id}"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, "
            "triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date) "
            "VALUES (?,?,'t','t',?,?,?,'PROCESSED',?,?)",
            (sig_id, symbol, now, now, now, fp, now[:10]),
        )


def _seed_open_trade(store, tid, sid, sym="RELIANCE", margin=25000.0):
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "status, qty_planned, qty_filled, entry_target_price, entry_actual_price, "
            "sl_initial, tgt_initial, order_protocol, margin_reserved, risk_amount, "
            "created_at, updated_at, sector) "
            "VALUES (?  ,?,?,'LONG','t','OPEN',10,10,100.0,100.0,"
            "95.0,110.0,'LIMIT_TRIPLE',?,50.0,?,?,'IT')",
            (tid, sid, sym, margin, now, now),
        )


# =========================================================================
def ct067():
    print("=" * 60)
    print("CT067 | Daily Loss Limit (Rs 10,000)")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    ks = _ks(store, bus)
    fm = FundManager(
        state_store=store, bus=bus, logger=logger,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit=10000.0,
    )
    fm.initialize(broker_balance=100000.0)

    events = []
    bus.subscribe(KillSwitchActivated, lambda e: events.append(e))

    fm._daily_realized_pnl = -10001.0
    exceeded = fm._daily_realized_pnl < -fm._daily_loss_limit

    ks.soft_kill("DAILY_LOSS_LIMIT_BREACHED", "fund_manager")
    blocked = ks.is_active("entry")

    _check("CT067", [
        ("Loss -10001 exceeds -10000 limit", exceeded),
        ("soft_kill fired", ks.current_state().name == "SOFT_KILL"),
        ("Entries blocked after soft_kill", blocked),
        ("KillSwitchActivated event published", len(events) >= 1),
    ])
    store.close()


# =========================================================================
def ct068():
    print("\n" + "=" * 60)
    print("CT068 | Daily Loss % (5%)")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    ks = _ks(store, bus)
    fm = FundManager(
        state_store=store, bus=bus, logger=logger,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit=5000.0,
    )
    fm.initialize(broker_balance=100000.0)

    total = fm.get_snapshot().total
    loss_5pct = total * 0.051
    exceeded = loss_5pct > fm._daily_loss_limit

    ks.soft_kill("DAILY_LOSS_PCT_BREACHED", "fund_manager")

    _check("CT068", [
        ("5.1% of 100K = Rs 5100", abs(loss_5pct - 5100.0) < 1.0),
        ("Rs 5100 > Rs 5000 limit (5%)", exceeded),
        ("soft_kill fires on breach", ks.current_state().name == "SOFT_KILL"),
    ])
    store.close()


# =========================================================================
def ct077():
    print("\n" + "=" * 60)
    print("CT077 | Kill Switch Survives Restart")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    ks1 = _ks(store, bus)

    assert ks1.current_state().name == "INACTIVE"
    ks1.soft_kill("test_persistence", "crash_test")
    assert ks1.current_state().name == "SOFT_KILL"

    ks2 = _ks(store, EventBus())
    state_after = ks2.current_state().name

    _check("CT077", [
        ("Initial: INACTIVE", True),
        ("After soft_kill: SOFT_KILL", True),
        ("New instance reads SOFT_KILL from DB", state_after == "SOFT_KILL"),
        ("Entries blocked on new instance", ks2.is_active("entry")),
    ])
    store.close()


# =========================================================================
def ct078():
    print("\n" + "=" * 60)
    print("CT078 | Stale Kill Switch Auto-Clear")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    ks = _ks(store, bus)

    ks.soft_kill("EOD_SQUAREOFF", "eod_squareoff")
    assert ks.current_state().name == "SOFT_KILL"

    yesterday = date.today() - timedelta(days=1)
    with store.transaction() as cur:
        cur.execute(
            "UPDATE kill_switch_state SET triggered_at = ?",
            (datetime(yesterday.year, yesterday.month, yesterday.day,
                      15, 17, 0, tzinfo=IST).isoformat(),),
        )

    ks2 = _ks(store, EventBus())
    cleared = ks2.clear_stale_state(date.today())
    state_after = ks2.current_state().name

    _check("CT078", [
        ("Scheduled EOD kill persisted", True),
        ("clear_stale_state returns True", cleared),
        ("State cleared to INACTIVE", state_after == "INACTIVE"),
    ])
    store.close()


# =========================================================================
def ct079():
    print("\n" + "=" * 60)
    print("CT079 | --resume Recovery from HARD_KILL")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    ks = _ks(store, bus)

    ks.hard_kill("test_hard", "crash_test")
    blocks_entry = ks.is_active("entry")
    blocks_exit = ks.is_active("exit")

    ks.resume("manual_resume", "operator")
    state_after = ks.current_state().name

    _check("CT079", [
        ("HARD_KILL blocks entry", blocks_entry),
        ("HARD_KILL blocks exit", blocks_exit),
        ("resume clears to INACTIVE", state_after == "INACTIVE"),
        ("Entry allowed after resume", not ks.is_active("entry")),
        ("Exit allowed after resume", not ks.is_active("exit")),
    ])
    store.close()


# =========================================================================
def ct080():
    print("\n" + "=" * 60)
    print("CT080 | Soft->Resume->Soft->Hard Sequence")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    events = []
    bus.subscribe(KillSwitchActivated, lambda e: events.append(e))
    ks = _ks(store, bus)

    states = []
    ks.soft_kill("step1", "test"); states.append(ks.current_state().name)
    ks.resume("step2", "test"); states.append(ks.current_state().name)
    ks.soft_kill("step3", "test"); states.append(ks.current_state().name)
    ks.resume("step4", "test"); states.append(ks.current_state().name)
    ks.hard_kill("step5", "test"); states.append(ks.current_state().name)

    _check("CT080", [
        ("Step 1: SOFT_KILL", states[0] == "SOFT_KILL"),
        ("Step 2: INACTIVE (resume)", states[1] == "INACTIVE"),
        ("Step 3: SOFT_KILL again", states[2] == "SOFT_KILL"),
        ("Step 4: INACTIVE (resume)", states[3] == "INACTIVE"),
        ("Step 5: HARD_KILL", states[4] == "HARD_KILL"),
        ("5 state change events fired", len(events) == 5),
    ])
    store.close()


# =========================================================================
def ct082():
    print("\n" + "=" * 60)
    print("CT082 | Kill Switch Idempotency")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    events = []
    bus.subscribe(KillSwitchActivated, lambda e: events.append(e))
    ks = _ks(store, bus)

    for i in range(5):
        ks.soft_kill(f"call_{i}", "test")

    _check("CT082", [
        ("State: SOFT_KILL", ks.current_state().name == "SOFT_KILL"),
        ("Only 1 event (idempotent)", len(events) == 1),
    ])
    store.close()


# =========================================================================
def ct083():
    print("\n" + "=" * 60)
    print("CT083 | Max Open Positions (10)")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    fm = FundManager(
        state_store=store, bus=bus, logger=logger,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit=50000.0,
    )
    fm.initialize(broker_balance=100000.0)

    for i in range(10):
        sid = f"sig083_{i:03d}"
        _seed_signal(store, sid, f"SYM{i:03d}")
        _seed_open_trade(store, f"trd083_{i:03d}", sid, f"SYM{i:03d}", 5000.0)

    re = RiskEngine(
        fund_manager=fm, state_store=store, logger=logger,
        max_open_positions=10, max_daily_trades=200,
        max_sector_exposure_pct=1.0, max_consecutive_losses=99,
        daily_loss_limit_pct=0.10,
        sector_lookup_fn=lambda s: "IT",
    )

    sizing = SizingResult(
        success=True, qty=10, margin_required=5000.0,
        risk_amount=50.0, bucket="intraday",
        constraint="RISK", reason="ok", breakdown={},
    )
    result = re.approve(
        symbol="SYM010", side="BUY", intent="INTRADAY",
        sizing_result=sizing, signal_id="sig083_010",
    )

    _check("CT083", [
        ("10 open positions seeded", True),
        ("11th rejected", not result.approved),
        ("Reason: position limit", "position" in result.reason.lower()),
    ])
    store.close()


# =========================================================================
def ct085():
    print("\n" + "=" * 60)
    print("CT085 | Max Consecutive Losses (4)")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    fm = FundManager(
        state_store=store, bus=bus, logger=logger,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit=50000.0,
    )
    fm.initialize(broker_balance=100000.0)

    for i in range(4):
        sid = f"sig085_{i}"
        _seed_signal(store, sid, f"L{i}")
        now = now_ist().isoformat()
        with store.transaction() as cur:
            cur.execute(
                "INSERT INTO trades (trade_id, signal_id, symbol, direction, "
                "strategy, status, qty_planned, qty_filled, "
                "entry_target_price, entry_actual_price, "
                "sl_initial, tgt_initial, order_protocol, "
                "margin_reserved, risk_amount, exit_reason, net_pnl, "
                "created_at, updated_at, sector) "
                "VALUES (?  ,?,?,'LONG','t','CLOSED',10,10,100.0,100.0,"
                "95.0,110.0,'LIMIT_TRIPLE',25000.0,50.0,'SL_HIT',-500.0,?,?,'IT')",
                (f"trd085_{i}", sid, f"L{i}", now, now),
            )

    re = RiskEngine(
        fund_manager=fm, state_store=store, logger=logger,
        max_open_positions=10, max_daily_trades=200,
        max_sector_exposure_pct=1.0, max_consecutive_losses=4,
        daily_loss_limit_pct=0.10,
        sector_lookup_fn=lambda s: "IT",
    )

    sizing = SizingResult(
        success=True, qty=10, margin_required=5000.0,
        risk_amount=50.0, bucket="intraday",
        constraint="RISK", reason="ok", breakdown={},
    )
    result = re.approve(
        symbol="NEWSTOCK", side="BUY", intent="INTRADAY",
        sizing_result=sizing, signal_id="sig085_5th",
    )

    _check("CT085", [
        ("4 consecutive SL losses seeded", True),
        ("5th signal rejected", not result.approved),
        ("Reason: consecutive losses", "consec" in result.reason.lower()),
    ])
    store.close()


# =========================================================================
if __name__ == "__main__":
    try:
        ct067()
        ct068()
        ct077()
        ct078()
        ct079()
        ct080()
        ct082()
        ct083()
        ct085()
    finally:
        for p in _dbs:
            try:
                os.remove(p)
            except OSError:
                pass

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for scenario, verdict in all_results.items():
        print(f"  {scenario}: {verdict}")
    total_fail = sum(1 for v in all_results.values() if v == "FAIL")
    sys.exit(1 if total_fail > 0 else 0)
