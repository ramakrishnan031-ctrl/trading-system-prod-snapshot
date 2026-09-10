#!/usr/bin/env python3
"""scripts/backup_evidence.py -- back up the forward evidence contract (Batch 1).

WHY THIS EXISTS. `data_store/evidence/*.jsonl` is excluded from
`output_retention.py`, so nothing deletes it -- but nothing BACKED IT UP either.
The daily cron backs up exactly two artefacts, both SQLite:
    db_backup        sqlite3 trading_system.db .backup backups/trading_system-<date>.db
    analytics_backup sqlite3 analytics.db      .backup backups/analytics-<date>.db
and `backup_retention.py` is scoped to the files at the base of
`data_store/backups` alone.

WHAT IT DOES. Copies each `signal_evidence_<ARM>_<DATE>.jsonl`, and each §6.7
failure ledger `evidence_failures_<ARM>_<DATE>.jsonl`, into
`data_store/backups/evidence/`, preserving the filename -- so the ARM and the
DATE survive into the backup set, and two machines' evidence can never merge
into one indistinguishable file.

⚠️ WHAT IT PROTECTS AGAINST -- AND WHAT IT DOES NOT. This is a SAME-DISK copy.
It protects against deletion, truncation or in-place corruption of the source.
It does NOT protect against loss of the disk or the machine: nothing in this
system copies ANY artefact off the box (the SQLite backups included). Off-box
protection is a separate, OPEN item. (The first version of this docstring said
this script closed the disk-loss gap. It never did.)

SAFETY
  * READ-ONLY on the source. It copies; it never deletes, truncates or rewrites.
  * ⭐ APPEND-ONLY AWARE. The source only ever grows, so an existing backup is
    replaced ONLY when it is a byte-prefix of the current source. If it is not
    -- the source shrank or was rewritten -- the backup is PRESERVED, nothing is
    overwritten, and the run fails loudly (rc 1): a corrupted source must never
    destroy the last good copy.
  * ATOMIC. The copy lands in a temp file beside the target, is verified to be a
    byte-prefix of the source (which may grow mid-copy) and no shorter than the
    backup it replaces, and only then takes its place via os.replace -- a crash
    mid-copy leaves the previous backup intact.
  * Skips a destination that already matches (idempotent, cheap to re-run).
  * Never raises into cron: rc 0 = ok, rc 1 = at least one file failed.
  * No `--dry-run` default, deliberately: unlike the retention scripts this one
    only ever ADDS or EXTENDS files, so the dangerous direction does not exist
    and a default dry-run would just mean "no backup happened".

USAGE
    PYTHONPATH=. python scripts/backup_evidence.py      # every present file
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "data_store" / "evidence"
DST_DIR = ROOT / "data_store" / "backups" / "evidence"
PATTERNS = ("signal_evidence_*.jsonl", "evidence_failures_*.jsonl")
_CHUNK = 1 << 20


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_prefix(prefix: Path, whole: Path) -> bool:
    """True iff the bytes of `prefix` are exactly the first bytes of `whole`."""
    n = prefix.stat().st_size
    if n > whole.stat().st_size:
        return False
    with open(prefix, "rb") as a, open(whole, "rb") as b:
        remaining = n
        while remaining:
            k = min(_CHUNK, remaining)
            if a.read(k) != b.read(k):
                return False
            remaining -= k
    return True


def backup_once(src_dir: Path = SRC_DIR, dst_dir: Path = DST_DIR) -> Tuple[int, int, List[str]]:
    """Copy or extend every evidence file. Returns (copied, skipped, failures)."""
    copied = skipped = 0
    failures: List[str] = []
    if not src_dir.is_dir():
        return (0, 0, [])
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted({p for pat in PATTERNS for p in src_dir.glob(pat)}):
        dst = dst_dir / src.name
        tmp = dst.with_name(dst.name + f".tmp-{os.getpid()}")
        try:
            if dst.exists():
                if _sha256(dst) == _sha256(src):
                    skipped += 1
                    continue
                if not _is_prefix(dst, src):
                    failures.append(
                        f"{src.name}: REFUSED -- the existing backup is not a prefix of "
                        f"the source (append-only violated: the source shrank or was "
                        f"rewritten). The backup is PRESERVED; investigate the source.")
                    continue
            shutil.copy2(src, tmp)
            # The source may have grown during the copy: what we copied must still
            # be a prefix of it, and no shorter than the backup it would replace.
            if not _is_prefix(tmp, src) or (
                    dst.exists() and tmp.stat().st_size < dst.stat().st_size):
                failures.append(f"{src.name}: copy verification failed -- "
                                f"the backup is left as it was")
                continue
            os.replace(tmp, dst)
            copied += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{src.name}: {exc}")
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
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
