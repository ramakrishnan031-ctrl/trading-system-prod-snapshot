"""Control Tower Phase 1c (HEALTH + STATUS + REPORTER + ACK) — unit tests.

Covers the findings lifecycle (auto-resolve + the ran-category guard +
re-occurrence), the weighted health score with ACK suppression, the
noise-controlled Telegram push delta, and an end-to-end run that writes health
+ status + the pull report.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import db_connect
from core.state_store import StateStore
from ops.control_tower import aggregator, health, reporter
from ops.control_tower.db import (
    ack_finding, auto_resolve_stale, get_findings, open_severities,
    unack_finding, upsert_finding,
)
from ops.control_tower.model import Finding

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_DIR = Path("config")


def _db(tmp_path) -> Path:
    db = tmp_path / "t.db"
    StateStore(db).close()
    return db


def _status_map(conn):
    return {r[3]: r[7] for r in get_findings(conn, ("OPEN", "ACKNOWLEDGED", "RESOLVED"))}


# ── health score + bands ──────────────────────────────────────────────────────

def test_health_bands():
    assert health.score_and_band([]) == (0, "HEALTHY")
    assert health.score_and_band(["MEDIUM", "MEDIUM"]) == (40, "HEALTHY")
    assert health.score_and_band(["HIGH", "MEDIUM"]) == (70, "WARNING")
    assert health.score_and_band(["HIGH"] * 4) == (200, "ATTENTION")
    assert health.score_and_band(["CRITICAL"] * 3 + ["HIGH"]) == (350, "CRITICAL")


# ── lifecycle: auto-resolve + ran-category guard + re-occurrence ──────────────

def test_auto_resolve_with_guard(tmp_path):
    db = _db(tmp_path)
    conn = db_connect.connect(db, attach=False)
    upsert_finding(conn, Finding("cron", "HIGH", "job", "A", reason="missed"), "2026-06-29T17:00:00+05:30")
    upsert_finding(conn, Finding("security", "LOW", "file", "S", reason="unavail"), "2026-06-29T17:00:00+05:30")
    conn.commit()
    # run #2: cron RAN (A re-detected), security did NOT run this time
    upsert_finding(conn, Finding("cron", "HIGH", "job", "A", reason="missed"), "2026-06-29T17:05:00+05:30")
    auto_resolve_stale(conn, {"cron"}, "2026-06-29T17:05:00+05:30", "2026-06-29T17:05:00+05:30")
    conn.commit()
    st = _status_map(conn)
    assert st["A"] == "OPEN"          # re-detected -> stays OPEN
    assert st["S"] == "OPEN"          # security NOT in ran -> GUARD preserves it
    # run #3: cron RAN but A NOT re-detected -> A auto-resolves
    auto_resolve_stale(conn, {"cron"}, "2026-06-29T17:10:00+05:30", "2026-06-29T17:10:00+05:30")
    conn.commit()
    assert _status_map(conn)["A"] == "RESOLVED"
    conn.close()


def test_reoccurrence_reopens(tmp_path):
    db = _db(tmp_path)
    conn = db_connect.connect(db, attach=False)
    f = Finding("cron", "HIGH", "job", "A", reason="missed")
    upsert_finding(conn, f, "2026-06-29T17:00:00+05:30")
    auto_resolve_stale(conn, {"cron"}, "2026-06-29T17:05:00+05:30", "2026-06-29T17:05:00+05:30")
    conn.commit()
    assert _status_map(conn)["A"] == "RESOLVED"
    upsert_finding(conn, f, "2026-06-29T17:10:00+05:30")   # detected again
    conn.commit()
    row = get_findings(conn, ("OPEN",))[0]
    conn.close()
    assert row[3] == "A" and row[7] == "OPEN" and row[9] is None   # reopened, resolved_at cleared


def test_ack_suppresses_health(tmp_path):
    db = _db(tmp_path)
    conn = db_connect.connect(db, attach=False)
    upsert_finding(conn, Finding("config", "HIGH", "config", "G1", reason="x"), "t1")
    conn.commit()
    assert health.score_and_band(open_severities(conn))[0] == 50
    fid = get_findings(conn, ("OPEN",))[0][0]
    ack_finding(conn, fid, "t2")
    assert health.score_and_band(open_severities(conn))[0] == 0    # ACK suppressed from the score
    unack_finding(conn, fid)
    assert health.score_and_band(open_severities(conn))[0] == 50
    conn.close()


# ── reporter push delta ───────────────────────────────────────────────────────

def test_select_push_delta():
    crit = Finding("x", "CRITICAL", "r", "c", reason="c")
    high = Finding("x", "HIGH", "r", "h", reason="h")
    med = Finding("x", "MEDIUM", "r", "m", reason="m")
    push = reporter.select_push([crit, high, med], {})            # all new
    assert crit in push and high in push and med not in push      # MEDIUM never
    assert high not in reporter.select_push([high], {("x", "h", "h"): "OPEN"})    # unchanged HIGH
    assert high in reporter.select_push([high], {("x", "h", "h"): "RESOLVED"})    # reopened HIGH
    assert crit not in reporter.select_push([crit], {("x", "c", "c"): "ACKNOWLEDGED"})  # acked CRIT


def test_format_telegram_overall_first_line():
    tier, title, body = reporter.format_telegram(
        "WARNING", [Finding("config", "HIGH", "config", "G1", reason="entry_end…")])
    assert body.splitlines()[0] == "OVERALL STATUS: WARNING"
    assert tier == "WARNING" and "issue(s)" in title


# ── end-to-end 1c ─────────────────────────────────────────────────────────────

class _CaptureNotifier:
    def __init__(self):
        self.sent = []

    def send(self, **kw):
        self.sent.append(kw)


def test_run_aggregation_1c_writes_health_status_report(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 6, 29, 17, 0, tzinfo=_IST)
    cap = _CaptureNotifier()
    summary = aggregator.run_aggregation(
        db, tmp_path, _CONFIG_DIR, now=now, write=True,
        notifier=cap, report_dir=tmp_path / "rep")
    assert summary["overall_status"] in ("HEALTHY", "WARNING", "ATTENTION", "CRITICAL")
    conn = db_connect.connect(db, attach=False)
    hs = conn.execute("SELECT health_score FROM control_tower_trends "
                      "WHERE date='2026-06-29'").fetchone()
    stt = conn.execute("SELECT overall_status, last_successful_run "
                       "FROM control_tower_status WHERE run_date='2026-06-29'").fetchone()
    conn.close()
    assert hs and hs[0] == summary["health_score"]               # health written (field-scoped)
    assert stt and stt[0] == summary["overall_status"] and stt[1] is not None
    assert (tmp_path / "rep" / "report_2026-06-29.html").exists()
    assert (tmp_path / "rep" / "report_2026-06-29.csv").exists()
    assert summary["pushed"] == bool(cap.sent)                   # push <-> a Telegram was sent
    if cap.sent:
        assert cap.sent[0]["body"].splitlines()[0].startswith("OVERALL STATUS:")
