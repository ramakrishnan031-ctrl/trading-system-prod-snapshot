"""
scripts/backfill_closure_source_w8.py — ONE-SHOT W8 backfill (written 27-Jul-2026)

⛔⛔ NOT RUN. WRITTEN FOR REVIEW. It rewrites historical rows that reports and
   studies have already read, so it is Rama's decision, not mine.
   Default is --dry-run; writing requires an explicit --commit.

WHAT IT DOES
    Populates `trades.closure_source` on the CLOSED_MANUAL rows whose own exit leg
    is recorded in `orders`. Measured 27-Jul against production: 35 of 41 rows have
    exactly ONE of their own SL/TGT/EOD legs COMPLETE (EOD 22 · SL 9 · TGT 4), and
    every CRITICAL "RMS/MANUAL CLOSE" alert ever sent was one of those.

WHY THIS IS NOT "INVENTING AN ANSWER"
    `order_manager._EXIT_REASON_TO_CLOSURE_SOURCE` deliberately OMITS MANUAL_CLOSE,
    because deriving a cause from `exit_reason` alone WOULD be inventing one. This
    script does not read `exit_reason`. It reads the ORDER that filled — the same
    evidence `orders/closure_classifier.py` uses live, and the same evidence CHECK1
    had in hand 0.43 s before it sent the wrong alert.

⛔ WHAT IT DELIBERATELY DOES NOT TOUCH — 6 rows, and this is the point
    SULA (15-Jun) · AGARIND (16-Jun)   the record is SILENT, not exonerating: they
        PREDATE exit-leg recording entirely (the first SL/TGT row in `orders` is
        17-Jun). We cannot classify them in EITHER direction.
    EVEREADY · AEROENTER · RCF          the three genuine external-close candidates.
        Their legs were cancelled cleanly and nothing of ours accounts for the
        close. Kite's trades() is same-day only, so this can never now be resolved.
    GICRE                               qty_filled=0 — a cancelled entry. There was
        never a position, so there is no closure to attribute.
    ⭐ A backfill that "cleaned" all 41 would be manufacturing answers for exactly
    the rows where the record is silent — the same failure, in the other direction.
    They stay NULL. NULL is the honest value for "we do not know"
    (docs/closure_source_contract.md); it is NOT EXTERNAL_UNATTRIBUTED, which is a
    positive claim we cannot support.

⚠️ exit_mechanism IS LEFT NULL, DELIBERATELY.
    The completing legs carry order_type LIMIT ×26 and SL ×9 — but NO canonical
    order_type -> exit_mechanism mapping exists anywhere in the codebase (the W8
    writer requires the caller to pass it; nothing does). Inventing one inside a
    backfill is precisely the second-classifier divergence W8 exists to retire.
    Agree the mapping first, then do that axis as its own change.

⏰ IT CANNOT RUN BEFORE THE v45 MIGRATION.
    Production was on schema 44 on 27-Jul; `closure_source` DID NOT EXIST. v45
    REBUILDS `trades` at the 08:15 boot, so anything written before that boot would
    be destroyed by it. This script REFUSES to run unless the column is present.

Usage:
    python scripts/backfill_closure_source_w8.py                 # dry-run (default)
    python scripts/backfill_closure_source_w8.py --commit        # writes
    python scripts/backfill_closure_source_w8.py --revert-file <path>   # undo
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data_store" / "trading_system.db"

# ⭐ The evidence predicate, stated ONCE. A row qualifies IFF exactly ONE distinct
# SL/TGT/EOD leg of ours reached COMPLETE.
#
# "Exactly one" is not defensive padding. Two different completed exit legs on one
# trade is a CONTRADICTION, and orders/closure_classifier.py resolves a
# contradiction to EXTERNAL_UNATTRIBUTED at CRITICAL — it does NOT pick the
# higher-precedence one. A backfill that silently picked one would disagree with
# the live classifier on the same evidence. Measured 27-Jul: 0 such rows. A guard
# that cannot fire on today's data still has to be there for tomorrow's.
_SELECT_CANDIDATES = """
    SELECT t.trade_id,
           t.symbol,
           DATE(t.created_at) AS d,
           t.closure_source   AS current_source,
           (SELECT GROUP_CONCAT(DISTINCT o.leg)
              FROM orders o
             WHERE o.trade_id = t.trade_id
               AND o.status = 'COMPLETE'
               AND o.leg IN ('SL', 'TGT', 'EOD')) AS legs
      FROM trades t
     WHERE t.status = 'CLOSED_MANUAL'
     ORDER BY t.created_at
