#!/usr/bin/env python3
"""Quick VM state check/reset script."""
import sqlite3
import os
import sys
from pathlib import Path

# Bug 8 (FIX-180): the hardcoded "~/trading-system/data_store/..." path was
# wrong on the VM (active deploy is ~/systems/trading-system) and CWD-fragile.
# Anchor to the project root (this file's parent.parent), with a DB_PATH env
# override for non-standard layouts.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
db_path = os.environ.get(
    "DB_PATH", str(PROJECT_ROOT / "data_store" / "trading_system.db")
)
if not os.path.exists(db_path):
    print(f"DB not found: {db_path}")
    sys.exit(1)

c = sqlite3.connect(db_path)
c.row_factory = sqlite3.Row

# List tables
tables = [t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"Tables ({len(tables)}):", tables[:10], "..." if len(tables) > 10 else "")

# Check kill_switch_state
if "kill_switch_state" in tables:
    row = c.execute("SELECT * FROM kill_switch_state WHERE id=1").fetchone()
    if row:
        print(f"Kill switch: state={row['state']}, reason={row['reason']}")

        # Reset if requested
        if len(sys.argv) > 1 and sys.argv[1] == "--reset":
            c.execute("""
                UPDATE kill_switch_state
                SET state='INACTIVE', reason='manual reset via script', triggered_by='operator'
                WHERE id=1
            """)
            c.commit()
            print("Kill switch RESET to INACTIVE")
    else:
        print("Kill switch: no row (will default to INACTIVE)")
else:
    print("Kill switch: table missing (will default to INACTIVE)")

# Check recent signals if --signals passed
if len(sys.argv) > 1 and sys.argv[1] == "--signals":
    print("\nRecent signals:")
    rows = c.execute("""
        SELECT signal_id, symbol, status, received_at
        FROM signals ORDER BY received_at DESC LIMIT 10
    """).fetchall()
    for r in rows:
        print(f"  {r['symbol']:12} {r['status']:12} {r['received_at']}")

# Check specific symbol if --symbol=XXX passed
for arg in sys.argv[1:]:
    if arg.startswith("--symbol="):
        sym = arg.split("=")[1]
        print(f"\nSignals for {sym}:")
        rows = c.execute("""
            SELECT signal_id, symbol, status, triggered_at, received_at
            FROM signals WHERE symbol=? ORDER BY received_at DESC LIMIT 5
        """, (sym,)).fetchall()
        for r in rows:
            trig = r['triggered_at'][:19] if r['triggered_at'] else 'N/A'
            recv = r['received_at'][:19] if r['received_at'] else 'N/A'
            print(f"  {r['status']:25} trig={trig} recv={recv}")

# Check recent trades if --trades passed
if len(sys.argv) > 1 and sys.argv[1] == "--trades":
    print("\nRecent trades (net_pnl):")
    rows = c.execute("""
        SELECT symbol, net_pnl, exit_time
        FROM trades WHERE net_pnl IS NOT NULL
        ORDER BY exit_time DESC LIMIT 10
    """).fetchall()
    for r in rows:
        pnl = r[1] if r[1] else 0
        exit_t = r[2][:19] if r[2] else 'N/A'
        sign = '+' if pnl >= 0 else ''
        print(f"  {r[0]:15} {sign}{pnl:>8.2f}  exit={exit_t}")

# Check open positions if --positions passed
if len(sys.argv) > 1 and sys.argv[1] == "--positions":
    print("\nOpen positions (status != CLOSED):")
    rows = c.execute("""
        SELECT trade_id, symbol, status, entry_time
        FROM trades WHERE status NOT IN ('CLOSED', 'CANCELLED')
        ORDER BY entry_time DESC LIMIT 15
    """).fetchall()
    if rows:
        for r in rows:
            entry_t = r[3][:19] if r[3] else 'N/A'
            print(f"  {r[1]:15} status={r[2]:15} entry={entry_t}")
    else:
        print("  (no open positions)")
    print(f"\nTotal open: {len(rows)}")

# Cleanup stale PENDING_FILL trades if --cleanup-pending passed
if len(sys.argv) > 1 and sys.argv[1] == "--cleanup-pending":
    print("\nCleaning up PENDING_FILL trades...")
    rows = c.execute("""
        SELECT trade_id, symbol, status FROM trades
        WHERE status = 'PENDING_FILL'
    """).fetchall()
    if rows:
        for r in rows:
            print(f"  Marking {r[1]} (trade_id={r[0][:12]}...) as CANCELLED")
        c.execute("""
            UPDATE trades SET status = 'CANCELLED',
            exit_reason = 'cleanup_stale_pending'
            WHERE status = 'PENDING_FILL'
        """)
        c.commit()
        print(f"  {len(rows)} trades marked CANCELLED")
    else:
        print("  No PENDING_FILL trades found")

# Cleanup orphaned orders if --cleanup-orders passed
if len(sys.argv) > 1 and sys.argv[1] == "--cleanup-orders":
    print("\nCleaning up orphaned PENDING orders...")
    rows = c.execute("""
        SELECT o.order_id, t.symbol, o.order_type, t.status as trade_status
        FROM orders o
        LEFT JOIN trades t ON o.trade_id = t.trade_id
        WHERE o.status = 'PENDING'
          AND (t.status IN ('CANCELLED', 'CLOSED', 'CLOSED_MANUAL') OR t.trade_id IS NULL)
    """).fetchall()
    if rows:
        for r in rows:
            sym = r[1] if r[1] else 'N/A'
            otype = r[2] if r[2] else 'N/A'
            tstatus = r[3] if r[3] else 'orphan'
            print(f"  {sym:15} {otype:10} trade_status={tstatus}")
        c.execute("""
            UPDATE orders SET status = 'CANCELLED'
            WHERE status = 'PENDING'
              AND trade_id IN (
                SELECT trade_id FROM trades
                WHERE status IN ('CANCELLED', 'CLOSED', 'CLOSED_MANUAL')
              )
        """)
        c.commit()
        print(f"  {len(rows)} orders marked CANCELLED")
    else:
        print("  No orphaned PENDING orders found")

# Quick health check if --health passed
if len(sys.argv) > 1 and sys.argv[1] == "--health":
    print("\n=== HEALTH CHECK ===")
    # Kill switch
    ks = c.execute("SELECT state FROM kill_switch_state WHERE id=1").fetchone()
    print(f"Kill switch: {ks[0] if ks else 'N/A'}")
    # Open positions
    open_count = c.execute("SELECT COUNT(*) FROM trades WHERE status NOT IN ('CLOSED', 'CANCELLED')").fetchone()[0]
    print(f"Open positions: {open_count}")
    # Recent signals
    recent_sig = c.execute("SELECT COUNT(*) FROM signals WHERE received_at > datetime('now', '-10 minutes')").fetchone()[0]
    print(f"Signals (last 10 min): {recent_sig}")
    # Pending orders
    pending_orders = c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('PENDING', 'OPEN', 'SUBMITTED')").fetchone()[0]
    print(f"Pending orders: {pending_orders}")

# Check order status breakdown if --orders passed
if len(sys.argv) > 1 and sys.argv[1] == "--orders":
    print("\nOrder status breakdown:")
    rows = c.execute("""
        SELECT order_type, status, COUNT(*) as cnt
        FROM orders GROUP BY order_type, status
        ORDER BY cnt DESC
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:10} {r[1]:15} count={r[2]}")

c.close()
