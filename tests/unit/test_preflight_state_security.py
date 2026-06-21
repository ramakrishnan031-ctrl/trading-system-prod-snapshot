"""
tests/unit/test_preflight_state_security.py -- Phase-A groups 5 (state) + 7 (security).
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import date
from pathlib import Path

from scripts.preflight.base import CheckContext, Status
from scripts.preflight.checks import security, state


def _ctx(tmp_path: Path, db: Path, **kw) -> CheckContext:
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    kw.setdefault("as_of_date", date(2026, 6, 22))
    return CheckContext(config_dir=cfg, db_path=db, **kw)


def _make_state_db(tmp_path, kill_state="INACTIVE", kill_triggered=None, trades=(), orders=()):
    d = tmp_path / "data_store"
    d.mkdir(parents=True, exist_ok=True)
    db = d / "trading_system.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE kill_switch_state (id INTEGER PRIMARY KEY, state TEXT, "
                 "reason TEXT, triggered_at TEXT, triggered_by TEXT)")
    conn.execute("INSERT INTO kill_switch_state(id,state,triggered_at) VALUES(1,?,?)",
                 (kill_state, kill_triggered))
    conn.execute("CREATE TABLE trades (trade_id TEXT PRIMARY KEY, status TEXT, "
                 "needs_tgt_retry INTEGER DEFAULT 0)")
    for i, (st, ntr) in enumerate(trades):
        conn.execute("INSERT INTO trades(trade_id,status,needs_tgt_retry) VALUES(?,?,?)",
                     (f"t{i}", st, ntr))
    conn.execute("CREATE TABLE orders (order_id TEXT PRIMARY KEY, status TEXT)")
    for i, st in enumerate(orders):
        conn.execute("INSERT INTO orders(order_id,status) VALUES(?,?)", (f"o{i}", st))
    conn.commit()
    conn.close()
    return db


# ── state: kill switch ────────────────────────────────────────────────────────────
def test_kill_inactive_pass(tmp_path):
    db = _make_state_db(tmp_path, kill_state="INACTIVE")
    assert state.KillSwitchStateCheck().run(_ctx(tmp_path, db)).status is Status.PASS


def test_kill_same_day_critical(tmp_path):
    db = _make_state_db(tmp_path, kill_state="HARD_KILL",
                        kill_triggered="2026-06-22T10:00:00+05:30")
    res = state.KillSwitchStateCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.FAIL and "HARD_KILL" in res.detail


def test_kill_prior_day_warns(tmp_path):
    db = _make_state_db(tmp_path, kill_state="SOFT_KILL",
                        kill_triggered="2026-06-21T15:15:00+05:30")
    res = state.KillSwitchStateCheck().run(_ctx(tmp_path, db))
    assert res.status is Status.WARN and "prior-day" in res.detail


# ── state: positions / orders / residue ─────────────────────────────────────────
def test_open_positions(tmp_path):
    db = _make_state_db(tmp_path, trades=[("OPEN", 0), ("CLOSED", 0)])
    assert state.OpenPositionsCheck().run(_ctx(tmp_path, db)).status is Status.FAIL
    db2 = _make_state_db(tmp_path / "b", trades=[("CLOSED", 0)])
    assert state.OpenPositionsCheck().run(_ctx(tmp_path / "b", db2)).status is Status.PASS


def test_open_orders(tmp_path):
    db = _make_state_db(tmp_path, orders=["OPEN", "COMPLETE"])
    assert state.OpenOrdersCheck().run(_ctx(tmp_path, db)).status is Status.FAIL
    db2 = _make_state_db(tmp_path / "b", orders=["COMPLETE", "CANCELLED"])
    assert state.OpenOrdersCheck().run(_ctx(tmp_path / "b", db2)).status is Status.PASS


def test_stuck_exiting(tmp_path):
    db = _make_state_db(tmp_path, trades=[("EXITING", 0)])
    assert state.StuckExitingCheck().run(_ctx(tmp_path, db)).status is Status.WARN
    db2 = _make_state_db(tmp_path / "b", trades=[("CLOSED", 0)])
    assert state.StuckExitingCheck().run(_ctx(tmp_path / "b", db2)).status is Status.PASS


def test_needs_tgt_retry(tmp_path):
    db = _make_state_db(tmp_path, trades=[("CLOSED", 1)])
    assert state.NeedsTgtRetryCheck().run(_ctx(tmp_path, db)).status is Status.WARN
    db2 = _make_state_db(tmp_path / "b", trades=[("CLOSED", 0)])
    assert state.NeedsTgtRetryCheck().run(_ctx(tmp_path / "b", db2)).status is Status.PASS


# ── security ─────────────────────────────────────────────────────────────────────
def _write_security_yaml(ctx, enabled=True):
    (ctx.config_dir / "security.yaml").write_text(
        f"copy_protection:\n  enabled: {'true' if enabled else 'false'}\n"
        f"  time_lock_start: 18\n  time_lock_end: 8\n", encoding="utf-8")


def test_security_watcher_fresh_stale_missing(tmp_path):
    db = _make_state_db(tmp_path)
    ctx = _ctx(tmp_path, db)
    chk = security.SecurityWatcherAliveCheck()
    sf = db.parent / "security_state.json"
    # missing
    assert chk.run(ctx).status is Status.FAIL
    # fresh
    sf.write_text("{}", encoding="utf-8")
    assert chk.run(ctx).status is Status.PASS
    # stale (2h old)
    old = time.time() - 7200
    os.utime(sf, (old, old))
    assert chk.run(ctx).status is Status.FAIL


def test_copy_protection_state(tmp_path):
    db = _make_state_db(tmp_path)
    ctx = _ctx(tmp_path, db)
    chk = security.CopyProtectionStateCheck()
    assert chk.run(ctx).status is Status.FAIL  # no security.yaml
    _write_security_yaml(ctx, enabled=True)
    assert chk.run(ctx).status is Status.PASS
    _write_security_yaml(ctx, enabled=False)
    assert chk.run(ctx).status is Status.FAIL


def test_auth_recovery_primed(tmp_path):
    db = _make_state_db(tmp_path)
    assert security.AuthRecoveryPrimedCheck().run(_ctx(tmp_path, db)).status is Status.PASS


def test_time_lock_window(tmp_path, monkeypatch):
    db = _make_state_db(tmp_path)
    ctx = _ctx(tmp_path, db)
    _write_security_yaml(ctx, enabled=True)
    chk = security.TimeLockWindowCheck()
    monkeypatch.setattr(security, "_current_hour_ist", lambda: 10)  # outside lock
    assert chk.run(ctx).status is Status.PASS
    monkeypatch.setattr(security, "_current_hour_ist", lambda: 20)  # inside (>=18)
    assert chk.run(ctx).status is Status.WARN
    monkeypatch.setattr(security, "_current_hour_ist", lambda: 3)   # inside (<8)
    assert chk.run(ctx).status is Status.WARN
