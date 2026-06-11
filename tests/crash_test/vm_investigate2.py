"""Supplementary investigation: trade status breakdown + FAILED detail + fm_ledger detail."""
import sqlite3, os

DB = os.path.expanduser("~/systems/trading-system/data_store/trading_system.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=== All trades today by status ===")
rows = conn.execute(
    "SELECT status, COUNT(*) as cnt FROM trades "
    "WHERE date(created_at)='2026-06-08' GROUP BY status ORDER BY cnt DESC"
).fetchall()
for r in rows:
    print(f"  {r['status']}: {r['cnt']}")

print("\n=== FAILED trades detail ===")
rows = conn.execute(
    "SELECT trade_id, symbol, status, created_at, updated_at FROM trades "
    "WHERE status='FAILED' AND date(created_at)='2026-06-08'"
).fetchall()
for r in rows:
    print(f"  {dict(r)}")

print("\n=== FM ledger RELEASE_USED entries today ===")
rows = conn.execute(
    "SELECT trade_id, pnl_delta, costs, reason, ts FROM fm_ledger "
    "WHERE entry_type='RELEASE_USED' AND date(ts)='2026-06-08' ORDER BY ts"
).fetchall()
total = 0
for r in rows:
    total += r['pnl_delta']
    tid = (r['trade_id'] or 'N/A')[:16]
    print(f"  trade={tid:16s} pnl={r['pnl_delta']:+.2f} costs={r['costs']:.2f} reason={r['reason']} ts={r['ts']}")
print(f"  TOTAL: {total:.2f}")

print("\n=== Trades table net_pnl detail ===")
rows = conn.execute(
    "SELECT trade_id, symbol, net_pnl, gross_pnl, charges, exit_reason FROM trades "
    "WHERE status='CLOSED' AND date(created_at)='2026-06-08' ORDER BY updated_at"
).fetchall()
total = 0
for r in rows:
    total += (r['net_pnl'] or 0)
    print(f"  {r['symbol']:15s} pnl={r['net_pnl']:+8.2f} gross={r['gross_pnl'] or 0:+8.2f} charges={r['charges'] or 0:.2f} exit={r['exit_reason']}")
print(f"  TOTAL: {total:.2f}")

print("\n=== Capital snapshot ===")
rows = conn.execute("SELECT * FROM capital_snapshot").fetchall()
if rows:
    for r in rows:
        print(f"  {dict(r)}")
else:
    print("  (empty — paper mode may not persist)")

print("\n=== Session table ===")
try:
    rows = conn.execute(
        "SELECT * FROM system_events WHERE date(ts)='2026-06-08' ORDER BY ts DESC LIMIT 10"
    ).fetchall()
    for r in rows:
        print(f"  {dict(r)}")
except Exception as e:
    print(f"  system_events error: {e}")

conn.close()
