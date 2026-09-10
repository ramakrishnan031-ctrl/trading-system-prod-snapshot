#!/usr/bin/env python3
"""scripts/backup_evidence.py -- back up the forward evidence contract (Batch 1).

WHY THIS EXISTS. `data_store/evidence/*.jsonl` is excluded from
`output_retention.py`, so nothing deletes it -- but nothing BACKS IT UP either.
The daily cron backs up exactly two artefacts, both SQLite:
    db_backup        sqlite3 trading_system.db .backup backups/trading_system-<date>.db
    analytics_backup sqlite3 analytics.db      .backup backups/analytics-<date>.db
and `backup_retention.py` is scoped to `data_store/backups` alone.

⛔ EXCLUDED-FROM-PRUNING IS NOT BACKED-UP. A disk loss takes the evidence with
it, and the contract forbids backfill, so the day is gone permanently. This
script closes that gap.

WHAT IT DOES. Copies each `signal_evidence_<ARM>_<DATE>.jsonl` into
`data_store/backups/evidence/`, preserving the filename -- so the ARM and the
DATE survive into the backup set, and two machines' evidence can never merge
into one indistinguishable file.

SAFETY
  * READ-ONLY on the source. It copies; it never deletes, truncates or rewrites.
  * Verifies by sha256 AFTER the copy and re-copies once on mismatch; a second
    mismatch is an error, never a silent bad backup.
  * Skips a destination that already matches (idempotent, cheap to re-run).
  * Never raises into cron: rc 0 = ok, rc 1 = at least one file failed.
  * `--dry-run` by default is deliberately NOT used here -- unlike the retention
    scripts, this one only ever ADDS files, so the dangerous direction does not
    exist and a default-dry-run would just mean "no backup happened".

USAGE
    PYTHONPATH=. python scripts/backup_evidence.py            # today + any older
    PYTHONPATH=. python scripts/backup_evidence.py --all      # every day present
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "data_store" / "evidence"
DST_DIR = ROOT / "data_store" / "backups" / "evidence"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_once(src_dir: Path = SRC_DIR, dst_dir: Path = DST_DIR) -> Tuple[int, int, List[str]]:
    """Copy every evidence file. Returns (copied, skipped, failures)."""
    copied = skipped = 0
    failures: List[str] = []
    if not src_dir.is_dir():
        return (0, 0, [])
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(src_dir.glob("signal_evidence_*.jsonl")):
        dst = dst_dir / src.name
        try:
            src_hash = _sha256(src)
            if dst.exists() and _sha256(dst) == src_hash:
                skipped += 1
                continue
            shutil.copy2(src, dst)
            if _sha256(dst) != src_hash:
                # One retry: the source is append-only and may have grown
                # mid-copy. A SECOND mismatch is a real failure.
                shutil.copy2(src, dst)
                if _sha256(dst) != _sha256(src):
                    failures.append(f"{src.name}: hash mismatch after retry")
                    continue
            copied += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{src.name}: {exc}")
    return (copied, skipped, failures)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Back up the forward evidence JSONL set")
    ap.add_argument("--all", action="store_true",
                    help="accepted for symmetry; every present file is copied either way")
    ap.parse_args(argv)

    copied, skipped, failures = backup_once()
    print(f"backup_evidence: copied={copied} skipped={skipped} failed={len(failures)} "
          f"src={SRC_DIR} dst={DST_DIR}")
    for f in failures:
        print(f"  FAILED {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
