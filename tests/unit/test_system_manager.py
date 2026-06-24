"""
tests/unit/test_system_manager.py — TASK #5 System Manager EOD.

Covers the safety-critical logic: config-vs-actual violations + SOFT_KILL
reasons, effective caps (live_test_mode), peak-concurrency sweep, report
integrity, full-report summary, and the standalone SOFT_KILL trigger.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore
import scripts.system_manager as sm


# ── fixtures / builders ──────────────────────────────────────────────────────

def _store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "sm.db")


def _cfg(max_open=5, max_daily=20, live_test=False, lt_open=1, lt_daily=6,
         daily_loss_limit=300.0, max_posval=2500.0):
    risk = SimpleNamespace(
        max_open_positions=max_open, max_daily_trades=max_daily,
        live_test_mode=live_test, live_test_max_open_positions=lt_open,
        live_test_max_entries_per_day=lt_daily,
    )
    ps = SimpleNamespace(
        risk_per_trade_pct=0.01, max_concentration_pct=0.10,
        max_position_value_rs=max_posval,
    )
    cap = SimpleNamespace(daily_loss_limit=daily_loss_limit)
    return SimpleNamespace(system=SimpleNamespace(risk=risk, position_sizing=ps, capital=cap))


def _mk_trade(store, tid, *, status="CLOSED", net_pnl=0.0, qty=1, entry=100.0,
              entry_time=None, exit_time=None, day="2026-06-19", direction="LONG",
              strategy="stratA", exit_reason="TGT_HIT", exit_price=None,
              entry_target=None, sl=95.0, tgt=110.0):
    created = f"{day}T10:00:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"sig_{tid}", "SYM" + tid, "SC", strategy, created, created,
             created, "TRADED", f"fp_{tid}", day),
        )
        cur.execute(
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,sector,"
            "qty_planned,qty_filled,entry_target_price,entry_actual_price,sl_initial,"
            "tgt_initial,margin_reserved,risk_amount,created_at,entry_time,exit_time,"
            "exit_price,exit_reason,net_pnl,status,order_protocol,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"sig_{tid}", "SYM" + tid, direction, strategy, "FIN",
             qty, qty, entry_target if entry_target is not None else entry, entry,
             sl, tgt, 500.0, 50.0, created,
             entry_time or f"{day}T10:00:00+05:30", exit_time or f"{day}T11:00:00+05:30",
             exit_price, exit_reason, net_pnl, status, "LIMIT_TRIPLE", created),
        )


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_effective_caps_live_test_overrides():
    assert sm._effective_caps(_cfg(max_open=5, max_daily=20, live_test=False)) == (5, 20, False)
    assert sm._effective_caps(_cfg(live_test=True, lt_open=1, lt_daily=6)) == (1, 6, True)


def test_max_concurrent_positions_sweep(tmp_path):
    s = _store(tmp_path)
    # 3 trades; t1 & t2 overlap (peak 2), t3 disjoint
    _mk_trade(s, "t1", entry_time="2026-06-19T10:00:00+05:30", exit_time="2026-06-19T10:30:00+05:30")
    _mk_trade(s, "t2", entry_time="2026-06-19T10:15:00+05:30", exit_time="2026-06-19T10:45:00+05:30")
    _mk_trade(s, "t3", entry_time="2026-06-19T11:00:00+05:30", exit_time="2026-06-19T11:30:00+05:30")
    assert sm._max_concurrent_positions(s, "2026-06-19") == 2
    s.close()


# ── Check 1: config vs actual ────────────────────────────────────────────────

def test_config_clean_no_violation(tmp_path):
    s = _store(tmp_path)
    _mk_trade(s, "t1", net_pnl=20.0, entry=100.0)  # 1 position, within live_test caps
    r = sm.config_vs_actual_check(s, _cfg(live_test=True, lt_open=1, lt_daily=6), "2026-06-19")
    assert r.violations == 0 and r.soft_kill_reason is None
    s.close()


def test_config_daily_trade_cap_violation_triggers_softkill(tmp_path):
    s = _store(tmp_path)
    for i in range(4):  # 4 trades > live_test max_daily=3
        _mk_trade(s, f"t{i}", entry_time=f"2026-06-19T1{i}:00:00+05:30",
                  exit_time=f"2026-06-19T1{i}:30:00+05:30")
    r = sm.config_vs_actual_check(s, _cfg(live_test=True, lt_open=1, lt_daily=3), "2026-06-19")
    assert r.violations >= 1
    assert r.soft_kill_reason is not None and "daily trade cap" in r.soft_kill_reason
    s.close()


def test_config_position_value_violation(tmp_path):
    s = _store(tmp_path)
    _mk_trade(s, "t1", qty=100, entry=100.0)  # 100*100 = 10,000 > cap 2,500
    r = sm.config_vs_actual_check(s, _cfg(live_test=True, max_posval=2500.0), "2026-06-19")
    assert any("position value" in ln.lower() for ln in r.lines)
    assert r.violations >= 1
    s.close()


def test_config_daily_loss_violation(tmp_path):
    s = _store(tmp_path)
    _mk_trade(s, "t1", net_pnl=-400.0)  # loss 400 > limit 300
    r = sm.config_vs_actual_check(s, _cfg(live_test=True, daily_loss_limit=300.0), "2026-06-19")
    assert r.violations >= 1 and "loss" in (r.soft_kill_reason or "").lower()
    s.close()


# ── Check 3: report integrity ────────────────────────────────────────────────

def test_report_integrity_missing_and_present(tmp_path):
    from datetime import date
    root = tmp_path
    (root / "logs").mkdir()
    # Use TODAY's date: report_integrity_check warns when a file's mtime-day != the
    # checked `day` (an intentional anti-staleness guard). The file is written now,
    # so its mtime is today — a hardcoded past date ("2026-06-19") made this test
    # pass ONLY on that calendar day (don't date-couple tests; derive the date).
    today = date.today().isoformat()
    (root / "logs" / f"system_{today}.log").write_text("x" * 1000, encoding="utf-8")
    r = sm.report_integrity_check(today, root)
    # system log present (ok); others missing (warnings)
    assert any("system log" in ln and "✅" in ln for ln in r.lines)
    assert r.warnings >= 1


# ── Report generation + summary ──────────────────────────────────────────────

def test_generate_full_report_summary_and_softkill_line():
    import datetime as _dt
    a = sm.CheckResult("📊 A"); a.ok("fine")
    b = sm.CheckResult("🛡️ B"); b.violation("bad", soft_kill_reason="HARD_KILL fired today")
    c = sm.CheckResult("📁 C"); c.warn("missing")
    text, v, w, reasons = sm.generate_full_report([a, b, c], _dt.date(2026, 6, 19))
    assert v == 1 and w == 1 and reasons == ["HARD_KILL fired today"]
    assert "SOFT_KILL triggered for tomorrow" in text
    assert "SUMMARY: 1 violation(s), 1 warning(s)" in text


def test_generate_full_report_all_clear():
    import datetime as _dt
    a = sm.CheckResult("📊 A"); a.ok("fine")
    text, v, w, reasons = sm.generate_full_report([a], _dt.date(2026, 6, 19))
    assert v == 0 and w == 0 and not reasons
    assert "✅ All clear." in text


# ── SOFT_KILL trigger (real KillSwitch on tmp store) ─────────────────────────

def test_trigger_soft_kill_sets_state(tmp_path):
    s = _store(tmp_path)
    ok = sm.trigger_soft_kill(s, ["daily trade cap exceeded (7 > 3)"], "2026-06-19")
    assert ok
    row = s.fetch_one("SELECT state,reason,triggered_by FROM kill_switch_state WHERE id=1", ())
    assert row["state"] == "SOFT_KILL"
    assert row["triggered_by"] == "system_manager_eod"
    assert "System Manager EOD" in row["reason"]
    s.close()
