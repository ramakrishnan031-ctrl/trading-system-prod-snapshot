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
  9 security (VM copy protection — Phase 3)

SOFT_KILL (tomorrow) is tripped ONLY on: a config violation (position/trade/loss
cap exceeded), a DB integrity failure, or a HARD_KILL having fired today. Missing
reports / strategy concerns are warnings, never kills. A SECURITY violation (copy
bypass / protection disabled) escalates the EOD report to CRITICAL (→ email) but
does NOT trip the trading SOFT_KILL — a security event is not a trading-safety halt.

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
import json
import os
import shutil
import subprocess
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


def _effective_caps(app_config) -> tuple[int, int]:
    """(max_open_positions, max_daily_trades). BUILD 1 (#3, 24-Jun): the
    live_test_mode override was deleted — these base caps are now the sole
    authority in both paper and live, so no swap remains."""
    r = app_config.system.risk
    return (int(r.max_open_positions), int(r.max_daily_trades))


def _day_capital(store: StateStore, app_config, config_dir: Path, day: str) -> tuple[float, str]:
    """
    BUILD 1 (#1/#2, 24-Jun): the day's capital used to recompute the now
    capital-relative audit thresholds. Single, parity-safe source (no broker
    call): the fm_ledger INIT row for the day. Falls back to the configured
    account paper_capital (accounts.csv) on a fresh DB / non-trading day so the
    thresholds are still non-zero. Returns (capital, source)."""
    cap = store.get_day_opening_capital(day)
    if cap and cap > 0:
        return float(cap), "fm_ledger"
    # Fresh-DB bootstrap — configured starting capital, DB-free, no broker call.
    try:
        from core.account_registry import AccountRegistry
        reg = AccountRegistry.load(config_dir / "accounts.csv")
        accts = reg.get_enabled_accounts()
        if accts:
            return float(accts[0].paper_capital), "accounts.csv(bootstrap)"
    except Exception:  # noqa: BLE001 — fallback must never crash the audit
        pass
    return 0.0, "unavailable"


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


def _count_in_log(root: Path, day: str, needle: str) -> int:
    """Count lines containing `needle` in today's system log. Kill activations are
    logged (`HARD_KILL ACTIVATED` / `SOFT_KILL ACTIVATED`) but NOT written to any
    queryable table and the kill_switch_state row is overwritten on clear — so the
    EOD log (complete after market close) is the only reliable source."""
    path = root / "logs" / f"system_{day}.log"
    if not path.exists():
        return 0
    n = 0
    try:
        with path.open(errors="replace") as f:
            for line in f:
                if needle in line:
                    n += 1
    except Exception:
        return 0
    return n


# ─────────────────────────────────────────────────────────────────────────────
# Check 1 — Config vs Actual
# ─────────────────────────────────────────────────────────────────────────────

