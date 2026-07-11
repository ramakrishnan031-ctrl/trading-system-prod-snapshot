"""
scripts/v3_shadow_soak_report.py — V3 SHADOW SOAK REPORT (read-only).

Produces the two shadow-soak reports so they can be generated without touching the
live system beyond a READ-ONLY DB open + a JSONL read:

  --scorer   [--since YYYY-MM-DD]   OLD-vs-NEW flip-set for scoring.v3_hardgate_mode
                                    (shadow). Reuses the VALIDATED classifier from
                                    scripts/v3_hardgate_parity_recompute.py (analyze),
                                    scoped to a session/date over the LIVE screener_results.
                                    Expected post-flip: UNCHANGED + age-band (FLIP_PASS/
                                    TIER_SHIFT) + rounding-boundary (FLIP_FAIL) ONLY,
                                    0 UNEXPLAINED (matches the offline parity artifact).
  --allocator [--since YYYY-MM-DD]  per-session summary of the allocator regret rows
                                    (data_store/allocator/regret.jsonl): windows,
                                    crowd_out, starvation, score_weighted_regret.

Default (no flag): run BOTH. NEVER writes; opens the DB read-only via core.db_connect.
A STOP condition (any UNEXPLAINED scorer flip, or an unexpected regret spike) is printed
loudly so the soak can be halted + the flags reverted to off.

Usage:
    python scripts/v3_shadow_soak_report.py                 # both, all dates
    python scripts/v3_shadow_soak_report.py --scorer --since 2026-07-13
    python scripts/v3_shadow_soak_report.py --allocator --since 2026-07-13
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DB = _REPO / "data_store" / "trading_system.db"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))  # import the sibling recompute module


def _load_v3_thresholds():
    """Read the v3 thresholds from config (authoritative)."""
    from core.config_loader import load_all
    cfg = load_all(_REPO / "config")
    s = cfg.scoring
    return (int(s.v3_min_pass_score), int(s.v3_high_score_threshold), int(s.v3_medium_score_threshold))


# ── SCORER flip-set (reuses the validated offline classifier) ────────────────
def scorer_report(since: str | None) -> int:
    from v3_hardgate_parity_recompute import analyze, _load_weights  # validated logic

    weights = _load_weights()
    v3_min, v3_high, v3_med = _load_v3_thresholds()

    where = ""
    params: tuple = ()
    if since:
        where = "WHERE date(ts) >= ?"
        params = (since,)
    if not _DB.exists():
        print(f"\n=== SCORER SHADOW FLIP-SET ===\n  (DB not found at {_DB} — run on the VM)")
        return 0
    # READ-ONLY open (mode=ro URI); screener_results lives in the MAIN db → no analytics attach.
    conn = sqlite3.connect(f"file:{_DB.as_posix()}?mode=ro", uri=True, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            f"SELECT signal_id, score, tier, status, step_results, ts "
            f"FROM screener_results {where} ORDER BY ts",
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    print(f"\n=== SCORER SHADOW FLIP-SET  (since={since or 'ALL'}, thresholds "
          f"{v3_min}/{v3_med}/{v3_high}) ===")
    print(f"screener_results rows: {len(rows)}")
    if not rows:
        print("  (no rows in range — run after the first post-flip session)")
        return 0
    res = analyze(rows, weights=weights, v3_min_pass=v3_min, v3_high=v3_high, v3_medium=v3_med)
    for cls in ("UNCHANGED", "FLIP_PASS", "FLIP_FAIL", "TIER_SHIFT", "NOW_GATED"):
        if cls in res["counts"]:
            print(f"  {cls:<10} {res['counts'][cls]}")
    n = res["total"]
    unchanged = res["counts"].get("UNCHANGED", 0)
    print(f"  UNCHANGED %: {100.0 * unchanged / n:.2f}" if n else "  n/a")
    print(f"  UNEXPLAINED: {len(res['unexplained'])}   PARITY_OK: {res['parity_ok']}")
    for v in res["flips"][:40]:
        print(f"    {v.classification:<10} {v.signal_id}  old={v.old_total} new={v.new_score} "
              f"tier={v.new_tier} gate={v.gate} :: {v.reason}")
    if res["unexplained"]:
        print("\n  🛑 STOP CONDITION: UNEXPLAINED scorer flip(s) present — "
              "NOT predicted by the offline parity artifact. REVERT v3_hardgate_mode -> off "
              "and report immediately.")
        return 2
    print("  ✅ every flip explained (age-band / rounding-boundary) — matches the artifact.")
    return 0


# ── ALLOCATOR regret summary (reads the shadow JSONL) ────────────────────────
def allocator_report(since: str | None) -> int:
    from core.config_loader import load_all
    cfg = load_all(_REPO / "config")
    path = _REPO / getattr(cfg.system.portfolio_allocator, "regret_log_path",
                           "data_store/allocator/regret.jsonl")
    print(f"\n=== ALLOCATOR SHADOW REGRET  (since={since or 'ALL'}) ===")
    print(f"regret log: {path}")
    if not path.exists():
        print("  (no regret.jsonl yet — allocator writes it once per window in shadow mode)")
        return 0
    per_session: dict = defaultdict(lambda: {"windows": 0, "crowd_out": 0, "starvation": 0,
                                             "swr": 0.0, "candidates": 0})
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            day = str(rec.get("ts", ""))[:10]
            if since and day and day < since:
                continue
            s = per_session[day or "unknown"]
            s["windows"] += 1
            s["crowd_out"] += int(rec.get("crowd_out", 0))
            s["starvation"] += int(rec.get("starvation", 0))
            s["swr"] += float(rec.get("score_weighted_regret", 0.0))
            s["candidates"] += int(rec.get("n_candidates", 0))
    if not per_session:
        print("  (no regret rows in range)")
        return 0
    print(f"  {'session':<12}{'windows':>8}{'cands':>7}{'crowd_out':>11}{'starv':>7}{'Σregret':>10}{'avg/win':>9}")
    for day in sorted(per_session):
        s = per_session[day]
        avg = s["swr"] / s["windows"] if s["windows"] else 0.0
        print(f"  {day:<12}{s['windows']:>8}{s['candidates']:>7}{s['crowd_out']:>11}"
              f"{s['starvation']:>7}{s['swr']:>10.2f}{avg:>9.2f}")
    print("  (crowd_out = FCFS took / ranked would drop · starvation = ranked would take / "
          "FCFS starved · Σregret = score-weighted, >0 ⇒ ranked captures more quality. "
          "DIRECTIONAL counterfactual — needs ≥5 sessions before any enforce discussion.)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="V3 shadow soak report (read-only)")
    ap.add_argument("--scorer", action="store_true", help="scorer OLD-vs-NEW flip-set")
    ap.add_argument("--allocator", action="store_true", help="allocator regret summary")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD lower bound (a session date)")
    args = ap.parse_args()
    run_both = not (args.scorer or args.allocator)
    rc = 0
    if args.scorer or run_both:
        rc = max(rc, scorer_report(args.since))
    if args.allocator or run_both:
        rc = max(rc, allocator_report(args.since))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
