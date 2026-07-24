#!/usr/bin/env python3
# =============================================================================
# scripts/backup_retention.py  —  T2: category-aware backup reaper.
#
# Replaces the old 02:00 `find -mtime +7 -delete`, which was pattern-scoped to
# the two DAILY categories only -> the pre_*_deploy/ad-hoc backups grew unbounded.
#
# Count-based keep-N per category, ordered by mtime (newest first):
#     pre_*                 -> keep newest 20   (deploy / ad-hoc rollback backups)
#     trading_system-*.db   -> keep newest 14   (daily main-DB backups)
#     analytics-*.db        -> keep newest 14   (daily analytics backups)
# (Monthly category DROPPED — no producer exists. Audit is LOG-ONLY — no table.)
#
# SAFETY (this is a DELETION job):
#   * DRY-RUN BY DEFAULT — prints the keep/delete plan, deletes nothing. Deletion
#     requires explicit --apply.
#   * NEVER-DELETE-NEWEST — each category always retains the newest N; the most
#     recent file can never be a delete candidate.
#   * SCOPED PATTERNS ONLY — operates within data_store/backups; every match is
#     realpath-asserted INSIDE that dir; symlinks refused; a file matching NO
#     category is refused + logged (never a wildcard/recursive delete).
#   * SANITY CAP — if a run would delete > --max-delete (default 10) files it
#     ABORTS (deletes nothing) + Telegram WARNING. Raise --max-delete after review.
#   * IDEMPOTENT — keep-N is stateless; a partial/re-run just re-evaluates.
# =============================================================================
from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_BACKUPS_DIR = _ROOT / "data_store" / "backups"
DEFAULT_MAX_DELETE = 10

# (glob, keep_newest_N). Patterns are disjoint by prefix and ANCHORED on the base
# `.db` file, so keep-N counts LOGICAL backups; the -wal/-shm sidecars are resolved
# separately in build_plan (reaped with their base .db) -- §E fix (24-Jul).
CATEGORIES: list[tuple[str, int]] = [
    ("pre_*.db", 20),             # deploy / ad-hoc rollback backups (incl. *_analytics.db)
    ("trading_system-*.db", 14),  # daily main-DB backups
    ("analytics-*.db", 14),       # daily analytics backups
]


@dataclass
class CatPlan:
    pattern: str
    keep_n: int
    present: int
    keep: list[Path] = field(default_factory=list)
    delete: list[Path] = field(default_factory=list)
    freed_bytes: int = 0


@dataclass
class Plan:
    backups_dir: Path
    categories: list[CatPlan]
    unmatched: list[Path]
    total_delete: int
    total_freed: int
    max_delete: int
    abort: bool  # total_delete > max_delete -> a sudden mass-delete, refuse
    # §E (24-Jul): SQLite -wal/-shm sidecars, resolved against their base .db.
    sidecar_delete: list[Path] = field(default_factory=list)      # reaped WITH a reaped/absent base
    sidecar_refused: list[tuple[Path, str]] = field(default_factory=list)  # (path, reason) e.g. non-zero -wal


@dataclass
class ExecResult:
    deleted: list[Path] = field(default_factory=list)
    freed_bytes: int = 0
    skipped: list[tuple[Path, str]] = field(default_factory=list)  # (path, reason)


_SIDECAR_SUFFIXES = ("-wal", "-shm")


def _is_sidecar(name: str) -> bool:
    """True for a SQLite WAL/SHM sidecar of a base `.db` file."""
    return name.endswith(_SIDECAR_SUFFIXES)


