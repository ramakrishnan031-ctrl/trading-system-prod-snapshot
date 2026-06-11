"""
tests/crash_test/network_controller.py — Tool 4: Block/unblock network connections.
Platform: Ubuntu VM only. Uses iptables. Requires sudo.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import ist_now_iso, append_jsonl, REPORTS_DIR

NETWORK_LOG = REPORTS_DIR / "network_log.jsonl"
MAX_DURATION = 300  # hard cap
CHAIN_COMMENT = "CRASH_TEST"

PRESETS = {
    "block_zerodha_rest": [
        {"rule": ["-A", "OUTPUT", "-d", "api.kite.trade", "-j", "DROP",
                  "-m", "comment", "--comment", CHAIN_COMMENT]}
    ],
    "block_zerodha_ws": [
        {"rule": ["-A", "OUTPUT", "-d", "ws.kite.trade", "-j", "DROP",
                  "-m", "comment", "--comment", CHAIN_COMMENT]},
    ],
    "block_zerodha_all": [
        {"rule": ["-A", "OUTPUT", "-d", "api.kite.trade", "-j", "DROP",
                  "-m", "comment", "--comment", CHAIN_COMMENT]},
        {"rule": ["-A", "OUTPUT", "-d", "ws.kite.trade", "-j", "DROP",
                  "-m", "comment", "--comment", CHAIN_COMMENT]},
    ],
    "block_telegram": [
        {"rule": ["-A", "OUTPUT", "-d", "api.telegram.org", "-j", "DROP",
                  "-m", "comment", "--comment", CHAIN_COMMENT]}
    ],
    "block_all_outbound": [
        # SSH always exempt — server responses (sport 22) + client connections (dport 22)
        {"rule": ["-A", "OUTPUT", "-p", "tcp", "--sport", "22", "-j", "ACCEPT",
                  "-m", "comment", "--comment", CHAIN_COMMENT]},
        {"rule": ["-A", "OUTPUT", "-p", "tcp", "--dport", "22", "-j", "ACCEPT",
                  "-m", "comment", "--comment", CHAIN_COMMENT]},
        {"rule": ["-A", "OUTPUT", "-j", "DROP",
                  "-m", "comment", "--comment", CHAIN_COMMENT]},
    ],
    "block_dns": [
        {"rule": ["-A", "OUTPUT", "-p", "udp", "--dport", "53", "-j", "DROP",
                  "-m", "comment", "--comment", CHAIN_COMMENT]}
    ],
}


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _run_iptables(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["iptables"] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _log(action: str, details: dict) -> None:
    record = {"ts": ist_now_iso(), "action": action, **details}
    print(json.dumps(record))
    append_jsonl(NETWORK_LOG, record)


def apply_preset(preset_name: str) -> bool:
    if preset_name not in PRESETS:
        print(f"ERROR: Unknown preset: {preset_name}")
        return False
    rules = PRESETS[preset_name]
    for r in rules:
        try:
            _run_iptables(r["rule"])
        except subprocess.CalledProcessError as e:
            _log("block_failed", {"preset": preset_name, "error": e.stderr})
            return False
    _log("block_applied", {"preset": preset_name})
    return True


def remove_preset(preset_name: str) -> bool:
    if preset_name not in PRESETS:
        print(f"ERROR: Unknown preset: {preset_name}")
        return False
    rules = PRESETS[preset_name]
    for r in rules:
        delete_rule = ["-D"] + r["rule"][1:]  # Replace -A with -D
        try:
            _run_iptables(delete_rule, check=False)
        except Exception:
            pass
    _log("block_removed", {"preset": preset_name})
    return True


def unblock_all() -> None:
    if not _is_linux():
        return
    # Remove all rules with CRASH_TEST comment
    while True:
        result = _run_iptables(
            ["-L", "OUTPUT", "-n", "--line-numbers", "-v"], check=False
        )
        lines = result.stdout.strip().split("\n") if result.stdout else []
        found = False
        for line in reversed(lines):
            if CHAIN_COMMENT in line:
                parts = line.split()
                if parts and parts[0].isdigit():
                    _run_iptables(["-D", "OUTPUT", parts[0]], check=False)
                    found = True
                    break
        if not found:
            break
    _log("unblock_all", {"status": "complete"})


def get_status() -> dict:
    if not _is_linux():
        return {"platform": platform.system(), "message": "iptables not available (not Linux)",
                "rules": []}
    result = _run_iptables(["-L", "OUTPUT", "-n", "-v"], check=False)
    lines = result.stdout.strip().split("\n") if result.stdout else []
    crash_test_rules = [l for l in lines if CHAIN_COMMENT in l]
    return {
        "active_crash_test_rules": len(crash_test_rules),
        "rules": crash_test_rules,
    }


def add_latency(ms: int, target: str) -> bool:
    try:
        subprocess.run(
            ["tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", f"{ms}ms"],
            capture_output=True, text=True, check=True
        )
        _log("latency_added", {"ms": ms, "target": target})
        return True
    except Exception as e:
        _log("latency_failed", {"ms": ms, "error": str(e)})
        return False


def remove_latency() -> bool:
    try:
        subprocess.run(
            ["tc", "qdisc", "del", "dev", "eth0", "root"],
            capture_output=True, text=True, check=False
        )
        _log("latency_removed", {})
        return True
    except Exception:
        return False


def _safety_timer(duration: int, preset: str) -> None:
    """Background thread that auto-unblocks after duration."""
    time.sleep(duration)
    print(f"\n[SAFETY] Auto-unblocking after {duration}s timeout")
    if preset == "ALL":
        unblock_all()
    else:
        remove_preset(preset)


def main():
    parser = argparse.ArgumentParser(description="Network Controller — Crash Test Tool 4")
    parser.add_argument("--preset", type=str,
                        choices=list(PRESETS.keys()),
                        help="Network block preset to apply")
    parser.add_argument("--duration", type=int, default=30,
                        help="Block duration in seconds (max 300)")
    parser.add_argument("--manual", action="store_true",
                        help="Block until explicitly unblocked")
    parser.add_argument("--unblock", type=str, help="Remove specific preset block")
    parser.add_argument("--unblock-all", action="store_true",
                        help="Remove all crash-test iptables rules")
    parser.add_argument("--status", action="store_true",
                        help="Show current crash-test rules")
    parser.add_argument("--latency", type=int, help="Add latency in ms")
    parser.add_argument("--target", type=str, default="all",
                        help="Target host for latency")
    parser.add_argument("--remove-latency", action="store_true",
                        help="Remove latency rules")
    args = parser.parse_args()

    if not _is_linux() and not args.status:
        print(json.dumps({
            "platform": platform.system(),
            "message": "Network controller requires Linux with iptables. "
                       "This tool is designed for the Ubuntu VM.",
            "status": "SKIPPED",
        }, indent=2))
        if args.status:
            return
        sys.exit(0)

    # Register cleanup on exit
    atexit.register(unblock_all)

    if args.status:
        status = get_status()
        print(json.dumps(status, indent=2))
        if status.get("active_crash_test_rules", 0) == 0:
            print("No crash-test iptables rules active")

    elif args.unblock_all:
        unblock_all()
        print("All crash-test rules removed")

    elif args.unblock:
        remove_preset(args.unblock)
        print(f"Removed preset: {args.unblock}")

    elif args.remove_latency:
        remove_latency()
        print("Latency rules removed")

    elif args.latency:
        add_latency(args.latency, args.target)

    elif args.preset:
        duration = min(args.duration, MAX_DURATION)
        ok = apply_preset(args.preset)
        if ok and not args.manual:
            print(f"Block applied: {args.preset} for {duration}s")
            timer = threading.Thread(
                target=_safety_timer, args=(duration, args.preset), daemon=True
            )
            timer.start()
            timer.join()
            print(f"Auto-unblocked after {duration}s")
        elif ok and args.manual:
            print(f"Block applied: {args.preset} (manual mode — use --unblock to remove)")
            print("Press Ctrl+C to unblock and exit")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                remove_preset(args.preset)
                print(f"\nUnblocked: {args.preset}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
