"""
tests/crash_test/crash_test_reporter.py — Tool 6: Aggregate scenario results into reports.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import (
    ist_now_iso, today_str, send_telegram, load_master_tracker,
    REPORTS_DIR, RESULTS_DIR,
)

CLASSIFICATIONS = [
    "PASS", "PASS_WITH_RISK", "FAIL", "KNOWN_LIMITATION",
    "DESIGN_GAP", "CANNOT_TEST", "NOT_APPLICABLE",
]

# Day -> scenario ID prefix mapping (will also filter by YAML day field)
DAY_MAP = {
    1: range(1, 37),
    2: range(37, 67),
    3: range(67, 93),
    4: range(93, 136),
    5: range(136, 183),
}


def _load_results(day: int | None = None, scenario_id: str | None = None) -> List[Dict]:
    """Load result JSON files."""
    results = []
    if scenario_id:
        path = RESULTS_DIR / f"{scenario_id}.json"
        if path.exists():
            with open(path) as f:
                results.append(json.load(f))
        return results

    for path in sorted(RESULTS_DIR.glob("CT*.json")):
        try:
            with open(path) as f:
                r = json.load(f)
            if day is not None:
                # Filter by day from result or by scenario number
                sid = r.get("scenario_id", "")
                num = int("".join(c for c in sid if c.isdigit()) or "0")
                if day in DAY_MAP and num in DAY_MAP[day]:
                    results.append(r)
                elif r.get("details", {}).get("day") == day:
                    results.append(r)
            else:
                results.append(r)
        except (json.JSONDecodeError, Exception):
            continue
    return results


def _generate_report(results: List[Dict], day: int | None, date: str) -> str:
    """Generate markdown report."""
    lines = []
    day_label = f"Day {day}" if day else "All Days"

    # Header
    lines.append(f"# Crash Test Report — {day_label}")
    lines.append(f"**Date:** {date}")
    lines.append(f"**Total Scenarios:** {len(results)}")
    lines.append(f"**Generated:** {ist_now_iso()}")
    lines.append("")

    if not results:
        lines.append("*No results found.*")
        return "\n".join(lines)

    # Summary table
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| CT# | Title | Priority | Classification | Invariants | Notes |")
    lines.append("|-----|-------|----------|----------------|------------|-------|")
    for r in results:
        sid = r.get("scenario_id", "?")
        title = r.get("title", "")[:40]
        priority = r.get("details", {}).get("priority", "")
        classification = r.get("classification", "?")
        inv = r.get("invariant_results", {}).get("overall", "N/A")
        notes = r.get("root_cause", "")[:30]
        lines.append(f"| {sid} | {title} | {priority} | {classification} | {inv} | {notes} |")
    lines.append("")

    # Statistics
    counts = Counter(r.get("classification", "UNKNOWN") for r in results)
    lines.append("## Statistics")
    lines.append("")
    stats = " | ".join(f"{c}: {counts.get(c, 0)}" for c in CLASSIFICATIONS)
    lines.append(stats)
    lines.append("")

    # Invariant violations
    violations = [r for r in results
                  if r.get("invariant_results", {}).get("overall") == "FAIL"]
    if violations:
        lines.append("## Invariant Violations")
        lines.append("")
        for v in violations:
            sid = v.get("scenario_id", "?")
            inv = v.get("invariant_results", {}).get("invariants", {})
            failed = [k for k, val in inv.items() if val.get("status") == "FAIL"]
            lines.append(f"- **{sid}**: Failed invariants: {', '.join(failed)}")
        lines.append("")

    # Critical findings
    fails = [r for r in results if r.get("classification") == "FAIL"]
    if fails:
        lines.append("## Critical Findings")
        lines.append("")
        for f_r in fails:
            sid = f_r.get("scenario_id", "?")
            title = f_r.get("title", "")
            root = f_r.get("root_cause", "Unknown")
            lines.append(f"### {sid}: {title}")
            lines.append(f"- **Root cause:** {root}")
            lines.append(f"- **Business impact:** {f_r.get('business_impact', 'Unknown')}")
            lines.append(f"- **Fix required:** {f_r.get('fix_required', False)}")
            lines.append("")

    # Known limitations
    kls = [r for r in results if r.get("classification") == "KNOWN_LIMITATION"]
    if kls:
        lines.append("## Known Limitations")
        lines.append("")
        for kl in kls:
            lines.append(f"- **{kl.get('scenario_id')}**: {kl.get('title', '')}")
        lines.append("")

    # Design gaps
    dgs = [r for r in results if r.get("classification") == "DESIGN_GAP"]
    if dgs:
        lines.append("## Design Gaps")
        lines.append("")
        for dg in dgs:
            lines.append(f"- **{dg.get('scenario_id')}**: {dg.get('title', '')}")
        lines.append("")

    # Fixes required
    fixes = [r for r in results if r.get("fix_required")]
    if fixes:
        lines.append("## Fixes Required")
        lines.append("")
        for fx in fixes:
            lines.append(f"- {fx.get('fix_id', 'TBD')}: {fx.get('scenario_id')} — "
                         f"{fx.get('title', '')}")
        lines.append("")

    # Recommendations
    lines.append("## Recommendations for Next Day")
    lines.append("")
    if fails:
        lines.append(f"- Fix {len(fails)} FAIL scenarios before proceeding")
    if violations:
        lines.append(f"- Investigate {len(violations)} invariant violations")
    if not fails and not violations:
        lines.append("- All clear — proceed to next day")
    lines.append("")

    return "\n".join(lines)


def _update_master_tracker(day: int, results: List[Dict]) -> None:
    tracker = load_master_tracker()
    day_entry = {
        "day": day,
        "date": today_str(),
        "scenarios_run": [r.get("scenario_id") for r in results],
        "results": {r.get("scenario_id"): r.get("classification") for r in results},
        "completed": True,
    }
    # Update or append day entry
    days = tracker.get("days", [])
    existing_idx = next((i for i, d in enumerate(days) if d.get("day") == day), None)
    if existing_idx is not None:
        days[existing_idx] = day_entry
    else:
        days.append(day_entry)
    tracker["days"] = days
    from tests.crash_test.ct_utils import MASTER_TRACKER_PATH
    with open(MASTER_TRACKER_PATH, "w") as f:
        json.dump(tracker, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Crash Test Reporter — Tool 6")
    parser.add_argument("--day", type=str, required=True,
                        help="Day number (1-5) or 'all'")
    parser.add_argument("--scenario", type=str, help="Single scenario report")
    args = parser.parse_args()

    if args.scenario:
        results = _load_results(scenario_id=args.scenario)
        report = _generate_report(results, None, today_str())
        print(report)
        return

    day = None if args.day == "all" else int(args.day)
    results = _load_results(day=day)

    if not results:
        day_label = f"day {day}" if day else "all days"
        print(f"No results found for {day_label}.")
        print(f"Results directory: {RESULTS_DIR}")
        return

    date = today_str()
    report = _generate_report(results, day, date)

    # Save report
    if day:
        report_path = REPORTS_DIR / f"day{day}_report_{date}.md"
    else:
        report_path = REPORTS_DIR / f"full_report_{date}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report)
    print(report)
    print(f"\nReport saved: {report_path}")

    # Update master tracker
    if day:
        _update_master_tracker(day, results)

    # Telegram summary
    counts = Counter(r.get("classification", "UNKNOWN") for r in results)
    total = len(results)
    passed = counts.get("PASS", 0) + counts.get("PASS_WITH_RISK", 0)
    failed = counts.get("FAIL", 0)
    day_label = f"Day {day}" if day else "All Days"
    tg_msg = (f"Crash Test {day_label}: {passed}/{total} PASS | {failed} FAIL")
    if failed:
        fail_ids = [r.get("scenario_id") for r in results if r.get("classification") == "FAIL"]
        tg_msg += f" | Critical: {', '.join(fail_ids[:5])}"
    sent = send_telegram(tg_msg)
    if not sent:
        print("(Telegram notification skipped — not configured or unavailable)")


if __name__ == "__main__":
    main()
