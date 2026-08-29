"""tests/unit/test_output_retention.py -- guards for the reports/logs reaper.

This job DELETES. Every test asserts a PROPERTY of the plan (keep-N holds,
ranking ignores mtime, families do not pool, unknown/undated files are refused)
rather than a fixed file count, so the suite still means something if the
window or the family list is retuned.

The load-bearing one is test_ranking_ignores_mtime_and_uses_filename_date: the
original implementation ranked by mtime, which under-retains across weekends and
ordered alert_watcher_2026-07-28.log (mtime 01-Aug) after files that were really
older. That defect is what these tests exist to keep out.

All filesystem work happens inside pytest's tmp_path.
"""
from __future__ import annotations

import logging
import os
import time

import pytest

from scripts.output_retention import (
    DEFAULT_MAX_DELETE,
    KEEP_PER_FAMILY,
    apply_plan,
    build_plan,
    file_date,
    retention_rule,
)

DAY = 86400.0


def _mk(path, mtime_age_days: float = 1.0, size: int = 64):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = time.time() - mtime_age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def tree(tmp_path):
    """A repo-shaped tree: one over-full family, one under-full family, plus the
    excluded, undated and unknown files the job must never delete."""
    out = tmp_path / "reports" / "output"
    logs = tmp_path / "logs"
    # 12 dated daily_report files, 2026-01-01 .. 2026-01-12
    for i in range(1, 13):
        _mk(out / f"daily_report_2026-01-{i:02d}.xlsx", size=1000)
    # a family smaller than the keep count
    for i in range(1, 4):
        _mk(out / f"daily_trade_review_report_2026-08-{i:02d}.xlsx", size=500)
    _mk(out / ".gitkeep", size=36)                       # git-tracked
    _mk(out / "STRAY_REPORT.xlsx", size=10)              # matches no family
    for i in range(1, 10):                               # 9 dated system logs
        _mk(logs / f"system_2026-01-{i:02d}.log", size=2000)
    _mk(logs / "cron-officer.log", size=10)              # append-mode, excluded
    _mk(logs / ".holiday_notified_2026-06-07", size=11)  # state marker, excluded
    return tmp_path


def _fam(plan, pattern):
    return next(f for f in plan.fams if f.pattern == pattern)


def test_keeps_exactly_n_newest_by_filename_date(tree):
    plan = build_plan(root=tree)
    fam = _fam(plan, "daily_report_*.xlsx")
    assert len(fam.keep) == KEEP_PER_FAMILY
    assert len(fam.delete) == fam.present - KEEP_PER_FAMILY
    kept = sorted(file_date(p) for p in fam.keep)
    assert kept == ["2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
                    "2026-01-10", "2026-01-11", "2026-01-12"]
    assert all(file_date(d) < min(kept) for d in fam.delete)


def test_ranking_ignores_mtime_and_uses_filename_date(tree):
    """The regression that motivated count-based retention.

    Give the OLDEST file by name the NEWEST mtime. An mtime-ranked reaper would
    keep it and drop a genuinely newer one; a filename-ranked reaper must not.
    """
    out = tree / "reports" / "output"
    oldest = out / "daily_report_2026-01-01.xlsx"
    newest = out / "daily_report_2026-01-12.xlsx"
    os.utime(oldest, (time.time(), time.time()))                    # freshest mtime
    os.utime(newest, (time.time() - 90 * DAY, time.time() - 90 * DAY))  # stalest

    plan = build_plan(root=tree)
    fam = _fam(plan, "daily_report_*.xlsx")
    assert oldest in fam.delete, "oldest-by-name must be deleted despite a fresh mtime"
    assert newest in fam.keep, "newest-by-name must be kept despite a stale mtime"


def test_families_are_counted_independently_not_pooled(tree):
    """A large family must not consume another family's keep budget."""
    plan = build_plan(root=tree)
    big = _fam(plan, "daily_report_*.xlsx")
    small = _fam(plan, "daily_trade_review_report_*.xlsx")
    logs = _fam(plan, "system_*.log")
    assert len(big.keep) == KEEP_PER_FAMILY
    assert small.delete == [], "an under-full family is never trimmed"
    assert len(small.keep) == small.present
    assert len(logs.keep) == KEEP_PER_FAMILY


