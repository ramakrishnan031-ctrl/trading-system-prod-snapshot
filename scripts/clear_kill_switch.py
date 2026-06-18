#!/usr/bin/env python3
"""
scripts/clear_kill_switch.py -- Trading System v2  FIX-188b

Clear (resume) the kill switch in the DB WITHOUT starting the trading loop or
acquiring the instance lock (port 5001). This is the systemd-friendly resume:
a standalone `main.py --resume` competes with trading-system.service for the
instance lock (the 18-Jun collision), so use this to clear the kill, then let
systemd run the service. `deploy/resume.sh` wraps stop -> clear -> start.

Usage:
    python scripts/clear_kill_switch.py [--db PATH] [--dry-run] [--force]
      --dry-run : report current state, do NOT clear (read-only intent)
      --force   : also clear HARD_KILL (emergency; confirm root cause first)

Exit codes:
    0  cleared, already INACTIVE, or dry-run
    1  refused (HARD_KILL without --force) or error
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from capital.kill_switch import KillSwitch
from core.events import EventBus
from core.logger import get_logger
from core.state_store import StateStore

_DEFAULT_DB = _ROOT / "data_store" / "trading_system.db"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Clear (resume) the kill switch without starting the trading loop."
    )
    p.add_argument("--db", type=Path, default=_DEFAULT_DB)
    p.add_argument("--dry-run", action="store_true", help="Report state; do not clear.")
    p.add_argument("--force", action="store_true", help="Also clear HARD_KILL (emergency).")
    args = p.parse_args(argv)
    log = get_logger("clear_kill_switch")

    if not args.db.exists():
        print(f"Database not found: {args.db}")
        return 1

    store = StateStore(args.db)
    try:
        ks = KillSwitch(store, EventBus(), log)
        status = ks.status()
        state, reason = status["state"], status.get("reason")

        if not ks.is_active("any"):
            print(f"Kill switch already INACTIVE (reason={reason!r}) -- nothing to do.")
            return 0

        if state == "HARD_KILL" and not args.force:
            print(f"HARD_KILL active (reason={reason!r}). Refusing without --force.")
            print("HARD_KILL is an emergency halt -- confirm the root cause is handled, then re-run with --force.")
            return 1

        if args.dry_run:
            print(f"[DRY-RUN] would clear {state} (reason={reason!r}) -> INACTIVE")
            return 0

        ks.resume(
            reason=f"cleared via scripts/clear_kill_switch.py (was {state})",
            resumed_by="operator",
        )
        print(f"Kill switch cleared: {state} -> INACTIVE.")
        print("Now run: sudo systemctl start trading-system.service  (or use deploy/resume.sh)")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
