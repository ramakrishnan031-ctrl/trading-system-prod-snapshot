"""
ops_dashboard/backend/services/pipeline_state.py

The 13-stage signal→trade funnel + halt rail. Card counts come from db_reader
(verified against the real v42 schema). The card COLOR is a PURE function
(derive_color) so it is table-testable independent of any DB.

Colour legend (Rama G1): GRAY not-reached / BLUE informational / ORANGE
intake-or-warning / GREEN success / RED rejected-failed / PURPLE duplicate.

⭐ 18-Aug-2026: the colour became the stage's FIXED SEMANTIC colour, gated only
on count and failures — the reading the approved artwork actually draws. YELLOW
"processing" left the palette: it was a freshness verdict, and the artwork's own
cards contradict a freshness rule (Orders Filled at 7 s GREEN, SL Hit at 141 s
AMBER). The freshest stage is still marked by `active`, which the screen draws
as a RING rather than a colour.
"""
from __future__ import annotations

from typing import Optional

from ..readers import db_reader
from . import freshness

# `kind` is retained as the stage's FAMILY — it is what `freshness` keys the
# entry-window question on and what a reader uses to group the funnel. ⛔ It no
# longer decides the colour; the fourth field does.
# ⭐ THE LABELS ARE THE APPROVED ARTWORK'S, VERBATIM — spaces, ⛔ never hyphens,
# and "Manual Exit" ⛔ not "Manual/Other-Exit". Two of the hyphenated names wrapped
# to a second line, which is why every card reserved two lines of label height.
#
# ⭐ THE FOURTH FIELD IS THE STAGE'S FIXED SEMANTIC COLOUR, and it replaces a
# freshness-derived one. THE ARTWORK IS NOT DRAWING FRESHNESS, and its own cards
# prove it: Orders Filled (last event 7 s before the mock clock) is GREEN while
# SL Hit (141 s) is AMBER — under a freshness rule the FRESHER card would be the
# highlighted one. So Received is AMBER because it is the intake, Orders Created
# is BLUE because it is informational, and SL Hit is AMBER because a stop firing
# is a warning — ⛔ not because any of them happened recently.
STAGE_DEFS = [
    ("received",         "Received",         "intake",           "ORANGE"),
    ("validated",        "Validated",        "validated",        "GREEN"),
    ("duplicate",        "Duplicate",        "duplicate",        "PURPLE"),
    ("rejected",         "Rejected",         "reject",           "RED"),
    ("risk_rejected",    "Risk Rejected",    "validation_issue", "ORANGE"),
    ("capital_rejected", "Capital Rejected", "validation_issue", "ORANGE"),
    ("orders_created",   "Orders Created",   "success",          "BLUE"),
    ("orders_placed",    "Orders Placed",    "success",          "GREEN"),
    ("orders_filled",    "Orders Filled",    "success",          "GREEN"),
    ("sl_hit",           "SL Hit",           "success",          "ORANGE"),
    ("tgt_hit",          "TGT Hit",          "success",          "GREEN"),
    ("manual_exit",      "Manual Exit",      "success",          "GRAY"),
    ("trade_closed",     "Trade Closed",     "success",          "GREEN"),
]


def derive_color(
    semantic: str,
    count: int,
    failures: int = 0,
) -> str:
    """Pure colour rule: the stage's FIXED semantic colour, gated on its count.

    ⭐ TWO INPUTS AND NO CLOCK. The approved artwork gives each stage one colour
    and keeps it; ⛔ it does not tint a card because an event happened recently
    (its Orders Filled at 7 s old is GREEN while its SL Hit at 141 s is AMBER —
    the freshness reading is the wrong way round, so freshness is not the rule).
    The freshest stage is still marked, but by `active` — a RING, ⛔ not a colour
    — so the live signal survives without fighting the artwork.

    ⭐ A ZERO STAGE IS NEUTRAL. A card cannot wear the warning colour while
    reading 0: on the artwork `Duplicate 0` and `Manual Exit 0` are both neutral,
    and the one card that disagrees — `Capital Rejected 0`, drawn amber — is the
    artwork contradicting itself across three zero-count cards. ⛔ DELIBERATE
    DEVIATION, RECORDED: an amber `Capital Rejected` reading 0 tells the operator
    there is a capital rejection when there is none.

    ⭐ FAILURES STILL WIN. A stage with recorded failures is RED whatever its
    semantic colour, because that is the one thing the operator must not miss.
    """
    if failures and failures > 0:
        return "RED"
    if count <= 0:
        return "GRAY"
    return semantic


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
    for key, name, kind, semantic in STAGE_DEFS:
        count, last_ts, failures = counts[key]
        age = freshness.age_seconds(last_ts, now) if last_ts else None
        exp = freshness.expected_activity(cfg, key, now)
        color = derive_color(semantic, count, failures)
        if (
            count > 0
            and age is not None
            and age <= freshness.PROCESSING_WINDOW_SEC
            and exp
            and (freshest_age is None or age < freshest_age)
        ):
            freshest_age, freshest_key = age, key
        stages.append({
            "key": key, "name": name, "kind": kind, "semantic": semantic,
            "count": int(count),
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
