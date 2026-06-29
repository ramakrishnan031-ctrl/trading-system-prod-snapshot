"""T2 backup-retention reaper — tests (a DELETION job, so safety is the focus).

Covers ChatGPT's checklist: keep-N selection + newest-preserved, dry-run vs apply,
the sanity cap (+ --max-delete override), scoped-pattern/symlink refusal, and the
locked / missing-file (race) handling. Exercises build_plan()/execute() directly,
so no Telegram/heartbeat side effects.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.backup_retention import build_plan, execute, format_report


def _mk(d: Path, name: str, mtime: int, size: int = 16) -> Path:
    p = d / name
    p.write_bytes(b"x" * size)
    os.utime(p, (mtime, mtime))
    return p


def _cat(plan, pattern):
    return next(c for c in plan.categories if c.pattern == pattern)


# ── keep-N + newest-preserved ─────────────────────────────────────────────────

def test_pre_keep20_delete5_newest_preserved(tmp_path):
    for i in range(25):                       # i=24 is newest (largest mtime)
        _mk(tmp_path, f"pre_deploy_{i:02d}.db", 1000 + i)
    plan = build_plan(tmp_path, max_delete=10)
    pre = _cat(plan, "pre_*")
    assert pre.present == 25 and len(pre.keep) == 20 and len(pre.delete) == 5
    assert not plan.abort                      # 5 <= 10
    # newest preserved; the 5 OLDEST are the delete candidates
    assert (tmp_path / "pre_deploy_24.db") in pre.keep
    assert {p.name for p in pre.delete} == {f"pre_deploy_{i:02d}.db" for i in range(5)}

    # dry-run deletes nothing
    res = execute(plan, apply=False)
    assert res.deleted == [] and all(p.exists() for p in pre.delete)

    # apply deletes exactly the 5 oldest; newest still there
    res = execute(plan, apply=True)
    assert len(res.deleted) == 5
    assert all(not p.exists() for p in pre.delete)
    assert (tmp_path / "pre_deploy_24.db").exists()
    assert res.freed_bytes == 5 * 16


def test_daily_keep14(tmp_path):
    for i in range(18):
        _mk(tmp_path, f"trading_system-2026-06-{i:02d}.db", 2000 + i)
    plan = build_plan(tmp_path, max_delete=10)
    ts = _cat(plan, "trading_system-*.db")
    assert len(ts.keep) == 14 and len(ts.delete) == 4
    assert (tmp_path / "trading_system-2026-06-17.db") in ts.keep  # newest kept


def test_analytics_keep14(tmp_path):
    for i in range(16):
        _mk(tmp_path, f"analytics-2026-06-{i:02d}.db", 3000 + i)
    plan = build_plan(tmp_path, max_delete=10)
    an = _cat(plan, "analytics-*.db")
    assert len(an.keep) == 14 and len(an.delete) == 2


def test_categories_are_disjoint(tmp_path):
    # pre_* must not swallow the daily files and vice-versa.
    _mk(tmp_path, "pre_telegram_deploy_x.db", 10)
    _mk(tmp_path, "pre_snr_v2_deploy_x_analytics.db", 11)   # deploy-analytics -> pre_*
    _mk(tmp_path, "trading_system-2026-06-01.db", 12)
    _mk(tmp_path, "analytics-2026-06-01.db", 13)
    plan = build_plan(tmp_path, max_delete=10)
    assert _cat(plan, "pre_*").present == 2
    assert _cat(plan, "trading_system-*.db").present == 1
    assert _cat(plan, "analytics-*.db").present == 1
    assert plan.total_delete == 0  # all under keep-N


# ── sanity cap ────────────────────────────────────────────────────────────────

def test_sanity_cap_aborts_and_override_permits(tmp_path):
    for i in range(35):                        # 35 - 20 = 15 deletes
        _mk(tmp_path, f"pre_deploy_{i:02d}.db", 1000 + i)
    plan = build_plan(tmp_path, max_delete=10)
    assert plan.total_delete == 15 and plan.abort is True
    # apply must delete NOTHING while aborting
    res = execute(plan, apply=True)
    assert res.deleted == []
    assert len({p for p in tmp_path.iterdir()}) == 35  # nothing removed

    # explicit override clears the abort and permits the catch-up
    plan2 = build_plan(tmp_path, max_delete=20)
    assert plan2.abort is False
    res2 = execute(plan2, apply=True)
    assert len(res2.deleted) == 15
    assert len(list(tmp_path.iterdir())) == 20  # newest 20 kept


# ── scoped-pattern / symlink refusal ─────────────────────────────────────────

def test_unmatched_file_refused(tmp_path):
    _mk(tmp_path, "pre_deploy_a.db", 10)
    _mk(tmp_path, "README.txt", 11)            # matches no category
    _mk(tmp_path, "trading_system_typo.db", 12)  # missing the '-' -> no match
    plan = build_plan(tmp_path, max_delete=10)
    names = {p.name for p in plan.unmatched}
    assert "README.txt" in names and "trading_system_typo.db" in names
    # unmatched files never appear in any delete list
    for c in plan.categories:
        assert not (set(c.delete) & set(plan.unmatched))
    res = execute(plan, apply=True)
    assert (tmp_path / "README.txt").exists()
    assert (tmp_path / "trading_system_typo.db").exists()


def test_symlink_refused(tmp_path):
    outside = tmp_path.parent / "outside_target.db"
    outside.write_bytes(b"keepme")
    link = tmp_path / "pre_evil_link.db"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")
    plan = build_plan(tmp_path, max_delete=10)
    # the symlink is refused (in unmatched), never a delete candidate
    assert link in plan.unmatched
    for c in plan.categories:
        assert link not in c.delete
    execute(plan, apply=True)
    assert outside.exists()  # symlink target untouched


# ── failure handling ─────────────────────────────────────────────────────────

def test_missing_file_race_skipped_gracefully(tmp_path):
    for i in range(25):
        _mk(tmp_path, f"pre_deploy_{i:02d}.db", 1000 + i)
    plan = build_plan(tmp_path, max_delete=10)
    victim = _cat(plan, "pre_*").delete[0]
    victim.unlink()                            # vanish between plan and execute
    res = execute(plan, apply=True)            # must not raise
    assert any(p == victim and "already-gone" in reason for p, reason in res.skipped)
    assert len(res.deleted) == 4               # the other 4 still deleted


def test_locked_file_skipped_not_force_deleted(tmp_path, monkeypatch):
    for i in range(25):
        _mk(tmp_path, f"pre_deploy_{i:02d}.db", 1000 + i)
    plan = build_plan(tmp_path, max_delete=10)
    locked = _cat(plan, "pre_*").delete[0]
    orig = Path.unlink

    def fake_unlink(self, *a, **k):
        if self.name == locked.name:
            raise PermissionError("file in use")
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    res = execute(plan, apply=True)
    assert any(p == locked and "locked" in reason for p, reason in res.skipped)
    assert locked.exists()                     # never force-deleted
    assert len(res.deleted) == 4


def test_empty_category_noop(tmp_path):
    _mk(tmp_path, "pre_only.db", 10)
    plan = build_plan(tmp_path, max_delete=10)
    assert _cat(plan, "analytics-*.db").present == 0
    assert plan.total_delete == 0


def test_format_report_smoke(tmp_path):
    _mk(tmp_path, "pre_deploy_a.db", 10)
    r = format_report(build_plan(tmp_path), apply=False)
    assert "BACKUP RETENTION" in r and "dry-run" in r
