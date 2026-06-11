"""CT145: Rapid Crash Cycle 3x — inject signals, SIGKILL, restart, repeat."""
from __future__ import annotations
import json, os, subprocess, sys, time, urllib.request, sqlite3

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv
load_dotenv()

DB = os.path.expanduser("~/systems/trading-system/data_store/trading_system.db")
PYTHON = sys.executable
SECRET = os.environ.get("WEBHOOK_SECRET", "")

def webhook_url(scanner="gap_go_long"):
    url = f"http://localhost:5000/webhook/{scanner}"
    if SECRET:
        url += f"?token={SECRET}"
    return url

def inject_signal(symbol):
    payload = json.dumps({
        "stocks": symbol,
        "trigger_prices": "100",
        "triggered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scan_name": "gap_go_long",
        "scan_url": "https://chartink.com/screener/gap_go_long",
    }).encode()
    try:
        req = urllib.request.Request(webhook_url(), data=payload, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.getcode()
    except Exception as e:
        return str(e)[:80]

def health():
    try:
        d = json.loads(urllib.request.urlopen("http://localhost:5000/health", timeout=5).read())
        return d.get("status", "unknown")
    except Exception:
        return "dead"

def sigkill():
    pids = subprocess.run(["pgrep", "-f", "main.py"], capture_output=True, text=True).stdout.strip()
    if pids:
        subprocess.run(["sudo", "kill", "-9"] + pids.split(), capture_output=True)
        return True
    return False

def start_system(wait=20):
    subprocess.run(["sudo", "systemctl", "start", "trading-system"], check=True)
    time.sleep(wait)

def db_query(sql):
    conn = sqlite3.connect(DB)
    result = conn.execute(sql).fetchone()[0]
    conn.close()
    return result

print("CT145: Rapid Crash Cycle 3x")
print("=" * 50)

pre_sigs = db_query("SELECT COUNT(*) FROM signals")
pre_ledger = db_query("SELECT COUNT(*) FROM fm_ledger")
print(f"Pre-test: signals={pre_sigs}, ledger={pre_ledger}")

for cycle in range(1, 4):
    print(f"\n--- CYCLE {cycle} ---")

    h = health()
    print(f"  Health: {h}")
    if h != "ok":
        print("  Starting system...")
        start_system()

    count = 1 if cycle == 3 else 2
    for i in range(1, count + 1):
        sym = f"CYC{cycle}SIG{i}"
        code = inject_signal(sym)
        print(f"  Injected {sym}: {code}")

    if cycle == 3:
        print("  Letting cycle 3 process normally (10s)...")
        time.sleep(10)
    else:
        print("  Wait 10s then SIGKILL...")
        time.sleep(10)
        killed = sigkill()
        print(f"  SIGKILL: {'done' if killed else 'no process found'}")
        time.sleep(2)
        print("  Restarting...")
        start_system()

print("\n=== POST-TEST VERIFICATION ===")
post_sigs = db_query("SELECT COUNT(*) FROM signals")
post_ledger = db_query("SELECT COUNT(*) FROM fm_ledger")
print(f"Post-test: signals={post_sigs}, ledger={post_ledger}")
print(f"New signals: {post_sigs - pre_sigs}")

dupe_sigs = db_query("SELECT COUNT(*) FROM (SELECT signal_id FROM signals GROUP BY signal_id HAVING COUNT(*)>1)")
print(f"Duplicate signal_ids: {dupe_sigs}")

integrity = db_query("PRAGMA quick_check")
print(f"DB integrity: {integrity}")

h = health()
print(f"Health: {h}")

startups = db_query("SELECT COUNT(*) FROM system_events WHERE event_type='STARTUP' AND timestamp > datetime('now','-10 minutes','localtime')")
print(f"Startups in last 10 min: {startups}")

print("\nCT145 COMPLETE")
