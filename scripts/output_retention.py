#!/usr/bin/env python3
# =============================================================================
# scripts/output_retention.py  --  keep-N reaper for reports/output/ and logs/.
#
# WHY THIS EXISTS: reports/output/ had NO retention at all -- it grew unbounded,
# and the ops_dashboard says so in as many words ("reports/output has NO
# retention -- grows unbounded"). logs/ was on a 30-day mtime cron that does not
# match every family in it.
#
# WHY COUNT-BASED AND NOT `-mtime +N`  (this is the whole design decision):
#   The retention unit is SEVEN TRADING DAYS, not seven calendar days. An mtime
#   rule cannot express that. `-mtime +7` spans a weekend and keeps ~5 trading
#   days; across a long weekend or a holiday cluster it keeps fewer still. It
#   UNDER-RETAINS, silently, and by a margin that varies with the calendar.
#
#   These producers only write on trading days, so "the newest 7 files" IS
#   "7 trading days" -- by construction, with no calendar arithmetic and no
#   holiday table to keep current. Same shape as backup_retention.py's keep-N.
#
#   Ranking is on the DATE IN THE FILENAME, never mtime. mtime lies here:
#   alert_watcher_2026-07-28.log carried an mtime of 01-Aug, and
#   alert_watcher_2026-08-01.log carried 18-Aug -- an mtime sort would have
#   ordered those two files wrongly against their own content.
#
# POLICY (per family; a family = one glob):
#     KEEP  -- sort by filename date DESCENDING, retain the newest
#              KEEP_PER_FAMILY (7), delete the rest. Each family is counted
#              INDEPENDENTLY; families are never pooled.
#     FLOOR -- structural: keep-N cannot take a family below N, so the
#              emptying case is impossible by construction rather than by a
#              separate guard.
#     CAP   -- if a run would delete more than --max-delete (default 25) files
#              it ABORTS and deletes NOTHING. Steady state is ~6 files/day, so
#              25 is ~4x headroom; a backlog clear needs an explicit override.
#
# SCOPE IS CLOSED, AND DELIBERATELY SO:
#   reports/output/  daily_report_*, daily_trade_review_report_*
#   logs/            system_*, debug_*, trades_*, reconciler_*, alert_watcher_*
#
#   NEVER TOUCHED (excluded by NAME):
#     reports/output/.gitkeep   -- GIT-TRACKED. Deleting it dirties the deployed
#                                  tree, and checkout -f restores it anyway.
#     logs/.holiday_notified_*  -- state markers, not output.
#     logs/cron-*.log           -- append-mode, undated: unrankable by date and
#                                  mismatched by any mtime rule.
#
#   A file in a scope dir that matches no family, or that matches a family but
#   carries NO PARSEABLE DATE, is REFUSED and logged -- never deleted. An
#   unrankable file must not be allowed to displace a real one from the keep set.
#
# OUT OF SCOPE ON PURPOSE -- DO NOT ADD THESE:
#   data_store/backups/    -- restore depth, owned by backup_retention.py
#                             (keep 20 pre_* / 14 daily / 14 analytics).
#   data_store/v3/*.jsonl  -- append-only and UNREGENERABLE; read IN FULL by
#                             v3_shadow_soak_report.py. Never delete.
#   data_store/candles/, t2_proof_*/, control_tower/, cron_marks/,
#   security_state.json.
#
# WHAT SURVIVES DELETION: both xlsx families rebuild from the DB --
# daily_trade_review is DB-pure by its own guardrail, daily_report is
# DB-derivable via config_snapshots. EXCEPT daily_report_2026-06-19 ..
# 2026-07-01, which PREDATE config_snapshots (first row 2026-07-02); those were
# archived to reports/archive_prereconstruct/ and are outside this scope.
# THE LOGS DO NOT REBUILD -- nothing regenerates a deleted log file.
#
# SAFETY: dry-run BY DEFAULT (--apply to delete) - realpath-asserted inside the
# owning scope dir - symlinks refused - never raises into cron, and records a
# heartbeat on BOTH the success and failure paths (monitored: true).
# =============================================================================
from __future__ import annotations

