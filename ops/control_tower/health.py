"""ops/control_tower/health.py -- Control Tower Phase 1c health score.

Weighted over OPEN findings ONLY (ACKNOWLEDGED is suppressed, so an accepted
known-issue can't pin the score; RESOLVED is gone). Written into the existing
control_tower_trends daily row WITHOUT clobbering the 1a size columns.
"""
from __future__ import annotations

WEIGHTS = {"CRITICAL": 100, "HIGH": 50, "MEDIUM": 20, "LOW": 5, "INFO": 1}
# (ceiling, band) — first match wins; above the last ceiling => CRITICAL.
_BANDS = ((50, "HEALTHY"), (150, "WARNING"), (300, "ATTENTION"))


def score_and_band(open_severities) -> tuple[int, str]:
    score = sum(WEIGHTS.get(s, 0) for s in open_severities)
    for ceiling, band in _BANDS:
        if score <= ceiling:
            return score, band
    return score, "CRITICAL"


def write_trend_health(conn, date_str: str, score: int, open_severities) -> None:
    """Field-scoped UPSERT of health_score + the OPEN per-severity counts into
    the date's control_tower_trends row (leaves the size columns to the
    size-logger)."""
    crit = sum(1 for s in open_severities if s == "CRITICAL")
    high = sum(1 for s in open_severities if s == "HIGH")
    med = sum(1 for s in open_severities if s == "MEDIUM")
    conn.execute(
        """
        INSERT INTO control_tower_trends
            (date, health_score, critical_count, high_count, medium_count)
        VALUES (:date, :score, :crit, :high, :med)
        ON CONFLICT(date) DO UPDATE SET
            health_score   = excluded.health_score,
            critical_count = excluded.critical_count,
            high_count     = excluded.high_count,
            medium_count   = excluded.medium_count
        """,
        {"date": date_str, "score": score, "crit": crit, "high": high, "med": med},
    )
