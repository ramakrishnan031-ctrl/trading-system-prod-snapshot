#!/usr/bin/env python3
"""
scripts/system_manager.py -- Trading System v2  (TASK #5 / System Manager EOD)

A daily "System Manager" that runs at 18:45 IST (after the Cron Officer EOD at
18:30) and performs deep cross-checks across the whole trading system — goes
beyond the Cron Officer (which only tracks job execution). It cross-validates
config vs actual behaviour, order quality, report integrity, system health,
strategy health, risk events, today-vs-yesterday, and tomorrow's readiness; then
reports (Telegram + email/sentinel on problems + a saved file) and trips
SOFT_KILL for tomorrow on a genuine safety violation.

Checks (each isolated — one failing check never crashes the report):
  1 config_vs_actual   2 order_quality     3 report_integrity   4 system_health
  5 strategy_health    6 risk_events       7 vs_yesterday       8 tomorrow_ready

SOFT_KILL (tomorrow) is tripped ONLY on: a config violation (position/trade/loss
cap exceeded), a DB integrity failure, or a HARD_KILL having fired today. Missing
reports / strategy concerns are warnings, never kills.

Parity: queries the same DB in paper and live; effective caps honour
live_test_mode (assumes live mode, the production case).

Usage (on the VM):
  python scripts/system_manager.py [--date YYYY-MM-DD] [--dry-run]
                                   [--config-dir DIR] [--db-path PATH]
                                   [--no-soft-kill]
Exit codes: 0 = clean; 2 = warnings only; 3 = violation(s)/soft-kill; 1 = error.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist
from utils.cron_heartbeat import record_heartbeat

_log = get_logger("system_manager")
_BAR = "━" * 30
_ACCOUNT = "LFL836"


# ─────────────────────────────────────────────────────────────────────────────
# Result model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    """Outcome of one check. `lines` are the human-readable report body; counts
    drive the summary; `soft_kill_reason` (set only by safety-critical checks)
    trips tomorrow's SOFT_KILL."""
    title: str
    lines: List[str] = field(default_factory=list)
    violations: int = 0
    warnings: int = 0
    soft_kill_reason: Optional[str] = None
    error: Optional[str] = None

    def ok(self, msg: str) -> None:
        self.lines.append(f"✅ {msg}")

    def warn(self, msg: str) -> None:
        self.lines.append(f"⚠️ {msg}")
        self.warnings += 1

    def violation(self, msg: str, soft_kill_reason: Optional[str] = None) -> None:
        self.lines.append(f"❌ {msg}")
        self.violations += 1
        if soft_kill_reason and not self.soft_kill_reason:
            self.soft_kill_reason = soft_kill_reason

    def info(self, msg: str) -> None:
        self.lines.append(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Trade statuses that represent an actually-executed (filled) position. Mirrors
# StateStore._EXECUTED_TRADE_STATUSES intent: excludes FAILED/CANCELLED/REJECTED.
_EXECUTED = ("OPEN", "PARTIAL", "EXITING", "CLOSED", "CLOSED_MANUAL")
_CLOSED = ("CLOSED", "CLOSED_MANUAL")


def _day(d: date) -> str:
    return d.isoformat()


def _prev_trading_day(d: date, config_dir: Path) -> date:
    """The most recent trading day strictly before `d` (skips weekends/holidays)."""
    try:
        from utils.holiday_guard import is_trading_day
    except Exception:
        is_trading_day = None
    cand = d - timedelta(days=1)
    for _ in range(15):
        if is_trading_day is None:
            if cand.weekday() < 5:
                return cand
        else:
            try:
                if is_trading_day(cand, config_dir):
                    return cand
            except Exception:
                if cand.weekday() < 5:
                    return cand
        cand -= timedelta(days=1)
    return d - timedelta(days=1)


def _scalar(store: StateStore, sql: str, params=()) -> float:
    row = store.fetch_one(sql, params)
    if not row:
        return 0.0
    val = row[0] if not hasattr(row, "keys") else row[list(row.keys())[0]]
    return float(val or 0.0)


def _effective_caps(app_config) -> tuple[int, int, bool]:
    """(max_open_positions, max_daily_trades, live_test_active). live_test_mode
    (assumed live) overrides the raw caps — the same logic main.py applies."""
    r = app_config.system.risk
    lt = bool(getattr(r, "live_test_mode", False))
    if lt:
        return (int(r.live_test_max_open_positions),
                int(r.live_test_max_entries_per_day), True)
    return (int(r.max_open_positions), int(r.max_daily_trades), False)


def _max_concurrent_positions(store: StateStore, day: str) -> int:
    """Sweep-line over today's executed trades' (entry_time, exit_time) to find
    the peak concurrent open count. Trades still open use a far-future exit."""
    rows = store.fetch_all(
        f"SELECT entry_time, exit_time FROM trades "
        f"WHERE substr(created_at,1,10)=? AND status IN {_EXECUTED}",
        (day,),
    )
    events: list[tuple[str, int]] = []
    for r in rows:
        et = r["entry_time"] or r["exit_time"]
        if not et:
            continue
        events.append((et, +1))
        xt = r["exit_time"] or "9999-12-31"
        events.append((xt, -1))
    events.sort(key=lambda e: (e[0], -e[1]))  # opens before closes at same ts
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


# ─────────────────────────────────────────────────────────────────────────────
# Check 1 — Config vs Actual
# ─────────────────────────────────────────────────────────────────────────────

def config_vs_actual_check(store: StateStore, app_config, day: str) -> CheckResult:
    res = CheckResult("📊 CONFIG vs ACTUAL")
    ps = app_config.system.position_sizing
    cap = app_config.system.capital
    eff_open, eff_daily, lt = _effective_caps(app_config)
    if lt:
        res.info(f"(live_test_mode ON — effective caps: max_open={eff_open}, max_daily={eff_daily})")

    # Max concurrent positions
    actual_open = _max_concurrent_positions(store, day)
    if actual_open > eff_open:
        res.violation(
            f"Max positions: cap {eff_open} / actual {actual_open} — VIOLATION",
            soft_kill_reason=f"position cap exceeded ({actual_open} > {eff_open})",
        )
    else:
        res.ok(f"Max positions: cap {eff_open} / actual {actual_open}")

    # Daily trades (executed)
    actual_trades = int(_scalar(
        store,
        f"SELECT COUNT(*) FROM trades WHERE substr(created_at,1,10)=? AND status IN {_EXECUTED}",
        (day,),
    ))
    if actual_trades > eff_daily:
        res.violation(
            f"Daily trades: cap {eff_daily} / actual {actual_trades} — VIOLATION",
            soft_kill_reason=f"daily trade cap exceeded ({actual_trades} > {eff_daily})",
        )
    else:
        res.ok(f"Daily trades: cap {eff_daily} / actual {actual_trades}")

    # Daily loss limit (absolute ₹). Realized = net_pnl of trades closed today.
    realized = _scalar(
        store,
        f"SELECT COALESCE(SUM(net_pnl),0) FROM trades "
        f"WHERE substr(created_at,1,10)=? AND status IN {_CLOSED}",
        (day,),
    )
    loss_limit = float(getattr(cap, "daily_loss_limit", 0.0) or 0.0)
    if loss_limit > 0 and realized < -loss_limit:
        res.violation(
            f"Daily loss: limit ₹{loss_limit:,.0f} / actual ₹{realized:,.2f} — VIOLATION",
            soft_kill_reason=f"daily loss limit breached (₹{realized:,.2f})",
        )
    else:
        res.ok(f"Daily loss: limit ₹{loss_limit:,.0f} / actual ₹{realized:,.2f}")

    # Max position value (₹) per trade
    max_posval = _scalar(
        store,
        f"SELECT COALESCE(MAX(qty_filled*entry_actual_price),0) FROM trades "
        f"WHERE substr(created_at,1,10)=? AND status IN {_EXECUTED}",
        (day,),
    )
    cap_posval = float(getattr(ps, "max_position_value_rs", 0.0) or 0.0)
    if cap_posval > 0 and max_posval > cap_posval + 0.01:
        res.violation(
            f"Max position value: cap ₹{cap_posval:,.0f} / actual ₹{max_posval:,.0f} — VIOLATION",
            soft_kill_reason=f"position value cap exceeded (₹{max_posval:,.0f})",
        )
    else:
        res.ok(f"Max position value: cap ₹{cap_posval:,.0f} / actual ₹{max_posval:,.0f}")

    # Max risk per trade (₹ risk_amount); informational vs risk_per_trade_pct.
    max_risk = _scalar(
        store,
        f"SELECT COALESCE(MAX(risk_amount),0) FROM trades "
        f"WHERE substr(created_at,1,10)=? AND status IN {_EXECUTED}",
        (day,),
    )
    res.info(f"ℹ️ Max risk/trade today: ₹{max_risk:,.2f} "
             f"(config risk_per_trade_pct={ps.risk_per_trade_pct:.1%}, concentration={ps.max_concentration_pct:.0%})")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 2 — Order Quality
# ─────────────────────────────────────────────────────────────────────────────

def order_quality_report(store: StateStore, day: str) -> CheckResult:
    res = CheckResult("📈 ORDER QUALITY")
    trades = store.fetch_all(
        f"SELECT trade_id,symbol,direction,entry_target_price,entry_actual_price,"
        f"sl_initial,tgt_initial,exit_price,exit_reason,net_pnl,status "
        f"FROM trades WHERE substr(created_at,1,10)=? AND status IN {_CLOSED} "
        f"ORDER BY exit_time",
        (day,),
    )
    if not trades:
        res.info("No closed trades today.")
        return res

    entry_slips: list[float] = []
    exit_slips: list[float] = []
    for t in trades:
        sym, dirn = t["symbol"], t["direction"]
        plan_e, fill_e = t["entry_target_price"], t["entry_actual_price"]
        if plan_e and fill_e and plan_e > 0:
            slip = (fill_e - plan_e) / plan_e
            entry_slips.append(slip)
        # exit slippage vs the relevant planned leg
        planned_exit = None
        if t["exit_reason"] == "SL_HIT":
            planned_exit = t["sl_initial"]
        elif t["exit_reason"] == "TGT_HIT":
            planned_exit = t["tgt_initial"]
        if planned_exit and t["exit_price"] and planned_exit > 0:
            exit_slips.append((t["exit_price"] - planned_exit) / planned_exit)
        res.info(f"  {sym} {dirn} {t['exit_reason'] or '—'}: "
                 f"entry ₹{plan_e or 0:.2f}→₹{fill_e or 0:.2f}, "
                 f"exit ₹{t['exit_price'] or 0:.2f}, P&L ₹{t['net_pnl'] or 0:.2f}")

    # Partial fills today
    partials = int(_scalar(
        store,
        "SELECT COUNT(*) FROM orders WHERE substr(placed_at,1,10)=? "
        "AND qty_filled>0 AND qty_filled<qty_requested",
        (day,),
    ))
    # Orphan exit orders: exit legs left non-terminal for a CLOSED trade today
    orphans = int(_scalar(
        store,
        f"SELECT COUNT(*) FROM orders o JOIN trades t ON o.trade_id=t.trade_id "
        f"WHERE substr(t.created_at,1,10)=? AND t.status IN {_CLOSED} "
        f"AND o.leg IN ('SL','TGT') AND o.status NOT IN "
        f"('CANCELLED','FAILED','EXPIRED','COMPLETE')",
        (day,),
    ))

    def _avg(xs):
        return f"{(sum(xs)/len(xs)*100):+.3f}%" if xs else "n/a"

    res.info(_BAR)
    res.info(f"Trades: {len(trades)} | Avg entry slip: {_avg(entry_slips)} | "
             f"Avg exit slip: {_avg(exit_slips)}")
    (res.ok if partials == 0 else res.warn)(f"Partial fills: {partials}")
    if orphans == 0:
        res.ok("Orphan exit orders: 0")
    else:
        # Warn (not SOFT_KILL): the reconciler / EOD cleanup own orphan resolution;
        # FIX-190 cancels exits before any flatten. Surface for review.
        res.warn(f"Orphan exit orders: {orphans} — uncancelled SL/TGT on closed trades")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 3 — Report Integrity
# ─────────────────────────────────────────────────────────────────────────────

def report_integrity_check(day: str, root: Path) -> CheckResult:
    """Verify today's expected outputs exist + are non-trivial. Real paths/exts
    confirmed by audit (mostly .md; daily_report.xlsx in reports/output/). Missing
    reports are WARNINGS (a no-trade day legitimately produces fewer)."""
    res = CheckResult("📁 REPORT INTEGRITY")

    def _check(label: str, path: Path, min_bytes: int, warn_if_missing: bool = True) -> None:
        if not path.exists():
            (res.warn if warn_if_missing else res.info)(f"{label}: MISSING ({path.name})")
            return
        size = path.stat().st_size
        mtime_day = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        if size < min_bytes:
            res.warn(f"{label}: present but tiny ({size}B < {min_bytes}B)")
        elif mtime_day != day:
            res.warn(f"{label}: stale (modified {mtime_day}, not {day})")
        else:
            res.ok(f"{label}: {size//1024 or 1} KB")

    _check("daily_report.xlsx", root / "reports/output" / f"daily_report_{day}.xlsx", 2000)
    _check("watchman.md", root / "reports/watchman" / f"watchman_{day}.md", 200)
    _check("flow_trace.md", root / "reports/flow_trace" / f"trace_{day}.md", 100)
    _check("system log", root / "logs" / f"system_{day}.log", 500)
    _check("DB backup", root / "data_store/backups" / f"trading_system-{day}.db", 10000)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 4 — System Health
# ─────────────────────────────────────────────────────────────────────────────

def system_health_check(store: StateStore, day: str, db_path: Path, root: Path) -> CheckResult:
    res = CheckResult("🏥 SYSTEM HEALTH")

    # Cron heartbeats today
    hb = store.fetch_all(
        "SELECT job_name,status FROM cron_heartbeat WHERE substr(executed_at,1,10)=?",
        (day,),
    )
    ran = {r["job_name"] for r in hb}
    failed = [r["job_name"] for r in hb if (r["status"] or "").upper() == "FAILED"]
    if failed:
        res.warn(f"Cron heartbeats: {len(ran)} jobs ran; FAILED: {', '.join(sorted(set(failed)))}")
    else:
        res.ok(f"Cron heartbeats: {len(ran)} jobs ran, 0 failed")

    # DB integrity — a real failure is a SOFT_KILL-worthy safety issue.
    try:
        row = store.fetch_one("PRAGMA integrity_check", ())
        integ = (row[0] if row else "unknown")
        if str(integ).lower() == "ok":
            res.ok("DB integrity: ok")
        else:
            res.violation(f"DB integrity: {integ}", soft_kill_reason=f"DB integrity check failed: {integ}")
    except Exception as exc:
        res.warn(f"DB integrity: could not run ({exc})")

    # Disk free
    try:
        du = shutil.disk_usage(str(root))
        free_pct = du.free / du.total * 100
        free_gb = du.free / 1e9
        (res.ok if free_pct >= 20 else res.warn)(
            f"Disk: {free_gb:.1f} GB free ({free_pct:.0f}%)")
    except Exception as exc:
        res.info(f"Disk: unavailable ({exc})")

    # Token validity
    try:
        from scripts.zerodha_login import is_token_valid
        valid = is_token_valid(_ACCOUNT, root / "data_store/session/zerodha_token.json")
        (res.ok if valid else res.warn)(f"Token ({_ACCOUNT}): {'valid' if valid else 'INVALID/expired'}")
    except Exception as exc:
        res.info(f"Token: check unavailable ({exc})")

    # Service starts today (STARTUP system events) — many = instability
    starts = int(_scalar(
        store,
        "SELECT COUNT(*) FROM system_events WHERE substr(timestamp,1,10)=? "
        "AND event_type='STARTUP'",
        (day,),
    ))
    crashes = int(_scalar(
        store,
        "SELECT COUNT(*) FROM system_events WHERE substr(timestamp,1,10)=? "
        "AND event_type='CRASH_DETECTED'",
        (day,),
    ))
    (res.warn if starts > 2 else res.ok)(f"Service starts today: {starts}; crashes detected: {crashes}")

    # Sentinel flags today (pending = undelivered CRITICAL alerts)
    try:
        ds = root / "data_store"
        pending = len(list(ds.glob("critical_alert_*.flag")))
        (res.ok if pending == 0 else res.warn)(f"Pending CRITICAL sentinels: {pending}")
    except Exception:
        pass

    # Current kill switch
    ks = store.fetch_one("SELECT state,reason FROM kill_switch_state WHERE id=1", ())
    state = (ks["state"] if ks else "INACTIVE") or "INACTIVE"
    if state == "INACTIVE":
        res.ok("Kill switch: INACTIVE")
    else:
        res.warn(f"Kill switch: {state} ({(ks['reason'] or '')[:50]})")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 5 — Strategy Health
# ─────────────────────────────────────────────────────────────────────────────

def trade_strategy_health(store: StateStore, day: str) -> CheckResult:
    res = CheckResult("📉 STRATEGY HEALTH")
    rows = store.fetch_all(
        f"SELECT COALESCE(strategy,'(none)') s, direction, net_pnl FROM trades "
        f"WHERE substr(created_at,1,10)=? AND status IN {_CLOSED}",
        (day,),
    )
    if not rows:
        res.info("No closed trades today — no per-strategy stats.")
    else:
        by: dict[str, dict] = {}
        for r in rows:
            d = by.setdefault(r["s"], {"n": 0, "wins": 0, "pnl": 0.0})
            d["n"] += 1
            d["pnl"] += float(r["net_pnl"] or 0)
            if (r["net_pnl"] or 0) > 0:
                d["wins"] += 1
        for s, d in sorted(by.items()):
            wr = d["wins"] / d["n"] * 100 if d["n"] else 0
            line = f"  {s}: {d['n']} trades, {wr:.0f}% win, ₹{d['pnl']:+.2f}"
            if d["n"] >= 2 and wr == 0:
                res.warn(line + " — 0% win rate today")
            else:
                res.info(line)
        # LONG vs SHORT split (memory: LONG historically weaker)
        longs = [r for r in rows if r["direction"] == "LONG"]
        shorts = [r for r in rows if r["direction"] == "SHORT"]

        def _wr(xs):
            return (sum(1 for r in xs if (r["net_pnl"] or 0) > 0) / len(xs) * 100) if xs else 0
        res.info(f"  LONG {len(longs)} ({_wr(longs):.0f}% win) | SHORT {len(shorts)} ({_wr(shorts):.0f}% win)")

    # Demotions / disables from strategy_metrics (today)
    sm = store.fetch_all(
        "SELECT strategy,win_rate,total_trades FROM strategy_metrics WHERE date=?",
        (day,),
    )
    weak = [r for r in sm if (r["total_trades"] or 0) >= 3 and (r["win_rate"] or 0) < 0.35]
    for r in weak:
        res.warn(f"Strategy {r['strategy']}: win_rate {(r['win_rate'] or 0):.0%} over {r['total_trades']} — review")
    if not weak and sm:
        res.ok(f"strategy_metrics: {len(sm)} strategies, none below review threshold")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 6 — Risk Events
# ─────────────────────────────────────────────────────────────────────────────

def risk_events_check(store: StateStore, day: str) -> CheckResult:
    res = CheckResult("🛡️ RISK EVENTS")

    def _rl_count(like: str) -> int:
        return int(_scalar(
            store,
            "SELECT COUNT(*) FROM reconciliation_log WHERE substr(ts,1,10)=? AND check_name=?",
            (day, like),
        ))

    hard = int(_scalar(
        store, "SELECT COUNT(*) FROM system_events WHERE substr(timestamp,1,10)=? "
               "AND (details LIKE '%HARD_KILL%' OR event_type LIKE '%HARD_KILL%')", (day,)))
    # current state catches a HARD_KILL still active even if no event row
    ks = store.fetch_one("SELECT state FROM kill_switch_state WHERE id=1", ())
    hard_active = (ks and ks["state"] == "HARD_KILL")
    if hard or hard_active:
        res.violation(
            f"HARD_KILL today: {hard} event(s)" + (" (currently ACTIVE)" if hard_active else ""),
            soft_kill_reason="HARD_KILL fired today" if not hard_active else "HARD_KILL currently active",
        )
    else:
        res.ok("HARD_KILL: 0 today")

    drift = _rl_count("CAPITAL_DRIFT")
    (res.ok if drift == 0 else res.warn)(f"Capital drift events: {drift}")
    manual = _rl_count("MANUAL_CLOSE")
    if manual:
        res.warn(f"Manual/external closes (CHECK1): {manual}")
    else:
        res.ok("Manual/external closes: 0")
    orphan_rl = int(_scalar(
        store,
        "SELECT COUNT(*) FROM reconciliation_log WHERE substr(ts,1,10)=? "
        "AND (lower(description) LIKE '%orphan%' OR check_name LIKE '%ORPHAN%')",
        (day,),
    ))
    (res.ok if orphan_rl == 0 else res.warn)(f"Orphan detections: {orphan_rl}")

    soft = int(_scalar(
        store, "SELECT COUNT(*) FROM system_events WHERE substr(timestamp,1,10)=? "
               "AND details LIKE '%SOFT_KILL%'", (day,)))
    res.info(f"ℹ️ SOFT_KILL events logged today: {soft}")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 7 — vs Yesterday
# ─────────────────────────────────────────────────────────────────────────────

def compare_with_yesterday(store: StateStore, day: str, prev: str) -> CheckResult:
    res = CheckResult("📅 vs YESTERDAY")

    def _trades(d):
        return int(_scalar(store, f"SELECT COUNT(*) FROM trades WHERE substr(created_at,1,10)=? AND status IN {_CLOSED}", (d,)))

    def _pnl(d):
        return _scalar(store, f"SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE substr(created_at,1,10)=? AND status IN {_CLOSED}", (d,))

    def _sig(d):
        return int(_scalar(store, "SELECT COUNT(*) FROM signals WHERE substr(received_at,1,10)=?", (d,)))

    def _rej(d):
        return int(_scalar(store, "SELECT COUNT(*) FROM signals WHERE substr(received_at,1,10)=? AND status LIKE 'REJECTED%'", (d,)))

    res.info(f"(today {day} vs prev trading day {prev})")
    for label, fn, fmt in [
        ("Trades", _trades, "{}"),
        ("P&L", _pnl, "₹{:,.2f}"),
        ("Signals recv", _sig, "{}"),
        ("Signals rejected", _rej, "{}"),
    ]:
        t, y = fn(day), fn(prev)
        spike = ""
        if isinstance(t, (int, float)) and y and abs(y) > 0 and abs(t) > 2 * abs(y):
            spike = " — ⚠️ >2x deviation"
            res.warnings += 1
        res.info(f"  {label}: today {fmt.format(t)} / prev {fmt.format(y)}{spike}")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 8 — Tomorrow Readiness
# ─────────────────────────────────────────────────────────────────────────────

def tomorrow_readiness_check(store: StateStore, app_config, day_date: date,
                             config_dir: Path) -> CheckResult:
    res = CheckResult("🔮 TOMORROW READINESS")
    # Next trading day
    try:
        from utils.holiday_guard import is_trading_day, next_trading_day
        nxt = next_trading_day(day_date, config_dir)
        trading = is_trading_day(nxt, config_dir)
        res.ok(f"Next trading day: {nxt.strftime('%d-%b (%A)')}")
    except Exception:
        nxt = day_date + timedelta(days=1)
        res.info(f"Next day: {nxt.isoformat()} (holiday calendar unavailable)")

    # DB clean: stuck signals / orphan orders / stuck in-flight trades
    stuck_sig = int(_scalar(store, "SELECT COUNT(*) FROM signals WHERE status='PROCESSING'", ()))
    stuck_trades = int(_scalar(store, "SELECT COUNT(*) FROM trades WHERE status IN ('PENDING','PENDING_FILL','EXITING')", ()))
    orphan_orders = int(_scalar(
        store,
        "SELECT COUNT(*) FROM orders o JOIN trades t ON o.trade_id=t.trade_id "
        "WHERE t.status IN ('CLOSED','CLOSED_MANUAL') AND o.leg IN ('SL','TGT') "
        "AND o.status NOT IN ('CANCELLED','FAILED','EXPIRED','COMPLETE')",
        (),
    ))
    clean = (stuck_sig == 0 and stuck_trades == 0 and orphan_orders == 0)
    (res.ok if clean else res.warn)(
        f"DB clean: {stuck_sig} stuck signals, {stuck_trades} stuck trades, {orphan_orders} orphan orders")

    # Kill switch — must be cleared before market open
    ks = store.fetch_one("SELECT state,reason FROM kill_switch_state WHERE id=1", ())
    state = (ks["state"] if ks else "INACTIVE") or "INACTIVE"
    if state == "INACTIVE":
        res.ok("Kill switch: INACTIVE (no --resume needed)")
    else:
        res.warn(f"Kill switch: {state} — needs deploy/resume.sh before market open")

    # live_test_mode reminder
    eff_open, eff_daily, lt = _effective_caps(app_config)
    if lt:
        res.info(f"ℹ️ live_test_mode ON: tomorrow max_open={eff_open}, max_daily={eff_daily}")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Report + send + soft-kill
# ─────────────────────────────────────────────────────────────────────────────

def generate_full_report(results: List[CheckResult], day_date: date) -> tuple[str, int, int, List[str]]:
    """Returns (report_text, total_violations, total_warnings, soft_kill_reasons)."""
    v = sum(r.violations for r in results)
    w = sum(r.warnings for r in results)
    reasons = [r.soft_kill_reason for r in results if r.soft_kill_reason]
    lines = [
        _BAR,
        f"🎯 [{_ACCOUNT}] SYSTEM MANAGER EOD — {day_date.strftime('%d-%b-%Y (%A)')}",
        _BAR,
    ]
    for r in results:
        lines.append("")
        lines.append(r.title)
        if r.error:
            lines.append(f"⚠️ check error: {r.error}")
        lines.extend(r.lines)
    lines += ["", _BAR, f"SUMMARY: {v} violation(s), {w} warning(s)"]
    if reasons:
        lines.append("🚫 SOFT_KILL triggered for tomorrow — manual deploy/resume.sh required")
        for rs in reasons:
            lines.append(f"   • {rs}")
    elif v == 0 and w == 0:
        lines.append("✅ All clear.")
    lines.append(_BAR)
    return "\n".join(lines), v, w, reasons


def _send(severity: str, title: str, body: str, config_dir: Path, dry_run: bool) -> None:
    """Telegram + (CRITICAL → sentinel → alert-watcher email). Mirrors cron_officer."""
    if dry_run:
        print(f"[DRY-RUN] would send {severity}: {title}")
        return
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=_log, config_dir=config_dir)
        if notifier is None:
            _log.info("system_manager.telegram_unconfigured")
            return
        notifier.send(severity=severity, title=title, body=body, source_module="system_manager")
    except Exception as exc:
        _log.error("system_manager.send_failed", extra={"error": str(exc)})


