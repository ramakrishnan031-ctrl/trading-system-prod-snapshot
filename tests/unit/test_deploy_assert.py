"""Deploy gate (15-Jul-2026): a lightweight pre-deploy tripwire that blocks ONLY on a
BLOCKER-class failure (DB integrity / schema-version parity). Monitoring / observability
defects are WARNINGS that never block a validated code deploy. New module => fail-on-old."""
from __future__ import annotations

from core.state_store import StateStore
from scripts.deploy_assert import BLOCKER, WARNING, run_assertions


def test_clean_db_has_no_blocker(tmp_path):
    db = tmp_path / "t.db"
    StateStore(db_path=db).close()          # fresh, current-schema DB
    rc, checks = run_assertions(db, tmp_path)
    assert rc == 0
    assert all(ok for _n, cls, ok, _d in checks if cls == BLOCKER)


def test_monitoring_degraded_warns_but_does_not_block(tmp_path):
    db = tmp_path / "t.db"
    StateStore(db_path=db).close()
    (tmp_path / "alert_watcher_degraded.json").write_text("{}")   # email delivery down
    rc, checks = run_assertions(db, tmp_path)
    assert rc == 0                                          # a monitoring defect NEVER blocks
    mon = [c for c in checks if c[0] == "monitoring.email_delivery"][0]
    assert mon[1] == WARNING and mon[2] is False           # WARNING class, and it failed


def test_missing_db_is_a_blocker(tmp_path):
    rc, checks = run_assertions(tmp_path / "nope.db", tmp_path)
    assert rc == 2                                          # BLOCKER → HOLD the deploy
    assert any(cls == BLOCKER and not ok for _n, cls, ok, _d in checks)