def test_family_smaller_than_keep_is_untouched(tree):
    plan = build_plan(root=tree)
    fam = _fam(plan, "daily_trade_review_report_*.xlsx")
    assert fam.present < KEEP_PER_FAMILY
    assert fam.delete == []


def test_undated_member_is_refused_and_takes_no_keep_slot(tree):
    """A file matching a family but carrying no date cannot be ranked, so it is
    refused -- and must not displace a real, rankable file from the keep set."""
    out = tree / "reports" / "output"
    _mk(out / "daily_report_FINAL.xlsx", size=10)
    plan = build_plan(root=tree)
    fam = _fam(plan, "daily_report_*.xlsx")
    assert "daily_report_FINAL.xlsx" in {p.name for p, _ in plan.refused}
    assert "daily_report_FINAL.xlsx" not in {p.name for p in plan.to_delete}
    assert len(fam.keep) == KEEP_PER_FAMILY
    assert fam.undated == 1


def test_excluded_names_are_neither_deleted_nor_refused(tree):
    plan = build_plan(root=tree)
    excluded = {".gitkeep", "cron-officer.log", ".holiday_notified_2026-06-07"}
    assert not (excluded & {p.name for p in plan.to_delete})
    assert not (excluded & {p.name for p, _ in plan.refused})


def test_unknown_file_is_refused_and_never_deleted(tree):
    plan = build_plan(root=tree)
    assert "STRAY_REPORT.xlsx" in {p.name for p, _ in plan.refused}
    assert "STRAY_REPORT.xlsx" not in {p.name for p in plan.to_delete}


def test_apply_deletes_exactly_the_plan_and_leaves_keep_n(tree):
    plan = build_plan(root=tree)
    planned = {p.name for p in plan.to_delete}
    deleted, freed = apply_plan(plan, logging.getLogger("test"))

    assert deleted == len(planned)
    assert freed > 0
    out = tree / "reports" / "output"
    assert len(list(out.glob("daily_report_*.xlsx"))) == KEEP_PER_FAMILY
    assert (out / ".gitkeep").exists()
    assert (tree / "logs" / "cron-officer.log").exists()
    assert not (planned & {p.name for p in out.iterdir()})


def test_apply_is_idempotent(tree):
    apply_plan(build_plan(root=tree), logging.getLogger("test"))
    assert build_plan(root=tree).to_delete == []


def test_symlink_is_refused_not_followed(tree, tmp_path):
    outside = tmp_path / "outside_target.xlsx"
    outside.write_bytes(b"do not touch")
    link = tree / "reports" / "output" / "daily_report_2025-01-01.xlsx"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")
    plan = build_plan(root=tree)
    assert link.name not in {p.name for p in plan.to_delete}
    assert outside.exists()


def test_retention_rule_states_the_live_constants():
    rule = retention_rule()
    assert str(KEEP_PER_FAMILY) in rule
    assert str(DEFAULT_MAX_DELETE) in rule
    assert "mtime" not in rule.lower(), "the rule is count-based, not mtime-based"


def test_registry_entry_matches_the_scripts_own_contract():
    """A monitored job that never heartbeats is the failure mode this avoids
    (preflight_phase_a/b/c and sr_detector_backfill are already in that set)."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    reg = yaml.safe_load((root / "config" / "cron_registry.yaml").read_text(encoding="utf-8"))

    def _find(node):
        if isinstance(node, dict):
            if "output_retention" in node and isinstance(node["output_retention"], dict):
                return node["output_retention"]
            for v in node.values():
                hit = _find(v)
                if hit:
                    return hit
        return None

    entry = _find(reg)
    assert entry is not None, "output_retention must be registered in cron_registry.yaml"
    assert entry["monitored"] is True
    assert entry["enabled"] is True
    assert "--apply" in entry["command"]
