"""
tests/unit/test_preflight_engine.py -- Phase B (engine readiness) checks +
phase_b_checks composition. HTTP is monkeypatched (no real app).
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from scripts.preflight.base import CheckContext, Criticality, Status
from scripts.preflight.checks import engine, phase_b_checks, services, vm_health


def _ctx(tmp_path, db=None):
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    return CheckContext(config_dir=cfg, db_path=db or (tmp_path / "trading_system.db"),
                        mode="live", as_of_date=date(2026, 6, 22), phase="B")


# 25-Jul-2026: capital now comes from fm_ledger + trades, not capital_snapshot.
# The old _db_capital() seeded a capital_snapshot row -- a table with 0 rows in
# production, so these checks were only ever exercised against a fixture-only
# shape. These builders model what the app actually writes.
_AS_OF = date(2026, 6, 22)


def _db_live_capital(tmp_path, opening=None, open_margin=(), pending_margin=(),
                     with_tables=True):
    """A DB shaped like production: one INIT ledger row per process start
    (bucket='both', full balance) plus trades carrying margin_reserved."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "trading_system.db"
    conn = sqlite3.connect(str(db))
    if with_tables:
        conn.execute("CREATE TABLE fm_ledger (ledger_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "ts TEXT NOT NULL, entry_type TEXT, amount REAL, bucket TEXT, "
                     "balance_before REAL, balance_after REAL, "
                     "date TEXT GENERATED ALWAYS AS (substr(ts,1,10)) STORED)")
        conn.execute("CREATE TABLE trades (trade_id TEXT PRIMARY KEY, status TEXT, "
                     "margin_reserved REAL)")
        if opening is not None:
            conn.execute("INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,"
                         "balance_after) VALUES(?,?,?,?,?,?)",
                         (_AS_OF.isoformat() + "T08:15:26.589351+05:30", "INIT",
                          opening, "both", 0.0, opening))
        for i, m in enumerate(open_margin):
            conn.execute("INSERT INTO trades VALUES(?,?,?)", ("o%d" % i, "OPEN", m))
        for i, m in enumerate(pending_margin):
            conn.execute("INSERT INTO trades VALUES(?,?,?)", ("p%d" % i, "PENDING_FILL", m))
    conn.commit()
    conn.close()
    return db


# ── app_health ────────────────────────────────────────────────────────────────────
def test_app_health(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (200, {"status": "healthy", "uptime_seconds": 12}))
    assert engine.AppHealthCheck().run(_ctx(tmp_path)).status is Status.PASS
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (503, {"status": "degraded", "checks": {"token": {"ok": False}, "db": {"ok": True}}}))
    res = engine.AppHealthCheck().run(_ctx(tmp_path))
    assert res.status is Status.FAIL and "token" in res.detail
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (0, {"error": "connection refused"}))
    assert engine.AppHealthCheck().run(_ctx(tmp_path)).status is Status.FAIL


def test_app_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (200, {"signals_received": 4, "open_positions": 1}))
    res = engine.AppMetricsCheck().run(_ctx(tmp_path))
    assert res.status is Status.PASS and res.metrics["signals_received"] == 4
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (0, {}))
    assert engine.AppMetricsCheck().run(_ctx(tmp_path)).status is Status.FAIL


# ── fund_manager_balance ────────────────────────────────────────────────────────────
# MEANING UNCHANGED (25-Jul-2026): still "is the fund manager's free cash sane?",
# still CRITICAL, still FAIL on NaN / <= 0. Only the SOURCE moved, off the
# permanently-empty capital_snapshot onto opening capital minus deployed margin.
def test_fund_manager_balance(tmp_path):
    ok = _db_live_capital(tmp_path, opening=10247.0, open_margin=(1000.0,))
    res = engine.FundManagerBalanceCheck().run(_ctx(tmp_path, ok))
    assert res.status is Status.PASS
    assert res.metrics["cash_floor"] == 9247.0     # 10247 - 1000 = the free-cash residual

    # fully deployed -> free cash <= 0 -> the SAME failure this check always made
    zero = _db_live_capital(tmp_path / "z", opening=10247.0, open_margin=(10247.0,))
    assert engine.FundManagerBalanceCheck().run(_ctx(tmp_path / "z", zero)).status is Status.FAIL

    # SQLite has no NaN: a NaN balance_after comes back as NULL, so this also
    # covers the NULL case. Both mean "the row exists but the balance is broken",
    # which is the crash-test NaN guard and must FAIL -- never be softened into
    # the "no INIT row yet" WARN.
    nan = _db_live_capital(tmp_path / "n", opening=float("nan"))
    res = engine.FundManagerBalanceCheck().run(_ctx(tmp_path / "n", nan))
    assert res.status is Status.FAIL
    assert "NaN/None" in res.detail


