"""One-shot VM investigation: LIQUIDPLUS, EOD coverage, PnL trust."""
import os, sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import get_db_connection

conn = get_db_connection(readonly=True)   # live DB, READ-ONLY via the one guard

print("=" * 70)
print("1. LIQUIDPLUS POSITIONS")
print("=" * 70)
rows = conn.execute(
    "SELECT trade_id, symbol, status, exit_reason, entry_actual_price, "
    "exit_price, net_pnl, created_at, updated_at "
    "FROM trades WHERE symbol LIKE '%LIQUID%' ORDER BY created_at DESC LIMIT 10"
).fetchall()
if rows:
    for r in rows:
        print(dict(r))
else:
    print("No LIQUIDPLUS trades found. Trades after 14:30:")
    rows2 = conn.execute(
        "SELECT trade_id, symbol, status, exit_reason, created_at "
        "FROM trades WHERE created_at >= '2026-06-08T14:30' ORDER BY created_at"
    ).fetchall()
    for r in rows2:
        print(dict(r))

print()
print("=" * 70)
print("2. EOD SQUAREOFF COVERAGE")
print("=" * 70)
open_after = conn.execute(
    "SELECT trade_id, symbol, status, created_at FROM trades WHERE status='OPEN'"
).fetchall()
print(f"Still OPEN: {len(open_after)}")
for r in open_after:
    print(f"  {dict(r)}")

eod = conn.execute(
    "SELECT trade_id, symbol, exit_reason, exit_price, net_pnl, updated_at "
    "FROM trades WHERE exit_reason='EOD_SQUAREOFF' ORDER BY updated_at DESC"
).fetchall()
print(f"\nEOD_SQUAREOFF closed: {len(eod)}")
for r in eod:
    print(f"  {dict(r)}")

cb = conn.execute(
    "SELECT trade_id, symbol, exit_reason, exit_price, net_pnl, updated_at "
    "FROM trades WHERE exit_reason LIKE '%CIRCUIT%' OR exit_reason LIKE '%FORCE%' "
    "ORDER BY updated_at DESC"
).fetchall()
print(f"\nCircuit breaker / force closed: {len(cb)}")
for r in cb:
    print(f"  {dict(r)}")

all_closed = conn.execute(
    "SELECT exit_reason, COUNT(*) as cnt FROM trades "
    "WHERE status='CLOSED' AND date(created_at)=date('now','localtime') "
    "GROUP BY exit_reason ORDER BY cnt DESC"
).fetchall()
print(f"\nToday closed by reason:")
for r in all_closed:
    print(f"  {r['exit_reason']}: {r['cnt']}")

print()
print("=" * 70)
print("3. P&L TRUSTABILITY")
print("=" * 70)

# 3a: missed fills
missed = conn.execute(
    "SELECT COUNT(*) as cnt FROM trades WHERE status='CLOSED' "
    "AND (exit_price IS NULL OR exit_price=0 OR net_pnl IS NULL OR net_pnl=0) "
    "AND date(created_at)=date('now','localtime')"
).fetchone()
print(f"3a. Missed fills (exit_price/pnl=0 or NULL): {missed['cnt']}")
if missed['cnt'] > 0:
    detail = conn.execute(
        "SELECT trade_id, symbol, exit_price, net_pnl, exit_reason FROM trades "
        "WHERE status='CLOSED' AND (exit_price IS NULL OR exit_price=0 OR net_pnl IS NULL OR net_pnl=0) "
        "AND date(created_at)=date('now','localtime')"
    ).fetchall()
    for r in detail:
        print(f"  {dict(r)}")

# 3b: emergency exits
emergency = conn.execute(
    "SELECT COUNT(*) as cnt FROM trades WHERE exit_reason='EMERGENCY_EXIT'"
).fetchone()
print(f"\n3b. Emergency exit trades: {emergency['cnt']}")
if emergency['cnt'] > 0:
    detail = conn.execute(
        "SELECT trade_id, symbol, exit_price, net_pnl FROM trades WHERE exit_reason='EMERGENCY_EXIT'"
    ).fetchall()
    for r in detail:
        print(f"  {dict(r)}")

# 3c: PnL cross-check
fm_release = conn.execute(
    "SELECT SUM(pnl_delta) as total_pnl FROM fm_ledger "
    "WHERE entry_type='RELEASE_USED' AND date(ts)=date('now','localtime')"
).fetchone()
trades_pnl = conn.execute(
    "SELECT SUM(net_pnl) as total FROM trades "
    "WHERE status='CLOSED' AND date(created_at)=date('now','localtime')"
).fetchone()
cap = conn.execute(
    "SELECT cash_floor, realized_pnl_today, margin_used, margin_reserved, "
    "charges_today, updated_at FROM capital_snapshot WHERE id=1"
).fetchone()

print(f"\n3c. PnL cross-check:")
print(f"  FM ledger RELEASE_USED pnl_delta sum: {fm_release['total_pnl']}")
print(f"  Trades table net_pnl sum:             {trades_pnl['total']}")
if cap:
    print(f"  Capital snapshot realized_pnl_today:  {cap['realized_pnl_today']}")
    print(f"  Capital snapshot cash_floor:          {cap['cash_floor']}")
    print(f"  Capital snapshot margin_reserved:     {cap['margin_reserved']}")
    print(f"  Capital snapshot margin_used:         {cap['margin_used']}")
    print(f"  Capital snapshot charges_today:       {cap['charges_today']}")
    print(f"  Capital snapshot updated_at:          {cap['updated_at']}")
    avail = cap['cash_floor'] + min(0, cap['realized_pnl_today']) - cap['margin_reserved'] - cap['margin_used']
    print(f"  Derived available:                    {avail:.2f}")
else:
    print("  Capital snapshot: NOT FOUND")

# variance
fm_val = fm_release['total_pnl'] or 0
tr_val = trades_pnl['total'] or 0
cap_val = cap['realized_pnl_today'] if cap else 0
cap_val = cap_val or 0
print(f"\n  Variance (fm_ledger vs trades):  {abs(fm_val - tr_val):.2f}")
print(f"  Variance (fm_ledger vs capital): {abs(fm_val - cap_val):.2f}")
print(f"  Variance (trades vs capital):    {abs(tr_val - cap_val):.2f}")

# Also check cancelled trades from orphan cleanup
cancelled = conn.execute(
    "SELECT COUNT(*) as cnt FROM trades WHERE status='CANCELLED' "
    "AND date(created_at)=date('now','localtime')"
).fetchone()
print(f"\n  Cancelled trades today: {cancelled['cnt']}")

conn.close()
