"""
tests/unit/test_check_cron_drift_content.py — Phase 3 content-drift pass.

Builds a "perfect" live crontab from generate(registry) (== no drift), then
mutates it for each of the 4 bidirectional cases. Uses the real enriched
registry so the test tracks the actual source of truth.

Run: python tests/unit/test_check_cron_drift_content.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.check_cron_drift import content_drift
from scripts.generate_crontab import compose, load_jobs

_REG = Path("config/cron_registry.yaml")


def _perfect_live() -> str:
    """The crontab that generate(registry) would install — by construction no drift."""
    return "\n".join(compose(j) for j in load_jobs(_REG) if j.get("enabled", True)) + "\n"


def _line_for(name: str) -> str:
    for j in load_jobs(_REG):
        if j["name"] == name:
            return compose(j)
    raise AssertionError(f"job {name} not in registry")


def test_no_drift_when_live_equals_generate() -> None:
    cd = content_drift(_REG, _perfect_live())
    assert not cd.has_any, (cd.absent_critical, cd.absent_warn, cd.unregistered, cd.unparseable)
    print("  OK no-drift baseline: live == generate(registry)")


def test_enabled_absent_is_critical() -> None:
    """A real (non-personal) job removed from live -> CRITICAL."""
    drop = _line_for("reconcile_positions")
    live = _perfect_live().replace(drop + "\n", "")
    cd = content_drift(_REG, live)
    assert "reconcile_positions" in cd.absent_critical
    assert "reconcile_positions" not in cd.absent_warn
    assert cd.has_critical
    print("  OK enabled non-personal absent -> CRITICAL")


def test_personal_tooling_absent_is_warn(tmp_path) -> None:
    """A personal_tooling job removed from live -> WARN, NOT critical.

    Builds its OWN registry rather than reading the live one. This test used to
    do `next(j for j in load_jobs(_REG) if j.get("personal_tooling"))`, which
    made it depend on a production job carrying that flag: when the four claude
    heartbeats (the only ones) were removed on 04-Aug-2026, it died with
    StopIteration. The behaviour under test never changed -- only the fixture
    source did, which is exactly what a test should not be exposed to.

    NOTE the branch is DORMANT, NOT DEAD: `personal_tooling: true` is still a
    valid registry flag, so this warn path fires the moment any future job
    carries it. That is why the branch is pinned here rather than deleted.
    """
    reg = tmp_path / "reg.yaml"
    reg.write_text(
        "jobs:\n"
        "  synthetic_normal:\n"
        "    enabled: true\n"
        "    personal_tooling: false\n"
        "    cron_expression: 0 1 * * *\n"
        "    command: scripts/synthetic_normal.py\n"
        "    env_wrapper: none\n"
        "    log_target: '>> logs/x.log 2>&1'\n"
        "  synthetic_personal:\n"
        "    enabled: true\n"
        "    personal_tooling: true\n"
        "    cron_expression: 0 2 * * *\n"
        "    command: scripts/synthetic_personal.py\n"
        "    env_wrapper: none\n"
        "    log_target: '>> logs/y.log 2>&1'\n",
        encoding="utf-8",
    )
    jobs = load_jobs(reg)
    name = next(j["name"] for j in jobs if j.get("personal_tooling"))
    perfect = "\n".join(compose(j) for j in jobs if j.get("enabled", True)) + "\n"
    dropped = next(compose(j) for j in jobs if j["name"] == name)
    live = perfect.replace(dropped + "\n", "")
    assert live != perfect, "fixture did not actually drop the personal_tooling line"

    cd = content_drift(reg, live)
    assert name in cd.absent_warn and name not in cd.absent_critical
    assert not cd.has_critical, "a missing heartbeat is not a trading outage"
    print(f"  OK personal_tooling absent ({name}) -> WARN, not CRITICAL")


def test_live_not_in_registry_is_unregistered() -> None:
    """A live job with no registry match -> RAN_UNVERIFIED (auto-discovery), not a drop."""
    new = ("0 0 * * * cd /home/ubuntu/systems/trading-system && set -a && . ./.env && set +a "
           "&& PYTHONPATH=. /home/ubuntu/systems/venv/bin/python scripts/some_future_job.py "
           ">> logs/x.log 2>&1")
    cd = content_drift(_REG, _perfect_live() + new + "\n")
    assert any("some_future_job.py" in u for u in cd.unregistered)
    assert not cd.absent_critical, "an unregistered live job is NOT a drop"
    print("  OK live-not-in-registry -> RAN_UNVERIFIED (no false drop)")


def test_unparseable_live_line_is_critical() -> None:
    cd = content_drift(_REG, _perfect_live() + "notacronline\n")
    assert "notacronline" in cd.unparseable and cd.has_critical
    print("  OK unparseable live line -> CRITICAL")


def test_reordered_wrapper_reads_as_drift() -> None:
    """FIX-189 guard: a reordered env wrapper composes differently -> the correct
    line is absent from live -> CRITICAL (normalization cannot hide it)."""
    good = _line_for("reconcile_positions")
    bad = good.replace("set -a && . ./.env && set +a", ". ./.env && set -a && set +a")
    assert bad != good
    live = _perfect_live().replace(good, bad)
    cd = content_drift(_REG, live)
    assert "reconcile_positions" in cd.absent_critical
    print("  OK reordered FIX-189 wrapper -> drift (CRITICAL)")


if __name__ == "__main__":
    tests = [
        test_no_drift_when_live_equals_generate,
        test_enabled_absent_is_critical,
        test_personal_tooling_absent_is_warn,
        test_live_not_in_registry_is_unregistered,
        test_unparseable_live_line_is_critical,
        test_reordered_wrapper_reads_as_drift,
    ]
    print("=" * 70)
    print("check_cron_drift content-pass -- Test Suite")
    print("=" * 70)
    failed = []
    for t in tests:
        print(f"\n-> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"  FAIL {e}")
    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)}")
        sys.exit(1)
    print(f"PASSED: all {len(tests)} tests")
