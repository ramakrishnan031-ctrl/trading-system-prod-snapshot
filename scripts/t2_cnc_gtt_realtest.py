#!/usr/bin/env python
"""
scripts/t2_cnc_gtt_realtest.py — SLICE2.5-P1 T2: REAL-API proof (MARKET HOURS ONLY).

THE PRODUCTION BLOCKER. Phase 1 is NOT done — and delivery_enabled stays false —
until this passes: a REAL CNC buy fills, a REAL OCO-GTT places + verifies, and a REAL
CNC sell of that share completes with NO manual CDSL TPIN/DDPI prompt.

⚠️  This places REAL orders with REAL money on the live account. Rama runs it MANUALLY
    during market hours. It is NEVER auto-run, never wired into a cron, never imported.
    It refuses without the explicit confirmation flag, refuses outside market hours.

SELF-SAFE ON EVERY EXIT PATH (ensure-flat, 30-Jun): no failure path leaves a naked or
unprotected CNC position. Specifically —
  • the square SELL is POLLED to COMPLETE *before* the GTT is deleted (the position is
    never momentarily unprotected);
  • the GTT is deleted ONLY when the position is confirmed FLAT — a rejected/failed
    square KEEPS the GTT (overnight protection) and flags the position for manual handling;
  • on ANY exit (incl. a mid-way exception, e.g. GTT placement fails after the buy),
    a held position is squared best-effort via `ensure_flat` (sells the actual broker
    net qty, so it can never accidentally go short), loudly logged.

USAGE (on the VM, market hours):
    cd /home/ubuntu/systems/trading-system && set -a && . ./.env && set +a
    PYTHONPATH=. /home/ubuntu/systems/venv/bin/python scripts/t2_cnc_gtt_realtest.py \
        --symbol IDEA --account LFL836 --i-understand-this-places-a-real-cnc-order

    Modes:
      (default)      single-session: BUY 1 CNC (confirm fill) → place OCO GTT →
                     get_gtt verify → CNC SELL square (poll COMPLETE = the no-TPIN
                     proof) → delete the GTT only once flat. ensure-flat on every exit.
      --dry-run      rehearsal: builds the adapter + reads LTP + prints the plan;
                     places NO real orders (no confirm flag / market-hours gate needed).
      --arm-overnight: BUY 1 CNC → place OCO GTT → verify → STOP (HOLD, do NOT square).
                     The definitive TPIN test: hold overnight, then NEXT DAY run
                     --close-overnight (or let the GTT trigger).
      --close-overnight <gtt_id>: NEXT DAY — sell the held 1 share (CNC), poll COMPLETE,
                     and delete the GTT ONLY if the sell completed (else KEEP the GTT).

PASS CRITERIA: real CNC buy filled · real OCO GTT placed + get_gtt shows product=CNC,
qty=1, two SELL legs, [SL,TGT] ascending · the CNC sell completed (status COMPLETE)
with ZERO manual TPIN intervention. Report the result to Rama.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import time as dt_time

_TERMINAL = {"COMPLETE", "REJECTED", "CANCELLED"}


def _is_market_hours() -> bool:
    from core.time_authority import now_ist
    now = now_ist().time()
    return dt_time(9, 15) <= now <= dt_time(15, 30)


def _build_live_adapter(account: str, paper: bool = False):
    """A minimal adapter with delivery_enabled=True (this script is the gated
    exception that exercises the real GTT path before the master lock is flipped).
    paper=True (for --dry-run) simulates fills/GTT and places nothing real."""
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
        paper_mode=paper,
        delivery_enabled=True,   # the gated exception — see module docstring
    )
    return adapter, kite, cfg


def _ltp(kite, symbol: str) -> float:
    q = kite.ltp([f"NSE:{symbol}"])
    return float(q[f"NSE:{symbol}"]["last_price"])


def _poll_terminal(kite, order_id, polls: int = 12, gap: float = 2.0) -> str:
    """Poll order_history until the status is terminal (COMPLETE/REJECTED/CANCELLED)
    or the budget is exhausted. Returns the last-seen status (UPPER), 'UNKNOWN' if
    never observed. No wall-clock dependency — a fixed poll budget."""
    last = "UNKNOWN"
    for i in range(polls):
        try:
            hist = kite.order_history(order_id)
            if hist:
                last = str(hist[-1].get("status", "UNKNOWN")).upper()
        except Exception:
            pass
        if last in _TERMINAL:
            return last
        if i < polls - 1:
            time.sleep(gap)
    return last


def _net_qty(kite, symbol: str) -> int:
    """Best-effort broker net day-qty for NSE:symbol (positive = long held). 0 on
    error / not found — so ensure_flat sells only what is actually held (never shorts)."""
    try:
        day = (kite.positions() or {}).get("day", []) or []
        for p in day:
            if p.get("tradingsymbol") == symbol:
                return int(p.get("quantity", 0))
    except Exception:
        pass
    return 0


def ensure_flat(adapter, kite, symbol: str, log) -> tuple[bool, str]:
    """Square whatever long qty is ACTUALLY held (never short). Returns (flat, detail).
    Used on every exit path so no held position is ever left un-squared."""
    held = _net_qty(kite, symbol)
    if held <= 0:
        return True, f"already flat (broker net qty={held})"
    try:
        s = adapter.place_order(symbol=symbol, side="SELL", qty=held, price=0.0,
                                order_type="MARKET", intent="DELIVERY", tag="t2_ensure_flat")
        st = _poll_terminal(kite, s.broker_order_id)
        if st == "COMPLETE":
            return True, f"ensure-flat SELL {s.broker_order_id} COMPLETE (squared qty={held})"
        return False, (f"ensure-flat SELL {s.broker_order_id} status={st} — "
                       f"MANUAL square of {held} {symbol} REQUIRED")
    except Exception as exc:  # noqa: BLE001
        return False, f"ensure-flat SELL FAILED ({exc}) — MANUAL square of {held} {symbol} REQUIRED"


def _delete_gtt(kite, gtt_id, log) -> None:
    try:
        kite.delete_gtt(int(gtt_id))
        log.info("cleanup: GTT %s deleted (position flat).", gtt_id)
    except Exception as exc:  # noqa: BLE001
        log.error("cleanup: delete_gtt note: %s", exc)


def run_single_session(adapter, kite, gtt_placer, symbol: str, qty: int, log,
                       *, arm_overnight: bool = False) -> int:
    """BUY → GTT → verify → (square|hold). Self-safe: GTT deleted ONLY when flat;
    held position squared on every exit. Returns 0 on PASS, non-zero otherwise."""
    flat = True               # no position yet
    intentional_hold = False  # arm-overnight holds on purpose
    gtt_id = None
    ok = False
    try:
        # 1) REAL CNC BUY + confirm the fill ────────────────────────────────────────
        entry = adapter.place_order(symbol=symbol, side="BUY", qty=qty, price=0.0,
                                    order_type="MARKET", intent="DELIVERY", tag="t2_entry")
        log.info("1) CNC BUY placed: %s — confirming fill", entry.broker_order_id)
        buy_st = _poll_terminal(kite, entry.broker_order_id)
        if buy_st in ("REJECTED", "CANCELLED"):
            log.error("BUY %s — nothing held; aborting (no cleanup needed).", buy_st)
            return 1
        flat = False  # COMPLETE or UNKNOWN -> assume held; ensure_flat verifies net qty
        ltp = _ltp(kite, symbol)
        log.info("   BUY status=%s; LTP now %s", buy_st, ltp)

        # 2) place the OCO GTT (deep SL, fill-ensuring TGT) — the protection ─────────
        sl_price = round(ltp * 0.97, 1)      # ~3% below for the test
        tgt_price = round(ltp * 1.05, 1)     # ~5% above for the test
        res = gtt_placer.place_for_fill(symbol=symbol, exit_side="SELL", qty=qty,
                                        sl_price=sl_price, tgt_price=tgt_price, trade_id="t2")
        gtt_id = res.gtt_id
        log.info("2) OCO GTT placed: gtt_id=%s sl_trigger=%s tgt_trigger=%s sl_limit=%s tgt_limit=%s",
                 gtt_id, res.sl_trigger, res.tgt_trigger, res.sl_limit, res.tgt_limit)

        # 3) verify via get_gtt ──────────────────────────────────────────────────────
        g = kite.get_gtt(int(gtt_id))
        cond, orders = g.get("condition", {}), g.get("orders", [])
        log.info("3) get_gtt: type=%s triggers=%s legs=%s", g.get("type"),
                 cond.get("trigger_values"),
                 [(o.get("transaction_type"), o.get("product"), o.get("quantity")) for o in orders])
        ok = (str(g.get("type")).lower() in ("two-leg", "oco")
              and all(o.get("product") == "CNC" and o.get("transaction_type") == "SELL"
                      and o.get("quantity") == qty for o in orders)
              and len(orders) == 2)
        log.info("   GTT verified: %s", ok)

        if arm_overnight:
            intentional_hold = True  # keep position + GTT for the overnight TPIN test
            log.info("ARMED for the overnight TPIN test. Position HELD with GTT %s. NEXT DAY: "
                     "--close-overnight %s (or let the GTT trigger). Not squaring now.", gtt_id, gtt_id)
            return 0 if ok else 1

        # 4) square via a CNC SELL — the no-TPIN proof — and CONFIRM COMPLETE ─────────
        sell = adapter.place_order(symbol=symbol, side="SELL", qty=qty, price=0.0,
                                   order_type="MARKET", intent="DELIVERY", tag="t2_square")
        sell_st = _poll_terminal(kite, sell.broker_order_id)
        log.info("4) CNC SELL placed: %s status=%s", sell.broker_order_id, sell_st)
        if sell_st == "COMPLETE":
            flat = True
            log.info("   square COMPLETE with NO manual TPIN intervention => no-TPIN criterion PASSES")
        else:
            # SELL-REJECT: DDPI/TPIN may be unauthorised. KEEP the GTT (protection); flag manual.
            log.critical("   square SELL status=%s — DDPI/TPIN may be unauthorised. KEEPING the GTT "
                         "(overnight protection) + flagging for MANUAL handling.", sell_st)
        return 0 if (ok and flat) else 1

    finally:
        # (A) ensure-flat: a still-held position on ANY exit path is squared (loud).
        if not flat and not intentional_hold:
            done, detail = ensure_flat(adapter, kite, symbol, log)
            (log.info if done else log.critical)("ensure-flat on exit: %s", detail)
            if done:
                flat = True
        # (B) delete the GTT ONLY when confirmed flat; a held/failed-square KEEPS it.
        if gtt_id and not intentional_hold:
            if flat:
                _delete_gtt(kite, gtt_id, log)
            else:
                log.critical("cleanup: position NOT confirmed flat — GTT %s KEPT for protection; "
                             "MANUAL square required.", gtt_id)


def run_close_overnight(adapter, kite, symbol: str, qty: int, gtt_id_str: str, log) -> int:
    """NEXT-DAY: sell the held share, poll COMPLETE, delete the GTT ONLY if the sell
    completed (a failed/rejected sell KEEPS the GTT + flags for manual handling)."""
    log.info("close-overnight: selling %s %s (CNC), then conditionally deleting GTT %s",
             qty, symbol, gtt_id_str)
    try:
        sell = adapter.place_order(symbol=symbol, side="SELL", qty=qty, price=0.0,
                                   order_type="MARKET", intent="DELIVERY", tag="t2_close")
        st = _poll_terminal(kite, sell.broker_order_id)
    except Exception as exc:  # noqa: BLE001
        log.critical("close-overnight SELL FAILED (%s) — GTT %s KEPT (protection); MANUAL handling.",
                     exc, gtt_id_str)
        return 1
    if st == "COMPLETE":
        log.info("close-overnight SELL %s COMPLETE (no TPIN prompt => PASS). Deleting GTT %s.",
                 sell.broker_order_id, gtt_id_str)
        _delete_gtt(kite, gtt_id_str, log)
        return 0
    log.critical("close-overnight SELL status=%s — DDPI/TPIN may be unauthorised. GTT %s KEPT; "
                 "MANUAL handling.", st, gtt_id_str)
    return 1


def run_dry_run(kite, symbol: str, qty: int, sl_off: float, tgt_small: float, log) -> int:
    """Rehearsal: read LTP + print the plan. Places NO real orders."""
    log.info("DRY-RUN: no real orders will be placed.")
    try:
        ltp = _ltp(kite, symbol)
    except Exception as exc:  # noqa: BLE001
        ltp = 0.0
        log.warning("DRY-RUN: LTP fetch failed (%s); using 0.0 for the plan.", exc)
    sl_price = round(ltp * 0.97, 1)
    tgt_price = round(ltp * 1.05, 1)
    log.info("DRY-RUN PLAN (LTP=%s): BUY %d %s CNC MARKET (confirm fill) -> place OCO GTT "
             "(SL~%s / TGT~%s) -> get_gtt verify -> CNC SELL %d square (poll COMPLETE = no-TPIN "
             "proof) -> delete GTT only once flat.", ltp, qty, symbol, sl_price, tgt_price, qty)
    log.info("DRY-RUN: on the real run, ensure-flat squares any held position on every exit and "
             "the GTT is NEVER deleted on a failed square.")
    return 0


def main(argv=None) -> int:
    from core.logger import get_logger
    log = get_logger("t2_cnc_gtt")

    p = argparse.ArgumentParser(description="SLICE2.5-P1 T2 real-API CNC+GTT proof")
    p.add_argument("--symbol", default="IDEA", help="a liquid, cheap scrip (1 share)")
    p.add_argument("--account", default="LFL836")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="rehearsal: build adapter + read LTP + print plan; place NOTHING")
    p.add_argument("--arm-overnight", action="store_true",
                   help="buy + place GTT + verify, then STOP (no square) for the TPIN test")
    p.add_argument("--close-overnight", metavar="GTT_ID", default=None,
                   help="next day: sell the held share + delete the GTT only if the sell completed")
    p.add_argument("--i-understand-this-places-a-real-cnc-order", action="store_true",
                   dest="confirm")
    args = p.parse_args(argv)

    # Guards (UNCHANGED) — bypassed only for --dry-run (which places nothing).
    if not args.dry_run:
        if not args.confirm:
            print("REFUSED: pass --i-understand-this-places-a-real-cnc-order to proceed "
                  "(this places REAL orders with REAL money). Or use --dry-run to rehearse.")
            return 2
        if not _is_market_hours():
            print("REFUSED: outside market hours (09:15–15:30 IST). GTT placement + CNC "
                  "fills require an open market.")
            return 2

    adapter, kite, cfg = _build_live_adapter(args.account, paper=args.dry_run)
    sym, qty = args.symbol, args.qty
    sl_off = cfg.system.capital.gtt_sl_limit_offset_pct
    tgt_small = cfg.system.capital.sl_limit_offset_pct
    print(f"=== T2 real-API proof: {sym} x{qty} (account {args.account})"
          f"{' [DRY-RUN]' if args.dry_run else ''} ===")

    if args.dry_run:
        return run_dry_run(kite, sym, qty, sl_off, tgt_small, log)

    if args.close_overnight:
        return run_close_overnight(adapter, kite, sym, qty, args.close_overnight, log)

    from orders.cnc_gtt import CncGttPlacer
    gtt_placer = CncGttPlacer(
        adapter, gtt_sl_limit_offset_pct=sl_off, gtt_tgt_limit_offset_pct=tgt_small,
        delivery_enabled=True, logger=log,
        quote_fn=adapter.get_quote, tick_fn=adapter._resolve_tick)
    return run_single_session(adapter, kite, gtt_placer, sym, qty, log,
                              arm_overnight=args.arm_overnight)


if __name__ == "__main__":
    sys.exit(main())
