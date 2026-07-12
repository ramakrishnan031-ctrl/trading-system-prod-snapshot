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


# ── V3 CHAIN would-be report — the G-KALYAN join (reads the shadow JSONL) ────
def _trade_outcomes() -> dict:
    """{signal_id: net_pnl} for CLOSED live trades (the actual outcome). Read-only;
    empty {} if the DB or the column is absent (dev tree)."""
    if not _DB.exists():
        return {}
    out: dict = {}
    conn = sqlite3.connect(f"file:{_DB.as_posix()}?mode=ro", uri=True, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT signal_id, net_pnl FROM trades WHERE signal_id IS NOT NULL "
            "AND net_pnl IS NOT NULL")
        for r in cur.fetchall():
            sid = r["signal_id"]
            out[sid] = out.get(sid, 0.0) + float(r["net_pnl"])
    except Exception as exc:
        print(f"  (trades outcome join unavailable: {exc})")
    finally:
        conn.close()
    return out


def _median(xs: list) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def v3_report(since: str | None) -> int:
    """G-KALYAN: join the V3 would-be verdicts to the ACTUAL trade outcomes and ask —
    of our LIVE-TRADED signals, how many would the S&R R:R gate have rejected, and did
    those trades make or lose money? If it rejected our WINNERS the gate/S&R is wrong;
    if it rejected our LOSERS that is the edge, measured on real money."""
    from core.config_loader import load_all
    cfg = load_all(_REPO / "config")
    path = _REPO / getattr(cfg.system.v3_chain, "would_be_log_path", "data_store/v3/would_be.jsonl")
    print(f"\n=== V3 CHAIN would-be report / G-KALYAN  (since={since or 'ALL'}) ===")
    print(f"would-be log: {path}")
    if not path.exists():
        print("  (no would_be.jsonl yet — the V3 chain writes it per signal in shadow mode)")
        return 0

    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            day = str(rec.get("signal_ts", ""))[:10]
            if since and day and day < since:
                continue
            rows.append(rec)
    if not rows:
        print("  (no would-be rows in range)")
        return 0

    n = len(rows)
    # (d) sr_sync_hit rate — is a future SYNC/enforce path viable without pre-warming?
    sync_hits = sum(1 for r in rows if r.get("sr_sync_hit"))
    # (e) gate-failure distribution (over ALL enriched signals).
    gate_fail = defaultdict(int)
    verdict_ct = defaultdict(int)
    for r in rows:
        verdict_ct[r.get("v3_verdict", "?")] += 1
        for g, gd in (r.get("gates") or {}).items():
            if isinstance(gd, dict) and gd.get("passed") is False:
                gate_fail[g] += 1

    print(f"  enriched signals: {n}")
    print(f"  sr_sync_hit: {sync_hits}/{n} ({100.0*sync_hits/n:.1f}%)  "
          f"-- low => a future SYNC/enforce path REQUIRES S&R cache pre-warming (pre-market, "
          f"over the day's Chartink universe). Expected ~0% today (no cache runs).")
    print("  gate-fail counts:  " + ("  ".join(f"{g}={c}" for g, c in sorted(gate_fail.items())) or "none"))
    print("  verdicts:          " + "  ".join(f"{v}={c}" for v, c in sorted(verdict_ct.items())))

    # (c) V3 R:R vs LIVE R:R distribution.
    v3_rrs = [float(r["v3_rr"]) for r in rows if r.get("v3_rr") is not None]
    live_rrs = [float(r["live_rr"]) for r in rows if r.get("live_rr") is not None]
    if v3_rrs:
        print(f"  V3 R:R   n={len(v3_rrs)} mean={sum(v3_rrs)/len(v3_rrs):.2f} median={_median(v3_rrs):.2f}")
    if live_rrs:
        print(f"  live R:R n={len(live_rrs)} mean={sum(live_rrs)/len(live_rrs):.2f} median={_median(live_rrs):.2f}")

    # (a)+(b) THE G-KALYAN JOIN — R:R gate verdict vs real P&L on TRADED signals.
    outcomes = _trade_outcomes()
    if not outcomes:
        print("\n  G-KALYAN: no trade outcomes to join (dev tree / no closed trades). "
              "Run on the VM after live sessions accumulate.")
        return 0
    keep_pnls, reject_pnls = [], []
    traded = 0
    for r in rows:
        sid = r.get("live_outcome_link") or r.get("signal_id")
        if sid not in outcomes:
            continue
        traded += 1
        rr_gate = ((r.get("gates") or {}).get("RR") or {}).get("passed")
        pnl = outcomes[sid]
        (keep_pnls if rr_gate else reject_pnls).append(pnl)

    print(f"\n  G-KALYAN — R:R gate vs REAL outcomes on {traded} live-traded signals:")
    if traded == 0:
        print("    (no would-be rows joined to a closed trade yet)")
        return 0
    n_rej = len(reject_pnls)
    print(f"    would-REJECT (R:R gate) : {n_rej}/{traded} ({100.0*n_rej/traded:.1f}% of our real trades)")
    if reject_pnls:
        print(f"      their ACTUAL P&L: total ₹{sum(reject_pnls):.2f}  "
              f"expectancy ₹{sum(reject_pnls)/n_rej:.2f}/trade  "
              f"wins {sum(1 for p in reject_pnls if p>0)}/{n_rej}")
    if keep_pnls:
        print(f"    would-KEEP  (R:R gate) : {len(keep_pnls)}  "
              f"total ₹{sum(keep_pnls):.2f}  expectancy ₹{sum(keep_pnls)/len(keep_pnls):.2f}/trade  "
              f"wins {sum(1 for p in keep_pnls if p>0)}/{len(keep_pnls)}")
    print("    INTERPRETATION: if the gate REJECTED our winners (reject-bucket P&L > 0), the "
          "gate or the S&R is wrong. If it rejected our losers (reject-bucket P&L < 0), that is "
          "the edge -- measured on real money, before PB-01 exists. Small N => provisional.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="V3 shadow soak report (read-only)")
    ap.add_argument("--scorer", action="store_true", help="scorer OLD-vs-NEW flip-set")
    ap.add_argument("--allocator", action="store_true", help="allocator regret summary")
    ap.add_argument("--v3", action="store_true", help="V3 chain would-be report (G-KALYAN join)")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD lower bound (a session date)")
    args = ap.parse_args()
    run_both = not (args.scorer or args.allocator or args.v3)
    rc = 0
    if args.scorer or run_both:
        rc = max(rc, scorer_report(args.since))
    if args.allocator or run_both:
        rc = max(rc, allocator_report(args.since))
    if args.v3 or run_both:
        rc = max(rc, v3_report(args.since))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
