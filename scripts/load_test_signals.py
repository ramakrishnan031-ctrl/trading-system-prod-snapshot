"""
scripts/load_test_signals.py -- Trading System v2  FIX-151

Purpose:
    Simulate burst/sustained webhook signal traffic to stress-test the
    signal queue and backpressure mechanism. Starts a mock webhook server
    internally (or targets a running instance), fires synthetic Chartink
    payloads, and measures throughput, latency, and error codes.

Test scenarios:
    burst      -- 100 signals in 10 seconds
    sustained  -- 50 signals/min for 5 min (250 total)
    overflow   -- 200 signals in 5 seconds (force 503)

Usage:
    python scripts/load_test_signals.py --scenario burst [--target http://localhost:5000]
    python scripts/load_test_signals.py --scenario sustained
    python scripts/load_test_signals.py --scenario overflow --dry-run

Output:
    reports/load_test/load_test_YYYY-MM-DD_<scenario>.md

Exit codes:
    0 -- all assertions passed
    1 -- error
    2 -- assertions failed (degradation detected)

WARNING: Do NOT run against production VM during market hours.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.time_authority import now_ist, today_ist

REPORT_DIR = _ROOT / "reports" / "load_test"
DEFAULT_TARGET = "http://localhost:5000"

SCANNER_NAMES = [
    "gap_fade_long", "gap_fade_short", "gap_go_long", "gap_go_short",
    "first_pullback_long", "first_pullback_short", "vwap_bounce_long",
    "vwap_rejection_short", "range_breakout_long", "range_breakout_short",
    "open_low_breakout_long", "open_high_breakdown_short",
]

SYMBOLS = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "BHARTIARTL", "ITC", "SBIN", "BAJFINANCE", "LT",
    "MARUTI", "HCLTECH", "WIPRO", "SUNPHARMA", "TATAMOTORS",
    "ADANIENT", "ASIANPAINT", "AXISBANK", "TATASTEEL", "HINDALCO",
]


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="load_test_signals",
        description="FIX-151: Webhook load test simulation.",
    )
    parser.add_argument("--scenario", choices=["burst", "sustained", "overflow"],
                        required=True)
    parser.add_argument("--target", default=DEFAULT_TARGET,
                        help="Webhook base URL (default: localhost:5000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan without sending requests")
    parser.add_argument("--token", default=None,
                        help="Auth token for ?token= param")
    return parser.parse_args(argv)


def _make_payload(symbol: str, scanner: str, price: float = 150.0) -> dict:
    return {
        "stocks": symbol,
        "trigger_prices": str(price),
        "triggered_at": now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        "scan_name": scanner,
        "scan_url": f"https://chartink.com/screener/{scanner}",
    }


def _send_signal(target: str, scanner: str, payload: dict,
                 token: str | None = None) -> dict:
    url = f"{target}/webhook/{scanner}"
    if token:
        url += f"?token={token}"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            elapsed_ms = (time.monotonic() - start) * 1000
            body = resp.read().decode("utf-8", errors="replace")
            headers = dict(resp.headers)
            return {
                "status": resp.status,
                "elapsed_ms": elapsed_ms,
                "body": body[:500],
                "queue_depth": headers.get("X-Queue-Depth", ""),
                "queue_warning": headers.get("X-Queue-Warning", ""),
            }
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return {
            "status": exc.code,
            "elapsed_ms": elapsed_ms,
            "body": body,
            "queue_depth": exc.headers.get("X-Queue-Depth", "") if exc.headers else "",
            "queue_warning": exc.headers.get("X-Queue-Warning", "") if exc.headers else "",
        }
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        return {
            "status": 0,
            "elapsed_ms": elapsed_ms,
            "body": str(exc)[:500],
            "queue_depth": "",
            "queue_warning": "",
        }


def _scenario_config(scenario: str) -> dict:
    if scenario == "burst":
        return {"total_signals": 100, "duration_sec": 10, "workers": 20}
    elif scenario == "sustained":
        return {"total_signals": 250, "duration_sec": 300, "workers": 5}
    elif scenario == "overflow":
        return {"total_signals": 200, "duration_sec": 5, "workers": 40}
    raise ValueError(f"Unknown scenario: {scenario}")


def _run_scenario(scenario: str, target: str, token: str | None,
                  log, dry_run: bool = False) -> dict:
    cfg = _scenario_config(scenario)
    total = cfg["total_signals"]
    duration = cfg["duration_sec"]
    workers = cfg["workers"]
    delay_per_signal = duration / total

    log.info("load_test: scenario=%s total=%d duration=%ds workers=%d target=%s",
             scenario, total, duration, workers, target)

    if dry_run:
        log.info("load_test: dry-run; would send %d signals over %ds", total, duration)
        return {
            "scenario": scenario,
            "total_signals": total,
            "dry_run": True,
            "results": [],
        }

    results = []
    start_time = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        for i in range(total):
            symbol = SYMBOLS[i % len(SYMBOLS)]
            scanner = SCANNER_NAMES[i % len(SCANNER_NAMES)]
            payload = _make_payload(symbol, scanner, price=100.0 + (i % 50))

            future = pool.submit(_send_signal, target, scanner, payload, token)
            futures.append(future)

            if delay_per_signal > 0 and i < total - 1:
                time.sleep(delay_per_signal)

        for future in as_completed(futures):
            results.append(future.result())

    wall_time = time.monotonic() - start_time

    status_counts = {}
    latencies = []
    warnings = 0
    for r in results:
        code = r["status"]
        status_counts[code] = status_counts.get(code, 0) + 1
        latencies.append(r["elapsed_ms"])
        if r.get("queue_warning"):
            warnings += 1

    latencies.sort()
    summary = {
        "scenario": scenario,
        "total_signals": total,
        "wall_time_sec": round(wall_time, 2),
        "throughput_per_sec": round(total / wall_time, 2) if wall_time > 0 else 0,
        "status_counts": status_counts,
        "latency_p50_ms": round(latencies[len(latencies) // 2], 1) if latencies else 0,
        "latency_p95_ms": round(latencies[int(len(latencies) * 0.95)], 1) if latencies else 0,
        "latency_p99_ms": round(latencies[int(len(latencies) * 0.99)], 1) if latencies else 0,
        "latency_min_ms": round(min(latencies), 1) if latencies else 0,
        "latency_max_ms": round(max(latencies), 1) if latencies else 0,
        "latency_avg_ms": round(statistics.mean(latencies), 1) if latencies else 0,
        "queue_warnings": warnings,
        "dry_run": False,
    }

    log.info(
        "load_test: DONE scenario=%s throughput=%.1f/s p50=%.1fms p95=%.1fms p99=%.1fms "
        "status=%s warnings=%d",
        scenario, summary["throughput_per_sec"],
        summary["latency_p50_ms"], summary["latency_p95_ms"],
        summary["latency_p99_ms"], status_counts, warnings,
    )

    return summary


def _check_assertions(summary: dict, scenario: str) -> list[str]:
    failures = []

    if scenario == "burst":
        ok_count = summary["status_counts"].get(200, 0)
        if ok_count < 50:
            failures.append(f"Burst: only {ok_count}/100 signals got 200 (expected >= 50)")
        if summary["latency_p95_ms"] > 5000:
            failures.append(f"Burst: p95 latency {summary['latency_p95_ms']}ms > 5000ms")

    elif scenario == "sustained":
        ok_count = summary["status_counts"].get(200, 0)
        total = summary["total_signals"]
        if ok_count < total * 0.8:
            failures.append(f"Sustained: only {ok_count}/{total} got 200 (expected >= 80%)")

    elif scenario == "overflow":
        reject_count = summary["status_counts"].get(503, 0)
        if reject_count == 0:
            failures.append("Overflow: expected some 503 responses but got none")

    return failures


def _write_report(summary: dict, assertions: list[str], scenario: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date_iso = today_ist()
    report_path = REPORT_DIR / f"load_test_{date_iso}_{scenario}.md"

    status_lines = "\n".join(
        f"  - HTTP {code}: {count}"
        for code, count in sorted(summary.get("status_counts", {}).items())
    )

    verdict = "PASS" if not assertions else "FAIL"
    assertion_lines = "\n".join(f"  - {a}" for a in assertions) if assertions else "  All assertions passed."

    report_path.write_text(
        f"# Load Test Report -- {date_iso}\n\n"
        f"**Scenario:** {scenario}\n"
        f"**Verdict:** {verdict}\n\n"
        f"---\n\n"
        f"## Summary\n\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Total signals | {summary['total_signals']} |\n"
        f"| Wall time | {summary.get('wall_time_sec', 'N/A')}s |\n"
        f"| Throughput | {summary.get('throughput_per_sec', 'N/A')} signals/sec |\n"
        f"| Queue warnings | {summary.get('queue_warnings', 0)} |\n\n"
        f"## Latency\n\n"
        f"| Percentile | Latency |\n"
        f"|------------|----------|\n"
        f"| min | {summary.get('latency_min_ms', 'N/A')}ms |\n"
        f"| p50 | {summary.get('latency_p50_ms', 'N/A')}ms |\n"
        f"| p95 | {summary.get('latency_p95_ms', 'N/A')}ms |\n"
        f"| p99 | {summary.get('latency_p99_ms', 'N/A')}ms |\n"
        f"| max | {summary.get('latency_max_ms', 'N/A')}ms |\n"
        f"| avg | {summary.get('latency_avg_ms', 'N/A')}ms |\n\n"
        f"## Response Codes\n\n{status_lines}\n\n"
        f"## Assertions\n\n{assertion_lines}\n",
        encoding="utf-8",
    )
    return report_path


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("load_test_signals")

    try:
        summary = _run_scenario(
            scenario=args.scenario,
            target=args.target,
            token=args.token,
            log=log,
            dry_run=args.dry_run,
        )

        if args.dry_run:
            return 0

        assertions = _check_assertions(summary, args.scenario)
        report_path = _write_report(summary, assertions, args.scenario)
        log.info("load_test: report saved to %s", report_path)

        if assertions:
            for a in assertions:
                log.warning("load_test: ASSERTION FAILED: %s", a)
            return 2

        return 0
    except Exception as exc:
        log.error("load_test_signals failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
