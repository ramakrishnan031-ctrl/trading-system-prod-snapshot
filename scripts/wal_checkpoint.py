"""
scripts/wal_checkpoint.py -- FIX-131 Item 23: WAL checkpoint cron script

Runs a PASSIVE WAL checkpoint on the trading DB. Intended to be called at
16:00 IST (after market close) via cron to prevent unbounded WAL growth.

Usage (add to crontab on VM):
  0 10 * * 1-5 /home/ubuntu/systems/venv/bin/python \
      /home/ubuntu/systems/trading-system/scripts/wal_checkpoint.py \
      >> /home/ubuntu/systems/trading-system/logs/wal_checkpoint.log 2>&1

(0 10 UTC = 15:30 IST + 30 min = 16:00 IST; adjusted for UTC+5:30)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_store import StateStore
from core.time_authority import now_ist


def main() -> None:
    db_path = Path(__file__).parent.parent / "data_store" / "trading_system.db"
    if not db_path.exists():
        print(f"{now_ist().isoformat()} WAL checkpoint: DB not found at {db_path} -- skipping")
        sys.exit(0)

    try:
        store = StateStore(db_path)
        stats = store.checkpoint_wal()
        store.close()
        print(
            f"{now_ist().isoformat()} WAL checkpoint(PASSIVE): "
            f"busy={stats['busy']} log={stats['log']} checkpointed={stats['checkpointed']}"
        )
        # FIX-145: Record heartbeat for cron drift monitoring
        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat("wal_checkpoint")
        except Exception:
            pass
    except Exception as exc:
        print(f"{now_ist().isoformat()} WAL checkpoint FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


def _cron_main() -> int:
    """Cron entry: S1 holiday-skip, then the real checkpoint.

    S1 (2026-07-17): market_day_only was decorative — nothing enforced it at the
    cron entry, so this ran on every NSE holiday. skip_if_non_trading_day FAILS
    OPEN (weekday fallback on any calendar error) so a trading day is never
    skipped. Guard here, not in main(), so a manual checkpoint still works.

    main() returns None (it signals failure by raising), so the exit code is 0
    unless it raises — preserved exactly as before.
    """
    from utils.cron_heartbeat import skip_if_non_trading_day

    if skip_if_non_trading_day("wal_checkpoint"):
        return 0
    main()
    return 0


if __name__ == "__main__":
    sys.exit(_cron_main())
