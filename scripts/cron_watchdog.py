#!/usr/bin/env python3
"""
scripts/cron_watchdog.py — Tier-2 watch-the-watcher (Phase 3).

Runs under SYSTEMD (deploy/systemd/cron-watchdog.timer ~19:30 IST), NOT cron — so
it cannot fail the same way as the cron jobs it watches (dead cron daemon / broken
shared env). Asserts that BOTH cron_officer_eod AND check_cron_drift heartbeated
today; if either is missing on a market day, it writes a CRITICAL sentinel which
the alert-watcher emails (a path independent of cron).

Exit 0 = both ran (or non-trading day); 1 = a watcher is down (sentinel written).
"""
from __future__ import annotations

import sys
from datetime import datetime, time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from alerts.critical import write_critical_sentinel
from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist
from core.account_registry import primary_account_tag

_log = get_logger("cron_watchdog")
_WATCHED = ("cron_officer_eod", "check_cron_drift")


def main() -> int:
    now = now_ist()
    today = now.date()
    if today.weekday() >= 5:                      # market-day jobs; not expected Sat/Sun
        print("cron_watchdog: weekend — watched jobs not expected; ok")
        return 0

    db = _ROOT / "data_store" / "trading_system.db"
    if not db.exists():
        _log.error("cron_watchdog.db_missing", extra={"db": str(db)})
        return 1
    store = StateStore(db)
    try:
        midnight = datetime.combine(today, time.min).isoformat()
        seen = {h["job_name"] for h in store.get_cron_heartbeats_since(midnight)}
    finally:
        store.close()

    missing = [j for j in _WATCHED if j not in seen]
    if not missing:
        print(f"cron_watchdog: ok — {', '.join(_WATCHED)} both heartbeated today")
        return 0

    # Cron-independent alert: a CRITICAL sentinel the (systemd) alert-watcher emails.
    try:
        write_critical_sentinel(
            title=f"[{primary_account_tag()}] CRON WATCHDOG — watcher down ({', '.join(missing)})",
            body=("The systemd cron-watchdog (independent of cron) found NO heartbeat "
                  f"today for: {', '.join(missing)}. The cron daemon or shared env may be "
                  "down — the Cron Officer / drift-check cannot self-report this. Investigate "
                  "crond, /bin/bash, and .env on the VM."),
            source_module="cron_watchdog",
            sentinel_dir=_ROOT / "data_store",
            context={"severity": "CRITICAL", "missing": missing},
        )
    except Exception as exc:  # noqa: BLE001
        _log.error("cron_watchdog.sentinel_failed", extra={"error": str(exc)})
    print(f"cron_watchdog: ALERT — missing {missing}; CRITICAL sentinel written")
    return 1


if __name__ == "__main__":
    sys.exit(main())