def _base_db_name(sidecar_name: str) -> str:
    """`foo.db-wal` / `foo.db-shm` -> `foo.db`."""
    for suf in _SIDECAR_SUFFIXES:
        if sidecar_name.endswith(suf):
            return sidecar_name[: -len(suf)]
    return sidecar_name


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def build_plan(backups_dir, max_delete: int = DEFAULT_MAX_DELETE,
               categories=CATEGORIES) -> Plan:
    """Compute the keep/delete plan. Pure: reads the filesystem, deletes nothing.

    §E (24-Jul): keep-N is computed on LOGICAL backups (the base `.db` files) only.
    The `-wal`/`-shm` sidecars are resolved AFTER: a sidecar is a delete candidate
    IFF its base `.db` is itself reaped this run OR its base `.db` is absent (an
    orphan) -- NEVER when the base `.db` survives (that would strand a live DB's WAL).
    A `-wal` that is not 0 bytes holds unflushed data and is REFUSED (kept + surfaced),
    never deleted.
    """
    base = Path(backups_dir).resolve()
    if not base.is_dir():
        raise NotADirectoryError(f"backups dir not found: {base}")

    entries = list(base.iterdir())
    # Symlinks are NEVER deletable (refuse + surface as unmatched).
    real_files = [p for p in entries if p.is_file() and not p.is_symlink()]
    refused_symlinks = [p for p in entries if p.is_symlink()]

    # keep-N counts LOGICAL backups: split base `.db` files from their sidecars.
    sidecar_files = [p for p in real_files if _is_sidecar(p.name)]
    db_files = [p for p in real_files if not _is_sidecar(p.name)]

    cats: list[CatPlan] = []
    matched: set[Path] = set()
    keep_db_names: set[str] = set()
    delete_db_names: set[str] = set()
    for pattern, keep_n in categories:
        members: list[Path] = []
        for p in db_files:
            if p in matched or not fnmatch.fnmatch(p.name, pattern):
                continue
            # Defense-in-depth: the resolved path must live inside `base`.
            if base not in p.resolve().parents:
                continue
            members.append(p)
            matched.add(p)
        members.sort(key=lambda x: x.stat().st_mtime, reverse=True)  # newest first
        keep, delete = members[:keep_n], members[keep_n:]
        keep_db_names.update(p.name for p in keep)
        delete_db_names.update(p.name for p in delete)
        freed = sum(x.stat().st_size for x in delete)
        cats.append(CatPlan(pattern, keep_n, len(members), keep, delete, freed))

    present_db_names = {p.name for p in db_files}

    # Sidecar resolution (§E). Reap IFF the base .db is reaped this run OR absent
    # (orphan); NEVER when the base survives. A non-zero -wal is refused (unflushed).
    sidecar_delete: list[Path] = []
    sidecar_refused: list[tuple[Path, str]] = []
    for sc in sidecar_files:
        bname = _base_db_name(sc.name)
        if bname in keep_db_names:
            continue  # base .db survives -> leave its sidecar alone (never strand a live WAL)
        base_reaped = bname in delete_db_names
        base_absent = bname not in present_db_names
        if not (base_reaped or base_absent):
            # base .db is present but matched no category (an unmatched .db that survives)
            continue
        if sc.name.endswith("-wal") and _safe_size(sc) > 0:
            sidecar_refused.append((sc, "non-zero -wal (unflushed data) -- refused"))
            continue
        sidecar_delete.append(sc)

    unmatched = [p for p in db_files if p not in matched] + refused_symlinks
    total_delete = sum(len(c.delete) for c in cats) + len(sidecar_delete)
    total_freed = (sum(c.freed_bytes for c in cats)
                   + sum(_safe_size(p) for p in sidecar_delete))
    return Plan(base, cats, unmatched, total_delete, total_freed,
                max_delete, total_delete > max_delete,
                sidecar_delete, sidecar_refused)


def execute(plan: Plan, apply: bool, log=None) -> ExecResult:
    """Delete the planned candidates (only when apply and not aborting)."""
    res = ExecResult()
    if not apply or plan.abort:
        return res
    # §E: base .db deletes first, then the sidecars reaped with them (sidecar_delete
    # already excludes any non-zero -wal and any sidecar whose base .db survives).
    to_delete = [p for cat in plan.categories for p in cat.delete] + list(plan.sidecar_delete)
    for p in to_delete:
        try:
            sz = p.stat().st_size
            p.unlink()
            res.deleted.append(p)
            res.freed_bytes += sz
        except FileNotFoundError:
            res.skipped.append((p, "already-gone"))      # list<->delete race
        except OSError as exc:
            res.skipped.append((p, f"locked/error: {exc}"))  # skip; retry next run
            if log:
                log.warning("backup_retention skip %s: %s", p.name, exc)
    return res


# ── reporting / I/O (only used by main) ──────────────────────────────────────
def format_report(plan: Plan, apply: bool) -> str:
    mode = "APPLY (deleting)" if apply else "dry-run (no deletions)"
    lines = [f"BACKUP RETENTION -- {mode}  |  {plan.backups_dir}"]
    for c in plan.categories:
        lines.append(f"  {c.pattern:<20} keep {c.keep_n:>2} / {c.present:>3} present"
                     f"   delete {len(c.delete):>2}   free {c.freed_bytes/1048576:.2f} MB")
    lines.append(f"  sidecars (-wal/-shm)  reap {len(plan.sidecar_delete):>2}"
                 f"   refused (non-zero -wal): {len(plan.sidecar_refused)}"
                 + "".join(f"\n      REFUSED {p.name}: {why}"
                           for p, why in plan.sidecar_refused))
    lines.append(f"  unmatched (refused, never deleted): {len(plan.unmatched)}"
                 + (": " + ", ".join(p.name for p in plan.unmatched) if plan.unmatched else ""))
    lines.append(f"  TOTAL delete {plan.total_delete}   free {plan.total_freed/1048576:.2f} MB"
                 f"   (sanity cap {plan.max_delete})")
    if plan.abort:
        lines.append(f"  *** ABORT: would delete {plan.total_delete} > cap {plan.max_delete} "
                     f"-- refusing. Review, then re-run with --max-delete N. ***")
    return "\n".join(lines)