def test_fund_manager_no_init_row_warns_rather_than_failing(tmp_path):
    """No INIT row for the day => WARN, not CRITICAL. Genuinely transient now:
    preflight B runs 09:14 and the seed lands ~08:15, so this can only mean the
    app has not seeded today at all."""
    db = _db_live_capital(tmp_path, opening=None)
    res = engine.FundManagerBalanceCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.WARN
    assert "no INIT ledger row" in res.detail


def test_fund_manager_degrades_to_warn_when_the_db_is_unreadable(tmp_path):
    """B5: preflight is a boot gate. An unexpected condition must WARN with a real
    reason and NEVER let an exception escape -- a crash here costs a trading day."""
    db = _db_live_capital(tmp_path, with_tables=False)      # tables absent
    res = engine.FundManagerBalanceCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.WARN
    assert "unreadable" in res.detail and res.detail.strip()


# ── B7 anti-vacuity: the old PERMANENT warn is gone ───────────────────────────
_OLD_WARN = "no capital_snapshot row yet (app may still be initialising)"


def test_the_old_permanent_capital_snapshot_warn_is_gone(tmp_path):
    """capital_snapshot has 0 rows in production, so this check emitted the SAME
    warning on EVERY run, forever -- a permanent WARN wearing a transient's
    wording. Two assertions: the check no longer reads that table at all, and the
    exact DB shape that used to produce the string no longer produces it.

    Not vacuous: on the pre-fix code this same DB returns _OLD_WARN verbatim.
    Demonstrated by planting -- see docs/audit/capital_snapshot_redirect_25jul2026.md.
    """
    import inspect
    assert "FROM capital_snapshot" not in inspect.getsource(engine)

    db = tmp_path / "trading_system.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE capital_snapshot (id INTEGER PRIMARY KEY, cash_floor REAL, "
                 "margin_used REAL)")
    conn.commit()
    conn.close()
    res = engine.FundManagerBalanceCheck().run(_ctx(tmp_path, db))
    assert res.detail != _OLD_WARN


# ── capital_deployment (NEW, alert-only) ────────────────────────────────
def test_capital_deployment_is_alert_only_never_critical():
    """Ruling 3: the deployment % is its OWN check and can never escalate a run to
    CRITICAL. FundManagerBalanceCheck keeps exactly one meaning."""
    assert engine.CapitalDeploymentCheck.criticality is Criticality.WARN
    assert engine.FundManagerBalanceCheck.criticality is Criticality.CRITICAL
    names = [c.name for c in engine.CHECKS]
    assert "capital_deployment" in names and "fund_manager_balance" in names


def test_capital_deployment_emits_a_real_nonzero_pct(tmp_path):
    """B8: a flat book proves nothing -- assert against KNOWN open positions.
    2 open x 1500 margin against a 10000 opening = 30%."""
    db = _db_live_capital(tmp_path, opening=10000.0,
                          open_margin=(1500.0, 1500.0), pending_margin=(500.0,))
    res = engine.CapitalDeploymentCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.PASS
    assert res.metrics["capital_deployed_pct"] == 30.0
    assert res.metrics["margin_used"] == 3000.0
    assert res.metrics["margin_reserved"] == 500.0    # PENDING_FILL stays separate
    assert res.metrics["total_capital"] == 10000.0


def test_capital_deployment_div0_states_a_reason_never_a_silent_zero(tmp_path):
    """B3: the div-0 guard must say WHY. A silent 0.0 reads as "nothing deployed"
    when the truth is "we could not tell"."""
    db = _db_live_capital(tmp_path, opening=None, open_margin=(1500.0,))
    res = engine.CapitalDeploymentCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.WARN
    assert "unavailable" in res.detail and "no INIT ledger row" in res.detail
    assert "capital_deployed_pct" not in res.metrics     # absent, not a fake 0.0

    zero = _db_live_capital(tmp_path / "z", opening=0.0, open_margin=(1500.0,))
    res = engine.CapitalDeploymentCheck().run(_ctx(tmp_path / "z", zero))
    assert res.status is Status.WARN and "non-positive" in res.detail
    assert "capital_deployed_pct" not in res.metrics


def test_capital_deployment_degrades_and_never_raises(tmp_path):
    """B5 for the new check too."""
    db = _db_live_capital(tmp_path, with_tables=False)
    res = engine.CapitalDeploymentCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.WARN and "unreadable" in res.detail


# ── capital_deployment: FIX 2 -- it had no predicate at all ──────────────
# RED-first. Against 645728d these two go red because the check returned
# _passed() for ANY value; the three guards below them are green on BOTH
# trees and must stay that way.

