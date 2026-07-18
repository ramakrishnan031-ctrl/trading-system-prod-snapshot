"""CT140: EOD Squareoff Failure — block REST at 15:14, watch squareoff fail at 15:17."""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys, time, urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import get_db_connection
from dotenv import load_dotenv
load_dotenv()

PYTHON = sys.executable

def health():
    try:
        return json.loads(urllib.request.urlopen("http://localhost:5000/health", timeout=5).read())
    except:
        return {"status": "dead"}

def open_trade_count():
    conn = get_db_connection(readonly=True)
    count = conn.execute("SELECT COUNT(*) FROM trades WHERE status IN ('OPEN','PARTIAL')").fetchone()[0]
    conn.close()
    return count

def get_open_trades():
    conn = get_db_connection(readonly=True)
    conn.row_factory = sqlite3.Row
    trades = conn.execute("SELECT trade_id, symbol, status FROM trades WHERE status IN ('OPEN','PARTIAL')").fetchall()
    conn.close()
    return [dict(t) for t in trades]

print("CT140: EOD Squareoff Failure — All Positions")
print("=" * 55)

# Step 1: Verify we have 3+ open positions
pre_trades = get_open_trades()
print(f"[1] Open trades before: {len(pre_trades)}")
for t in pre_trades:
    print(f"    {t['symbol']:12s} | {t['status']}")

if len(pre_trades) < 3:
    print("SKIP: Need 3+ open positions. Inject signals first.")
    sys.exit(0)

# Step 2: Wait for 15:14:00 IST
from datetime import datetime
from zoneinfo import ZoneInfo
ist = ZoneInfo("Asia/Kolkata")

target = datetime.now(ist).replace(hour=15, minute=14, second=0, microsecond=0)
now = datetime.now(ist)
wait = (target - now).total_seconds()

if wait > 0:
    print(f"\n[2] Waiting {wait:.0f}s until 15:14:00 IST...")
    time.sleep(wait)
elif wait < -180:
    print(f"\n[2] Past 15:17 already ({now.strftime('%H:%M:%S')}). Too late for this test.")
    sys.exit(0)

print(f"[2] Time: {datetime.now(ist).strftime('%H:%M:%S')} IST — blocking REST")

# Step 3: Block Zerodha REST
print("[3] Blocking Zerodha REST (120s)...")
block_result = subprocess.run(
    ["sudo", PYTHON, "tests/crash_test/network_controller.py",
     "--preset", "block_zerodha_rest", "--duration", "120"],
    capture_output=True, text=True, cwd=os.path.expanduser("~/systems/trading-system"),
    timeout=10,
)
print(f"    Block result: exit={block_result.returncode}")
if block_result.stdout.strip():
    print(f"    {block_result.stdout.strip()[:200]}")

# Step 4: Watch for squareoff at 15:17
print("\n[4] Waiting for EOD squareoff (15:17)...")
squareoff_target = datetime.now(ist).replace(hour=15, minute=17, second=30)
remaining = (squareoff_target - datetime.now(ist)).total_seconds()
if remaining > 0:
    time.sleep(min(remaining, 240))

# Step 5: Check what happened
print(f"\n[5] Time: {datetime.now(ist).strftime('%H:%M:%S')} IST — checking results")

post_trades = get_open_trades()
print(f"    Open trades after squareoff attempt: {len(post_trades)}")
for t in post_trades:
    print(f"    {t['symbol']:12s} | {t['status']}")

h = health()
print(f"    System health: {h.get('status', 'unknown')}")
print(f"    Kill switch: {'ACTIVE' if h.get('kill_switch_active') else 'INACTIVE'}")

# Step 6: Unblock
print("\n[6] Unblocking REST...")
subprocess.run(
    ["sudo", PYTHON, "tests/crash_test/network_controller.py", "--unblock-all"],
    capture_output=True, text=True, cwd=os.path.expanduser("~/systems/trading-system"),
    timeout=10,
)

# Step 7: Check logs for squareoff failures
print("\n[7] Checking logs for squareoff results...")
log_check = subprocess.run(
    ["bash", "-c", "grep -i 'squareoff\\|eod.*exit\\|force_close\\|MARKET.*fail\\|exit.*fail' /home/ubuntu/systems/trading-system/logs/system_2026-06-10.log 2>/dev/null | tail -20 || grep -i 'squareoff\\|eod.*exit\\|force_close' /home/ubuntu/systems/trading-system/logs/trading_*.log 2>/dev/null | tail -20"],
    capture_output=True, text=True, timeout=10,
)
if log_check.stdout.strip():
    for line in log_check.stdout.strip().split("\n")[-10:]:
        print(f"    {line[:150]}")
else:
    print("    No squareoff log entries found")

# Assertions
print("\n=== ASSERTIONS ===")
crashed = h.get("status") != "ok"
positions_preserved = len(post_trades) >= len(pre_trades) - 1
print(f"  System did NOT crash: {'PASS' if not crashed else 'FAIL'}")
print(f"  Positions preserved or natural exits only: {'PASS' if positions_preserved else 'FAIL'}")

print("\nCT140 COMPLETE")