def _audit_line(plan: Plan, apply: bool, res: ExecResult, status: str) -> str:
    per = " ".join(f"{c.pattern.rstrip('*-.db') or c.pattern}=keep{len(c.keep)}/del{len(c.delete)}"
                   for c in plan.categories)
    return (f"backup_retention_run mode={'apply' if apply else 'dry-run'} status={status} "
            f"{per} sidecar_reap={len(plan.sidecar_delete)} "
            f"sidecar_refused={len(plan.sidecar_refused)} "
            f"unmatched={len(plan.unmatched)} deleted={len(res.deleted)} "
            f"skipped={len(res.skipped)} freed_mb={res.freed_bytes/1048576:.2f}")


def _telegram(title: str, body: str, log) -> None:
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=log)
        if not notifier:
            return
        notifier.send(severity="WARNING", title=title, body=body,
                      source_module="backup_retention")
    except Exception as exc:  # alerting must never break the job
        log.debug("backup_retention_telegram_failed: %s", exc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="T2 category-aware backup reaper (keep-N, dry-run default).")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    ap.add_argument("--backups-dir", default=str(DEFAULT_BACKUPS_DIR), help="(test override)")
    ap.add_argument("--max-delete", type=int, default=DEFAULT_MAX_DELETE,
                    help=f"sanity cap; abort if a run would delete more (default {DEFAULT_MAX_DELETE})")
    args = ap.parse_args(argv)

    from core.logger import get_logger
    log = get_logger("backup_retention")

    try:
        plan = build_plan(args.backups_dir, max_delete=args.max_delete)
    except Exception as exc:
        log.error("backup_retention build_plan failed: %s", exc)
        print(f"backup_retention: ERROR {exc}", file=sys.stderr)
        return 1

    report = format_report(plan, args.apply)
    print(report)
    for ln in report.splitlines():
        log.info("%s", ln)

    if plan.unmatched:
        log.warning("backup_retention refused %d unmatched file(s): %s",
                    len(plan.unmatched), ", ".join(p.name for p in plan.unmatched))

    if not args.apply:
        log.info("%s", _audit_line(plan, False, ExecResult(), "DRYRUN"))
        return 0  # dry-run: never alerts, never heartbeats (it's a preview)

    # ── --apply path ──
    from utils.cron_heartbeat import record_heartbeat
    if plan.abort:
        msg = (f"Backup retention ABORTED: a run would delete {plan.total_delete} files "
               f"(> cap {plan.max_delete}). Nothing deleted. Review data_store/backups, "
               f"then re-run with --max-delete N if legitimate.")
        log.error("%s", msg)
        log.info("%s", _audit_line(plan, True, ExecResult(), "ABORT"))
        _telegram("Backup Retention ABORT", msg, log)
        record_heartbeat("backup_retention", status="FAILED", message=msg[:200])
        return 2

    res = execute(plan, apply=True, log=log)
    status = "PARTIAL" if res.skipped else "SUCCESS"
    log.info("%s", _audit_line(plan, True, res, status))

    # Telegram ONLY when something happened (avoid nightly 0-delete noise; the
    # heartbeat + Control Tower already give run visibility).
    if res.deleted or res.skipped:
        per = "\n".join(f"- {c.pattern}: deleted {len([d for d in res.deleted if d in c.delete])}, "
                        f"kept {len(c.keep)}" for c in plan.categories)
        body = (f"Deleted {len(res.deleted)} backup(s), freed {res.freed_bytes/1048576:.1f} MB.\n"
                f"{per}")
        if res.skipped:
            body += f"\nSkipped {len(res.skipped)} (locked/race; retry next run)."
        _telegram("Backup Retention", body, log)

    record_heartbeat("backup_retention", status=status,
                     message=f"deleted={len(res.deleted)} freed_mb={res.freed_bytes/1048576:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
