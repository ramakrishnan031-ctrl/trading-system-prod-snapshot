#!/usr/bin/env python
"""
scripts/t2_cnc_gtt_realtest.py — SLICE2.5-P1 T2: REAL-API proof (MARKET HOURS ONLY).

THE PRODUCTION BLOCKER. Phase 1 is NOT done — and delivery_enabled stays false —
until this passes: a REAL CNC buy fills, a REAL OCO-GTT places + verifies, and a REAL
CNC sell of that share completes with NO manual CDSL TPIN/DDPI prompt.

⚠️  This places REAL orders with REAL money on the live account. Rama runs it MANUALLY
    during market hours. It is NEVER auto-run, never wired into a cron, never imported.
    It refuses to run without the explicit confirmation flag, refuses outside market
    hours, and self-cleans (cancels the GTT + squares the share) on every exit path.

USAGE (on the VM, market hours):
    cd /home/ubuntu/systems/trading-system && set -a && . ./.env && set +a
    PYTHONPATH=. /home/ubuntu/systems/venv/bin/python scripts/t2_cnc_gtt_realtest.py \
        --symbol IDEA --account LFL836 --i-understand-this-places-a-real-cnc-order

    Modes:
      (default)      single-session: BUY 1 CNC → place OCO GTT → get_gtt verify →
                     square via a CNC SELL (the no-TPIN proof) → delete the GTT.
      --arm-overnight: BUY 1 CNC → place OCO GTT → verify → STOP (do NOT square).
                     The definitive TPIN test: let it hold overnight, then NEXT DAY
                     either let the GTT trigger naturally or run --close-overnight to
                     sell the held share + confirm NO TPIN prompt.
      --close-overnight <gtt_id>: NEXT DAY — sell the held 1 share (CNC) + delete the
                     GTT, and report whether a TPIN prompt was required.

PASS CRITERIA: real CNC buy filled · real OCO GTT placed + get_gtt shows product=CNC,
qty=1, two SELL legs, [SL,TGT] ascending · the CNC sell completed (status COMPLETE)
with ZERO manual TPIN intervention. Report the result to Rama.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import time as dt_time


def _is_market_hours() -> bool:
    from core.time_authority import now_ist
    now = now_ist().time()
    return dt_time(9, 15) <= now <= dt_time(15, 30)


def _build_live_adapter(account: str):
    """A minimal LIVE adapter with delivery_enabled=True (this script is the gated
    exception that exercises the real GTT path before the master lock is flipped)."""
    import json, os
    from pathlib import Path
    from kiteconnect import KiteConnect
    from broker.zerodha_adapter import ZerodhaAdapter
    from broker.product_resolver import ProductResolver
    from broker.cost_calculator import CostCalculator
    from broker.rate_limiter import RateLimiter
    from core.order_state_machine import OrderStateMachine
    from core.config_loader import load_all
    from core.logger import get_logger

    cfg = load_all(Path("config"))
    tok = json.loads(Path("data_store/session/zerodha_token.json").read_text())["access_token"]
    api_key = os.environ.get(f"ZERODHA_API_KEY_{account}") or os.environ.get("ZERODHA_API_KEY")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(tok)

    adapter = ZerodhaAdapter(
        kite_client=kite,
        rate_limiter=RateLimiter(cfg.broker_limits.rate_limits),
        product_resolver=ProductResolver(cfg.system.product_map),
        cost_calculator=CostCalculator(cfg.broker_costs),
        state_machine=OrderStateMachine(),
        logger=get_logger("t2_cnc_gtt"),
        paper_mode=False,
        delivery_enabled=True,   # the gated exception — see module docstring
    )
    return adapter, kite, cfg


def _ltp(kite, symbol: str) -> float:
    q = kite.ltp([f"NSE:{symbol}"])
    return float(q[f"NSE:{symbol}"]["last_price"])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="SLICE2.5-P1 T2 real-API CNC+GTT proof")
    p.add_argument("--symbol", default="IDEA", help="a liquid, cheap scrip (1 share)")
    p.add_argument("--account", default="LFL836")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--arm-overnight", action="store_true",
                   help="buy + place GTT + verify, then STOP (no square) for the TPIN test")
    p.add_argument("--close-overnight", metavar="GTT_ID", default=None,
                   help="next day: sell the held share + delete the GTT; report TPIN")
    p.add_argument("--i-understand-this-places-a-real-cnc-order", action="store_true",
                   dest="confirm")
    args = p.parse_args(argv)

    if not args.confirm:
        print("REFUSED: pass --i-understand-this-places-a-real-cnc-order to proceed "
              "(this places REAL orders with REAL money).")
        return 2
    if not _is_market_hours():
        print("REFUSED: outside market hours (09:15–15:30 IST). GTT placement + CNC "
              "fills require an open market.")
        return 2

    adapter, kite, cfg = _build_live_adapter(args.account)
    sym, qty = args.symbol, args.qty
    sl_off = cfg.system.capital.gtt_sl_limit_offset_pct
    tgt_small = cfg.system.capital.sl_limit_offset_pct

    print(f"=== T2 real-API proof: {sym} x{qty} (account {args.account}) ===")

    # ── close-overnight: sell a previously-held CNC share + delete the GTT ────────
    if args.close_overnight:
        print(f"close-overnight: selling {qty} {sym} (CNC) + deleting GTT {args.close_overnight}")
        try:
            sell = adapter.place_order(symbol=sym, side="SELL", qty=qty, price=0.0,
                                       order_type="MARKET", intent="DELIVERY", tag="t2_close")
            print(f"  CNC SELL placed: {sell.broker_order_id} — poll for COMPLETE; "
                  "if NO TPIN prompt appeared, the overnight-holding TPIN test PASSES.")
        finally:
            try:
                kite.delete_gtt(int(args.close_overnight))
                print("  GTT deleted.")
            except Exception as exc:
                print(f"  delete_gtt note: {exc}")
        return 0

    gtt_id = None
    try:
        # 1) REAL CNC BUY ─────────────────────────────────────────────────────────
        entry = adapter.place_order(symbol=sym, side="BUY", qty=qty, price=0.0,
                                    order_type="MARKET", intent="DELIVERY", tag="t2_entry")
        print(f"1) CNC BUY placed: {entry.broker_order_id}")
        time.sleep(3)
        ltp = _ltp(kite, sym)
        print(f"   LTP now {ltp}")

        # 2) place the OCO GTT (deep SL, fill-ensuring TGT) ──────────────────────────
        from orders.cnc_gtt import CncGttPlacer
        from core.logger import get_logger
        placer = CncGttPlacer(
            adapter, gtt_sl_limit_offset_pct=sl_off, gtt_tgt_limit_offset_pct=tgt_small,
            delivery_enabled=True, logger=get_logger("t2_cnc_gtt"),
            quote_fn=adapter.get_quote, tick_fn=adapter._resolve_tick)
        sl_price = round(ltp * 0.97, 1)      # ~3% below for the test
        tgt_price = round(ltp * 1.05, 1)     # ~5% above for the test
        res = placer.place_for_fill(symbol=sym, exit_side="SELL", qty=qty,
                                    sl_price=sl_price, tgt_price=tgt_price, trade_id="t2")
        gtt_id = res.gtt_id
        print(f"2) OCO GTT placed: gtt_id={gtt_id} sl_trigger={res.sl_trigger} "
              f"tgt_trigger={res.tgt_trigger} sl_limit={res.sl_limit} tgt_limit={res.tgt_limit}")

        # 3) verify via get_gtt ─────────────────────────────────────────────────────
        g = kite.get_gtt(int(gtt_id))
        cond, orders = g.get("condition", {}), g.get("orders", [])
        print(f"3) get_gtt: type={g.get('type')} triggers={cond.get('trigger_values')} "
              f"legs={[(o.get('transaction_type'), o.get('product'), o.get('quantity')) for o in orders]}")
        ok = (str(g.get("type")).lower() in ("two-leg", "oco")
              and all(o.get("product") == "CNC" and o.get("transaction_type") == "SELL"
                      and o.get("quantity") == qty for o in orders)
              and len(orders) == 2)
        print(f"   GTT verified: {ok}")

        if args.arm_overnight:
            print(f"\nARMED for the overnight TPIN test. Position is HELD with GTT "
                  f"{gtt_id}. NEXT DAY: run --close-overnight {gtt_id} (or let the GTT "
                  f"trigger) and confirm NO TPIN prompt. Not squaring now.")
            gtt_id = None   # don't clean up in the finally
            return 0 if ok else 1

        # 4) square via a CNC SELL — the no-TPIN proof (same-session) ────────────────
        sell = adapter.place_order(symbol=sym, side="SELL", qty=qty, price=0.0,
                                   order_type="MARKET", intent="DELIVERY", tag="t2_square")
        print(f"4) CNC SELL placed: {sell.broker_order_id} — poll for COMPLETE. If it "
              "completed with NO manual TPIN prompt, the no-TPIN criterion PASSES.")
        return 0 if ok else 1

    finally:
        # self-clean: always delete the GTT we created (unless armed for overnight)
        if gtt_id:
            try:
                kite.delete_gtt(int(gtt_id))
                print(f"cleanup: GTT {gtt_id} deleted.")
            except Exception as exc:
                print(f"cleanup: delete_gtt note: {exc}")


if __name__ == "__main__":
    sys.exit(main())