import argparse
import fnmatch
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

KEEP_PER_FAMILY = 7          # seven TRADING days, by construction
DEFAULT_MAX_DELETE = 25

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# (subdir, family globs, never-touch globs)
SCOPES: list[tuple[str, list[str], list[str]]] = [
    ("reports/output",
     ["daily_report_*.xlsx", "daily_trade_review_report_*.xlsx"],
     [".gitkeep"]),
    ("logs",
     ["system_*.log", "debug_*.log", "trades_*.log", "reconciler_*.log",
      "alert_watcher_*.log"],
     [".holiday_notified_*", "cron-*.log"]),
]


def retention_rule() -> str:
    """State the LIVE rule for the daily cron report.

    Derived from the constant rather than restated, so a wrong window shows up
    in the next report instead of hiding behind a stale sentence.
    """
    return (f"Output retention: keep newest {KEEP_PER_FAMILY} per family "
            f"(= {KEEP_PER_FAMILY} trading days, ranked by filename date) "
            f"in reports/output + logs; cap {DEFAULT_MAX_DELETE} per run")


def file_date(p: Path) -> str | None:
    """The YYYY-MM-DD embedded in the filename, or None if absent.

    Deliberately NOT mtime: a log rotated or touched later carries an mtime that
    disagrees with the day it describes.
    """
    m = _DATE_RE.search(p.name)
    return m.group(1) if m else None


@dataclass
class FamPlan:
    scope: str
    base: Path
    pattern: str
    present: int
    keep: list = field(default_factory=list)
    delete: list = field(default_factory=list)
    freed: int = 0
    undated: int = 0             # matched the family but carried no date


@dataclass
class Plan:
    fams: list = field(default_factory=list)
    refused: list = field(default_factory=list)   # (path, reason)

    @property
    def to_delete(self) -> list:
        return [p for f in self.fams for p in f.delete]

    @property
    def freed(self) -> int:
        return sum(f.freed for f in self.fams)


def build_plan(root: Path = _ROOT, keep: int = KEEP_PER_FAMILY) -> Plan:
    """Pure planner: decides what WOULD be deleted. Touches nothing."""
    plan = Plan()

    for subdir, families, never in SCOPES:
        base = (root / subdir).resolve()
        if not base.is_dir():
            continue
        matched: set = set()

        for pattern in families:
            members, undated = [], 0
            for p in base.iterdir():
                if not p.is_file() or p.is_symlink():
                    continue
                if not fnmatch.fnmatch(p.name, pattern):
                    continue
                matched.add(p)
                if file_date(p) is None:
                    # Unrankable: refuse it rather than let it take a keep slot.
                    plan.refused.append((p, "no date in filename"))
                    undated += 1
                else:
                    members.append(p)

            # Rank by filename date, newest first; name breaks ties so the
            # ordering is total and deterministic.
            members.sort(key=lambda q: (file_date(q), q.name), reverse=True)
            doomed = members[keep:]

            plan.fams.append(FamPlan(
                scope=subdir, base=base, pattern=pattern, present=len(members),
                keep=members[:keep], delete=doomed,
                freed=sum(p.stat().st_size for p in doomed),
                undated=undated,
            ))

        # Everything else in the scope dir: excluded by name, or REFUSED.
        for p in base.iterdir():
            if not p.is_file() or p in matched:
                continue
            if p.is_symlink():
                plan.refused.append((p, "symlink"))
            elif not any(fnmatch.fnmatch(p.name, g) for g in never):
                plan.refused.append((p, "matches no family"))
            # else: excluded by design -- not an error, not reported as refused

    return plan


