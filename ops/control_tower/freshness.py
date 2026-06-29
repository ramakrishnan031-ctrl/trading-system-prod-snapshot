"""ops/control_tower/freshness.py -- Control Tower Phase 1b freshness engine.

Per pipeline stage computes expected_by / actual_at / delay_minutes / status
(OK | OVERDUE | MISSING | NA) and a Finding for OVERDUE/MISSING. Authoritative
signal = the table's max(ts)/count for today; the candles fetch MARKER mtime is
the fallback actual_at. NA = not yet due -> never a finding. One row per
(run_date, stage). Uses the caller's connection (main + analytics ATTACHed).
"""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

from .model import Finding

# deadline-driven stages (expected_by HH:MM IST)
_DEADLINE = {"candles": time(15, 40), "reconstruction": time(15, 50), "eod": time(16, 0)}
_WINDOW_CLOSE = time(15, 30)            # signals / orders / executions
_OVERDUE_GRACE_MIN = 15                 # present but later than this -> OVERDUE


def _scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _delay_minutes(actual_iso, expected_dt):
    try:
        a = datetime.fromisoformat(actual_iso)
    except (TypeError, ValueError):
        return None
    if a.tzinfo is not None and expected_dt.tzinfo is None:
        expected_dt = expected_dt.replace(tzinfo=a.tzinfo)
    if a.tzinfo is None and expected_dt.tzinfo is not None:
        a = a.replace(tzinfo=expected_dt.tzinfo)
    return int((a - expected_dt).total_seconds() // 60)


def _marker_mtime(markers_dir: Path, name: str, tz, today: str):
    p = Path(markers_dir) / f"{name}.done"
    if not p.exists():
        return None
    iso = datetime.fromtimestamp(p.stat().st_mtime, tz).isoformat()
    return iso if iso[:10] == today else None


def evaluate(conn, now: datetime, markers_dir: Path):
    """Return (freshness_rows, findings)."""
    d = now.strftime("%Y-%m-%d")
    tz = now.tzinfo
    rows: list[dict] = []
    findings: list[Finding] = []

    def emit(stage: str, deadline_t, actual_at, present: bool):
        cutoff_t = deadline_t or _WINDOW_CLOSE
        cutoff_dt = datetime.combine(now.date(), cutoff_t, tzinfo=tz)
        exp_dt = datetime.combine(now.date(), deadline_t, tzinfo=tz) if deadline_t else None
        delay = _delay_minutes(actual_at, exp_dt) if (actual_at and exp_dt) else None
        if present:
            status = "OVERDUE" if (delay is not None and delay > _OVERDUE_GRACE_MIN) else "OK"
        elif now >= cutoff_dt:
            status = "MISSING"
        else:
            status = "NA"                       # not yet due -> never a finding
        rows.append({
            "stage": stage,
            "expected_by": exp_dt.isoformat() if exp_dt else None,
            "actual_at": actual_at,
            "delay_minutes": delay,
            "status": status,
        })
        if status in ("OVERDUE", "MISSING"):
            findings.append(Finding(
                category="freshness",
                severity="HIGH" if status == "MISSING" else "MEDIUM",
                resource_type="stage", resource_name=stage,
                reason=f"{stage} {status.lower()} (expected by {cutoff_t.strftime('%H:%M')})",
                location="pipeline",
                recommended_action=f"check the {stage} source / job",
            ))

    # candles — analytics.candles.date present today; actual_at = fetch marker mtime
    emit("candles", _DEADLINE["candles"],
         _marker_mtime(markers_dir, "fetch_daily_candles", tz, d),
         _scalar(conn, "SELECT MAX(date) FROM candles") == d)

    # signals — signals.received_at
    sig_n = _scalar(conn, "SELECT COUNT(*) FROM signals WHERE substr(received_at,1,10)=?", (d,)) or 0
    emit("signals", None,
         _scalar(conn, "SELECT MAX(received_at) FROM signals WHERE substr(received_at,1,10)=?", (d,)),
         sig_n > 0)

    # orders — orders.placed_at
    ord_n = _scalar(conn, "SELECT COUNT(*) FROM orders WHERE substr(placed_at,1,10)=?", (d,)) or 0
    emit("orders", None,
         _scalar(conn, "SELECT MAX(placed_at) FROM orders WHERE substr(placed_at,1,10)=?", (d,)),
         ord_n > 0)

    # executions — order_execution_log (fill_timestamp or created_at)
    ex_n = _scalar(conn, "SELECT COUNT(*) FROM order_execution_log "
                   "WHERE substr(COALESCE(fill_timestamp,created_at),1,10)=?", (d,)) or 0
    emit("executions", None,
         _scalar(conn, "SELECT MAX(COALESCE(fill_timestamp,created_at)) FROM order_execution_log "
                 "WHERE substr(COALESCE(fill_timestamp,created_at),1,10)=?", (d,)),
         ex_n > 0)

    # reconstruction — excursion_reconstruction_runs latest started today
    emit("reconstruction", _DEADLINE["reconstruction"],
         _scalar(conn, "SELECT MAX(started_at) FROM excursion_reconstruction_runs "
                 "WHERE substr(started_at,1,10)=?", (d,)),
         _scalar(conn, "SELECT COUNT(*) FROM excursion_reconstruction_runs "
                 "WHERE substr(started_at,1,10)=?", (d,)) > 0)

    # eod — eod_squareoff_log today COMPLETE
    eod_at = _scalar(conn, "SELECT fired_at FROM eod_squareoff_log "
                     "WHERE fired_date=? AND status='COMPLETE'", (d,))
    emit("eod", _DEADLINE["eod"], eod_at, eod_at is not None)

    return rows, findings
