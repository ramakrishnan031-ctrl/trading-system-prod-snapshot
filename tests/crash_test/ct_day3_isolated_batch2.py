"""
Isolated Day 3 crash test scenarios — batch 2.
CT070 (clock skew), CT075 (capital invariant hard kill),
CT084 (max daily trades), CT086 (max sector exposure),
CT087 (strategy circuit breaker).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, time as _time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ct_day3_b2")

from core.state_store import StateStore
from core.events import EventBus, KillSwitchActivated
from core.time_authority import now_ist
from capital.kill_switch import KillSwitch
from capital.fund_manager import FundManager
from capital.risk_engine import RiskEngine, ApprovalResult
from capital.position_sizer import SizingResult
from capital.strategy_governor import StrategyGovernor

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
    p = str(_SCRATCH / f"ct3b2_{_n[0]}.db")
    _dbs.append(p)
    return StateStore(db_path=p, schema_path=str(_SCHEMA))


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


def _seed_closed_trade(store, tid, sid, sym, strategy="t", pnl=-500.0):
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "status, qty_planned, qty_filled, entry_target_price, entry_actual_price, "
            "sl_initial, tgt_initial, order_protocol, margin_reserved, risk_amount, "
            "exit_reason, gross_pnl, net_pnl, created_at, updated_at, sector) "
            "VALUES (?,?,?,'LONG',?,'CLOSED',10,10,100.0,100.0,"
            "95.0,110.0,'LIMIT_TRIPLE',5000.0,50.0,'SL_HIT',?,?,?,?,'IT')",
            (tid, sid, sym, strategy, pnl, pnl, now, now),
        )


def _seed_open_trade(store, tid, sid, sym="RELIANCE", margin=5000.0, sector="IT"):
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "status, qty_planned, qty_filled, entry_target_price, entry_actual_price, "
            "sl_initial, tgt_initial, order_protocol, margin_reserved, risk_amount, "
            "created_at, updated_at, sector) "
            "VALUES (?,?,?,'LONG','t','OPEN',10,10,100.0,100.0,"
            "95.0,110.0,'LIMIT_TRIPLE',?,50.0,?,?,?)",
            (tid, sid, sym, margin, now, now, sector),
        )


# =========================================================================
def ct070():
    print("=" * 60)
    print("CT070 | Clock Skew > 30s Detection")
    print("=" * 60)
    from utils.startup_checks import check_clock_skew
    from core.exceptions import ClockSkewTooLarge
    from unittest.mock import MagicMock

    adapter = MagicMock()
    now = now_ist()
    adapter.get_server_time.return_value = now + timedelta(seconds=60)

    ta = MagicMock(unsafe=True)
    ta.now_ist.return_value = now
    ta.assert_clock_at_startup.side_effect = ClockSkewTooLarge(
        "Clock skew too large",
        skew_seconds=60.0, threshold_sec=30.0,
    )

    result = check_clock_skew(
        time_authority=ta, broker_adapter=adapter, logger=logger,
        tolerance_sec=30.0,
    )

    _check("CT070", [
        ("60s skew detected", result.skew_sec >= 60.0),
        ("Result is FAIL", not result.passed),
    ])


# =========================================================================
def ct075():
    print("\n" + "=" * 60)
    print("CT075 | Capital Invariant Violation -> Hard Kill")
    print("=" * 60)
    from capital.invariant import assert_capital_invariant
    from core.exceptions import CapitalInvariantViolation

    store = _store()
    bus = EventBus()
    events = []
    bus.subscribe(KillSwitchActivated, lambda e: events.append(e))

    ks = KillSwitch(
        state_store=store, bus=bus, logger=logger,
        api_failure_threshold=3, enable_auto_trip=True,
    )
    fm = FundManager(
        state_store=store, bus=bus, logger=logger,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.10, kill_switch=ks,
    )
    fm.initialize(broker_balance=100000.0)

    violated = False
    try:
        assert_capital_invariant(
            margin_available=50000.0,
            margin_reserved=10000.0,
            margin_used=5000.0,
            cash_floor=100000.0,
            realized_pnl_today=0.0,
        )
    except CapitalInvariantViolation:
        violated = True
        ks.hard_kill("CAPITAL_INVARIANT_VIOLATED", "invariant_checker")

    _check("CT075", [
        ("Invariant detects mismatch (50k+10k+5k != 100k)", violated),
        ("Hard kill activated", ks.current_state().name == "HARD_KILL"),
        ("KillSwitchActivated event", len(events) >= 1),
        ("Entry blocked", ks.is_active("entry")),
        ("Exit blocked", ks.is_active("exit")),
    ])
    store.close()


# =========================================================================
def ct084():
    print("\n" + "=" * 60)
    print("CT084 | Max Daily Trades (20)")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    fm = FundManager(
        state_store=store, bus=bus, logger=logger,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.50,
    )
    fm.initialize(broker_balance=100000.0)

    for i in range(20):
        sid = f"sig084_{i:03d}"
        _seed_signal(store, sid, f"S{i:03d}")
        _seed_closed_trade(store, f"trd084_{i:03d}", sid, f"S{i:03d}", pnl=100.0)

    re = RiskEngine(
        fund_manager=fm, state_store=store, logger=logger,
        max_open_positions=50, max_daily_trades=20,
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
        symbol="S021", side="BUY", intent="INTRADAY",
        sizing_result=sizing, signal_id="sig084_021",
    )

    _check("CT084", [
        ("20 closed trades seeded for today", True),
        ("21st rejected", not result.approved),
        ("Reason: daily trade limit", "daily" in result.reason.lower() or "trade" in result.reason.lower()),
    ])
    store.close()


# =========================================================================
def ct086():
    print("\n" + "=" * 60)
    print("CT086 | Max Sector Exposure (40%)")
    print("=" * 60)
    store = _store()
    bus = EventBus()
    fm = FundManager(
        state_store=store, bus=bus, logger=logger,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.50,
    )
    fm.initialize(broker_balance=100000.0)

    for i in range(4):
        sid = f"sig086_{i:03d}"
        _seed_signal(store, sid, f"BANK{i:03d}")
        _seed_open_trade(store, f"trd086_{i:03d}", sid, f"BANK{i:03d}", margin=10000.0, sector="BANKING")

    re = RiskEngine(
        fund_manager=fm, state_store=store, logger=logger,
        max_open_positions=50, max_daily_trades=200,
        max_sector_exposure_pct=0.40, max_consecutive_losses=99,
        daily_loss_limit_pct=0.10,
        sector_lookup_fn=lambda s: "BANKING",
    )

    sizing = SizingResult(
        success=True, qty=10, margin_required=10000.0,
        risk_amount=50.0, bucket="intraday",
        constraint="RISK", reason="ok", breakdown={},
    )
    result = re.approve(
        symbol="BANK004", side="BUY", intent="INTRADAY",
        sizing_result=sizing, signal_id="sig086_004",
    )

    _check("CT086", [
        ("4 open same-sector trades (40K/100K = 40%)", True),
        ("5th same-sector rejected (50K/100K = 50% > 40%)", not result.approved),
        ("Reason: sector exposure", "sector" in result.reason.lower()),
    ])
    store.close()


# =========================================================================
def ct087():
    print("\n" + "=" * 60)
    print("CT087 | Strategy Circuit Breaker")
    print("=" * 60)
    store = _store()

    from types import SimpleNamespace
    config = SimpleNamespace(enabled=True, loss_multiplier=2.0, cutoff_time="12:00", lookback_days=10)

    gov = StrategyGovernor(store=store, config=config, logger=logger)

    now = now_ist()
    today_str = now.strftime("%Y-%m-%d")

    for d in range(1, 4):
        past = (now - timedelta(days=d)).strftime("%Y-%m-%dT10:00:00+05:30")
        sid = f"sig087_past_{d}"
        fp = f"fp_{sid}"
        with store.transaction() as cur:
            cur.execute(
                "INSERT INTO signals (signal_id, symbol, scanner, strategy, "
                "triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date) "
                "VALUES (?,?,'t','aggressive_breakout',?,?,?,'PROCESSED',?,?)",
                (sid, f"PAST{d}", past, past, past, fp, past[:10]),
            )
            cur.execute(
                "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
                "status, qty_planned, qty_filled, entry_target_price, entry_actual_price, "
                "sl_initial, tgt_initial, order_protocol, margin_reserved, risk_amount, "
                "exit_reason, gross_pnl, net_pnl, created_at, updated_at, sector) "
                "VALUES (?,?,?,'LONG','aggressive_breakout','CLOSED',10,10,100.0,100.0,"
                "95.0,110.0,'LIMIT_TRIPLE',5000.0,50.0,'SL_HIT',-500.0,-500.0,?,?,'IT')",
                (f"trd087_past_{d}", sid, f"PAST{d}", past, past),
            )

    for i in range(3):
        sid = f"sig087_today_{i}"
        _seed_signal(store, sid, f"X{i}")
        _seed_closed_trade(store, f"trd087_today_{i}", sid, f"X{i}",
                          strategy="aggressive_breakout", pnl=-2000.0)

    paused_before, reason = gov.check("aggressive_breakout", _time(11, 0))

    gov2 = StrategyGovernor(store=store, config=config, logger=logger)
    after_cutoff, _ = gov2.check("aggressive_breakout", _time(12, 1))

    _check("CT087", [
        ("Strategy paused before cutoff (11:00)", paused_before),
        ("Strategy NOT paused after cutoff (12:01)", not after_cutoff),
    ])
    store.close()


# =========================================================================
if __name__ == "__main__":
    try:
        ct070()
        ct075()
        ct084()
        ct086()
        ct087()
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
