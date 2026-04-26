"""
scripts/preflight_scanner_check.py — Trading System v2

Purpose:
    Standalone CLI that operator runs BEFORE market open to verify all
    Chartink scanner URLs are reachable and returning expected shape (PF1-PF8).
    Thin wrapper around utils.startup_checks.check_scanner_connectivity().

Usage:
    python scripts/preflight_scanner_check.py [--config PATH] [--verbose] [--timeout N]

Exit codes:
    0 — all scanners reachable with 2xx
    1 — one or more unreachable, missing config, or import error

Locked: PF1-PF8
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="preflight_scanner_check",
        description="Pre-flight Chartink scanner connectivity check (PF1-PF8).",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default="config",
        help="Path to config directory (default: config/)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print response snippet per scanner.",
    )
    parser.add_argument(
        "--timeout",
        metavar="N",
        type=int,
        default=10,
        help="HTTP timeout in seconds (default: 10).",
    )
    return parser.parse_args(argv)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP fetcher
# ─────────────────────────────────────────────────────────────────────────────

_HEADERS = {"User-Agent": "TradingSystem/2.0 preflight-check"}
_SNIPPET_LEN = 300

# A.4 (2026-04-25): retry policy for transient HTTP failures. Operator runs
# this script before every trading day, so a single network blip causing a
# false-failure prompts cancelling trading -- expensive. Two retries with
# 1s linear backoff turns most transient failures into success without
# materially slowing a real-failure case (3 attempts * 10s timeout = 30s).
# 4xx responses (config errors -- bad URL) are NOT retried; only network
# exceptions and 5xx are eligible.
_PREFLIGHT_RETRIES = 2          # i.e. up to 3 attempts total
_PREFLIGHT_RETRY_BACKOFF_SEC = 1.0


def _make_capturing_fetcher(
    snippets: Dict[str, str],
    timeout_override: Optional[int] = None,
    sleep_fn=None,
):
    """
    Return http_fetcher_fn compatible with check_scanner_connectivity.
    Captures response snippets keyed by URL so --verbose can display them
    without re-fetching (PF5, PF6).

    A.4: tolerates transient failures with up to _PREFLIGHT_RETRIES retries.
    sleep_fn is injected for tests so they don't actually wait.
    """
    if sleep_fn is None:
        import time as _time
        sleep_fn = _time.sleep

    def _fetch(url: str, timeout: float) -> Tuple[Optional[int], str]:
        t = timeout_override if timeout_override is not None else timeout
        last_status: Optional[int] = None
        last_detail: str = ""
        for attempt in range(_PREFLIGHT_RETRIES + 1):
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=t)
                snippet = resp.text[:_SNIPPET_LEN] if resp.text else ""
                snippets[url] = snippet
                # 4xx is operator config (bad URL): no retry.
                # 5xx is server-side: retry within budget.
                if 500 <= resp.status_code < 600 and attempt < _PREFLIGHT_RETRIES:
                    last_status, last_detail = resp.status_code, snippet
                    sleep_fn(_PREFLIGHT_RETRY_BACKOFF_SEC)
                    continue
                return resp.status_code, snippet
            except requests.RequestException as exc:
                last_status, last_detail = None, str(exc)
                snippets[url] = ""
                if attempt < _PREFLIGHT_RETRIES:
                    sleep_fn(_PREFLIGHT_RETRY_BACKOFF_SEC)
                    continue
                return None, str(exc)
        return last_status, last_detail

    return _fetch


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def _print_table(results, verbose: bool, snippets: Dict[str, str]) -> None:
    if not results:
        print("  (no scanners configured)")
        return

    name_w = max(len(r.scanner_name) for r in results)
    url_w  = min(max(len(r.url) for r in results), 52)

    header = (
        f"{'SCANNER':<{name_w}}  {'URL':<{url_w}}  {'OK':>5}  {'STATUS':>6}  DETAIL"
    )
    sep = "-" * (name_w + url_w + 30)
    print(header)
    print(sep)

    for r in results:
        ok_str     = "YES"  if r.reachable else "NO"
        status_str = str(r.status_code) if r.status_code is not None else "-"
        detail     = r.error or ("has-content" if r.response_has_content else "empty-body")
        url_display = (r.url[: url_w - 1] + "~") if len(r.url) > url_w else r.url
        print(
            f"{r.scanner_name:<{name_w}}  {url_display:<{url_w}}  "
            f"{ok_str:>5}  {status_str:>6}  {detail}"
        )

    print(sep)

    if verbose:
        print()
        for r in results:
            snippet = snippets.get(r.url, "")
            print(f"  [{r.scanner_name}]")
            if snippet:
                # Show first 200 chars, ASCII-safe
                safe = snippet[:200].encode("ascii", "replace").decode("ascii")
                print(f"    snippet: {safe!r}")
            elif r.error:
                print(f"    error:   {r.error}")
            else:
                print(f"    (empty body)")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger("preflight_scanner")

    # Validate config directory
    config_dir = Path(args.config)
    if not config_dir.is_dir():
        print(f"ERROR: config directory not found: {config_dir}", file=sys.stderr)
        return 1

    # Load AppConfig
    try:
        from core.config_loader import load_all
    except ImportError as exc:
        print(f"ERROR: cannot import core.config_loader: {exc}", file=sys.stderr)
        return 1

    try:
        app_config = load_all(config_dir)
    except Exception as exc:
        print(f"ERROR: failed to load config from {config_dir}: {exc}", file=sys.stderr)
        return 1

    # Run connectivity check via library (PF6 — no duplicate logic)
    from utils.startup_checks import check_scanner_connectivity

    snippets: Dict[str, str] = {}
    http_fetcher = _make_capturing_fetcher(snippets, timeout_override=args.timeout)

    result = check_scanner_connectivity(
        app_config.scan_webhook_map,
        app_config.chartink_scanners,
        http_fetcher,
        log,
        timeout_sec=float(args.timeout),
    )

    # Print summary
    n = len(result.results)
    reachable_n = sum(1 for r in result.results if r.reachable)
    print(f"\nPreflight scanner check  ({n} scanners, timeout={args.timeout}s)\n")
    _print_table(result.results, verbose=args.verbose, snippets=snippets)

    if result.all_reachable:
        print(f"\nAll {n} scanners reachable. OK")
        return 0
    else:
        failed = [r.scanner_name for r in result.results if not r.reachable]
        print(
            f"\nFAILED: {len(failed)} of {n} scanner(s) unreachable: "
            + ", ".join(failed)
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