def test_capital_deployment_the_live_10aug_shape_is_not_green(tmp_path):
    """THE MEASURED CONDITION, with the live numbers of 10-Aug-2026.

    Broker net at the 08:15 boot was 209.80 while two CNC positions carried
    446.106 + 460.91632 = 907.02 of margin. The check printed
    "capital deployed 432.3%" and PASSED, on the same boot the capital
    invariant hard-killed on NEGATIVE_MARGIN_AVAILABLE -844.08.

    432.3% is not a deployment level: the denominator is CASH and the
    numerator includes money already converted into a holding. The check must
    say so rather than print it as a percentage.
    """
    cash, legs = 209.80, (446.106, 460.91632)
    db = _db_live_capital(tmp_path, opening=cash, open_margin=legs)
    res = engine.CapitalDeploymentCheck().run(_ctx(tmp_path, db))

    assert res.status is Status.WARN, (
        f"the live 10-Aug shape is still reported as {res.status}: {res.detail}")
    # Derived from the operands, not transcribed: the value is whatever these
    # numbers make it, and the point is the STATUS, not the digits.
    assert res.metrics["capital_deployed_pct"] == round(sum(legs) / cash * 100, 2)
    assert "432.3%" in res.detail          # what the live run printed
    # It must name the bound, both bases, and the cause -- not just go amber.
    assert "over 100%" in res.detail
    assert "opening cash" in res.detail
    assert "different bases" in res.detail and "CARRIED" in res.detail
    # And it stays alert-only: a WARN can never escalate a run to CRITICAL.
    assert engine.CapitalDeploymentCheck.criticality is Criticality.WARN


def test_capital_deployment_warns_the_moment_used_passes_opening(tmp_path):
    """The bound is the capital identity, not a tuned number: strictly above
    100% warns, exactly 100% does not."""
    over = _db_live_capital(tmp_path / "over", opening=1000.0,
                            open_margin=(1000.01,))
    assert engine.CapitalDeploymentCheck().run(
        _ctx(tmp_path / "over", over)).status is Status.WARN

    exact = _db_live_capital(tmp_path / "exact", opening=1000.0,
                             open_margin=(1000.0,))
    res = engine.CapitalDeploymentCheck().run(_ctx(tmp_path / "exact", exact))
    assert res.status is Status.PASS
    assert res.metrics["capital_deployed_pct"] == 100.0


def test_capital_deployment_an_ordinary_carried_book_still_passes(tmp_path):
    """MUST-NOT-CHANGE guard: green pre-fix AND post-fix, VERIFIED by running
    it with scripts/preflight/checks/engine.py reverted to 645728d.

    A carried delivery position is NORMAL -- it must not alarm merely for
    existing. 2,000 of carry against 10,000 of cash is 20% and says nothing.

    Status and metrics only. An earlier form also asserted the new wording
    here, which made the guard red on the old tree for a cosmetic reason --
    i.e. it stopped being a two-tree control. The wording is pinned below
    instead."""
    db = _db_live_capital(tmp_path, opening=10000.0, open_margin=(2000.0,),
                          pending_margin=(500.0,))
    res = engine.CapitalDeploymentCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.PASS
    assert res.metrics["capital_deployed_pct"] == 20.0


def test_capital_deployment_pass_text_names_its_base(tmp_path):
    """Every percentage carries its base or it is not a number. Post-fix-only
    by construction: it pins wording the old check did not have."""
    db = _db_live_capital(tmp_path, opening=10000.0, open_margin=(2000.0,))
    res = engine.CapitalDeploymentCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.PASS
    assert "of opening cash" in res.detail


def test_capital_deployment_flat_book_passes(tmp_path):
    """MUST-NOT-CHANGE: green pre-fix and post-fix. Nothing open, nothing
    said."""
    db = _db_live_capital(tmp_path, opening=10000.0)
    res = engine.CapitalDeploymentCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.PASS
    assert res.metrics["capital_deployed_pct"] == 0.0


# ── ntp strict (Phase B escalation) ──────────────────────────────────────────────
def test_ntp_strict(tmp_path, monkeypatch):
    chk = engine.NtpStrictCheck()
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 0.1)
    assert chk.run(_ctx(tmp_path)).status is Status.PASS
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 1.0)
    assert chk.run(_ctx(tmp_path)).status is Status.WARN
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 3.0)
    assert chk.run(_ctx(tmp_path)).status is Status.FAIL
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: None)
    assert chk.run(_ctx(tmp_path)).status is Status.SKIPPED


# ── phase B composition ─────────────────────────────────────────────────────────────
def test_phase_b_composition():
    cs = phase_b_checks()
    names = [c.name for c in cs]
    assert "svc_trading_system" in names          # app process (alert-only)
    assert {"app_health", "app_metrics", "fund_manager_balance", "vm_ntp_strict"} <= set(names)
    assert "kite_token_fresh_today" in names       # fast re-gate
    # trading-system service check must NOT auto-start the app (token-watcher owns it)
    svc = next(c for c in cs if c.name == "svc_trading_system")
    assert svc.auto_fixable is False
