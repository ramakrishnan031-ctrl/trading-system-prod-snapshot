#!/usr/bin/env python3
"""Quick VM state check/reset script."""
import sqlite3
import os
import sys

db_path = os.path.expanduser("~/trading-system/data_store/trading_system.db")
if not os.path.exists(db_path):
    print(f"DB not found: {db_path}")
    exit(1)

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

c.close()
