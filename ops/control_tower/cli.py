"""ops/control_tower/cli.py -- Control Tower Phase 1c operator ACK CLI.

  ct list        -> OPEN + ACKNOWLEDGED findings
  ct ack <id>    -> ACKNOWLEDGED (acked_at=now)   [suppressed from the health score]
  ct unack <id>  -> OPEN (clears the ack)

Deterministic, auditable, single-operator. No Telegram-reply ACK.
Run: ops/control_tower/cli.py list|ack|unack [id]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import db_connect  # noqa: E402
from core.time_authority import now_ist  # noqa: E402
from ops.control_tower import db as ctdb  # noqa: E402

_DEFAULT_DB = _ROOT / "data_store" / "trading_system.db"


def _print_list(rows) -> None:
    if not rows:
        print("No OPEN / ACKNOWLEDGED findings.")
        return
    print(f"{'id':>4}  {'severity':<8} {'status':<12} {'category':<10} "
          f"{'resource':<28} first_seen")
    for r in rows:
        print(f"{r[0]:>4}  {r[1]:<8} {r[7]:<12} {r[2]:<10} {str(r[3])[:28]:<28} "
              f"{r[5]}")
        print(f"        {r[4]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ct", description="Control Tower ACK CLI")
    ap.add_argument("--db", default=str(_DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("ack"); a.add_argument("id", type=int)
    u = sub.add_parser("unack"); u.add_argument("id", type=int)
    args = ap.parse_args(argv)

    conn = db_connect.connect(Path(args.db), attach=False)
    try:
        if args.cmd == "list":
            _print_list(ctdb.get_findings(conn, ("OPEN", "ACKNOWLEDGED")))
        elif args.cmd == "ack":
            n = ctdb.ack_finding(conn, args.id, now_ist().isoformat())
            print(f"acked #{args.id}" if n else f"#{args.id} not ackable (missing/resolved)")
        elif args.cmd == "unack":
            n = ctdb.unack_finding(conn, args.id)
            print(f"unacked #{args.id}" if n else f"#{args.id} was not ACKNOWLEDGED")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