def _save_report(report: str, day: str, root: Path) -> Optional[Path]:
    try:
        out_dir = root / "reports" / "system_manager"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{day}.txt"
        path.write_text(report, encoding="utf-8")
        return path
    except Exception as exc:
        _log.error("system_manager.save_failed", extra={"error": str(exc)})
        return None


def trigger_soft_kill(store: StateStore, reasons: List[str], day: str) -> bool:
    """Trip SOFT_KILL for tomorrow. Standalone (no instance lock). Returns True if set."""
    try:
        from capital.kill_switch import KillSwitch
        from core.events import EventBus
        ks = KillSwitch(store, EventBus(), get_logger("kill_switch"))
        if ks.is_active():
            _log.info("system_manager.soft_kill_skip_already_active")
            return True
        reason = f"System Manager EOD {day}: " + "; ".join(reasons)
        ks.soft_kill(reason=reason[:240], triggered_by="system_manager_eod")
        return True
    except Exception as exc:
        _log.error("system_manager.soft_kill_failed", extra={"error": str(exc)})
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration / CLI
# ─────────────────────────────────────────────────────────────────────────────

def run(store: StateStore, app_config, day_date: date, config_dir: Path,
        db_path: Path, root: Path) -> tuple[str, int, int, List[str]]:
    """Run all 8 checks (each isolated) and build the report."""
    day = _day(day_date)
    prev = _prev_trading_day(day_date, config_dir)
    specs = [
        lambda: config_vs_actual_check(store, app_config, day),
        lambda: order_quality_report(store, day),
        lambda: report_integrity_check(day, root),
        lambda: system_health_check(store, day, db_path, root),
        lambda: trade_strategy_health(store, day),
        lambda: risk_events_check(store, day),
        lambda: compare_with_yesterday(store, day, _day(prev)),
        lambda: tomorrow_readiness_check(store, app_config, day_date, config_dir),
    ]
    titles = ["CONFIG vs ACTUAL", "ORDER QUALITY", "REPORT INTEGRITY", "SYSTEM HEALTH",
              "STRATEGY HEALTH", "RISK EVENTS", "vs YESTERDAY", "TOMORROW READINESS"]
    results: List[CheckResult] = []
    for spec, title in zip(specs, titles):
        try:
            results.append(spec())
        except Exception as exc:
            _log.error("system_manager.check_failed", extra={"check": title, "error": str(exc)})
            cr = CheckResult(f"⚠️ {title}")
            cr.error = str(exc)
            cr.warnings += 1
            results.append(cr)
    return generate_full_report(results, day_date)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="System Manager EOD")
    p.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (default: today IST)")
    p.add_argument("--config-dir", type=Path, default=Path("config"))
    p.add_argument("--db-path", type=Path, default=Path("data_store/trading_system.db"))
    p.add_argument("--dry-run", action="store_true", help="Print only; no Telegram/email/soft-kill.")
    p.add_argument("--no-soft-kill", action="store_true", help="Report violations but do NOT trip SOFT_KILL.")
    args = p.parse_args(argv)

    # Report contains emoji/box-drawing chars; make stdout UTF-8 (no-op on the VM,
    # fixes a non-UTF-8 console / redirected stream from choking).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    day_date = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else now_ist().date())
    if not args.db_path.exists():
        print(f"Database not found: {args.db_path}")
        return 1

    try:
        from core.config_loader import load_all
        app_config = load_all(args.config_dir)
    except Exception as exc:
        _log.error("system_manager.config_load_failed", extra={"error": str(exc)})
        print(f"Config load failed: {exc}")
        return 1

    store = StateStore(args.db_path)
    try:
        report, violations, warnings, reasons = run(
            store, app_config, day_date, args.config_dir, args.db_path, _ROOT,
        )
        print(report)
        _save_report(report, _day(day_date), _ROOT)

        # SOFT_KILL on a genuine safety violation (unless suppressed/dry-run).
        soft_killed = False
        if reasons and not args.no_soft_kill and not args.dry_run:
            soft_killed = trigger_soft_kill(store, reasons, _day(day_date))

        severity = "CRITICAL" if (violations or reasons) else "INFO"
        title = ("System Manager EOD — ACTION REQUIRED" if severity == "CRITICAL"
                 else "System Manager EOD — clean")
        _send(severity, title, report, args.config_dir, args.dry_run)

        record_heartbeat(
            "system_manager_eod",
            status="SUCCESS" if violations == 0 else "PARTIAL",
            message=f"{violations}v/{warnings}w" + (" soft_kill" if soft_killed else ""),
            db_path=args.db_path,
        )
        return 3 if (violations or reasons) else (2 if warnings else 0)
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
