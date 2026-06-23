"""
tests/unit/test_generate_crontab.py — self-maintaining cron generator.

Locks the migration-gate proof: compose() and parse() are exact, byte-for-byte
inverses, and compose() is deterministic. Fixtures are VERBATIM real lines from
the live crontab (one per env_wrapper variant + the tricky cases: %-escaped
backup, no-PYTHONPATH variant, inconsistent markers, claude heartbeat). Drift
guards prove a reordered/dropped env wrapper does NOT silently parse as a valid
python job (so downstream drift detection cannot be fooled by over-normalization).

Run: python tests/unit/test_generate_crontab.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.generate_crontab import compose, parse

# ── VERBATIM live lines, one per structural variant ─────────────────────────
_P = "/home/ubuntu/systems/trading-system"
_V = "/home/ubuntu/systems/venv/bin/python"
_M = f"{_P}/data_store/cron_marks"

FIXTURES = {
    # shell · env none · no log · marker
    "log_cleanup":
        f"0 0 * * * find {_P}/logs -name '*.log' -mtime +30 -delete; rc=$?; "
        f'mkdir -p {_M}; echo "$rc $(date -Iseconds)" > {_M}/log_cleanup.done',
    # shell · env none · log · marker · %-escaped $(date +\\%Y...)
    "db_backup":
        f'0 1 * * * sqlite3 {_P}/data_store/trading_system.db ".backup '
        f'{_P}/data_store/backups/trading_system-$(date +\\%Y-\\%m-\\%d).db" '
        f">> {_P}/logs/cron-db-backup.log 2>&1; rc=$?; mkdir -p {_M}; "
        f'echo "$rc $(date -Iseconds)" > {_M}/db_backup.done',
    # shell · env none · log · marker · escaped find parens
    "backup_retention":
        f'0 2 * * * find {_P}/data_store/backups \\( -name "trading_system-*.db" '
        f'-o -name "analytics-*.db" \\) -mtime +7 -delete >> {_P}/logs/cron-db-backup.log '
        f'2>&1; rc=$?; mkdir -p {_M}; echo "$rc $(date -Iseconds)" > {_M}/backup_retention.done',
    # python (PYTHONPATH) · marker · intraday cron expr
    "capture_metrics":
        f"*/5 9-15 * * 1-5 cd {_P} && set -a && . ./.env && set +a && PYTHONPATH=. "
        f"{_V} scripts/capture_metrics_baseline.py >> logs/cron-metrics.log 2>&1; rc=$?; "
        f'mkdir -p {_M}; echo "$rc $(date -Iseconds)" > {_M}/capture_metrics.done',
    # python (PYTHONPATH) · no marker · --account arg
    "reconcile_positions":
        f"45 15 * * 1-5 cd {_P} && set -a && . ./.env && set +a && PYTHONPATH=. "
        f"{_V} scripts/reconcile_positions.py --account LFL836 >> logs/cron-reconcile-positions.log 2>&1",
    # python (PYTHONPATH) · marker · python -m module
    "preflight_phase_a":
        f"30 8 * * 1-5 cd {_P} && set -a && . ./.env && set +a && PYTHONPATH=. "
        f"{_V} -m scripts.preflight.orchestrator --phase A >> logs/preflight.log 2>&1; rc=$?; "
        f'mkdir -p {_M}; echo "$rc $(date -Iseconds)" > {_M}/preflight_phase_a.done',
    # python_nopath (NO PYTHONPATH=.) · no marker
    "wal_checkpoint":
        f"0 16 * * 1-5 cd {_P} && set -a && . ./.env && set +a && "
        f"{_V} scripts/wal_checkpoint.py >> logs/wal_checkpoint.log 2>&1",
    # python_nopath · python -m module
    "daily_report":
        f"5 16 * * 1-5 cd {_P} && set -a && . ./.env && set +a && "
        f"{_V} -m reports.daily_report >> logs/daily_report.log 2>&1",
    # claude_cd · no marker · different cd dir
    "claude_heartbeat":
        '30 5 * * * cd /home/ubuntu/tools/claude && /usr/bin/claude -p '
        '"Generate a random 8-character string. Print only the string." '
        ">> /home/ubuntu/tools/claude/cron.log 2>&1",
}

_EXPECT_ENV = {
    "log_cleanup": "none", "db_backup": "none", "backup_retention": "none",
    "capture_metrics": "python", "reconcile_positions": "python",
    "preflight_phase_a": "python", "wal_checkpoint": "python_nopath",
    "daily_report": "python_nopath", "claude_heartbeat": "claude_cd",
}


def test_round_trip_byte_exact() -> None:
    """compose(parse(line)) == line, byte-for-byte, for every variant."""
    for name, line in FIXTURES.items():
        rebuilt = compose(parse(line))
        assert rebuilt == line, f"{name}: round-trip drift\n  in : {line!r}\n  out: {rebuilt!r}"
    print(f"  OK round-trip byte-exact: {len(FIXTURES)} variants")


def test_env_wrapper_classification() -> None:
    """parse() classifies the env_wrapper of each variant correctly."""
    for name, line in FIXTURES.items():
        got = parse(line)["env_wrapper"]
        assert got == _EXPECT_ENV[name], f"{name}: env_wrapper {got!r} != {_EXPECT_ENV[name]!r}"
    print(f"  OK env_wrapper classified for all {len(FIXTURES)} variants")


def test_compose_deterministic() -> None:
    """compose() depends only on fields — no timestamps/random/host lookups."""
    for name, line in FIXTURES.items():
        f = parse(line)
        assert compose(f) == compose(f), f"{name}: non-deterministic compose()"
    print("  OK compose() deterministic (generate twice -> identical)")


def test_marker_and_log_extracted() -> None:
    """marker_name + log_target parsed where present; None where absent."""
    db = parse(FIXTURES["db_backup"])
    assert db["marker_name"] == "db_backup" and db["log_target"] == f">> {_P}/logs/cron-db-backup.log 2>&1"
    lc = parse(FIXTURES["log_cleanup"])
    assert lc["marker_name"] == "log_cleanup" and lc["log_target"] is None
    rp = parse(FIXTURES["reconcile_positions"])
    assert rp["marker_name"] is None and rp["log_target"].endswith("2>&1")
    # %-escape preserved verbatim inside command
    assert "$(date +\\%Y-\\%m-\\%d)" in parse(FIXTURES["db_backup"])["command"]
    print("  OK marker/log extraction + %-escape preserved")


def test_drift_guard_reordered_wrapper_not_accepted() -> None:
    """A reordered/dropped env wrapper must NOT parse as a valid python job —
    else downstream drift detection could be fooled. It falls to env=none with
    the broken wrapper living in `command`, so compose != the correct line."""
    # `. ./.env` BEFORE `set -a` => env NOT exported (functionally broken)
    reordered = (f"45 15 * * 1-5 cd {_P} && . ./.env && set -a && set +a && PYTHONPATH=. "
                 f"{_V} scripts/reconcile_positions.py --account LFL836 >> logs/cron-reconcile-positions.log 2>&1")
    pf = parse(reordered)
    assert pf["env_wrapper"] == "none", "reordered wrapper must NOT classify as python"
    assert pf != parse(FIXTURES["reconcile_positions"]), "reordered must differ from correct (=> drift)"
    # dropped wrapper entirely
    dropped = (f"45 15 * * 1-5 cd {_P} && {_V} scripts/reconcile_positions.py "
               f"--account LFL836 >> logs/cron-reconcile-positions.log 2>&1")
    assert parse(dropped)["env_wrapper"] == "none"
    assert parse(dropped) != parse(FIXTURES["reconcile_positions"])
    print("  OK drift guard: reordered/dropped wrapper != valid python (drift-detectable)")


if __name__ == "__main__":
    tests = [
        test_round_trip_byte_exact,
        test_env_wrapper_classification,
        test_compose_deterministic,
        test_marker_and_log_extracted,
        test_drift_guard_reordered_wrapper_not_accepted,
    ]
    print("=" * 70)
    print("generate_crontab.py -- Test Suite")
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
