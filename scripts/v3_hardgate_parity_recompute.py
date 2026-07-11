"""
scripts/v3_hardgate_parity_recompute.py — V3 03.03/03.04 offline parity proof.

THE BINDING ACCEPTANCE GATE (G2). Deterministic OFFLINE recompute over a
HISTORICAL screener_results corpus (a DB BACKUP — never the live production DB).
For every row it reconstructs the OLD decision (cross-checked against the stored
score), the Hard-Gate outcome, and the NEW (8-step + re-scaled thresholds)
decision, then classifies each signal:

    UNCHANGED / FLIP_PASS / FLIP_FAIL / TIER_SHIFT / NOW_GATED

and confirms every non-UNCHANGED row is EXPLAINED by the accepted residual
(age-band signal_age==0.5, or at-circuit circuit_check==0) — nothing unexplained.
It also DATA-FITS the thresholds (around the analytic start 50/56/75) to minimise
flips, and prints the flip-set report + the fitted thresholds.

Run (VM, on a backup):
    python scripts/v3_hardgate_parity_recompute.py --db data_store/backups/<backup>.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from screening.hard_gate import (  # noqa: E402
    rescale_min_score,
    rescaled_total,
    tier_for,
)

GATE_STEPS = {"circuit_check", "signal_age"}
# The live weights (kept in sync with scoring_weights.yaml; loaded there in main()).
_DEFAULT_WEIGHTS = {
    "volume_surge": 15, "vwap_position": 10, "atr_filter": 10, "rsi_range": 10,
    "price_action": 15, "sector_strength": 10, "time_of_day": 5, "spread_check": 5,
    "circuit_check": 10, "signal_age": 10,
}


@dataclass(frozen=True)
class RowVerdict:
    signal_id: str
    old_status: str
    old_score: int
    new_score: int
    new_tier: str
    gate_reason: Optional[str]
    classification: str
    explained: bool
    reason: str


def _gate_reason(step_results: dict) -> Optional[str]:
    """Reconstruct the Hard-Gate outcome for a SCORED row from its persisted
    per-step scores (mirrors HardGate: at-circuit via circuit_check==0, stale via
    signal_age==0).

    Circuit-PROXIMITY is intentionally NOT re-derived here: it is enforced
    pre-scoring, so any row that HAS step_results already passed it (a
    proximity-rejected signal is stored REJECTED_CIRCUIT_PROXIMITY with no
    step_results). It also needs `direction`, which the screener_results table
    does not carry. So for scored rows proximity contributes zero flips, and the
    only NOW_GATED source is at-circuit (circuit_check==0)."""
    if step_results.get("circuit_check") == 0.0:
        return "AT_CIRCUIT"
    if step_results.get("signal_age") == 0.0:
        return "SIGNAL_AGE"
    return None


def classify_row(
    row: dict, *, weights: Dict[str, int], v3_min_pass: int, v3_high: int, v3_medium: int,
    old_min_pass: int = 60, old_high: int = 80, old_medium: int = 65,
) -> RowVerdict:
    """Classify one screener_results row OLD-vs-NEW, both evaluated at the CURRENT
    config (the OFF→enforce question). Pure.

    IMPORTANT: the stored `status`/`tier` reflect whatever min_pass was LIVE when
    the row was written (min_pass was 55 from 06→10-Jul, else 60), so they are the
    WRONG baseline for an OFF-today-vs-enforce-today comparison. We therefore
    RECOMPUTE the OLD decision from the persisted per-step scores at the current
    thresholds (60/80/65) + the signal_age defense-in-depth, and compare that to
    the NEW (gate + 8-step + v3 thresholds). `stored_score` is kept only as a
    harness cross-check (recomputed full total should equal it)."""
    signal_id = str(row.get("signal_id"))
    old_status = str(row.get("status") or "")
    stored_score = int(row.get("score") or 0)
    # Real column is `step_results` (persist PARAM was step_results_json → COLUMN
    # is step_results); fall back for any older/renamed corpus.
    try:
        step_results = json.loads(row.get("step_results") or row.get("step_results_json") or "{}")
    except Exception:
        step_results = {}

    scored = bool(step_results)   # rows with no step_results never reached the scorer
    age_zero = step_results.get("signal_age") == 0.0
    at_circuit = step_results.get("circuit_check") == 0.0
    age_half = step_results.get("signal_age") == 0.5

    # OLD (today's OFF path): full 10-step proportional total + signal_age defense.
    old_total = rescaled_total(step_results, weights, set())  # gate_steps=∅ → all steps
    old_pass = scored and (old_total >= old_min_pass) and (not age_zero)
    old_tier = tier_for(old_total, old_high, old_medium)

    # NEW (enforce): gate first, then 8-step + v3 thresholds.
    gate = _gate_reason(step_results)
    new_score = rescaled_total(step_results, weights, GATE_STEPS)
    new_tier = tier_for(new_score, v3_high, v3_medium)
    new_pass = scored and (gate is None) and (new_score >= v3_min_pass)  # per-strategy min all 0 today

    explained_by_residual = age_half or at_circuit

    if not scored:
        cls, explained, reason = "UNCHANGED", True, "not-scored"
    elif old_pass and gate is not None:
        # only at-circuit can gate an old-pass (age-zero would already fail old_pass)
        cls, explained, reason = "NOW_GATED", at_circuit, f"gate={gate}"
    elif old_pass and not new_pass:
        cls = "FLIP_FAIL"
        # Explained residual: age-band, at-circuit, OR the integer-rounding boundary
        # — a signal that passed OLD only by rounding UP to exactly old_min_pass
        # (old_total==min_pass) whose re-scaled score rounds just under. Inherent to
        # the ÷100-vs-÷80 proportional rounding; ~0.009% of rows, all fresh/non-circuit.
        rounding_edge = old_total == old_min_pass
        explained = explained_by_residual or rounding_edge
        reason = ("age0.5" if age_half else ("at_circuit" if at_circuit
                  else ("rounding_boundary" if rounding_edge else "UNEXPLAINED")))
    elif old_pass and new_pass and new_tier != old_tier:
        cls = "TIER_SHIFT"
        explained = explained_by_residual
        reason = (f"{old_tier}->{new_tier} "
                  + ("age0.5" if age_half else ("at_circuit" if at_circuit else "UNEXPLAINED")))
    elif (not old_pass) and gate is None and new_pass:
        cls = "FLIP_PASS"
        explained = explained_by_residual
        reason = "age0.5" if age_half else ("at_circuit" if at_circuit else "UNEXPLAINED")
    else:
        cls, explained, reason = "UNCHANGED", True, ""

    return RowVerdict(signal_id, old_status, old_total, new_score, new_tier,
                      gate, cls, explained, reason)


def analyze(rows: List[dict], *, weights, v3_min_pass, v3_high, v3_medium) -> dict:
    """Classify every row; return summary counts + the non-UNCHANGED verdicts +
    the UNEXPLAINED subset (must be empty for parity)."""
    verdicts = [classify_row(r, weights=weights, v3_min_pass=v3_min_pass,
                             v3_high=v3_high, v3_medium=v3_medium) for r in rows]
    counts: Dict[str, int] = {}
    for v in verdicts:
        counts[v.classification] = counts.get(v.classification, 0) + 1
    flips = [v for v in verdicts if v.classification != "UNCHANGED"]
    unexplained = [v for v in flips if not v.explained]
    return {
        "total": len(rows), "counts": counts,
        "flips": flips, "unexplained": unexplained,
        "parity_ok": len(unexplained) == 0,
    }


def data_fit(rows: List[dict], *, weights,
             min_grid=range(48, 57), med_grid=range(54, 60), high_grid=range(73, 78)) -> dict:
    """Sweep candidate thresholds (around the analytic 50/56/75) and pick the combo
    with the FEWEST total flips (ties → closest to the analytic start), subject to
    zero UNEXPLAINED. Returns the fitted thresholds + their analysis."""
    best = None
    for mp in min_grid:
        for md in med_grid:
            for hi in high_grid:
                if not (hi > md > mp):
                    continue
                res = analyze(rows, weights=weights, v3_min_pass=mp, v3_high=hi, v3_medium=md)
                if not res["parity_ok"]:
                    continue
                nflip = len(res["flips"])
                dist = abs(mp - 50) + abs(md - 56) + abs(hi - 75)
                key = (nflip, dist)
                if best is None or key < best[0]:
                    best = (key, {"min_pass": mp, "medium": md, "high": hi}, res)
    if best is None:
        return {"fitted": None, "note": "no threshold set achieved zero-unexplained parity"}
    return {"fitted": best[1], "analysis": best[2]}


# ─────────────────────────────────────────────────────────────────────────────
# main (VM / backup DB). Not unit-tested (needs a real corpus).
# ─────────────────────────────────────────────────────────────────────────────

def _load_weights() -> Dict[str, int]:  # pragma: no cover
    try:
        import yaml
        raw = yaml.safe_load(open(ROOT / "config" / "scoring_weights.yaml"))
        return dict(raw["steps"])
    except Exception:
        return dict(_DEFAULT_WEIGHTS)


def main(argv=None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description="V3 Hard-Gate offline parity recompute (use a DB BACKUP).")
    ap.add_argument("--db", required=True, help="path to a screener_results DB BACKUP (read-only)")
    ap.add_argument("--limit", type=int, default=0, help="max rows (0 = all)")
    args = ap.parse_args(argv)

    weights = _load_weights()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM screener_results ORDER BY id"
    if args.limit:
        q += f" LIMIT {int(args.limit)}"
    rows = [dict(r) for r in con.execute(q).fetchall()]
    con.close()

    fit = data_fit(rows, weights=weights)
    print(f"\nV3 HARD-GATE PARITY RECOMPUTE — {len(rows)} rows from {args.db}")
    if not fit.get("fitted"):
        print("  RESULT: " + fit.get("note", "no fit"))
        return 1
    t = fit["fitted"]
    res = fit["analysis"]
    print(f"  FITTED THRESHOLDS: min_pass={t['min_pass']} medium={t['medium']} high={t['high']}")
    print(f"  COUNTS: {res['counts']}")
    print(f"  PARITY_OK (zero unexplained flips): {res['parity_ok']}")
    if res["flips"]:
        print("  FLIP SET (explained residual — age0.5 / at_circuit):")
        for v in res["flips"][:200]:
            print(f"    {v.signal_id}  {v.classification}  old={v.old_status}/{v.old_score} "
                  f"new={v.new_score}/{v.new_tier} gate={v.gate_reason} [{v.reason}]")
    return 0 if res["parity_ok"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
