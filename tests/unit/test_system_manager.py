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


def _cfg(max_open=5, max_daily=20, daily_loss_limit_pct=0.03, max_posval_pct=0.40):
    # BUILD 1 (#1/#2/#3): live_test_* fields deleted; limits are now pct-of-capital.
    risk = SimpleNamespace(
        max_open_positions=max_open, max_daily_trades=max_daily,
        daily_loss_limit_pct=daily_loss_limit_pct,
    )
    ps = SimpleNamespace(
        risk_per_trade_pct=0.01, max_concentration_pct=0.10,
        max_position_value_pct=max_posval_pct,
    )
    cap = SimpleNamespace()
    return SimpleNamespace(system=SimpleNamespace(risk=risk, position_sizing=ps, capital=cap))


def _seed_capital(store, capital=10_000.0, day="2026-06-19"):
    """BUILD 1: seed an fm_ledger INIT row so the auditor's capital-relative
    thresholds (pct × day capital) resolve to a real basis."""
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO fm_ledger (ts, entry_type, amount, bucket, "
            "balance_before, balance_after) VALUES (?,?,?,?,?,?)",
            (f"{day}T09:15:00+05:30", "INIT", capital, "both", 0.0, capital),
        )


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

def test_effective_caps_returns_base_caps():
    # BUILD 1 (#3): live_test override deleted — base caps are the sole authority.
    assert sm._effective_caps(_cfg(max_open=5, max_daily=20)) == (5, 20)
    assert sm._effective_caps(_cfg(max_open=3, max_daily=6)) == (3, 6)


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
    _seed_capital(s)  # ₹10k day capital
    _mk_trade(s, "t1", net_pnl=20.0, entry=100.0)  # 1 position, within caps
    r = sm.config_vs_actual_check(s, _cfg(), "2026-06-19", tmp_path)
    assert r.violations == 0 and r.soft_kill_reason is None
    s.close()


def test_config_daily_trade_cap_violation_triggers_softkill(tmp_path):
    s = _store(tmp_path)
    _seed_capital(s)
    for i in range(4):  # 4 trades > max_daily=3
        _mk_trade(s, f"t{i}", entry_time=f"2026-06-19T1{i}:00:00+05:30",
                  exit_time=f"2026-06-19T1{i}:30:00+05:30")
    r = sm.config_vs_actual_check(s, _cfg(max_daily=3), "2026-06-19", tmp_path)
    assert r.violations >= 1
    assert r.soft_kill_reason is not None and "daily trade cap" in r.soft_kill_reason
    s.close()


def test_config_position_value_violation(tmp_path):
    s = _store(tmp_path)
    _seed_capital(s)  # cap = 40% × 10k = ₹4,000
    _mk_trade(s, "t1", qty=100, entry=100.0)  # 100*100 = 10,000 > cap 4,000
    r = sm.config_vs_actual_check(s, _cfg(max_posval_pct=0.40), "2026-06-19", tmp_path)
    assert any("position value" in ln.lower() for ln in r.lines)
    assert r.violations >= 1
    s.close()


def test_config_daily_loss_violation(tmp_path):
    s = _store(tmp_path)
    _seed_capital(s)  # limit = 3% × 10k = ₹300
    _mk_trade(s, "t1", net_pnl=-400.0)  # loss 400 > limit 300
    r = sm.config_vs_actual_check(s, _cfg(daily_loss_limit_pct=0.03), "2026-06-19", tmp_path)
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


# ── stray .pyc detector (28-Jul-2026) ────────────────────────────────────────
# ⭐ THE POINT OF THESE TESTS: the detector's trigger condition has MEASURED ZERO
# occurrences in both trees. A guard whose firing path has never executed is not
# a guard yet — it is a guard-shaped thing. Every branch below is made to fire.
# ⛔ Every plant lives in pytest's `tmp_path`, so nothing is ever written into the
#    repo and NOTHING is ever planted in the deployed tree. Isolation is
#    structural here, not a cleanup step that could be forgotten.

def _plant(root, relpath: str, body: bytes = b"\x00pyc") -> None:
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)


def test_stray_pyc_sourceless_is_CRITICAL(tmp_path):
    """Type B with NO sibling .py — the importable case. This is the one that
    silently breaks deployed-tree-equals-HEAD, so it must reach `violation`."""
    _plant(tmp_path, "scripts/ghost.pyc")
    res = sm.stray_pyc_check(tmp_path)
    assert res.violations == 1, res.lines
    assert res.warnings == 0, res.lines
    assert "SOURCELESS" in " ".join(res.lines)
    # ⛔ monitoring only — a build artefact must never stop tomorrow's trading.
    assert res.soft_kill_reason is None


def test_stray_pyc_beside_its_source_is_WARNING_not_critical(tmp_path):
    """Same file, but its .py is present ⇒ inert residue. Severity is conditional
    on the PROPERTY (importability), never on how many times it has been seen."""
    _plant(tmp_path, "scripts/ghost.pyc")
    (tmp_path / "scripts" / "ghost.py").write_text("x = 1\n")
    res = sm.stray_pyc_check(tmp_path)
    assert res.violations == 0, res.lines
    assert res.warnings == 1, res.lines
    assert res.soft_kill_reason is None


def test_stray_pyc_inside_excluded_dir_is_SILENT(tmp_path):
    """An entry in _PYC_SCAN_EXCLUDED_DIRS means 'not ours'. Planted in the
    WORST shape (sourceless) to prove exclusion beats severity."""
    _plant(tmp_path, "venv/lib/site-packages/vendored.pyc")
    _plant(tmp_path, "sats/semgrep-env/thing.pyc")
    res = sm.stray_pyc_check(tmp_path)
    assert res.violations == 0 and res.warnings == 0, res.lines


def test_stray_pyc_type_a_inside_pycache_is_SILENT(tmp_path):
    """Type A: an orphan inside __pycache__ with no source anywhere. MEASURED
    28-Jul to be NOT importable (PEP 3147 — __pycache__ is a cache keyed to an
    existing .py), so it is a search nuisance and must not raise anything."""
    _plant(tmp_path, "scripts/__pycache__/ghost.cpython-311.pyc")
    res = sm.stray_pyc_check(tmp_path)
    assert res.violations == 0 and res.warnings == 0, res.lines


def test_stray_pyc_clean_tree_is_SILENT(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "real.py").write_text("x = 1\n")
    res = sm.stray_pyc_check(tmp_path)
    assert res.violations == 0 and res.warnings == 0, res.lines
    assert any("no .pyc outside __pycache__" in ln for ln in res.lines)


def test_stray_pyc_the_real_repo_is_currently_clean():
    """The live tree, read-only. Documents the measured 28-Jul baseline: if this
    ever goes red, a real stray appeared — that is the finding, not a flake."""
    res = sm.stray_pyc_check(Path(__file__).parent.parent.parent)
    assert res.violations == 0, res.lines