def _assert_inside(p: Path, base: Path) -> None:
    rp, rb = os.path.realpath(p), os.path.realpath(base)
    if not rp.startswith(rb + os.sep):
        raise RuntimeError(f"refusing path outside its scope dir: {p}")


def render(plan: Plan, applying: bool, max_delete: int) -> str:
    head = "APPLY (deleting)" if applying else "DRY-RUN (no deletion)"
    out = [f"OUTPUT RETENTION -- {head}  |  {retention_rule()}"]
    cur = None
    for f in plan.fams:
        if f.scope != cur:
            out.append(f"  {f.scope}")
            cur = f.scope
        oldest = file_date(f.keep[-1]) if f.keep else "-"
        note = f"   [{f.undated} undated refused]" if f.undated else ""
        out.append(f"    {f.pattern:<32} present {f.present:>3}  keep {len(f.keep):>3}"
                   f"  delete {len(f.delete):>3}  free {f.freed / 1048576:.2f} MB"
                   f"  oldest-kept {oldest}{note}")
    if plan.refused:
        names = ", ".join(p.name for p, _ in plan.refused[:8])
        more = "" if len(plan.refused) <= 8 else f" (+{len(plan.refused) - 8} more)"
        out.append(f"  refused (never deleted): {len(plan.refused)}: {names}{more}")
    else:
        out.append("  refused (never deleted): 0")
    out.append(f"  TOTAL delete {len(plan.to_delete)}   "
               f"free {plan.freed / 1048576:.2f} MB   (cap {max_delete})")
    return "\n".join(out)


def apply_plan(plan: Plan, log: logging.Logger) -> tuple[int, int]:
    """Delete the planned files. Each path is realpath-asserted inside the base
    of the family that selected it -- never inferred from the path string."""
    deleted = freed = 0
    for fam in plan.fams:
        for p in fam.delete:
            try:
                _assert_inside(p, fam.base)
                size = p.stat().st_size
                p.unlink()
                deleted += 1
                freed += size
            except FileNotFoundError:
                log.warning("output_retention skip %s: already-gone", p.name)
            except Exception as exc:                  # noqa: BLE001
                log.warning("output_retention skip %s: %s", p.name, exc)
    return deleted, freed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="keep-N retention for reports/output/ and logs/ "
                    "(N = trading days, ranked by filename date).")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default: dry-run, deletes nothing)")
    ap.add_argument("--keep", type=int, default=KEEP_PER_FAMILY,
                    help=f"files to keep per family (default {KEEP_PER_FAMILY})")
    ap.add_argument("--max-delete", type=int, default=DEFAULT_MAX_DELETE,
                    help=f"abort the run if it would delete more than N files "
                         f"(default {DEFAULT_MAX_DELETE})")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("output_retention")
    started = time.perf_counter()

    status, deleted, freed = "SUCCESS", 0, 0
    try:
        plan = build_plan(keep=args.keep)
        print(render(plan, args.apply, args.max_delete))
        for p, why in plan.refused:
            log.warning("output_retention refused %s: %s", p.name, why)

        n = len(plan.to_delete)
        if n > args.max_delete:
            status = "FAILED"
            log.error("output_retention ABORTED: would delete %d files > cap %d. "
                      "NOTHING deleted. Review, then re-run with --max-delete N.",
                      n, args.max_delete)
        elif args.apply:
            deleted, freed = apply_plan(plan, log)
            log.info("output_retention: deleted=%d freed_mb=%.2f",
                     deleted, freed / 1048576)
    except Exception as exc:                          # noqa: BLE001
        status = "FAILED"
        log.error("output_retention failed: %s", exc)

    # monitored: true in cron_registry -> the Cron Officer expects this heartbeat.
    try:
        from utils.cron_heartbeat import record_heartbeat
        record_heartbeat("output_retention", status=status,
                         duration_sec=time.perf_counter() - started,
                         message=f"deleted={deleted} freed_mb={freed / 1048576:.2f}")
    except Exception:                                 # pragma: no cover -- best-effort
        pass

    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