def config_vs_actual_check(store: StateStore, app_config, day: str,
                           config_dir: Path) -> CheckResult:
    res = CheckResult("📊 CONFIG vs ACTUAL")
    ps = app_config.system.position_sizing
    risk = app_config.system.risk
    eff_open, eff_daily = _effective_caps(app_config)
    # BUILD 1 (#1/#2): the daily-loss + position-value caps are now
    # capital-relative (pct × capital). Source the day's capital from fm_ledger
    # (accounts.csv fallback) and recompute the ₹ thresholds the same way the
    # runtime does — single pct source, no broker call.
    day_capital, cap_src = _day_capital(store, app_config, config_dir, day)
    res.info(f"(day capital ₹{day_capital:,.0f} via {cap_src})")

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

    # Daily loss limit. BUILD 1 (#1): ₹ limit = daily_loss_limit_pct × day capital.
    # Realized = net_pnl of trades closed today.
    realized = _scalar(
        store,
        f"SELECT COALESCE(SUM(net_pnl),0) FROM trades "
        f"WHERE substr(created_at,1,10)=? AND status IN {_CLOSED}",
        (day,),
    )
    loss_limit = float(risk.daily_loss_limit_pct) * day_capital
    if loss_limit > 0 and realized < -loss_limit:
        res.violation(
            f"Daily loss: limit ₹{loss_limit:,.0f} ({risk.daily_loss_limit_pct:.0%}) / actual ₹{realized:,.2f} — VIOLATION",
            soft_kill_reason=f"daily loss limit breached (₹{realized:,.2f})",
        )
    else:
        res.ok(f"Daily loss: limit ₹{loss_limit:,.0f} ({risk.daily_loss_limit_pct:.0%}) / actual ₹{realized:,.2f}")

    # Max position value per trade. BUILD 1 (#2): cap = max_position_value_pct × day capital.
    max_posval = _scalar(
        store,
        f"SELECT COALESCE(MAX(qty_filled*entry_actual_price),0) FROM trades "
        f"WHERE substr(created_at,1,10)=? AND status IN {_EXECUTED}",
        (day,),
    )
    cap_posval = float(getattr(ps, "max_position_value_pct", 0.0) or 0.0) * day_capital
    if cap_posval > 0 and max_posval > cap_posval + 0.01:
        res.violation(
            f"Max position value: cap ₹{cap_posval:,.0f} ({ps.max_position_value_pct:.0%}) / actual ₹{max_posval:,.0f} — VIOLATION",
            soft_kill_reason=f"position value cap exceeded (₹{max_posval:,.0f})",
        )
    else:
        res.ok(f"Max position value: cap ₹{cap_posval:,.0f} ({ps.max_position_value_pct:.0%}) / actual ₹{max_posval:,.0f}")

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
        f"SELECT trade_id,symbol,direction,strategy,entry_target_price,entry_actual_price,"
        f"sl_initial,tgt_initial,exit_price,exit_reason,gross_pnl,charges,net_pnl,status "
        f"FROM trades WHERE substr(created_at,1,10)=? AND status IN {_CLOSED} "
        f"ORDER BY exit_time",
        (day,),
    )
    if not trades:
        res.info("No closed trades today.")
        return res

    entry_slips: list[float] = []
    exit_slips: list[float] = []
    gross_total = charges_total = net_total = 0.0
    for t in trades:
        sym, dirn, strat = t["symbol"], t["direction"], t["strategy"] or "(none)"
        plan_e, fill_e = t["entry_target_price"], t["entry_actual_price"]
        gross_total += float(t["gross_pnl"] or 0)
        charges_total += float(t["charges"] or 0)
        net_total += float(t["net_pnl"] or 0)
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
        res.info(f"  {strat} → {sym} {dirn} {t['exit_reason'] or '—'}: "
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
    # gross vs net: the broker positions page shows GROSS (pre-charges); this
    # report's P&L (here and the daily-loss/vs-yesterday lines below) is NET
    # (post brokerage+STT+exchange+GST+SEBI+stamp) — this line makes the two
    # reconcile instead of looking like a discrepancy (03-Jul: ₹3.55 gap on a
    # ₹-12.52/₹-16.07 day was confirmed to be exactly the day's total charges).
    res.info(f"P&L: gross ₹{gross_total:,.2f} | charges ₹{charges_total:,.2f} | "
             f"net ₹{net_total:,.2f} (broker positions page shows GROSS)")
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
    # daily_trade_review = the redesigned report (Phase C, 01-Jul-2026); the primary
    # deliverable going forward. daily_report.xlsx stays checked during the parallel
    # bake-in and is removed here when daily_report is retired.
    _check("daily_trade_review.xlsx",
           root / "reports/output" / f"daily_trade_review_report_{day}.xlsx", 2000)
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

def _enabled_strategy_names(config_dir: Path) -> list[str]:
    """Strategy names with `enabled: true` in config/strategies/*.yaml — the
    universe for STRATEGY HEALTH so a strategy with zero trades today still
    gets a line, not just the ones `trades` happens to have rows for today.
    Best-effort: [] if the dir/files are unreadable (never crashes the check)."""
    names: list[str] = []
    try:
        import yaml
        for f in sorted((config_dir / "strategies").glob("*.yaml")):
            try:
                raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if raw.get("enabled", True) and raw.get("name"):
                names.append(str(raw["name"]))
    except Exception:
        pass
    return names


def trade_strategy_health(store: StateStore, day: str, config_dir: Path) -> CheckResult:
    res = CheckResult("📉 STRATEGY HEALTH")
    rows = store.fetch_all(
        f"SELECT COALESCE(strategy,'(none)') s, direction, net_pnl FROM trades "
        f"WHERE substr(created_at,1,10)=? AND status IN {_CLOSED}",
        (day,),
    )
    by: dict[str, dict] = {}
    for r in rows:
        d = by.setdefault(r["s"], {"n": 0, "wins": 0, "pnl": 0.0})
        d["n"] += 1
        d["pnl"] += float(r["net_pnl"] or 0)
        if (r["net_pnl"] or 0) > 0:
            d["wins"] += 1

    all_names = sorted(set(_enabled_strategy_names(config_dir)) | set(by.keys()))
    if not all_names:
        res.info("No strategies configured and no closed trades today.")
    else:
        if not rows:
            res.info("No closed trades today — 0-trade line per enabled strategy below.")
        for s in all_names:
            d = by.get(s)
            if d is None:
                res.info(f"  {s}: 0 trades, — win, ₹0.00")
                continue
            wr = d["wins"] / d["n"] * 100 if d["n"] else 0
            line = f"  {s}: {d['n']} trades, {wr:.0f}% win, ₹{d['pnl']:+.2f}"
            if d["n"] >= 2 and wr == 0:
                res.warn(line + " — 0% win rate today")
            else:
                res.info(line)
        if rows:
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

def risk_events_check(store: StateStore, day: str, root: Path) -> CheckResult:
    res = CheckResult("🛡️ RISK EVENTS")

    def _rl_count(like: str) -> int:
        return int(_scalar(
            store,
            "SELECT COUNT(*) FROM reconciliation_log WHERE substr(ts,1,10)=? AND check_name=?",
            (day, like),
        ))

    # Log-based (the only reliable source — see _count_in_log). Also catch a
    # HARD_KILL still active right now even if its log line predates today.
    hard = _count_in_log(root, day, "HARD_KILL ACTIVATED")
    ks = store.fetch_one("SELECT state FROM kill_switch_state WHERE id=1", ())
    hard_active = bool(ks and ks["state"] == "HARD_KILL")
    if hard or hard_active:
        res.violation(
            f"HARD_KILL today: {hard} activation(s)" + (" (currently ACTIVE)" if hard_active else " (cleared)"),
            soft_kill_reason="HARD_KILL currently active" if hard_active else "HARD_KILL fired today",
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

    soft = _count_in_log(root, day, "SOFT_KILL ACTIVATED")
    res.info(f"ℹ️ SOFT_KILL activations today: {soft}")
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

    # DB clean: stuck signals / orphan orders / stuck in-flight trades.
    # A PROCESSING signal OLDER than 15 min is genuinely stuck (a worker died
    # mid-pipeline); freshly-PROCESSING ones are just being worked — so this is
    # not a false positive if run during market hours while the service is live.
    cutoff = (now_ist() - timedelta(minutes=15)).isoformat()
    stuck_sig = int(_scalar(store, "SELECT COUNT(*) FROM signals WHERE status='PROCESSING' AND received_at < ?", (cutoff,)))
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

    # Kill switch — will it still be active at the NEXT open?
    #
    # Ledger #8c (03-Aug-2026). This line used to say "needs deploy/resume.sh
    # before market open" for ANY non-INACTIVE state. That instruction was WRONG,
    # and it is the one an operator reads at night: `main.py:1902` calls
    # `clear_stale_state(<boot date>)`, which clears ANY kill whose triggered_at
    # DATE is strictly earlier than the boot date — regardless of type
    # (SOFT_KILL/HARD_KILL, scheduled or emergency). That is the HEADLESS
    # GUARANTEE (kill_switch.py:284-297). Since `nxt` is always AFTER day_date,
    # a kill visible here is a prior-day kill on `nxt` and auto-clears.
    #
    # ⛔ The discriminator is the DATE, not the reason. SCHEDULED_KILL_REASONS
    # governs `auto_clear_scheduled_kill()`, which is the SAME-DAY restart path —
    # a different question from "is tomorrow ready?". Reusing it here would
    # encode a distinction that decides nothing at this call site.
    ks = store.fetch_one(
        "SELECT state,reason,triggered_at FROM kill_switch_state WHERE id=1", ())
    state = (ks["state"] if ks else "INACTIVE") or "INACTIVE"
    if state == "INACTIVE":
        res.ok("Kill switch: INACTIVE (no --resume needed)")
    else:
        reason = (ks["reason"] or "")[:50] if ks else ""
        # Parse defensively: if we cannot establish the date we must NOT claim
        # it is safe — fall through to the actionable warning.
        trig_date = None
        try:
            raw = (ks["triggered_at"] or "") if ks else ""
            if raw:
                trig_date = datetime.fromisoformat(raw).date()
        except (ValueError, TypeError, KeyError, IndexError):
            trig_date = None

        if trig_date is not None and trig_date < nxt:
            res.info(
                f"Kill switch: {state} ({reason}) from {trig_date.isoformat()} — "
                f"prior-day at the next open, so the {nxt.isoformat()} 08:15 boot "
                f"auto-clears it (HEADLESS GUARANTEE). No action needed; "
                f"⛔ do NOT run deploy/resume.sh for this."
            )
        elif trig_date is not None:
            res.warn(
                f"Kill switch: {state} ({reason}) is dated {trig_date.isoformat()}, "
                f"NOT before the next trading day {nxt.isoformat()} — it will NOT "
                f"auto-clear. Investigate (clock skew or a future-dated row); "
                f"deploy/resume.sh is the sanctioned clear."
            )
        else:
            res.warn(
                f"Kill switch: {state} ({reason}) — triggered_at unreadable, so "
                f"auto-clear CANNOT be confirmed. Verify before market open; "
                f"deploy/resume.sh is the sanctioned clear."
            )

    # BUILD 1 (#3): base caps are the sole authority now (live_test deleted).
    eff_open, eff_daily = _effective_caps(app_config)
    res.info(f"ℹ️ Caps: tomorrow max_open={eff_open}, max_daily={eff_daily}")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 9 — Security (VM copy protection)   [VM Security Manager Phase 3]
# ─────────────────────────────────────────────────────────────────────────────

def _read_copy_audit(path: Path, day: str) -> tuple[dict, int, int]:
    """Tally today's copy-audit events. Returns (counts_by_event, bypasses, disables)."""
    counts: dict[str, int] = {}
    bypasses = disables = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return counts, 0, 0
    for line in lines:
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if str(rec.get("ts", ""))[:10] != day:
            continue
        ev = str(rec.get("event", "?"))
        counts[ev] = counts.get(ev, 0) + 1
        if ev == "COPY_BYPASS_DETECTED":
            bypasses += 1
        elif ev == "COPY_PROTECTION_DISABLED":
            disables += 1
    return counts, bypasses, disables


def _auditd_copy_rules() -> Optional[int]:
    """Count loaded auditd copy_attempt rules (None if it can't be checked)."""
    try:
        out = subprocess.run(["sudo", "-n", "auditctl", "-l"],
                             capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL)
        if out.returncode != 0:
            return None
        return sum(1 for ln in out.stdout.splitlines() if "copy_attempt" in ln)
    except Exception:
        return None


def _audit_log_writable(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return os.access(path.parent, os.W_OK)
    except Exception:
        return False


def _security_state_age_sec(root: Path) -> Optional[float]:
    """Seconds since the security-watcher last wrote its state file (None if absent).
    The watcher rewrites data_store/security_state.json every ~60s pass."""
    try:
        mtime = (root / "data_store" / "security_state.json").stat().st_mtime
    except OSError:
        return None
    return max(0.0, datetime.now().timestamp() - mtime)


def security_check(day: str, root: Path, config_dir: Path) -> CheckResult:
    """VM copy-protection EOD check (Phase 3). Three jobs:
      (1) daily copy-audit summary (tokens / allowed / denied / bypass / disable);
      (2) flag a COPY_BYPASS or copy-protection-DISABLED today as a VIOLATION so it
          escalates via the EOD CRITICAL report + email — but it does NOT trip the
          trading SOFT_KILL (a security event is not a trading-safety halt);
      (3) verify protection is still healthy: auditd copy_attempt rules loaded,
          copy_protection enabled, audit log writable, security-watcher alive."""
    res = CheckResult("🔒 SECURITY (COPY PROTECTION)")

    try:
        from scripts.security_monitor import SecConfig
        sec = SecConfig.load(config_dir / "security.yaml")
        audit_path = Path(sec.copy_audit_log_path)
        enabled = bool(sec.copy_protection_enabled)
    except Exception as exc:
        res.warn(f"security.yaml unreadable ({exc}) — copy-protection status unknown")
        return res

    # (1) daily copy-audit summary
    counts, bypasses, disables = _read_copy_audit(audit_path, day)
    issued = counts.get("COPY_TOKEN_ISSUED", 0)
    allowed = counts.get("COPY_ALLOWED", 0)
    denied = sum(v for k, v in counts.items() if k.startswith("COPY_DENIED_"))
    res.info(f"Copy events today: tokens issued {issued} | allowed {allowed} | denied {denied}")
    if counts:
        res.info("  " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    # (2) bypass / disable -> VIOLATION (escalate; NOT a trading soft-kill)
    if bypasses:
        res.violation(f"COPY BYPASS today: {bypasses} outbound copy(ies) without a token — "
                      f"possible VM->PC exfiltration. SECURITY violation (investigate; "
                      f"does not halt trading).")
    if disables:
        res.violation(f"Copy protection DISABLED today ({disables}x) — the gate was turned "
                      f"off. SECURITY violation (investigate; does not halt trading).")
    if not bypasses and not disables:
        res.ok("No copy-bypass / protection-disabled events today")

    # (3) protection healthy?
    if enabled:
        res.ok("copy_protection.enabled: true")
    else:
        res.violation("copy_protection.enabled: FALSE — VM->PC copying is currently unrestricted")

    n_rules = _auditd_copy_rules()
    if n_rules is None:
        res.info("ℹ️ auditd copy_attempt rules: could not check (auditctl/sudo unavailable here)")
    elif n_rules >= 3:
        res.ok(f"auditd copy_attempt rules: {n_rules} loaded")
    else:
        res.warn(f"auditd copy_attempt rules: {n_rules} (expected ≥3) — bypass detection degraded")

    if _audit_log_writable(audit_path):
        res.ok("copy audit log writable")
    else:
        res.warn(f"copy audit log dir not writable ({audit_path.parent}) — events may be lost")

    age = _security_state_age_sec(root)
    if age is None:
        res.warn("security-watcher: state file missing — watcher may never have run")
    elif age <= 300:
        res.ok(f"security-watcher: alive (last pass {int(age)}s ago)")
    else:
        res.warn(f"security-watcher: STALE — last pass {int(age)//60}m ago, service may be DOWN")
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


def _send(severity: str, title: str, body: str, config_dir: Path, dry_run: bool,
          sentinel_dir: str = "data_store") -> None:
    """Telegram (always attempt) + email (ALWAYS — every EOD report, not just
    CRITICAL days). Mirrors cron_officer's EOD email_backup: the Telegram send
    uses write_sentinel=False so a WARNING/INFO day doesn't rely on the
    notifier's CRITICAL-only sentinel path to reach the inbox, and a violation
    day doesn't get double-sentineled into two emails."""
    if dry_run:
        print(f"[DRY-RUN] would send {severity}: {title}")
        return
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=_log, config_dir=config_dir)
        if notifier is None:
            _log.info("system_manager.telegram_unconfigured")
        else:
            notifier.send(severity=severity, title=title, body=body,
                          source_module="system_manager", write_sentinel=False)
    except Exception as exc:
        _log.error("system_manager.send_failed", extra={"error": str(exc)})
    try:
        from alerts.critical import write_critical_sentinel
        write_critical_sentinel(title=title, body=body, source_module="system_manager",
                                context={"severity": severity}, sentinel_dir=sentinel_dir)
    except Exception as exc:
        _log.error("system_manager.email_sentinel_failed", extra={"error": str(exc)})


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


def slippage_overrides_check(store: StateStore, app_config, day: str) -> CheckResult:
    """Phase 3a — VISIBILITY ONLY (never a violation, never a SOFT_KILL). Shows
    the active entry-slippage tolerance overrides and how today's trades resolved
    against each rule, so Rama can see which manual overrides are in force and
    spot one that may be mis-set (e.g. a symbol override aborting most entries).
    Abort/fill detail lives in the slippage logs + Phase-2 reports; this is a
    one-glance summary."""
    res = CheckResult("🎯 SLIPPAGE TOLERANCE OVERRIDES (Phase 3a)")
    try:
        sc = app_config.system.entry_gate.slippage_control
        mode = getattr(sc, "mode", "?")
        if mode != "sl_fraction":
            res.info(f"ℹ️ slippage_control mode = {mode} — override hierarchy applies "
                     f"only to sl_fraction; nothing to show.")
            return res
        ov = getattr(sc, "overrides", None)
        glob = getattr(sc, "max_slippage_fraction", None)
        enabled = bool(getattr(ov, "enabled", False)) if ov else False
        n_sym = len(getattr(ov, "by_symbol", {}) or {}) if ov else 0
        n_strat = len(getattr(ov, "by_strategy", {}) or {}) if ov else 0
        n_band = len(getattr(ov, "by_price_band", {}) or {}) if ov else 0
        res.info(f"ℹ️ Global fraction {glob} | overrides {'ON' if enabled else 'OFF'} — "
                 f"{n_sym} symbol, {n_strat} strategy, {n_band} band")

        # Today's resolution usage by source (executed vs rejected). Note: REJECTED
        # counts ANY rejection reason (slippage abort, RR gate, EOD cutoff, …), so
        # the "review" marker is a hint to check the logs, never a hard signal.
        rows = store.fetch_all(
            "SELECT tolerance_source AS src, status, COUNT(*) AS n FROM trades "
            "WHERE substr(created_at,1,10)=? AND tolerance_source IS NOT NULL "
            "GROUP BY tolerance_source, status", (day,))
        usage: dict = {}
        for r in rows or []:
            src = r["src"] or "global"
            d = usage.setdefault(src, {"placed": 0, "rejected": 0, "total": 0})
            n = int(r["n"] or 0)
            d["total"] += n
            if r["status"] in _EXECUTED:
                d["placed"] += n
            elif r["status"] == "REJECTED":
                d["rejected"] += n
        if not usage:
            res.info("ℹ️ No trades resolved a tolerance source today.")
        else:
            for src in sorted(usage, key=lambda s: -usage[s]["total"]):
                d = usage[src]
                # Hint (not a warning) when a NON-global override rejected a lot.
                hint = ("  ← mostly rejected; review this override (logs)"
                        if (src != "global" and d["rejected"] >= 3
                            and d["rejected"] > d["placed"]) else "")
                res.info(f"ℹ️ {src}: {d['placed']} placed / {d['rejected']} rejected today{hint}")
    except Exception as exc:
        res.info(f"ℹ️ slippage override visibility unavailable: {exc}")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 11 — Stray .pyc (sourceless-import hazard)
# ─────────────────────────────────────────────────────────────────────────────

# Directories the stray-.pyc scan skips. AN ENTRY MEANS: "bytecode under here is
# not ours — its layout is upstream's business, not a break of OUR deployed-tree
# invariant." Both entries were MEASURED on 28-Jul-2026, not assumed:
#   venv - the third-party virtualenv (8,206 .py files). Present in the DEV root
#          only; on the VM it lives at ~/systems/venv, OUTSIDE the deployed tree,
#          so the nightly run never walks it and this entry is inert there.
#   sats - security-tooling scratch (bandit-env / semgrep-env — more venvs).
# ⛔ DO NOT ADD AN ENTRY SPECULATIVELY. Measured the same day: there are ZERO
#    .pyc files outside __pycache__ anywhere — PC tree, deployed tree, and inside
#    both venvs. So every entry here exists for SCAN COST and OWNERSHIP, never to
#    silence a known false positive. If you add one, say which file it silences.
_PYC_SCAN_EXCLUDED_DIRS: tuple[str, ...] = ("venv", "sats")


def stray_pyc_check(root: Path) -> CheckResult:
    """Detect `.pyc` files that can execute code which is not in HEAD.

    ⭐ THE PROPERTY, MEASURED (28-Jul-2026) RATHER THAN ASSUMED — PEP 3147:
      · `pkg/__pycache__/x.cpython-311.pyc` with no `x.py`  -> NOT importable.
        `__pycache__` is only a cache KEYED TO an existing source file.
      · `pkg/x.pyc` with no `x.py`                          -> **IMPORTABLE**.
        This is the legacy "sourceless" layout and it really does execute.
    So the hazard is not "a .pyc exists"; it is "a .pyc sits where a .py would".

    WHY THIS CHECK EXISTS AT ALL. `post-receive` deploys with `git checkout -f`,
    which overwrites tracked files but NEVER removes untracked ones — and all
    bytecode is untracked. Production proves the residue survives: the source of
    `tests/unit/test_daily_review.py` was deleted in 01e07b7, yet its bytecode has
    sat in the deployed tree for over two months. That residue is INERT (Type A),
    but the same mechanism would preserve an importable Type B indefinitely.

    ⭐ AND THERE IS NO OTHER BACKSTOP — MEASURED, AND IT CORRECTS AN EARLIER
    CLAIM OF MINE. I previously recorded that `.gitignore` had no bare `*.pyc`
    rule, so a Type B would at least show up as untracked in `git status` on the
    PC. That is FALSE: `.gitignore:46` is `*.py[cod]`, which matches `.pyc`.
    Verified by planting one — `git check-ignore` names line 46 and `git status`
    shows nothing. ⇒ git is blind to this class EVERYWHERE, not just on the VM,
    and the deployed tree has no `.git` at all. This check is the only detector.

    SEVERITY IS CONDITIONAL ON THE PROPERTY, NOT ON THE OCCURRENCE COUNT — a
    count-based ladder ("warn once, escalate on the second") would let the
    genuinely dangerous case sit at WARNING for a whole night.
      · sourceless (no sibling .py) -> violation() -> the EOD report goes CRITICAL
      · beside its .py              -> warn()      -> residue, cannot be imported

    ⛔ MONITORING ONLY: this never sets `soft_kill_reason`. A stray build artefact
       must not be able to stop tomorrow's trading.

    ⛔⛔ `git clean -xdf` MUST NEVER BE RUN IN THE DEPLOYED TREE. It is the
    obvious-looking way to fix what this check reports, and it would destroy
    `data_store/` (the live database AND its backups), `logs/`, and `.env` —
    all untracked or ignored there. Remove the offending file by path. This
    prohibition is written HERE, not only in the report, because here is where
    somebody reaching for the quick fix is standing.
    """
    res = CheckResult("🧹 STRAY .pyc")
    excluded = set(_PYC_SCAN_EXCLUDED_DIRS)
    try:
        stray = [
            p for p in root.rglob("*.pyc")
            if p.parent.name != "__pycache__"
            and not (set(p.relative_to(root).parts) & excluded)
        ]
    except OSError as exc:  # an unreadable tree must not kill the EOD report
        res.warn(f"scan could not complete: {exc}")
        return res

    if not stray:
        res.ok("no .pyc outside __pycache__ — nothing importable that HEAD lacks")
        return res

    for p in sorted(stray):
        rel = p.relative_to(root)
        if p.with_suffix(".py").exists():
            res.warn(f"{rel}: bytecode beside its own source — inert residue, "
                     "not importable on its own")
        else:
            res.violation(
                f"{rel}: SOURCELESS .pyc — importable with NO .py beside it. "
                "This can execute code that is not in HEAD, which silently "
                "breaks deployed-tree-equals-HEAD. Delete the file BY PATH "
                "(never `git clean -xdf` here)."
            )
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Check 12 — Deployed tree vs HEAD  (ledger #10 / IA-P10-01)
# ─────────────────────────────────────────────────────────────────────────────
# Deploy is `git --git-dir=<bare> --work-tree=<target> checkout -f`
# (deploy/hooks/post-receive). Its integrity was inherited from DISCIPLINE and
# never from a check — this campaign re-answered "is the running code the
# audited code" BY HAND ten times. This turns that ritual into a nightly
# artifact. `checkout -f` also leaves UNTRACKED files in place, so a stray .py
# is the other half of the question (check 11 covers only the .pyc slice).
#
# ⛔⛔ READ-ONLY BY CONSTRUCTION — and getting there required a correction that
# is worth stating, because the obvious form is WRONG:
#   · `git diff HEAD` needs an index. Pointing GIT_INDEX_FILE at an EMPTY temp
#     file makes git treat every tracked file as deleted. MEASURED on a
#     provably CLEAN tree: "1250 files changed, 344936 deletions(-)" — i.e. the
#     check would scream on its very first, perfectly healthy run.
#   · Using the repo's REAL index instead would let git refresh (write) the
#     DEPLOY repo's index from a monitoring job.
#   ⇒ We COPY the real index to a temp file and diff against the COPY. Correct
#     output, and the deploy repo is never written. Both halves are asserted by
#     test (clean⇒empty, planted change⇒detected, real index mtime unchanged).
#
# ⛔ This check NEVER sets soft_kill_reason. A dirty deployed tree is reported
# loudly and is NOT a reason to halt tomorrow automatically: the failure class
# (partial checkout, stray file, hook drift) is environment-caused, so the rule
# is DEGRADE+ALARM, not BLOCK. Escalation is the operator's call.

_DEPLOY_GIT_TIMEOUT_SEC = 30


def _resolve_deploy_git_dir(root: Path) -> Optional[Path]:
    """The bare repo the hook pushes into, else the tree's own .git (PC).

    VM layout: work tree /home/ubuntu/systems/trading-system has NO .git of its
    own — the bare repo lives at ~/trading-system.git. PC layout: an ordinary
    .git inside the tree. Returns None when neither is present, which is a
    DEGRADE case, not a violation.
    """
    bare = Path.home() / "trading-system.git"
    if (bare / "HEAD").exists():
        return bare
    local = root / ".git"
    if local.exists():
        return local
    return None


def deployed_tree_check(root: Path, git_dir: Optional[Path] = None) -> CheckResult:
    """Ledger #10 / IA-P10-01: does the deployed work tree still equal HEAD?"""
    res = CheckResult("🌳 DEPLOYED TREE vs HEAD")
    gd = git_dir or _resolve_deploy_git_dir(root)
    if gd is None:
        res.warn(
            "no git dir found (looked for ~/trading-system.git then "
            f"{root}/.git) — cannot verify deployed-tree-equals-HEAD here"
        )
        return res

    import tempfile

    common = ["git", f"--git-dir={gd}", f"--work-tree={root}"]
    tmp_index = None
    try:
        src_index = gd / "index"
        with tempfile.NamedTemporaryFile(prefix="sysmgr_idx_", delete=False) as fh:
            tmp_index = Path(fh.name)
        if src_index.exists():
            shutil.copyfile(src_index, tmp_index)
        else:
            # No index yet (a bare repo that has never checked out). An empty
            # index would report every file as deleted, so say so and stop
            # rather than emit a false violation.
            res.warn(f"{gd}/index does not exist — deploy has not checked out "
                     "here yet; nothing to compare")
            return res

        env = dict(os.environ, GIT_INDEX_FILE=str(tmp_index))

        head = subprocess.run(common + ["rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              timeout=_DEPLOY_GIT_TIMEOUT_SEC, env=env)
        head_sha = head.stdout.strip() if head.returncode == 0 else "<unknown>"

        diff = subprocess.run(common + ["diff", "--stat", "HEAD"],
                              capture_output=True, text=True,
                              timeout=_DEPLOY_GIT_TIMEOUT_SEC, env=env)
        if diff.returncode != 0:
            res.warn(f"git diff failed (rc={diff.returncode}): "
                     f"{(diff.stderr or '').strip()[:200]}")
            return res

        drift = [ln for ln in diff.stdout.splitlines() if ln.strip()]

        untracked = subprocess.run(
            common + ["ls-files", "--others", "--exclude-standard", "--", "*.py"],
            capture_output=True, text=True,
            timeout=_DEPLOY_GIT_TIMEOUT_SEC, env=env)
        stray_py = [ln for ln in untracked.stdout.splitlines() if ln.strip()] \
            if untracked.returncode == 0 else []

        if not drift and not stray_py:
            res.ok(f"deployed tree == HEAD ({head_sha}) — no tracked drift, "
                   "no untracked .py")
            return res

        if drift:
            res.violation(
                f"deployed tree DIFFERS from HEAD ({head_sha}) in "
                f"{len(drift) - 1 if len(drift) > 1 else len(drift)} file(s) — "
                "the running code is NOT the audited code. "
                f"First lines: {'; '.join(drift[:5])}"
            )
        # Output is bounded deliberately: this check runs LAST, so it is the
        # first thing Telegram's 4096-char truncation drops — on exactly the
        # night it matters most. The full list always survives in
        # reports/system_manager/<day>.txt and the email sentinel.
        for rel in sorted(stray_py)[:10]:
            res.violation(
                f"{rel}: UNTRACKED .py in the deployed tree — `checkout -f` "
                "leaves untracked files in place, so this can be imported and "
                "is in NO commit."
            )
        if len(stray_py) > 10:
            res.warn(f"...and {len(stray_py) - 10} more untracked .py file(s) "
                     "— see the saved report for the full list")
        return res

    except subprocess.TimeoutExpired:
        res.warn(f"git did not answer within {_DEPLOY_GIT_TIMEOUT_SEC}s — "
                 "check skipped, NOT a verdict")
        return res
    except (OSError, ValueError) as exc:
        res.warn(f"check could not complete: {exc}")
        return res
    finally:
        if tmp_index is not None:
            try:
                tmp_index.unlink()
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration / CLI
# ─────────────────────────────────────────────────────────────────────────────

def run(store: StateStore, app_config, day_date: date, config_dir: Path,
        db_path: Path, root: Path) -> tuple[str, int, int, List[str]]:
    """Run all 12 checks (each isolated) and build the report."""
    day = _day(day_date)
    prev = _prev_trading_day(day_date, config_dir)
    specs = [
        lambda: config_vs_actual_check(store, app_config, day, config_dir),
        lambda: order_quality_report(store, day),
        lambda: report_integrity_check(day, root),
        lambda: system_health_check(store, day, db_path, root),
        lambda: trade_strategy_health(store, day, config_dir),
        lambda: risk_events_check(store, day, root),
        lambda: compare_with_yesterday(store, day, _day(prev)),
        lambda: tomorrow_readiness_check(store, app_config, day_date, config_dir),
        lambda: security_check(day, root, config_dir),
        lambda: slippage_overrides_check(store, app_config, day),
        lambda: stray_pyc_check(root),
        lambda: deployed_tree_check(root),
    ]
    titles = ["CONFIG vs ACTUAL", "ORDER QUALITY", "REPORT INTEGRITY", "SYSTEM HEALTH",
              "STRATEGY HEALTH", "RISK EVENTS", "vs YESTERDAY", "TOMORROW READINESS",
              "SECURITY (COPY PROTECTION)", "SLIPPAGE OVERRIDES", "STRAY .pyc",
              "DEPLOYED TREE vs HEAD"]
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
        sentinel_dir = str(getattr(app_config.system.alerts, "sentinel_dir", "data_store"))
        _send(severity, title, report, args.config_dir, args.dry_run, sentinel_dir)

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