"""

_LEG_TO_SOURCE = {"SL": "OWN_SL", "TGT": "OWN_TGT", "EOD": "OWN_EOD"}


def _parse_args(argv):
    """⚠️ Args are parsed BEFORE anything opens a database.

    This is not style. Several existing scripts/*.py open the LIVE store at import
    or before reading --db, which is why 'never run scripts/*.py --db <copy>' is a
    standing rule — there is no safe read-only invocation of them. This script has
    no --db at all and touches nothing until after this returns.
    """
    p = argparse.ArgumentParser(description="W8 closure_source backfill (one-shot)")
    p.add_argument("--commit", action="store_true",
                   help="actually write. Without it, this is a dry run.")
    p.add_argument("--revert-file", metavar="PATH",
                   help="undo a previous run using its receipt JSON")
    return p.parse_args(argv)


def _classify(rows):
    """Split the population by evidence. Returns (to_write, skipped, contradictions)."""
    to_write, skipped, contradictions = [], [], []
    for r in rows:
        legs = sorted({x for x in (r["legs"] or "").split(",") if x})
        if len(legs) == 1:
            to_write.append((r, legs[0]))
        elif len(legs) > 1:
            contradictions.append((r, legs))
        else:
            skipped.append(r)
    return to_write, skipped, contradictions


def _take_backup(stamp: str) -> Path:
    """A consistent copy, via SQLite's ONLINE BACKUP API — never a file copy.

    ⛔ A plain cp/scp of a live WAL database can copy a torn page and produce a
    backup that opens cleanly and restores with gaps. `.backup` takes a consistent
    snapshot across the WAL. Verified with integrity_check before it is trusted.
    """
    import sqlite3

    dest = DB_PATH.parent / "backups" / f"pre_w8_backfill_{stamp}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(DB_PATH))
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
            ok = out.execute("PRAGMA integrity_check").fetchone()[0]
            n = out.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        finally:
            out.close()
    finally:
        src.close()
    if ok != "ok":
        raise SystemExit(f"REFUSING: backup integrity_check said {ok!r}, not 'ok'.")
    if n == 0:
        # A copy that opens but reports 0 rows is a FAILED copy, not an empty DB.
        raise SystemExit("REFUSING: the backup contains 0 trades — that is a failed "
                         "copy, not an empty database.")
    return dest


def _write_receipt(stamp: str, to_write) -> Path:
    """The undo record. ⚠️ Beside the DB it describes, NOT under ROOT — a receipt
    naming rows in one database while sitting next to another is a trap, and
    pinning it to ROOT also made this script write into the repo (the exact class
    the 27-Jul test-isolation guards were built to close)."""
    path = DB_PATH.parent / f"w8_backfill_receipt_{stamp}.json"
    path.write_text(json.dumps({
        "written_at": stamp,
        "db": str(DB_PATH),
        "note": "W8 closure_source backfill; exit_mechanism deliberately NULL",
        "rows": [{"trade_id": r["trade_id"], "symbol": r["symbol"], "leg": leg,
                  "wrote": _LEG_TO_SOURCE[leg], "was": r["current_source"]}
                 for r, leg in to_write],
    }, indent=2), encoding="utf-8")
    return path


def _require_v45(store) -> None:
    """Fail LOUD if the column is absent. Never create it — a data script must not
    migrate, and MIGRATION IS main.py's alone (core/state_store guard `ed1c4b9`)."""
    cols = {r["name"] for r in store.fetch_all("PRAGMA table_info(trades)")}
    if "closure_source" not in cols:
        raise SystemExit(
            "REFUSING: trades.closure_source does not exist. Production was on "
            "schema 44 on 27-Jul; v45 adds it by REBUILDING `trades` at the 08:15 "
            "boot. Run this only AFTER that boot is confirmed clean — a write "
            "before the rebuild would be destroyed by it."
        )


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    from core.state_store import StateStore

    store = StateStore(db_path=str(DB_PATH))
    try:
        _require_v45(store)

        if args.revert_file:
            return _revert(store, Path(args.revert_file))

        rows = [dict(r) for r in store.fetch_all(_SELECT_CANDIDATES)]
        to_write, skipped, contradictions = _classify(rows)

        # ── BEFORE ────────────────────────────────────────────────────────────
        before = dict(Counter(
            (r["current_source"] or "NULL") for r in rows))
        print(f"CLOSED_MANUAL rows            : {len(rows)}")
        print(f"  closure_source BEFORE       : {before}")
        print(f"  would WRITE (1 own leg)     : {len(to_write)}  "
              f"{dict(Counter(_LEG_TO_SOURCE[l] for _r, l in to_write))}")
        print(f"  LEFT NULL (no own leg)      : {len(skipped)}")
        for r in skipped:
            print(f"      {r['d']}  {r['symbol']}")
        if contradictions:
            print(f"  ⛔ CONTRADICTIONS           : {len(contradictions)}")
            for r, legs in contradictions:
                print(f"      {r['d']}  {r['symbol']}  legs={legs}")
            raise SystemExit(
                "REFUSING: a trade has two different COMPLETE exit legs. The live "
                "classifier calls that EXTERNAL_UNATTRIBUTED at CRITICAL; a "
                "backfill must not disagree with it on the same evidence. "
                "Investigate these rows by hand."
            )

        already = [r for r, _l in to_write if r["current_source"]]
        if already:
            print(f"  ⚠️ {len(already)} already have a value — the COALESCE guard "
                  f"leaves them untouched (idempotent).")

        if not args.commit:
            print("\nDRY RUN — nothing written. Re-run with --commit to apply.")
            return 0

        # ── THE BACKUP, TAKEN BY THE SCRIPT ITSELF ────────────────────────────
        # ⭐ Structural, not procedural. "Take a backup first" written in a runbook
        # is a step that gets skipped; taken here it cannot be. If it fails, the
        # write does not happen.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = _take_backup(stamp)
        print(f"  backup taken                : {backup} "
              f"({backup.stat().st_size:,} B)")

        # ── THE WRITE ─────────────────────────────────────────────────────────
        # Through the COALESCE-guarded setter, NOT raw SQL: it can only fill a NULL,
        # so it can never clobber a value the live exit path has since written.
        # exit_mechanism is omitted -> stays NULL (see the module docstring).
        preexisting = {r["trade_id"] for r, _l in to_write if r["current_source"]}
        for r, leg in to_write:
            store.set_trade_closure_axes(r["trade_id"], _LEG_TO_SOURCE[leg])

        # ⭐⭐ THE RECEIPT IS WRITTEN IMMEDIATELY AFTER THE WRITE, BEFORE THE
        # VERIFICATION. Ordering is the whole point: if verification runs first and
        # fails, the rows are already changed and there is NO undo record -- the
        # one state this script must never be able to produce. Backup -> write ->
        # receipt -> verify means an undo path exists from the instant anything
        # changes.
        path = _write_receipt(stamp, to_write)
        print(f"  receipt (the exact undo set): {path}")

        # ── AFTER + THE VERIFICATION ──────────────────────────────────────────
        # ⭐ This is the part that makes the run evidence rather than a report of
        # itself: re-read from the DB and check the value against the EVIDENCE
        # again, not against what we intended to write.
        after_rows = [dict(r) for r in store.fetch_all(_SELECT_CANDIDATES)]
        after = dict(Counter((r["current_source"] or "NULL") for r in after_rows))
        print(f"\n  closure_source AFTER        : {after}")

        bad = []
        for r in after_rows:
            # ⚠️ A row that ALREADY carried a value before this run is the live
            # path's, not ours. The COALESCE guard deliberately left it alone, and
            # from the 28-Jul boot onward CHECK1 writes this column itself -- so
            # EXTERNAL_UNATTRIBUTED sitting on a row whose `orders` history shows an
            # own leg is a real, expected state (the live path saw the broker; this
            # script only sees history). Verifying against the evidence there would
            # report the guard WORKING as a failure.
            if r["trade_id"] in preexisting:
                continue
            legs = sorted({x for x in (r["legs"] or "").split(",") if x})
            expected = _LEG_TO_SOURCE[legs[0]] if len(legs) == 1 else None
            got = r["current_source"]
            if expected is None and got is not None:
                bad.append((r["symbol"], "should be NULL", got))
            elif expected is not None and got != expected:
                bad.append((r["symbol"], expected, got))
        if bad:
            print("  ⛔ VERIFICATION FAILED:")
            for s, exp, got in bad:
                print(f"      {s}: expected {exp}, got {got}")
            return 1

        n_null = sum(1 for r in after_rows if not r["current_source"])
        print(f"  ✅ VERIFIED: every row this run wrote matches its own `orders` "
              f"evidence; {n_null} left NULL by design.")
        print(f"  REVERSE: python scripts/backfill_closure_source_w8.py "
              f"--revert-file {path}")
        return 0
    finally:
        store.close()


def _revert(store, path: Path) -> int:
    """Undo, using the receipt — so the reverse touches EXACTLY the rows the run
    touched, and no row that already had a value before it."""
    receipt = json.loads(path.read_text(encoding="utf-8"))
    targets = [r for r in receipt["rows"] if r["was"] is None]
    print(f"reverting {len(targets)} rows written at {receipt['written_at']}")
    with store.transaction() as cur:
        for r in targets:
            cur.execute(
                "UPDATE trades SET closure_source = NULL WHERE trade_id = ? "
                "AND closure_source = ?",
                (r["trade_id"], r["wrote"]),
            )
    left = store.fetch_one(
        "SELECT COUNT(*) n FROM trades WHERE status='CLOSED_MANUAL' "
        "AND closure_source IS NOT NULL")
    print(f"  CLOSED_MANUAL rows still carrying a value: {left['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
