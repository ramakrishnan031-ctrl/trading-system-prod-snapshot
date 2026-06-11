"""
tests/crash_test/resource_monitor.py — Tool 12: Continuous resource monitoring.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import (
    ist_now_iso, ist_now, http_get, append_jsonl, RESOURCES_DIR,
)

HEALTH_URL = "http://localhost:5000/health"
PID_FILE = RESOURCES_DIR / "monitor.pid"
ALERT_LOG = RESOURCES_DIR / "alerts.jsonl"

CSV_COLUMNS = [
    "timestamp", "cpu_pct", "ram_pct", "ram_mb", "disk_pct", "swap_pct",
    "thread_count", "fd_count", "socket_count", "queue_depth", "pid",
]

# Alert thresholds
THRESHOLDS = {
    "ram_growth_pct": 10.0,     # > 10% growth from baseline over 5 min
    "thread_growth": 0,          # any thread count increase
    "fd_growth": 10,             # > 10 FD increase over 5 min
    "disk_pct": 85.0,
    "ram_pct": 90.0,
    "cpu_sustained_pct": 95.0,
    "cpu_sustained_sec": 60,
}


def _find_trading_pid() -> Optional[int]:
    """Find PID of trading system (main.py)."""
    try:
        if platform.system() == "Linux":
            result = subprocess.run(
                ["pgrep", "-f", "main.py"], capture_output=True, text=True
            )
            pids = result.stdout.strip().split("\n")
            for p in pids:
                if p.strip().isdigit():
                    return int(p.strip())
        else:
            # Windows
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
                capture_output=True, text=True
            )
            for line in result.stdout.split("\n"):
                if "main.py" in line.lower():
                    parts = line.split(",")
                    if len(parts) >= 2:
                        pid = parts[1].strip().strip('"')
                        if pid.isdigit():
                            return int(pid)
    except Exception:
        pass
    return None


def _get_metrics() -> Dict[str, Any]:
    """Capture a single metrics snapshot."""
    metrics = {
        "timestamp": ist_now_iso(),
        "cpu_pct": -1,
        "ram_pct": -1,
        "ram_mb": -1,
        "disk_pct": -1,
        "swap_pct": -1,
        "thread_count": -1,
        "fd_count": -1,
        "socket_count": -1,
        "queue_depth": -1,
        "pid": -1,
    }

    try:
        import psutil
        metrics["cpu_pct"] = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        metrics["ram_pct"] = mem.percent
        metrics["ram_mb"] = round(mem.used / (1024 * 1024), 1)

        disk = psutil.disk_usage("/")
        metrics["disk_pct"] = disk.percent

        swap = psutil.swap_memory()
        metrics["swap_pct"] = swap.percent
    except ImportError:
        # Fallback without psutil
        if platform.system() == "Linux":
            try:
                with open("/proc/meminfo") as f:
                    meminfo = f.read()
                total = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1))
                avail = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1))
                metrics["ram_pct"] = round((1 - avail / total) * 100, 1)
                metrics["ram_mb"] = round((total - avail) / 1024, 1)
            except Exception:
                pass
    except Exception:
        pass

    # Trading system PID and process metrics
    pid = _find_trading_pid()
    metrics["pid"] = pid or -1

    if pid and platform.system() == "Linux":
        try:
            # Thread count
            task_dir = Path(f"/proc/{pid}/task")
            if task_dir.exists():
                metrics["thread_count"] = len(list(task_dir.iterdir()))

            # FD count
            fd_dir = Path(f"/proc/{pid}/fd")
            if fd_dir.exists():
                metrics["fd_count"] = len(list(fd_dir.iterdir()))
        except Exception:
            pass

        try:
            import psutil
            proc = psutil.Process(pid)
            metrics["thread_count"] = proc.num_threads()
            metrics["socket_count"] = len([c for c in psutil.net_connections()
                                           if c.pid == pid])
        except Exception:
            pass

    # Signal queue depth from health endpoint
    health = http_get(HEALTH_URL)
    if health:
        metrics["queue_depth"] = health.get("queue_depth",
                                             health.get("signal_queue_depth", -1))

    return metrics


def _check_alerts(metrics: Dict, baseline: Optional[Dict],
                  history: list) -> list:
    """Check for alert conditions."""
    alerts = []

    if metrics["disk_pct"] > THRESHOLDS["disk_pct"]:
        alerts.append({"type": "HIGH_DISK", "value": metrics["disk_pct"],
                       "threshold": THRESHOLDS["disk_pct"]})

    if metrics["ram_pct"] > THRESHOLDS["ram_pct"]:
        alerts.append({"type": "HIGH_RAM", "value": metrics["ram_pct"],
                       "threshold": THRESHOLDS["ram_pct"]})

    if baseline:
        # Memory growth check (over 5 min = 300 samples at 1s)
        if len(history) >= 300:
            baseline_ram = history[-300]["ram_mb"]
            current_ram = metrics["ram_mb"]
            if baseline_ram > 0:
                growth = ((current_ram - baseline_ram) / baseline_ram) * 100
                if growth > THRESHOLDS["ram_growth_pct"]:
                    alerts.append({"type": "MEMORY_LEAK", "growth_pct": round(growth, 1),
                                   "baseline_mb": baseline_ram, "current_mb": current_ram})

        # Thread leak
        if (baseline.get("thread_count", -1) > 0 and
                metrics["thread_count"] > baseline["thread_count"]):
            alerts.append({"type": "THREAD_LEAK",
                           "baseline": baseline["thread_count"],
                           "current": metrics["thread_count"]})

        # FD leak (over 5 min)
        if len(history) >= 300:
            baseline_fd = history[-300]["fd_count"]
            if baseline_fd > 0 and metrics["fd_count"] - baseline_fd > THRESHOLDS["fd_growth"]:
                alerts.append({"type": "FD_LEAK",
                               "baseline": baseline_fd,
                               "current": metrics["fd_count"]})

    # CPU sustained check
    if len(history) >= THRESHOLDS["cpu_sustained_sec"]:
        recent = history[-THRESHOLDS["cpu_sustained_sec"]:]
        if all(m.get("cpu_pct", 0) > THRESHOLDS["cpu_sustained_pct"] for m in recent):
            alerts.append({"type": "CPU_SUSTAINED_HIGH",
                           "duration_sec": THRESHOLDS["cpu_sustained_sec"],
                           "avg_cpu": round(sum(m["cpu_pct"] for m in recent) / len(recent), 1)})

    return alerts


def do_live() -> None:
    """Print single metrics snapshot."""
    metrics = _get_metrics()
    print(json.dumps(metrics, indent=2))


def do_start(day: int) -> None:
    """Start background monitoring daemon."""
    csv_path = RESOURCES_DIR / f"day{day}_resources.csv"
    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)

    # Fork to background
    if platform.system() == "Linux":
        pid = os.fork()
        if pid > 0:
            # Parent
            with open(PID_FILE, "w") as f:
                f.write(str(pid))
            print(f"Resource monitor started (PID: {pid})")
            print(f"CSV: {csv_path}")
            return
        # Child — detach
        os.setsid()
    else:
        # Windows: run in foreground (or user can background with &)
        print(f"Resource monitor running (foreground on Windows)")
        print(f"CSV: {csv_path}")
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

    # Write CSV header
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()

    baseline = None
    history: list = []
    running = True

    def handle_term(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, handle_term)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, handle_term)

    while running:
        try:
            metrics = _get_metrics()
            if baseline is None:
                baseline = metrics.copy()
            history.append(metrics)

            # Keep only last 600 samples (10 min) in memory
            if len(history) > 600:
                history = history[-600:]

            # Write to CSV
            with open(csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writerow({k: metrics.get(k, "") for k in CSV_COLUMNS})

            # Check alerts
            alerts = _check_alerts(metrics, baseline, history)
            for alert in alerts:
                msg = f"WARNING: {alert['type']} - {json.dumps(alert)}"
                print(msg, file=sys.stderr)
                append_jsonl(ALERT_LOG, {"ts": ist_now_iso(), **alert})

            time.sleep(1)
        except Exception as e:
            print(f"Monitor error: {e}", file=sys.stderr)
            time.sleep(5)

    # Cleanup PID file
    if PID_FILE.exists():
        PID_FILE.unlink()
    print("Resource monitor stopped")


def do_stop() -> None:
    """Stop the background monitor."""
    if not PID_FILE.exists():
        print("No monitor running (PID file not found)")
        return

    pid_str = PID_FILE.read_text().strip()
    if not pid_str.isdigit():
        print(f"Invalid PID file content: {pid_str}")
        PID_FILE.unlink()
        return

    pid = int(pid_str)
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to monitor PID {pid}")
    except ProcessLookupError:
        print(f"Monitor PID {pid} not running")
    except Exception as e:
        print(f"Error stopping monitor: {e}")

    if PID_FILE.exists():
        PID_FILE.unlink()


def do_report(day: int) -> None:
    """Generate summary report from CSV."""
    csv_path = RESOURCES_DIR / f"day{day}_resources.csv"
    if not csv_path.exists():
        print(f"No data file found: {csv_path}")
        return

    rows = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        print("No data rows found")
        return

    # Compute min/max/avg for numeric columns
    numeric_cols = ["cpu_pct", "ram_pct", "ram_mb", "disk_pct", "swap_pct",
                    "thread_count", "fd_count", "socket_count", "queue_depth"]
    stats = {}
    for col in numeric_cols:
        values = []
        for r in rows:
            try:
                v = float(r.get(col, -1))
                if v >= 0:
                    values.append(v)
            except (ValueError, TypeError):
                pass
        if values:
            stats[col] = {
                "min": round(min(values), 1),
                "max": round(max(values), 1),
                "avg": round(sum(values) / len(values), 1),
            }

    # Growth rates
    if len(rows) > 10:
        first_ram = float(rows[0].get("ram_mb", 0) or 0)
        last_ram = float(rows[-1].get("ram_mb", 0) or 0)
        if first_ram > 0:
            stats["ram_growth_pct"] = round(((last_ram - first_ram) / first_ram) * 100, 2)

    # Alerts
    alert_count = 0
    if ALERT_LOG.exists():
        with open(ALERT_LOG) as f:
            alert_count = sum(1 for _ in f)

    report = {
        "day": day,
        "samples": len(rows),
        "duration_minutes": round(len(rows) / 60, 1),
        "first_sample": rows[0].get("timestamp", "?"),
        "last_sample": rows[-1].get("timestamp", "?"),
        "stats": stats,
        "alerts_triggered": alert_count,
    }

    print(json.dumps(report, indent=2))


def main():
    # Need this for fallback without psutil
    global re
    import re as _re
    re = _re

    parser = argparse.ArgumentParser(description="Resource Monitor — Crash Test Tool 12")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", action="store_true", help="Start background monitoring")
    group.add_argument("--stop", action="store_true", help="Stop background monitor")
    group.add_argument("--report", action="store_true", help="Generate summary report")
    group.add_argument("--live", action="store_true", help="Print current snapshot")
    parser.add_argument("--day", type=int, default=0, help="Day number for file naming")
    args = parser.parse_args()

    if args.live:
        do_live()
    elif args.start:
        do_start(args.day)
    elif args.stop:
        do_stop()
    elif args.report:
        do_report(args.day)


if __name__ == "__main__":
    main()
