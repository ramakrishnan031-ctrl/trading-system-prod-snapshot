"""NI-14 — panel status follows the SEVERITY of findings, not their existence.

THE DEFECT (23-Aug-2026): three sites keyed on whether a findings list was
non-empty, which made the GREEN state UNREACHABLE for any source able to emit an
INFO finding:

    status = ("critical" if any CRITICAL else "warn" if findings else "ok")
    "status": "OK_WITH_FINDINGS" if detected else "OK"

On the live box the security monitor carries a standing INFO `rootspike`
finding, so the security panel read `warn` whenever that finding was present and
the field lost all discrimination. Measured 23-Aug: `last_run.json` =
`clean:false, findings_count:1, max_severity:"INFO"`.

WHY THE EXISTING SUITE DID NOT CATCH IT: `test_read_security_absent_clean_dirty_
stale` covers `clean:True` (no findings) and `max_severity:"WARNING"` (-> MEDIUM)
— it never exercises a DIRTY run whose max severity is INFO, which is the only
input that distinguishes the two derivations. That gap is closed here.

VOCABULARY (severity.py): the tower scale is CRITICAL > HIGH > MEDIUM > LOW >
INFO. A native security WARNING arrives as MEDIUM via SECURITY_MAP, so a test
keying on the literal "WARNING" inside the aggregator would be dead code.
LOW maps to `warn`, deliberately: LOW is not INFO, and the security adapter uses
LOW for "last_run.json unreadable".
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import db_connect
from core.state_store import StateStore
from ops.control_tower import aggregator, disk, freshness
from ops.control_tower.model import Finding, SourceResult
from ops.control_tower.severity import RANK, TOWER_SCALE, status_for

_IST = timezone(timedelta(hours=5, minutes=30))


# ── the eight rulings, exhaustively ──────────────────────────────────────────
def test_no_findings_is_green():
    assert status_for([]) == "ok"


def test_info_only_is_green():
    assert status_for(["INFO"]) == "ok"
    assert status_for(["INFO", "INFO", "INFO"]) == "ok"


def test_warning_present_is_amber():
    # a native security WARNING arrives as MEDIUM
    assert status_for(["MEDIUM"]) == "warn"


def test_critical_present_is_red():
    assert status_for(["CRITICAL"]) == "critical"


def test_info_plus_warning_is_amber():
    assert status_for(["INFO", "MEDIUM"]) == "warn"


def test_info_plus_critical_is_red():
    assert status_for(["INFO", "CRITICAL"]) == "critical"


def test_warning_plus_critical_is_red():
    assert status_for(["MEDIUM", "CRITICAL"]) == "critical"


def test_info_warning_critical_is_red():
    assert status_for(["INFO", "MEDIUM", "CRITICAL"]) == "critical"


# ── precedence + the LOW judgement call, pinned ──────────────────────────────
def test_precedence_is_critical_over_warning_over_info():
    assert status_for(["INFO", "LOW", "MEDIUM", "HIGH"]) == "warn"
    assert status_for(["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]) == "critical"


def test_low_is_amber_not_green_and_this_is_deliberate():
    """LOW is not INFO. The security adapter emits LOW for an unreadable
    last_run.json -- a real condition, so it must not read GREEN."""
    assert status_for(["LOW"]) == "warn"


def test_every_tower_severity_is_classified():
    """No member of the vocabulary may fall through unclassified -- a property,
    not a fixed list, so a new severity cannot be added silently."""
    for sev in TOWER_SCALE:
        assert status_for([sev]) in ("ok", "warn", "critical")
    assert status_for(["NOT_A_SEVERITY"]) == "ok"      # unknown is ignored, not fatal
    assert set(TOWER_SCALE) == set(RANK)


# ── the regression test that would have caught the original defect ───────────
def _write_last_run(tmp_path, now, *, clean, max_severity, count):
    secdir = tmp_path / "data_store" / "security"
    secdir.mkdir(parents=True, exist_ok=True)
    (secdir / "last_run.json").write_text(json.dumps({
        "version": 1, "timestamp": now.isoformat(), "checks_run": 10,
        "findings_count": count, "max_severity": max_severity, "clean": clean,
    }))


def test_info_only_security_run_does_not_pin_the_panel(tmp_path):
    """THE ONE THAT FAILS ON THE PRE-FIX TREE.

    Reproduces the live 23-Aug state exactly: a DIRTY run whose max severity is
    INFO. Old derivation -> "warn" (findings non-empty). New -> "ok".
    """
    now = datetime(2026, 8, 23, 18, 59, tzinfo=_IST)
    _write_last_run(tmp_path, now, clean=False, max_severity="INFO", count=1)
    r = aggregator.read_security(tmp_path, now)
    assert r.status == "ok", "an INFO-only run must not pin the panel to warn"


def test_info_only_run_still_reports_the_finding(tmp_path):
    """N-3: only the STATUS derivation changed. The finding itself must remain
    visible -- an INFO finding going missing would be worse than the defect."""
    now = datetime(2026, 8, 23, 18, 59, tzinfo=_IST)
    _write_last_run(tmp_path, now, clean=False, max_severity="INFO", count=1)
    r = aggregator.read_security(tmp_path, now)
    assert len(r.findings) == 1
    assert r.findings[0].severity == "INFO"
    assert "security findings present" in r.findings[0].reason
    assert "clean=False" in r.detail


def test_warning_run_still_amber_and_critical_still_red(tmp_path):
    """The fix must not make the panel quieter for real severities."""
    now = datetime(2026, 8, 23, 18, 59, tzinfo=_IST)
    _write_last_run(tmp_path, now, clean=False, max_severity="WARNING", count=3)
    assert aggregator.read_security(tmp_path, now).status == "warn"
    _write_last_run(tmp_path, now, clean=False, max_severity="CRITICAL", count=2)
    assert aggregator.read_security(tmp_path, now).status == "critical"


# ── THE SECOND PINNED FIELD: the run-row label ───────────────────────────────
# `write_run(... "status": "OK_WITH_FINDINGS" if detected else "OK")` keyed on
# existence too, so an INFO-only run was labelled OK_WITH_FINDINGS. NI-14 is not
# complete while either field survives, so it is pinned here as well.

def _only(findings):
    """Drive run_aggregation with an exactly-known finding set."""
    def patch(monkeypatch):
        empty = lambda *a, **k: SourceResult("x", "ok", [], detail="")
        monkeypatch.setattr(aggregator, "read_security",
                            lambda *a, **k: SourceResult("security", "ok", list(findings), detail=""))
        for name in ("read_cron", "read_config", "read_excursion"):
            monkeypatch.setattr(aggregator, name, empty)
        monkeypatch.setattr(freshness, "evaluate", lambda *a, **k: ([], []))
        monkeypatch.setattr(disk, "evaluate", lambda *a, **k: ({}, []))
    return patch


def _run_status(tmp_path, monkeypatch, findings):
    db = tmp_path / "t.db"
    StateStore(db).close()
    _only(findings)(monkeypatch)
    now = datetime(2026, 8, 23, 19, 0, tzinfo=_IST)
    summary = aggregator.run_aggregation(db, tmp_path, Path("config"), now=now, write=True)
    conn = db_connect.connect(db, attach=False)
    row = conn.execute("SELECT findings_total, status FROM control_tower_runs").fetchone()
    conn.close()
    return summary, row


def _info():
    return Finding("security", "INFO", "service", "security-watcher",
                   reason="security findings present (count=1, native max=INFO)",
                   location="last_run.json")


def _medium():
    return Finding("security", "MEDIUM", "service", "security-watcher",
                   reason="security findings present (count=1, native max=WARNING)",
                   location="last_run.json")


def test_run_label_is_OK_when_the_only_finding_is_INFO(tmp_path, monkeypatch):
    """FAILS ON THE PRE-FIX TREE: `if detected` was true, so the row read
    OK_WITH_FINDINGS even though nothing above INFO was found."""
    summary, row = _run_status(tmp_path, monkeypatch, [_info()])
    assert summary["findings_total"] == 1           # the finding is NOT hidden
    assert sum(summary["counts"].values()) == 0     # ...and INFO is not counted
    assert row[0] == 1
    assert row[1] == "OK", "an INFO-only run must be labelled OK"


def test_run_label_is_OK_WITH_FINDINGS_above_info(tmp_path, monkeypatch):
    summary, row = _run_status(tmp_path, monkeypatch, [_medium()])
    assert sum(summary["counts"].values()) == 1
    assert row[1] == "OK_WITH_FINDINGS"


def test_run_label_is_OK_when_there_are_no_findings_at_all(tmp_path, monkeypatch):
    summary, row = _run_status(tmp_path, monkeypatch, [])
    assert summary["findings_total"] == 0
    assert row[1] == "OK"
