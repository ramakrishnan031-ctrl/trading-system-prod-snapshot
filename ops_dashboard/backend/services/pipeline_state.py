"""
ops_dashboard/backend/services/pipeline_state.py

The 13-stage signal→trade funnel + halt rail. Card counts come from db_reader
(verified against the real v42 schema). The card COLOR is a PURE function
(derive_color) so it is table-testable independent of any DB.

Color legend (Rama G1): GRAY not-reached / BLUE received / YELLOW processing /
ORANGE validation-stage issue / GREEN success / RED rejected-failed /
PURPLE duplicate.
"""
from __future__ import annotations

from typing import Optional

from ..readers import db_reader
from . import freshness

# Stage kinds → resting-color family.
#   intake            → BLUE when count>0
#   validated/success → GREEN when count>0 (RED if failures>0)
#   duplicate         → PURPLE when count>0
#   reject            → RED when count>0
#   validation_issue  → ORANGE when count>0
STAGE_DEFS = [
    ("received",       "Received",         "intake"),
    ("validated",      "Validated",        "validated"),
    ("duplicate",      "Duplicate",        "duplicate"),
    ("rejected",       "Rejected",         "reject"),
    ("risk_rejected",  "Risk-Rejected",    "validation_issue"),
    ("capital_rejected", "Capital-Rejected", "validation_issue"),
    ("orders_created", "Orders-Created",   "success"),
    ("orders_placed",  "Orders-Placed",    "success"),
    ("orders_filled",  "Orders-Filled",    "success"),
    ("sl_hit",         "SL-Hit",           "success"),
    ("tgt_hit",        "TGT-Hit",          "success"),
    ("manual_exit",    "Manual/Other-Exit", "success"),
    ("trade_closed",   "Trade-Closed",     "success"),
]


def derive_color(
    kind: str,
    count: int,
    failures: int = 0,
    last_event_age_sec: Optional[float] = None,
    expected_activity: bool = False,
) -> str:
    """Pure color rule. Function of (kind, count, failures, age, expected)."""
    if kind == "duplicate":
        return "PURPLE" if count > 0 else "GRAY"
    if kind == "reject":
        return "RED" if count > 0 else "GRAY"
    if kind == "validation_issue":
        return "ORANGE" if count > 0 else "GRAY"
    # intake / validated / success
    if failures and failures > 0:
        return "RED"
    if count <= 0:
        return "GRAY"
    # Actively processing (fresh event inside the active window) → YELLOW.
    if (
        expected_activity
        and last_event_age_sec is not None
        and last_event_age_sec <= freshness.PROCESSING_WINDOW_SEC
    ):
        return "YELLOW"
    return "BLUE" if kind == "intake" else "GREEN"


def build_pipeline(cfg: dict, today: Optional[str] = None, now=None) -> dict:
    """Assemble all 13 stage cards + the halt rail overlay."""
    now = now or freshness.ist_now()
    today = today or freshness.ist_today_iso(now)

    funnel = db_reader.webhook_funnel(cfg, today)
    dup = db_reader.signals_duplicate_count(cfg, today)
    # Residual reject bucket = webhook rejects minus the DB-recorded duplicate
    # slice (W9: the true dup volume is not separable; see capacity inventory D-notes).
    rejected_residual = max(0, funnel["rejected_total"] - dup)
    entry = db_reader.orders_entry_counts(cfg, today)
    sl = db_reader.orders_exit_leg_filled(cfg, today, "SL")
    tgt = db_reader.orders_exit_leg_filled(cfg, today, "TGT")
    closed = db_reader.trades_closed_counts(cfg, today)

    counts = {
        "received":        (funnel["received"], funnel["last_ts"], 0),
        "validated":       (funnel["validated"], funnel["last_ts"], 0),
        "duplicate":       (dup, funnel["last_ts"], 0),
        "rejected":        (rejected_residual, funnel["last_ts"], 0),
        "risk_rejected":   (db_reader.signals_risk_rejected(cfg, today), None, 0),
        "capital_rejected": (db_reader.signals_capital_rejected(cfg, today), None, 0),
        "orders_created":  (entry["created"], entry["last_ts"], 0),
        "orders_placed":   (entry["placed"], entry["last_ts"], 0),
        "orders_filled":   (entry["filled"], entry["last_ts"], 0),
        "sl_hit":          (sl["count"], sl["last_ts"], 0),
        "tgt_hit":         (tgt["count"], tgt["last_ts"], 0),
        "manual_exit":     (closed["other_exit"], closed["last_ts"], 0),
        "trade_closed":    (closed["closed"], closed["last_ts"], 0),
    }

    stages = []
    freshest_age: Optional[float] = None
    freshest_key: Optional[str] = None
    for key, name, kind in STAGE_DEFS:
        count, last_ts, failures = counts[key]
        age = freshness.age_seconds(last_ts, now) if last_ts else None
        exp = freshness.expected_activity(cfg, key, now)
        color = derive_color(kind, count, failures, age, exp)
        if (
            count > 0
            and age is not None
            and age <= freshness.PROCESSING_WINDOW_SEC
            and exp
            and (freshest_age is None or age < freshest_age)
        ):
            freshest_age, freshest_key = age, key
        stages.append({
            "key": key, "name": name, "kind": kind, "count": int(count),
            "failures": int(failures), "color": color,
            "last_event": last_ts, "last_event_age_sec": age,
            "expected_activity": exp, "active": False,
        })

    for s in stages:
        s["active"] = (s["key"] == freshest_key)

    ks = db_reader.get_kill_switch(cfg)
    halt = {
        "state": ks.get("state", "INACTIVE"),
        "reason": ks.get("reason"),
        "triggered_at": ks.get("triggered_at"),
        "triggered_by": ks.get("triggered_by"),
        "halted": ks.get("state", "INACTIVE") in ("SOFT_KILL", "HARD_KILL"),
    }

    return {"today": today, "stages": stages, "halt": halt}
