"""CT143: Afternoon signal injection (13:00 IST window)."""
from __future__ import annotations
import json, os, sys, time, urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv
load_dotenv()

SECRET = os.environ.get("WEBHOOK_SECRET", "")
URL = "http://localhost:5000/webhook/chartink"

SIGNALS = [
    {
        "scanner_name": "CT143_afternoon",
        "stocks": "VOLTAS(524113),PERSISTENT(533179)",
        "triggered_at": "",  # filled dynamically
        "scan_url": "https://chartink.com/screener/ct143-afternoon",
    },
]


def inject(payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{URL}?token={SECRET}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return {"status": resp.status, "body": resp.read().decode()[:200]}
    except Exception as e:
        return {"error": str(e)}


from datetime import datetime
from zoneinfo import ZoneInfo
ist = ZoneInfo("Asia/Kolkata")
now_str = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

print(f"CT143 Afternoon Injection — {now_str} IST")
print("=" * 50)

for sig in SIGNALS:
    sig["triggered_at"] = now_str
    print(f"\nInjecting: {sig['stocks']}")
    result = inject(sig)
    print(f"  Result: {result}")
    time.sleep(1)

# Check health after injection
time.sleep(3)
try:
    h = json.loads(urllib.request.urlopen("http://localhost:5000/health", timeout=5).read())
    print(f"\nHealth: {h['status']}, queue: {h['queue_depth']}")
except Exception as e:
    print(f"\nHealth check failed: {e}")

print("\nCT143 afternoon injection complete.")
