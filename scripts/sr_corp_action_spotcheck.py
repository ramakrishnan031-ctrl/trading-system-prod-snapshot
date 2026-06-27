"""
scripts/sr_corp_action_spotcheck.py — Trading System v2 · S&R Detector V1

Purpose (spec C, one-time verification):
    Confirm whether kite.historical_data() returns split/bonus-ADJUSTED equity
    candles by default. The detector clusters swing pivots over 6 months — an
    UNADJUSTED split (e.g. 2:1 → price halves overnight) would create a phantom
    gap and a false zone. Run ONCE against a known-split stock on the VM (needs a
    live token); note the result in SYSTEM_MAP.

How it works:
    Fetch daily candles spanning a known split/bonus ex-date and report the
    largest overnight close-to-close ratio. ADJUSTED data is continuous (ratios
    near 1.0); UNADJUSTED data shows a ratio near the split factor (≈0.5 for 2:1,
    ≈0.2 for 5:1, etc.).

Usage (VM):
    python scripts/sr_corp_action_spotcheck.py --symbol IDEA --around 2026-01-15 --window 20
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOKEN_PATH = ROOT / "data_store" / "session" / "zerodha_token.json"


def _build_kite():
    import json
    from kiteconnect import KiteConnect  # type: ignore[import]
    if not TOKEN_PATH.exists():
        print(f"ERROR: no token at {TOKEN_PATH} — run on the VM with a live session.")
        return None
    td = json.loads(TOKEN_PATH.read_text())
    k = KiteConnect(api_key=td["api_key"])
    k.set_access_token(td["access_token"])
    return k


def _token_for(kite, symbol: str):
    for i in kite.instruments("NSE"):
        if i["tradingsymbol"] == symbol:
            return i["instrument_token"]
    return None


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="sr_corp_action_spotcheck")
    p.add_argument("--symbol", required=True, help="NSE tradingsymbol with a known split/bonus")
    p.add_argument("--around", required=True, help="ex-date YYYY-MM-DD")
    p.add_argument("--window", type=int, default=20, help="days each side")
    args = p.parse_args(argv)

    kite = _build_kite()
    if kite is None:
        return 1

    token = _token_for(kite, args.symbol)
    if token is None:
        print(f"ERROR: {args.symbol} not found in NSE instruments")
        return 1

    ex = datetime.strptime(args.around, "%Y-%m-%d")
    rows = kite.historical_data(
        instrument_token=token,
        from_date=ex - timedelta(days=args.window),
        to_date=ex + timedelta(days=args.window),
        interval="day",
    )
    if not rows or len(rows) < 2:
        print("ERROR: not enough candles returned")
        return 1

    worst_ratio = 1.0
    worst_when = None
    prev = rows[0]
    for c in rows[1:]:
        if prev["close"]:
            ratio = c["close"] / prev["close"]
            if abs(ratio - 1.0) > abs(worst_ratio - 1.0):
                worst_ratio = ratio
                worst_when = (prev["date"], c["date"])
        prev = c

    print(f"{args.symbol} around {args.around}: {len(rows)} daily candles")
    print(f"largest overnight close ratio = {worst_ratio:.3f} at {worst_when}")
    if abs(worst_ratio - 1.0) < 0.15:
        print("VERDICT: ADJUSTED (continuous) — historical_data returns split/bonus-adjusted candles. ✅")
    else:
        print("VERDICT: looks UNADJUSTED (a split-sized jump) — detector must adjust or "
              "restrict lookback. ⚠️  Note this in SYSTEM_MAP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
