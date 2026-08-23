#!/usr/bin/env python
"""
scripts/t2_cnc_gtt_realtest.py — SLICE2.5-P1 T2: REAL-API proof (MARKET HOURS ONLY).

THE PRODUCTION BLOCKER, AS ORIGINALLY WRITTEN. NI-12 (23-Aug-2026): the clause saying
delivery_enabled stays false until this passes is STALE — delivery_enabled is TRUE (:102)
and delivery has traded. Whether this script ever passed is NOT recorded here; do not read
the flag being true as proof that it did. What follows still describes what it proves: a REAL CNC buy fills, a REAL OCO-GTT places + verifies, and a REAL
CNC sell of that share completes with NO manual CDSL TPIN/DDPI prompt.

⚠️  This places REAL orders with REAL money on the live account. Rama runs it MANUALLY
    during market hours. It is NEVER auto-run, never wired into a cron, never imported.
    It refuses without the explicit confirmation flag, refuses outside market hours.

ISOLATED DURABLE STATE (throwaway DB, 07-Jul): the CNC-GTT lifecycle (SLICE2.5-P2)
persists to a `gtt_state` table so the P2 pass criterion (a durable ACTIVE row that
mirrors the broker GTT) is verifiable. This proof MUST NOT touch the live
`trading_system.db`: it builds a fresh, THROWAWAY StateStore under
`data_store/t2_proof_<ts>/t2_proof.db` (its own subdir, so the sibling analytics.db is
isolated too — never the live `data_store/analytics.db`), seeds the FK parents
(signals -> trades, trade_id='t2') the `gtt_state` FK requires, and wires that store
into the CncGttPlacer. The prod store cannot be used: `gtt_state.trade_id` FKs to
`trades(trade_id)` with `PRAGMA foreign_keys=ON`, so a synthetic trade_id would FK-fail
(and would risk leaving a stale-ACTIVE row that the next real boot would hydrate).

SELF-SAFE ON EVERY EXIT PATH (ensure-flat, 30-Jun): no failure path leaves a naked or
unprotected CNC position. Specifically —
  • the square SELL is POLLED to COMPLETE *before* the GTT is deleted (the position is
    never momentarily unprotected);
  • the GTT is deleted ONLY when the position is confirmed FLAT — a rejected/failed
    square KEEPS the GTT (overnight protection) and flags the position for manual handling;
  • on ANY exit (incl. a mid-way exception, e.g. GTT placement fails after the buy),
    a held position is squared best-effort via `ensure_flat` (sells the actual broker
    net qty, so it can never accidentally go short), loudly logged.

MARKETABLE-LIMIT ORDERS (10-Jul): all 4 real placements (buy, square, ensure-flat,
close-overnight) are marketable LIMIT — fresh LTP ± EMERGENCY_EXIT_BUFFER_PCT (1%),
tick-snapped — NOT raw order_type="MARKET". Zerodha's API refuses MARKET without
market-protection (the 10-Jul canary block); a marketable LIMIT crosses the spread
(fills like a market) while capping worst-case slippage, mirroring the live system's
emergency exits (orders.price_math.marketable_limit_price). A missing fresh LTP aborts
that placement — never a MARKET fallback. The OCO-GTT legs are already trigger+LIMIT
(not MARKET), so they are unchanged.

USAGE (on the VM, market hours):
    cd /home/ubuntu/systems/trading-system && set -a && . ./.env && set +a
    PYTHONPATH=. /home/ubuntu/systems/venv/bin/python scripts/t2_cnc_gtt_realtest.py \
        --symbol IDEA --account LFL836 --i-understand-this-places-a-real-cnc-order

    Modes:
      (default)      single-session: BUY 1 CNC (confirm fill) → place OCO GTT →
                     get_gtt verify + durable gtt_state row (throwaway DB) → CNC SELL
                     square (poll COMPLETE = the no-TPIN proof) → delete the GTT only
                     once flat. ensure-flat on every exit.
      --dry-run      rehearsal: builds the PAPER adapter + throwaway store, places a
                     PAPER OCO GTT through the store-wired placer, verifies a gtt_state
                     row landed in the THROWAWAY DB, then cleans up. Places NO real
                     orders, touches NO live DB (no confirm flag / market-hours gate).
      --arm-overnight: BUY 1 CNC → place OCO GTT → verify → STOP (HOLD, do NOT square).
                     The definitive TPIN test: hold overnight, then NEXT DAY run
                     --close-overnight (or let the GTT trigger).
      --close-overnight <gtt_id>: NEXT DAY — sell the held 1 share (CNC), poll COMPLETE,
                     and delete the GTT ONLY if the sell completed (else KEEP the GTT).

PASS CRITERIA: real CNC buy filled · real OCO GTT placed + get_gtt shows product=CNC,
qty=1, two SELL legs, [SL,TGT] ascending · a durable ACTIVE gtt_state row mirrors it ·
the CNC sell completed (status COMPLETE) with ZERO manual TPIN intervention. Report the
result to Rama.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import time as dt_time
from pathlib import Path

_TERMINAL = {"COMPLETE", "REJECTED", "CANCELLED"}

# Fixed reference price for the PAPER --dry-run GTT so the store-wiring proof is
# deterministic and needs no live quote. sl/tgt straddle it for the C8 distance gate.
_PAPER_DRY_RUN_REF_PRICE = 100.0

# ─────────────────────────────────────────────────────────────────────────────
# ⭐ THE OCO BAND — ONE DEFINITION, THREE USE SITES (widened 27-Jul-2026, Rama).
#
# WHY IT WAS WIDENED, from -3%/+5%: a same-day trigger VOIDS the arm (a day trade
# means no demat debit, so nothing is proved) and costs a market day. Measured over
# 735 real symbol-days of 1-min candles (19-Jun..24-Jul-2026, 482 symbols): from an
# 11:30 anchor, P(touch -3% or +5% before close) = 33.9% overall and 52.2% for
# shares under Rs 150. At -10%/+10% that falls to ~9.0%, and combined with a ~13:00
# arm to ~2.8%. ⭐ Band width dominates every other lever AND costs zero exposure.
#
# ⛔ WHY NOT WIDER. Our own _validate_trigger_distance has a MINIMUM (0.25%) and NO
# maximum — but a BROKER-SIDE cap on trigger distance is UNVERIFIED from source. A
# GTT the broker REJECTS burns the day exactly as a triggered one does, so this is
# deliberately the SMALLEST band that solves the problem, not the safest-looking
# one. ⛔ Do not "improve" this to +/-20% without first proving the broker accepts
# it.
#
# ⭐ Widening breaks no assertion: broker_ok/store_ok check the two-leg/OCO shape,
# SELL+CNC+qty, and the durable gtt_state row. NEITHER LOOKS AT TRIGGER LEVELS --
# the structure is the proof, the levels are incidental. (Verified 27-Jul: no test
# in test_t2_limit_paths.py or test_t2_ensure_flat.py references either offset.)
#
# ⚠️ The arm TIME (~13:00) is NOT here and must not be: this script has no
# scheduler, only a 09:15-15:30 market-hours guard. The anchor is an OPERATOR
# instruction and lives in docs/T2_RUNBOOK_29-JUL.txt.
_OCO_SL_MULT = 0.90     # -10%
_OCO_TGT_MULT = 1.10    # +10%
# ─────────────────────────────────────────────────────────────────────────────

# Synthetic identity for the isolated proof (seeded into the throwaway DB's FK chain).
_T2_TRADE_ID = "t2"
_T2_SIGNAL_ID = "t2_signal"

# The live DBs the proof must NEVER touch (isolation guard).
_LIVE_MAIN_DB = Path("data_store/trading_system.db")
_LIVE_ANALYTICS_DB = Path("data_store/analytics.db")


def _is_market_hours() -> bool:
    from core.time_authority import now_ist
    now = now_ist().time()
    return dt_time(9, 15) <= now <= dt_time(15, 30)


def _paper_quote_provider(symbols):
    """Synthetic quote_provider for the PAPER adapter — paper get_quote requires one
    (else NotImplementedError). Returns a fixed reference price per symbol so the paper
    GTT path (place_for_fill's mandatory LTP + C8 straddle check) works with NO live
    broker session. Live mode passes quote_provider=None and uses the real kite quote."""
    from broker.zerodha_adapter import Quote
    from core.time_authority import now_ist
    ts = now_ist()
    return {
        s: Quote(symbol=s, last_price=_PAPER_DRY_RUN_REF_PRICE,
                 bid=_PAPER_DRY_RUN_REF_PRICE, ask=_PAPER_DRY_RUN_REF_PRICE,
                 volume=0, ts=ts)
        for s in symbols
    }


def _build_live_adapter(account: str, paper: bool = False):
    """A minimal adapter with delivery_enabled=True (this script is the gated
    exception that exercises the real GTT path before the master lock is flipped).
    paper=True (for --dry-run) simulates fills/GTT, places nothing real, and injects a
    synthetic quote_provider so the paper GTT path has its mandatory LTP."""
    import json, os
    from kiteconnect import KiteConnect
    from broker.zerodha_adapter import ZerodhaAdapter
    from broker.product_resolver import ProductResolver
    from broker.cost_calculator import CostCalculator
    from broker.rate_limiter import RateLimiter
    from broker.order_state_machine import OrderStateMachine   # drift #1: broker.*, not core.*
    from core.config_loader import load_all
    from core.logger import get_logger

    cfg = load_all(Path("config"))
    tok = json.loads(Path("data_store/session/zerodha_token.json").read_text())["access_token"]
    api_key = os.environ.get(f"ZERODHA_API_KEY_{account}") or os.environ.get("ZERODHA_API_KEY")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(tok)

    adapter = ZerodhaAdapter(
        kite_client=kite,
        # drift #2: RateLimiter takes the whole BrokerLimitsConfig (mirrors main.py:1640);
        # BrokerLimitsConfig has no `.rate_limits` attribute.
        rate_limiter=RateLimiter(cfg.broker_limits),
        product_resolver=ProductResolver(cfg.system.product_map),
        cost_calculator=CostCalculator(cfg.broker_costs),
        state_machine=OrderStateMachine(),
        logger=get_logger("t2_cnc_gtt"),
        paper_mode=paper,
        delivery_enabled=True,   # the gated exception — see module docstring
        quote_provider=_paper_quote_provider if paper else None,
    )
    return adapter, kite, cfg


# ── throwaway (ISOLATED) durable store for the gtt_state proof ────────────────────
def _throwaway_store_path() -> Path:
    """A fresh, per-run throwaway main-DB path in its OWN subdir. The subdir matters:
    StateStore ATTACHes an analytics.db that sits BESIDE the main DB
    (core.db_connect.analytics_path_for = parent/analytics.db), so a bare
    data_store/t2_proof_<ts>.db would ATTACH the LIVE data_store/analytics.db. The
    subdir isolates the analytics sibling too."""
    from core.time_authority import now_ist
    stamp = now_ist().strftime("%Y%m%d_%H%M%S")
    return Path("data_store") / f"t2_proof_{stamp}" / "t2_proof.db"


def _assert_isolated(db_path: Path) -> None:
    """Refuse to run if the store path (or its analytics sibling) could be a LIVE DB.
    The T2 proof MUST touch only a throwaway DB."""
    from core.db_connect import analytics_path_for
    resolved = db_path.resolve()
    resolved_analytics = analytics_path_for(db_path).resolve()
    if db_path.name == _LIVE_MAIN_DB.name or resolved == _LIVE_MAIN_DB.resolve():
        raise SystemExit(f"T2 isolation guard: refusing the live main DB ({resolved})")
    if resolved_analytics == _LIVE_ANALYTICS_DB.resolve():
        raise SystemExit(
            f"T2 isolation guard: the analytics sibling would be the LIVE analytics.db "
            f"({resolved_analytics}) — use a dedicated subdir")


def _open_throwaway_store(log):
    """Build a fresh, ISOLATED StateStore for the T2 proof (never the live DB) and seed
    the FK parents `gtt_state.trade_id` requires. Returns (store, db_path).

    FK chain (verified vs schema.sql, PRAGMA foreign_keys=ON):
      gtt_state.trade_id -> trades(trade_id) -> signals(signal_id).
    The 03-Jul spec said "seed one trades row"; the trades.signal_id NOT-NULL FK to
    signals means a signals PARENT row is required first, else the trades INSERT itself
    FK-fails. So we seed signals (trade_id=NULL) THEN trades (trade_id='t2')."""
    from core.state_store import StateStore
    from core.time_authority import now_ist

    db_path = _throwaway_store_path()
    _assert_isolated(db_path)
    store = StateStore(db_path)          # a fresh file self-migrates the full schema (v41)

    now = now_ist().isoformat()
    ref = _PAPER_DRY_RUN_REF_PRICE
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals "
            "(signal_id, symbol, scanner, strategy, triggered_at, received_at, "
            " expires_at, status, fingerprint, fingerprint_date) "
            "VALUES (?, 'IDEA', 't2_proof', 't2_proof', ?, ?, ?, 'PROCESSED', ?, ?)",
            (_T2_SIGNAL_ID, now, now, now, f"t2fp_{_T2_SIGNAL_ID}", now[:10]),
        )
        cur.execute(
            "INSERT INTO trades "
            "(trade_id, signal_id, symbol, direction, strategy, qty_planned, "
            " entry_target_price, sl_initial, tgt_initial, margin_reserved, "
            " risk_amount, created_at, status, order_protocol, updated_at) "
            "VALUES (?, ?, 'IDEA', 'LONG', 't2_proof', 1, ?, ?, ?, 0, 0, ?, 'OPEN', "
            " 'LIMIT_TRIPLE', ?)",
            (_T2_TRADE_ID, _T2_SIGNAL_ID, ref, round(ref * _OCO_SL_MULT, 1),
             round(ref * _OCO_TGT_MULT, 1), now, now),
        )
    log.info("t2: throwaway store ready at %s (seeded signals+trades trade_id=%s)",
             db_path, _T2_TRADE_ID)
    return store, db_path


def _ltp(kite, symbol: str) -> float:
    q = kite.ltp([f"NSE:{symbol}"])
    return float(q[f"NSE:{symbol}"]["last_price"])


def _marketable_limit(adapter, kite, symbol: str, side: str, log) -> float:
    """Fresh-LTP MARKETABLE LIMIT price — the live system's convention
    (``orders.price_math.marketable_limit_price`` + ``EMERGENCY_EXIT_BUFFER_PCT``, FIX-181):

        BUY  -> LTP + buffer (round UP)   -> crosses the spread, fills like a market buy
        SELL -> LTP - buffer (round DOWN) -> crosses the spread, fills like a market sell

    tick-snapped (Zerodha rejects off-tick). Zerodha's API REFUSES raw ``order_type=
    "MARKET"`` without market-protection (the 10-Jul canary block), so T2 places a
    marketable LIMIT instead — identical to the live emergency-exit path — which fills
    immediately while capping worst-case slippage at the buffer. A missing/invalid fresh
    LTP ABORTS this placement (raises) — it NEVER falls back to MARKET."""
    from orders.price_math import marketable_limit_price, EMERGENCY_EXIT_BUFFER_PCT
    try:
        ltp = _ltp(kite, symbol)
    except Exception as exc:  # noqa: BLE001 — a missing quote must abort, not MARKET
        raise RuntimeError(f"no fresh LTP for {symbol} ({exc}) -> refusing to place "
                           f"without a marketable limit (NO MARKET fallback)") from exc
    if not ltp or ltp <= 0:
        raise RuntimeError(f"non-positive LTP {ltp!r} for {symbol} -> refusing to place "
                           f"without a marketable limit (NO MARKET fallback)")
    tick = adapter._resolve_tick(symbol)   # fail-safe: DEFAULT_TICK when no cache wired
    px = marketable_limit_price(side, ltp, EMERGENCY_EXIT_BUFFER_PCT, tick)
    log.info("marketable LIMIT: %s %s ltp=%.2f -> limit=%.2f (buffer=%.2f%%, tick=%.2f)",
             side, symbol, ltp, px, EMERGENCY_EXIT_BUFFER_PCT * 100, tick)
    return px


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
        px = _marketable_limit(adapter, kite, symbol, "SELL", log)
        s = adapter.place_order(symbol=symbol, side="SELL", qty=held, price=px,
                                order_type="LIMIT", intent="DELIVERY", tag="t2_ensure_flat")
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


def _cancel_gtt_state(store, gtt_id, log) -> None:
    """Best-effort: mark the durable gtt_state row CANCELLED on cleanup so the throwaway
    store carries no stale-ACTIVE row (parity with prod SmartTgt/monitor lifecycle)."""
    try:
        from core.time_authority import now_ist
        store.set_gtt_state_status(int(gtt_id), "CANCELLED", now_ist().isoformat())
    except Exception as exc:  # noqa: BLE001
        log.error("cleanup: gtt_state CANCELLED note: %s", exc)


def run_single_session(adapter, kite, gtt_placer, store, symbol: str, qty: int, log,
                       *, arm_overnight: bool = False) -> int:
    """BUY → GTT → verify (broker + durable gtt_state) → (square|hold). Self-safe: GTT
    deleted ONLY when flat; held position squared on every exit. Returns 0 on PASS."""
    flat = True               # no position yet
    intentional_hold = False  # arm-overnight holds on purpose
    gtt_id = None
    ok = False
    try:
        # 1) REAL CNC BUY (marketable LIMIT — Zerodha refuses raw MARKET via API) + confirm fill ─
        buy_px = _marketable_limit(adapter, kite, symbol, "BUY", log)
        entry = adapter.place_order(symbol=symbol, side="BUY", qty=qty, price=buy_px,
                                    order_type="LIMIT", intent="DELIVERY", tag="t2_entry")
        log.info("1) CNC BUY placed: %s — confirming fill", entry.broker_order_id)
        buy_st = _poll_terminal(kite, entry.broker_order_id)
        if buy_st in ("REJECTED", "CANCELLED"):
            log.error("BUY %s — nothing held; aborting (no cleanup needed).", buy_st)
            return 1
        flat = False  # COMPLETE or UNKNOWN -> assume held; ensure_flat verifies net qty
        ltp = _ltp(kite, symbol)
        log.info("   BUY status=%s; LTP now %s", buy_st, ltp)

        # 2) place the OCO GTT (deep SL, fill-ensuring TGT) — the protection ─────────
        # ⭐ THE REAL PLACEMENT. This is the band that decides whether Wednesday's
        # arm survives to Thursday, or triggers same-day and voids itself.
        sl_price = round(ltp * _OCO_SL_MULT, 1)      # -10% below
        tgt_price = round(ltp * _OCO_TGT_MULT, 1)    # +10% above
        res = gtt_placer.place_for_fill(symbol=symbol, exit_side="SELL", qty=qty,
                                        sl_price=sl_price, tgt_price=tgt_price,
                                        trade_id=_T2_TRADE_ID)
        gtt_id = res.gtt_id
        log.info("2) OCO GTT placed: gtt_id=%s sl_trigger=%s tgt_trigger=%s sl_limit=%s tgt_limit=%s",
                 gtt_id, res.sl_trigger, res.tgt_trigger, res.sl_limit, res.tgt_limit)

        # 3) verify via get_gtt (broker) AND the durable gtt_state row (P2 criterion) ─
        g = kite.get_gtt(int(gtt_id))
        cond, orders = g.get("condition", {}), g.get("orders", [])
        log.info("3) get_gtt: type=%s triggers=%s legs=%s", g.get("type"),
                 cond.get("trigger_values"),
                 [(o.get("transaction_type"), o.get("product"), o.get("quantity")) for o in orders])
        broker_ok = (str(g.get("type")).lower() in ("two-leg", "oco")
                     and all(o.get("product") == "CNC" and o.get("transaction_type") == "SELL"
                             and o.get("quantity") == qty for o in orders)
                     and len(orders) == 2)
        active_row = store.get_active_gtt_for_trade(_T2_TRADE_ID)
        store_ok = (active_row is not None and str(active_row["gtt_id"]) == str(gtt_id)
                    and active_row["status"] == "ACTIVE" and int(active_row["qty"]) == qty)
        log.info("   verified: broker=%s durable_gtt_state(P2)=%s (row present=%s)",
                 broker_ok, store_ok, active_row is not None)
        ok = broker_ok and store_ok

        if arm_overnight:
            intentional_hold = True  # keep position + GTT for the overnight TPIN test
            log.info("ARMED for the overnight TPIN test. Position HELD with GTT %s. NEXT DAY: "
                     "--close-overnight %s (or let the GTT trigger). Not squaring now.", gtt_id, gtt_id)
            return 0 if ok else 1

        # 4) square via a CNC SELL (marketable LIMIT) — the no-TPIN proof — and CONFIRM COMPLETE ─
        sell_px = _marketable_limit(adapter, kite, symbol, "SELL", log)
        sell = adapter.place_order(symbol=symbol, side="SELL", qty=qty, price=sell_px,
                                   order_type="LIMIT", intent="DELIVERY", tag="t2_square")
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
                _cancel_gtt_state(store, gtt_id, log)  # keep the durable mirror consistent
            else:
                log.critical("cleanup: position NOT confirmed flat — GTT %s KEPT for protection; "
                             "MANUAL square required.", gtt_id)


def run_close_overnight(adapter, kite, symbol: str, qty: int, gtt_id_str: str, log) -> int:
    """NEXT-DAY: sell the held share, poll COMPLETE, delete the GTT ONLY if the sell
    completed (a failed/rejected sell KEEPS the GTT + flags for manual handling)."""
    log.info("close-overnight: selling %s %s (CNC), then conditionally deleting GTT %s",
             qty, symbol, gtt_id_str)
    try:
        px = _marketable_limit(adapter, kite, symbol, "SELL", log)
        sell = adapter.place_order(symbol=symbol, side="SELL", qty=qty, price=px,
                                   order_type="LIMIT", intent="DELIVERY", tag="t2_close")
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


def run_dry_run(adapter, kite, gtt_placer, store, store_path, symbol: str, qty: int, log) -> int:
    """Rehearsal — places NO real orders, touches NO live DB. Proves drift #3 + the
    throwaway-DB store wiring END-TO-END: the store-wired PAPER placer places a paper
    OCO GTT and a durable gtt_state row lands in the THROWAWAY DB; then verify + clean
    up. Returns 0 iff the store row + paper GTT + isolation all check out."""
    log.info("DRY-RUN: no real orders; PAPER adapter + THROWAWAY store only.")
    if not getattr(adapter, "_paper", False):
        log.critical("DRY-RUN abort: adapter is NOT in paper mode (would risk a real call).")
        return 1

    # (informational) best-effort real LTP for context — NEVER used for the proof.
    try:
        real_ltp = _ltp(kite, symbol)
        log.info("DRY-RUN: real LTP for %s = %s (context only).", symbol, real_ltp)
    except Exception as exc:  # noqa: BLE001
        log.info("DRY-RUN: real LTP fetch skipped (%s); proof uses synthetic %.2f.",
                 exc, _PAPER_DRY_RUN_REF_PRICE)

    ref = _PAPER_DRY_RUN_REF_PRICE
    # Same band as the live path, so a --dry-run rehearsal reports the parameters
    # the real run will actually use. A dry-run printing a different band would be
    # the same defect as a card describing parameters the script does not have.
    sl_price, tgt_price = round(ref * _OCO_SL_MULT, 1), round(ref * _OCO_TGT_MULT, 1)
    log.info("DRY-RUN PLAN: BUY %d %s CNC -> OCO GTT (SL~%s/TGT~%s straddling %.2f) -> "
             "verify durable gtt_state row in the throwaway DB -> square -> delete GTT "
             "once flat. ensure-flat squares any held position on every exit.",
             qty, symbol, sl_price, tgt_price, ref)

    # 1) exercise the STORE-WIRED placer (paper) -> writes the durable gtt_state row.
    res = gtt_placer.place_for_fill(symbol=symbol, exit_side="SELL", qty=qty,
                                    sl_price=sl_price, tgt_price=tgt_price,
                                    trade_id=_T2_TRADE_ID, tag="t2_dryrun")
    log.info("DRY-RUN: paper GTT placed gtt_id=%s (sl_trigger=%s tgt_trigger=%s)",
             res.gtt_id, res.sl_trigger, res.tgt_trigger)

    # 2) VERIFY the durable gtt_state row landed in the THROWAWAY DB (drift #3 proof).
    row = store.get_active_gtt_for_trade(_T2_TRADE_ID)
    active = store.get_active_gtt_states()
    store_ok = (row is not None and str(row["gtt_id"]) == str(res.gtt_id)
                and row["status"] == "ACTIVE" and int(row["qty"]) == qty
                and row["trade_id"] == _T2_TRADE_ID)
    log.info("DRY-RUN: gtt_state ACTIVE rows=%d; row-for-%s present=%s match=%s",
             len(active), _T2_TRADE_ID, row is not None, store_ok)

    # 3) VERIFY the paper broker GTT mirrors it (product=CNC, two SELL legs).
    g = adapter.get_gtt(int(res.gtt_id)) or {}
    orders = g.get("orders", [])
    gtt_ok = (len(orders) == 2 and all(o.get("product") == "CNC"
              and o.get("transaction_type") == "SELL" for o in orders))
    log.info("DRY-RUN: paper get_gtt legs=%s gtt_ok=%s",
             [(o.get("transaction_type"), o.get("product"), o.get("quantity")) for o in orders],
             gtt_ok)

    # 4) CLEANUP — mark the gtt_state row CANCELLED + drop the paper GTT (no stale ACTIVE).
    _cancel_gtt_state(store, res.gtt_id, log)
    try:
        adapter.delete_gtt(int(res.gtt_id))
    except Exception as exc:  # noqa: BLE001
        log.error("DRY-RUN: paper delete_gtt note: %s", exc)
    remaining = len(store.get_active_gtt_states())
    log.info("DRY-RUN: cleanup done; ACTIVE gtt_state rows now=%d", remaining)

    # 5) ISOLATION confirmations.
    resolved = store_path.resolve()
    isolated = (store_path.name != _LIVE_MAIN_DB.name
                and resolved != _LIVE_MAIN_DB.resolve())
    log.info("DRY-RUN ISOLATION: durable store db=%s (throwaway=%s); the live "
             "trading_system.db is NOT opened by this run.", resolved, isolated)

    ok = store_ok and gtt_ok and isolated and remaining == 0
    log.info("DRY-RUN RESULT: %s (store_ok=%s gtt_ok=%s isolated=%s clean=%s)",
             "PASS" if ok else "FAIL", store_ok, gtt_ok, isolated, remaining == 0)
    return 0 if ok else 1


def main(argv=None) -> int:
    from core.logger import get_logger
    log = get_logger("t2_cnc_gtt")

    p = argparse.ArgumentParser(description="SLICE2.5-P1 T2 real-API CNC+GTT proof")
    p.add_argument("--symbol", default="IDEA", help="a liquid, cheap scrip (1 share)")
    p.add_argument("--account", default="LFL836")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="rehearsal: paper adapter + throwaway store, prove the gtt_state "
                        "wiring; place NOTHING, touch NO live DB")
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

    # close-overnight sells + deletes a GTT by id; it needs no placer/durable store.
    if args.close_overnight:
        return run_close_overnight(adapter, kite, sym, qty, args.close_overnight, log)

    # Build the THROWAWAY (isolated) durable store + seed its FK chain + the STORE-WIRED
    # placer (drift #3: store= was omitted -> None -> no gtt_state row). Used by BOTH
    # the dry-run proof and the live single-session proof so the P2 lifecycle is
    # verifiable without ever touching the live trading_system.db.
    from orders.cnc_gtt import CncGttPlacer
    store, store_path = _open_throwaway_store(log)
    try:
        gtt_placer = CncGttPlacer(
            adapter, gtt_sl_limit_offset_pct=sl_off, gtt_tgt_limit_offset_pct=tgt_small,
            delivery_enabled=True, logger=log,
            quote_fn=adapter.get_quote, tick_fn=adapter._resolve_tick,
            store=store,   # SLICE2.5-P2 durable gtt_state persistence (drift #3 fix)
        )
        gtt_placer.hydrate_from_store()   # mirrors main.py; 0 on a fresh throwaway store

        if args.dry_run:
            return run_dry_run(adapter, kite, gtt_placer, store, store_path, sym, qty, log)
        return run_single_session(adapter, kite, gtt_placer, store, sym, qty, log,
                                  arm_overnight=args.arm_overnight)
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
