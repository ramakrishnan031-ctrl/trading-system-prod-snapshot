"""ops/control_tower/aggregator.py -- Control Tower Phase 1b.

Reads the 4 Phase-0 sources via monitor ADAPTERS, runs the freshness engine and
the disk-finding check, UPSERTs every detected issue into control_tower_findings
(dedup identity = category/resource_name/reason), and writes ONE
control_tower_runs row with RAW per-severity counts.

NO reporting / Telegram / health-score / status roll-up / ACK / pull report --
all of that is Phase 1c.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import db_connect  # noqa: E402
from ops.control_tower import disk, freshness  # noqa: E402
from ops.control_tower.db import (  # noqa: E402
    ensure_indexes, replace_freshness, upsert_finding, write_run,
)
from ops.control_tower.model import Finding, SourceResult  # noqa: E402
from ops.control_tower.severity import (  # noqa: E402
    CONFIG_MAP, SECURITY_MAP, map_severity,
)

_IST = timezone(timedelta(hours=5, minutes=30))
_log = logging.getLogger("control_tower.aggregator")
_SECURITY_STALE_MIN = 15            # watcher runs ~60s; older than this => stale


def _age_minutes(iso: str, now: datetime):
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=now.tzinfo)
    return (now - t).total_seconds() / 60.0


# ── A. security adapter ───────────────────────────────────────────────────────
def read_security(root, now: datetime) -> SourceResult:
    p = Path(root) / "data_store" / "security" / "last_run.json"
    if not p.exists():
        return SourceResult("security", "unavailable", [Finding(
            "security", "LOW", "file", "security/last_run.json",
            reason="security monitor status unavailable (last_run.json absent — "
                   "F1 not deployed or monitor not yet run)",
            location=str(p),
            recommended_action="deploy F1 / check security-watcher.service")],
            detail="absent")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return SourceResult("security", "unavailable", [Finding(
            "security", "LOW", "file", "security/last_run.json",
            reason=f"security last_run.json unreadable: {exc}", location=str(p))],
            detail="unreadable")

    findings: list[Finding] = []
    age = _age_minutes(data.get("timestamp"), now)
    if age is not None and age > _SECURITY_STALE_MIN:
        findings.append(Finding(
            "security", "MEDIUM", "service", "security-watcher",
            reason=f"security monitor stale — last run {age:.0f} min ago "
                   f"(watcher runs ~60s)",
            location=str(p), recommended_action="check security-watcher.service"))
    if not data.get("clean", True):
        native = str(data.get("max_severity", "INFO"))
        sev = map_severity(SECURITY_MAP, native)        # WARNING->MEDIUM (severity.py)
        findings.append(Finding(
            "security", sev, "service", "security-watcher",
            reason=f"security findings present (count={data.get('findings_count')}, "
                   f"native max={native})",
            location=str(p), recommended_action="review security alerts / sentinels"))
    status = ("critical" if any(f.severity == "CRITICAL" for f in findings)
              else "warn" if findings else "ok")
    return SourceResult("security", status, findings,
                        detail=f"clean={data.get('clean')} count={data.get('findings_count')}")


# ── B. cron adapter ───────────────────────────────────────────────────────────
def _marker_today(markers_dir: Path, name, today: str) -> bool:
    if not name:
        return False
    p = Path(markers_dir) / f"{name}.done"
    if not p.exists():
        return False
    return datetime.fromtimestamp(p.stat().st_mtime, _IST).isoformat()[:10] == today


def read_cron(conn, config_dir: Path, markers_dir: Path, now: datetime) -> SourceResult:
    from core.cron_registry import load_cron_registry
    reg = load_cron_registry(Path(config_dir) / "cron_registry.yaml")
    expected = reg.expected_heartbeat_jobs(now.date(), Path(config_dir), before_time=now.time())
    d = now.strftime("%Y-%m-%d")
    hb: dict = {}
    for row in conn.execute(
        "SELECT job_name, status, MAX(executed_at) FROM cron_heartbeat "
        "WHERE substr(executed_at,1,10)=? GROUP BY job_name", (d,)):
        hb[row[0]] = row[1]

    findings: list[Finding] = []
    for j in expected:
        seen = (j.name in hb) or _marker_today(markers_dir, j.marker_name, d)
        if j.name in hb and hb[j.name] == "FAILED":
            findings.append(Finding(
                "cron", "HIGH", "job", j.name,
                reason=f"cron job {j.name} heartbeat FAILED today",
                location="cron_heartbeat", recommended_action="inspect the job log"))
        elif not seen:
            findings.append(Finding(
                "cron", "HIGH" if j.critical else "MEDIUM", "job", j.name,
                reason=f"cron job {j.name} missed — no heartbeat/marker today "
                       f"(due by {j.due_time})",
                location="cron_heartbeat|cron_marks",
                recommended_action="check crontab + the job log"))
    status = ("critical" if any(f.severity == "CRITICAL" for f in findings)
              else "warn" if findings else "ok")
    return SourceResult("cron", status, findings, detail=f"{len(expected)} expected by now")


# ── C. config adapter ─────────────────────────────────────────────────────────
def read_config(config_dir: Path) -> SourceResult:
    from core.config_loader import load_all
    from core.config_auditor import audit_app_config
    try:
        app = load_all(Path(config_dir))
    except Exception as exc:  # a contradictory config makes load_all raise (BLOCK gate)
        return SourceResult("config", "critical", [Finding(
            "config", "CRITICAL", "config", "load_all",
            reason=f"config failed to load (contradiction?): "
                   f"{type(exc).__name__}: {exc}",
            location="config", recommended_action="fix the contradictory config")],
            detail="load_failed")
    strategies = None
    try:
        from strategies.loader import StrategyLoader
        strategies = StrategyLoader().load_all_strategies(
            Path(config_dir) / "strategies",
            force_intraday_only=app.system.force_intraday_only)
    except Exception:  # group A3 / by-strategy checks simply degrade
        strategies = None
    report = audit_app_config(app, config_dir=Path(config_dir), strategies=strategies)
    findings = [Finding(
        "config", map_severity(CONFIG_MAP, f.severity.value), "config",
        f"group_{f.group}:{f.code}", reason=f.message[:240], location="config_auditor",
        recommended_action="review config sanity") for f in report.actionable]
    status = "critical" if report.blocks else "warn" if report.warns else "ok"
    return SourceResult("config", status, findings, detail=f"verdict={report.verdict}")


# ── D. excursion adapter ──────────────────────────────────────────────────────
def read_excursion(conn) -> SourceResult:
    row = conn.execute(
        "SELECT status, started_at, trades_failed FROM excursion_reconstruction_runs "
        "ORDER BY started_at DESC LIMIT 1").fetchone()
    if row is None:
        return SourceResult("excursion", "not_run", [], detail="no runs yet (0 rows)")
    status, started, failed = row[0], row[1], row[2]
    findings: list[Finding] = []
    if status == "FAILED":
        findings.append(Finding(
            "excursion", "HIGH", "job", "reconstruct_excursions",
            reason=f"last reconstruction run FAILED ({failed} trades failed, {started})",
            location="excursion_reconstruction_runs",
            recommended_action="re-run reconstruct_excursions --daily; check the log"))
    return SourceResult("excursion", "failed" if status == "FAILED" else "ok",
                        findings, detail=f"latest={status}")


# ── orchestrator ──────────────────────────────────────────────────────────────
def run_aggregation(db_path, root, config_dir, now: datetime | None = None,
                    write: bool = True) -> dict:
    now = now or datetime.now(_IST)
    started_iso = now.isoformat()
    t0 = _time.perf_counter()
    conn = db_connect.connect(Path(db_path), attach=True)
    try:
        ensure_indexes(conn)
        markers_dir = Path(root) / "data_store" / "cron_marks"
        sources = [
            read_security(root, now),
            read_cron(conn, config_dir, markers_dir, now),
            read_config(config_dir),
            read_excursion(conn),
        ]
        fresh_rows, fresh_findings = freshness.evaluate(conn, now, markers_dir)
        _sizes, disk_findings = disk.evaluate(conn, root, db_path, now.strftime("%Y-%m-%d"))

        all_findings = [f for s in sources for f in s.findings] + fresh_findings + disk_findings
        counts = {sev: sum(1 for f in all_findings if f.severity == sev)
                  for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
        checks_run = len(sources) + len(fresh_rows) + 1   # +1 disk check
        run_id = uuid.uuid4().hex

        if write:
            for f in all_findings:
                upsert_finding(conn, f, started_iso)
            replace_freshness(conn, now.strftime("%Y-%m-%d"), fresh_rows)
            write_run(conn, {
                "run_id": run_id, "started_at": started_iso,
                "completed_at": datetime.now(_IST).isoformat(),
                "duration_s": round(_time.perf_counter() - t0, 3),
                "checks_run": checks_run, "findings_total": len(all_findings),
                "critical_count": counts["CRITICAL"], "high_count": counts["HIGH"],
                "medium_count": counts["MEDIUM"], "low_count": counts["LOW"],
                "status": "OK_WITH_FINDINGS" if all_findings else "OK",
            })
            conn.commit()
        return {
            "run_id": run_id, "sources": {s.source: s.status for s in sources},
            "findings_total": len(all_findings), "counts": counts,
            "freshness": {r["stage"]: r["status"] for r in fresh_rows},
            "checks_run": checks_run,
        }
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Control Tower aggregator (Phase 1b)")
    ap.add_argument("--db", default=str(_ROOT / "data_store" / "trading_system.db"))
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--config-dir", default=str(_ROOT / "config"))
    ap.add_argument("--no-write", action="store_true", help="compute but do not write rows")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_aggregation(args.db, args.root, args.config_dir, write=not args.no_write)
    _log.info("control_tower.aggregator: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
