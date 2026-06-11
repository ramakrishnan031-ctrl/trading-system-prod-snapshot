"""
tests/crash_test/signal_injector.py — Tool 2: Simulate Chartink webhook payloads.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv as _load_dotenv
_load_dotenv()
from tests.crash_test.ct_utils import (
    ist_now, ist_now_iso, http_post, append_jsonl, REPORTS_DIR,
)

WEBHOOK_URL = "http://localhost:5000/webhook"
INJECTOR_LOG = REPORTS_DIR / "injector_log.jsonl"

def _webhook_url(scanner: str) -> str:
    """Build webhook URL with auth token from WEBHOOK_SECRET env var."""
    import os
    url = f"{WEBHOOK_URL}/{scanner}"
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if secret:
        url += f"?token={secret}"
    return url

DEFAULT_SCANNERS = [
    "gap_fade_short", "gap_fade_long", "open_low_breakout_long",
    "first_pullback_long", "vwap_bounce_long", "gap_go_long",
    "range_breakout_long", "open_high_breakdown_short",
    "first_pullback_short", "vwap_rejection_short",
]

DEFAULT_SYMBOLS = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "SBIN", "AXISBANK", "WIPRO", "HCLTECH", "KOTAKBANK",
    "BAJFINANCE", "LT", "ITC", "BHARTIARTL", "MARUTI",
]


def _build_payload(
    symbols: List[str],
    scanner: str,
    prices: List[float],
    triggered_at: str,
) -> Dict[str, Any]:
    return {
        "stocks": ",".join(symbols),
        "trigger_prices": ",".join(str(p) for p in prices),
        "triggered_at": triggered_at,
        "scan_name": scanner,
        "scan_url": f"https://chartink.com/screener/{scanner}",
    }


def _send_and_log(
    scanner: str, payload: Dict, mode: str, symbol_label: str, dry_run: bool = False
) -> Dict:
    url = _webhook_url(scanner)
    if dry_run:
        record = {
            "ts": ist_now_iso(), "mode": mode, "symbol": symbol_label,
            "dry_run": True, "url": url, "payload": payload,
        }
        print(json.dumps(record, indent=2))
        return record

    status, body, latency = http_post(url, payload)
    record = {
        "ts": ist_now_iso(), "mode": mode, "symbol": symbol_label,
        "http_status": status, "response": body, "latency_ms": round(latency, 1),
    }
    print(json.dumps(record))
    append_jsonl(INJECTOR_LOG, record)
    return record


def mode_single(args):
    now = args.triggered_at or ist_now().strftime("%Y-%m-%d %H:%M:%S")
    payload = _build_payload([args.symbol], args.scanner, [args.price], now)
    _send_and_log(args.scanner, payload, "single", args.symbol, args.dry_run)


def mode_burst(args):
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS
    symbols = symbols[:args.count]
    while len(symbols) < args.count:
        symbols.append(f"STOCK{len(symbols)+1:03d}")
    now = args.triggered_at or ist_now().strftime("%Y-%m-%d %H:%M:%S")
    for i, sym in enumerate(symbols):
        payload = _build_payload([sym], args.scanner, [args.price], now)
        _send_and_log(args.scanner, payload, "burst", sym, args.dry_run)
        if i < len(symbols) - 1 and args.delay_ms > 0:
            time.sleep(args.delay_ms / 1000.0)


def mode_flood(args):
    symbols = args.symbols.split(",") if args.symbols else []
    while len(symbols) < args.count:
        symbols.append(f"STOCK{len(symbols)+1:03d}")
    symbols = symbols[:args.count]
    now = args.triggered_at or ist_now().strftime("%Y-%m-%d %H:%M:%S")
    for i, sym in enumerate(symbols):
        payload = _build_payload([sym], args.scanner, [args.price], now)
        _send_and_log(args.scanner, payload, "flood", sym, args.dry_run)
        if args.delay_ms > 0:
            time.sleep(args.delay_ms / 1000.0)


def mode_malformed(args):
    scanner = args.scanner or "gap_fade_short"
    malformed_payloads = {
        "missing_fields": {},
        "missing_stocks": {"scan_name": scanner},
        "missing_symbol": {"stocks": "", "trigger_prices": "", "scan_name": scanner},
        "wrong_types": {"stocks": 12345, "trigger_prices": "abc", "scan_name": scanner},
        "oversized": {
            "stocks": ",".join(f"SYM{i:04d}" for i in range(1000)),
            "trigger_prices": ",".join("100.0" for _ in range(1000)),
            "scan_name": scanner, "triggered_at": ist_now().strftime("%Y-%m-%d %H:%M:%S"),
            "scan_url": f"https://chartink.com/screener/{scanner}",
        },
        "extra_fields": {
            "stocks": "RELIANCE", "trigger_prices": "1000.0",
            "scan_name": scanner,
            "triggered_at": ist_now().strftime("%Y-%m-%d %H:%M:%S"),
            "scan_url": f"https://chartink.com/screener/{scanner}",
            "junk": "data", "extra": 999,
        },
        "none_body": None,
        "wrong_content_type": {
            "stocks": "RELIANCE", "trigger_prices": "1000.0",
            "scan_name": scanner,
            "triggered_at": ist_now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    payload = malformed_payloads.get(args.type)

    if args.type == "none_body":
        url = _webhook_url(scanner)
        if args.dry_run:
            print(json.dumps({"mode": "malformed", "type": args.type,
                              "url": url, "payload": None}, indent=2))
            return
        import urllib.request
        req = urllib.request.Request(url, data=b"", method="POST",
                                     headers={"Content-Type": "application/json"})
        start = time.perf_counter()
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            status = resp.status
            body = resp.read().decode()
        except Exception as e:
            status = getattr(e, "code", 0)
            body = str(e)
        latency = (time.perf_counter() - start) * 1000
        record = {"ts": ist_now_iso(), "mode": "malformed", "type": args.type,
                  "http_status": status, "response": body, "latency_ms": round(latency, 1)}
        print(json.dumps(record))
        append_jsonl(INJECTOR_LOG, record)
        return

    if args.type == "wrong_content_type":
        url = _webhook_url(scanner)
        if args.dry_run:
            print(json.dumps({"mode": "malformed", "type": args.type,
                              "url": url, "payload": payload}, indent=2))
            return
        import urllib.request
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "text/plain"})
        start = time.perf_counter()
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            status = resp.status
            body = resp.read().decode()
        except Exception as e:
            status = getattr(e, "code", 0)
            body = str(e)
        latency = (time.perf_counter() - start) * 1000
        record = {"ts": ist_now_iso(), "mode": "malformed", "type": args.type,
                  "http_status": status, "response": body, "latency_ms": round(latency, 1)}
        print(json.dumps(record))
        append_jsonl(INJECTOR_LOG, record)
        return

    if payload is not None:
        _send_and_log(scanner, payload, f"malformed/{args.type}", args.type, args.dry_run)


def mode_expired(args):
    from datetime import timedelta
    expired_time = ist_now() - timedelta(seconds=args.age_seconds)
    triggered_at = expired_time.strftime("%Y-%m-%d %H:%M:%S")
    payload = _build_payload([args.symbol], args.scanner, [args.price], triggered_at)
    _send_and_log(args.scanner, payload, "expired", args.symbol, args.dry_run)


def mode_duplicate(args):
    now = ist_now().strftime("%Y-%m-%d %H:%M:%S")
    for i in range(args.count):
        payload = _build_payload([args.symbol], args.scanner, [args.price], now)
        _send_and_log(args.scanner, payload, "duplicate", f"{args.symbol}_{i+1}", args.dry_run)
        if i < args.count - 1:
            time.sleep(args.delay_seconds)


def main():
    parser = argparse.ArgumentParser(description="Signal Injector — Crash Test Tool 2")
    parser.add_argument("--mode", required=True,
                        choices=["single", "burst", "flood", "malformed", "expired", "duplicate"])
    parser.add_argument("--symbol", type=str, default="RELIANCE")
    parser.add_argument("--symbols", type=str, default="")
    parser.add_argument("--scanner", type=str, default="gap_fade_short")
    parser.add_argument("--price", type=float, default=1000.0)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--delay-ms", type=int, default=200)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--triggered-at", type=str, default="")
    parser.add_argument("--age-seconds", type=int, default=700)
    parser.add_argument("--type", type=str, default="missing_fields",
                        choices=["missing_fields", "missing_stocks", "missing_symbol",
                                 "wrong_types", "oversized", "extra_fields",
                                 "none_body", "wrong_content_type"])
    parser.add_argument("--dry-run", action="store_true",
                        help="Print payload without sending HTTP request")
    args = parser.parse_args()

    dispatch = {
        "single": mode_single,
        "burst": mode_burst,
        "flood": mode_flood,
        "malformed": mode_malformed,
        "expired": mode_expired,
        "duplicate": mode_duplicate,
    }
    dispatch[args.mode](args)


if __name__ == "__main__":
    main()
