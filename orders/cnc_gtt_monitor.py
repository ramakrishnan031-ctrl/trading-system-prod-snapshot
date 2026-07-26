"""
orders/cnc_gtt_monitor.py — SLICE2.5-P2 (25-Jun-2026): durable CNC overnight-GTT reconcile.

THE shared routine (startup [4a] + 15-min monitor [4b], DRY) that keeps a delivery
position's ONE broker-side OCO GTT in sync with reality. The BROKER GTT is the
authority; ``gtt_state`` is the durable local mirror.

Per ACTIVE gtt_state row it gathers the live broker GTT (get_gtts) + the held qty
(holdings + same-day CNC positions) and applies the M1 (qty) + M2 (one ACTIVE GTT)
invariants, then acts per the K6 ladder:

  GTT active + holding + qty match     -> healthy (touch last_verified_at)
  GTT triggered + holding FLAT         -> GTT_EXIT: finalise trade (3.5) — the PRIMARY
                                          delivery exit path, SYSTEM-OWNED (not human)
  GTT triggered + holding STILL > 0    -> F6 CRITICAL re-protect the remaining qty
  GTT missing + holding + qty match    -> AUTO-RECREATE in-hours (Y3 fresh LTP) /
                                          QUEUE pre-open (Y1) + WARN
  QTY MISMATCH (held != row.qty, > 0)  -> CRITICAL ONCE (Y2 needs_review) + cancel the
                                          wrong-qty GTT + NO recreate + NO soft-kill
  GTT active + holding FLAT            -> orphan: forensic-log + delete_gtt + finalise
  >1 ACTIVE GTT for one trade          -> SOFT-KILL (ownership ambiguity)

Parity: paper backs get_gtts/holdings/place_gtt/delete_gtt with an in-memory store,
so the whole routine runs end-to-end in paper (Y7 injected-state tests). Y4: a broker
gather failure DEFERS the cycle + alerts — it never crashes and never treats "no data"
as "no positions". delivery_enabled stays false in Phase 2 (durability + safety only).

⚠️ ONE THING PAPER CANNOT DO, AND IT SAYS SO (26-Jul-2026): the paper stores are
in-memory and die with the nightly restart while `gtt_state` survives, so the morning
after a paper carry this routine emits a clean GTT_EXIT for a position that was never
held. `_announce_paper_carry_blind_spot` states that BEFORE the misleading exit rather
than after it — paper-only, by a mode check. The carry is a live-only proof; see
docs/audit/paper_overnight_carry_26jul2026.md.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from broker.cost_calculator import round_trip_costs_or_zero
from core.constants import PRODUCT_TO_INTENT
from core.events import PositionClosed
from core.time_authority import now_ist

# 50-cap guard (Step 5): Kite allows ~50 active GTTs per account.
_GTT_CAP_MAX = 50
_GTT_CAP_WARN = 45


class CncGttMonitor:
    """Reconciles every system OCO-GTT against the broker. Used by the reconciler at
    startup and on the 15-min in-hours cadence (Step 4 wires the cadence)."""

    def __init__(
        self,
        *,
        store: Any,
        adapter: Any,
        placer: Any,
        fund_manager: Any,
        kill_switch: Any,
        notifier: Any,
        bus: Any,
        logger: Any,
        mode: str = "LIVE",
        market_hours_fn: Optional[Callable[[], bool]] = None,
        cost_calculator: Optional[Any] = None,  # E4: real costs on GTT closes
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._placer = placer
        self._fm = fund_manager
        self._ks = kill_switch
        self._notifier = notifier
        self._bus = bus
        self._log = logger
        self._mode = mode
        # E4 (2026-07-17): the SAME mode-agnostic CostCalculator instance the
        # normal exit path uses, so a GTT close writes a NET pnl_delta too.
        # None (unwired) degrades to costs=0.0 loudly — the pre-E4 behaviour.
        self._cost_calculator = cost_calculator
        # in-hours predicate (Y1 pre-open vs in-hours recreate). Default: always
        # in-hours (so a missing-market-window in tests doesn't block recreate).
        self._in_hours = market_hours_fn or (lambda: True)
        # Y1: trades whose GTT was found missing PRE-OPEN, queued to recreate on the
        # FIRST in-hours cycle. spec keyed by trade_id -> recreate spec.
        self._preopen_queue: Dict[str, dict] = {}
        # FIX-183: gtt_ids already WARNED about as an UNADOPTABLE orphan (0 / >1 open
        # delivery trades, trade already protected, or unreadable). The adoption
        # prepass runs every reconcile cycle (~15s) — alert ONCE per gtt_id per
        # process so it never spams (esp. for benign human GTTs that match the
        # 0-trades case). Mirrors the reconciler's FIX-182 once-per-symbol discipline.
        self._adopt_warned: set = set()
        # Paper blind-spot announcement (26-Jul-2026): dates already announced, so a
        # row latched by needs_review cannot re-announce every 15-minute cycle. Same
        # once-per-thing discipline as _adopt_warned above.
        self._paper_carry_warned: set = set()
        self._lock = threading.Lock()

    # ── public ──────────────────────────────────────────────────────────────
    def reconcile(self, *, in_hours: Optional[bool] = None) -> List[str]:
        """Run ONE reconcile pass. Returns a list of action labels (for logging/tests).
        Safe to call from startup (pre-open OK) and the 15-min in-hours cadence."""
        if in_hours is None:
            in_hours = bool(self._in_hours())

        actions: List[str] = []

        # Y1: drain the pre-open re-placement queue on the FIRST in-hours cycle,
        # BEFORE the broker gather, so the main loop sees the freshly-recreated GTTs
        # as healthy rather than "missing" off a now-stale snapshot.
        if in_hours and self._preopen_queue:
            actions.extend(self._drain_preopen())

        gathered = self._gather()
        if gathered is None:
            # Y4: broker unavailable -> defer + alert, never act on no-data.
            return [*actions, "deferred:broker_unavailable"]
        broker_gtts, held_qty = gathered

        rows = self._store.get_active_gtt_states()

        # Say what this pass CANNOT prove, BEFORE it produces artefacts that look
        # exactly like proof (paper only; see the method).
        actions.extend(self._announce_paper_carry_blind_spot(rows))

        # M2: >1 ACTIVE GTT for one trade -> ownership ambiguity -> SOFT-KILL.
        by_trade: Dict[str, list] = {}
        for r in rows:
            by_trade.setdefault(r["trade_id"], []).append(r)
        for trade_id, trows in by_trade.items():
            if len(trows) > 1:
                actions.append(self._soft_kill(
                    f">1 ACTIVE GTT for trade {trade_id} "
                    f"(gtt_ids={[t['gtt_id'] for t in trows]}) — ownership ambiguity"))

        for r in rows:
            if len(by_trade.get(r["trade_id"], [])) > 1:
                continue  # handled by the M2 soft-kill above
            actions.append(self._handle_row(r, broker_gtts, held_qty, in_hours))

        # Step 5: orphan sweep + 50-cap guard (operates on the broker GTT list).
        actions.extend(self._orphan_sweep_and_cap(broker_gtts, rows))
        return actions

    # ── FIX-183: orphan-GTT ADOPTION (reconcile_once prepass) ───────────────────
    def adopt_orphan_gtts(self) -> List[str]:
        """Reconstruct any LIVE broker GTT (status 'active') that has NO gtt_state
        row, correlating it to its open delivery trade — restoring the locked
        "broker GTT = authority, gtt_state = self-healing mirror" principle.

        Wired as a NARROW prepass at the top of order_reconciler._reconcile() so an
        adopted row excludes the carried CNC trade from CHECK1 BEFORE CHECK1 can
        mis-mark it CLOSED_MANUAL (the C2.1 gap: a carried holding lives in
        holdings(), not positions(), so the reconciler's bp is None).

        Adoption ONLY ever INSERTs a gtt_state row or WARNs — it NEVER deletes a
        live GTT and never touches an intraday position. Fail-safe: a broker gather
        failure defers (never crashes, never treats no-data as no-GTTs). Parity:
        paper get_gtts() returns the identical dict shape. Returns action labels."""
        try:
            gtts = self._adapter.get_gtts() or []
        except Exception as exc:  # noqa: BLE001 — broker down -> skip this prepass
            self._log.error("cnc_gtt_adoption: get_gtts failed: %s", exc)
            return ["adopt_deferred:broker_unavailable"]

        out: List[str] = []
        delivery_by_symbol: Optional[Dict[str, list]] = None  # lazy (only if needed)

        for g in gtts:
            if not isinstance(g, dict):
                continue
            if str(g.get("status", "")).lower() != "active":
                continue  # only LIVE protection is adoptable (not triggered/cancelled/…)
            gid = g.get("id")
            if gid is None:
                continue
            if self._store.get_gtt_state_by_id(gid) is not None:
                continue  # already tracked (any status) — not an orphan

            recon = self._reconstruct_from_broker_gtt(g)
            if recon is None:
                out.append(self._warn_adopt_once(
                    gid, f"adopt_unreadable:{gid}",
                    "GTT adoption skipped — unreadable GTT",
                    f"Active broker GTT {gid} has no parseable OCO legs/triggers; "
                    f"left alone (no gtt_state row created)."))
                continue
            symbol = recon["symbol"]

            if delivery_by_symbol is None:
                delivery_by_symbol = self._delivery_open_trades_by_symbol()
            candidates = delivery_by_symbol.get(symbol, [])

            if len(candidates) == 0:
                out.append(self._warn_adopt_once(
                    gid, f"adopt_no_trade:{symbol}",
                    f"Orphan GTT — no open delivery trade ({symbol})",
                    f"Active broker GTT {gid} for {symbol} has no gtt_state row and "
                    f"no OPEN/PARTIAL CNC trade to adopt onto; left alone for review."))
                continue
            if len(candidates) > 1:
                out.append(self._warn_adopt_once(
                    gid, f"adopt_ambiguous:{symbol}",
                    f"Orphan GTT — ambiguous match ({symbol})",
                    f"Active broker GTT {gid} for {symbol} matches {len(candidates)} "
                    f"open CNC trades; NOT auto-adopted (manual review)."))
                continue

            trade = candidates[0]
            trade_id = trade["trade_id"]
            if self._store.get_active_gtt_for_trade(trade_id) is not None:
                out.append(self._warn_adopt_once(
                    gid, f"adopt_already_active:{symbol}",
                    f"Orphan GTT — trade already protected ({symbol})",
                    f"Active broker GTT {gid} for {symbol} (trade {trade_id}) but the "
                    f"trade already holds an ACTIVE gtt_state row; NOT adopted (two "
                    f"live GTTs — the monitor's M2/ownership path handles it)."))
                continue

            try:
                self._store.insert_gtt_state(
                    gtt_id=recon["gtt_id"], trade_id=trade_id, symbol=symbol,
                    exit_side=recon["exit_side"], qty=recon["qty"],
                    sl_trigger=recon["sl_trigger"], sl_limit=recon["sl_limit"],
                    tgt_trigger=recon["tgt_trigger"], tgt_limit=recon["tgt_limit"],
                    created_at=self._now(),
                )
            except Exception as exc:  # noqa: BLE001 — never crash the prepass
                self._log.error("cnc_gtt_adoption: insert failed for %s/%s: %s",
                                symbol, gid, exc)
                out.append(f"adopt_insert_failed:{symbol}")
                continue
            self._adopt_warned.discard(gid)  # adopted -> clear any stale warn latch
            self._log.info("cnc_gtt_adoption.adopted", extra={
                "gtt_id": recon["gtt_id"], "trade_id": trade_id, "symbol": symbol,
                "qty": recon["qty"], "exit_side": recon["exit_side"],
                "sl_trigger": recon["sl_trigger"], "sl_limit": recon["sl_limit"],
                "tgt_trigger": recon["tgt_trigger"], "tgt_limit": recon["tgt_limit"]})
            out.append(f"adopted:{symbol}")
        return out

    @staticmethod
    def _reconstruct_from_broker_gtt(g: dict) -> Optional[dict]:
        """Derive every gtt_state field (except trade_id) from a broker GTT dict.
        SL/TGT are split by trigger MAGNITUDE (lower trigger = SL leg, higher =
        TGT leg) — robust to any broker-side leg reordering. trigger_values and
        orders are parallel (index i ↔ leg i). Returns None if unparseable."""
        cond = g.get("condition") or {}
        symbol = cond.get("tradingsymbol")
        tvs = cond.get("trigger_values") or []
        orders = g.get("orders") or []
        if not symbol or len(tvs) < 2 or len(orders) < 2:
            return None
        try:
            paired = sorted(zip((float(t) for t in tvs), orders), key=lambda p: p[0])
            (sl_trigger, sl_leg), (tgt_trigger, tgt_leg) = paired[0], paired[-1]
            sl_limit = float(sl_leg.get("price"))
            tgt_limit = float(tgt_leg.get("price"))
            qty = int(sl_leg.get("quantity") or tgt_leg.get("quantity") or 0)
            exit_side = sl_leg.get("transaction_type") or tgt_leg.get("transaction_type")
        except (TypeError, ValueError, AttributeError):
            return None
        if not exit_side or qty <= 0:
            return None
        return {
            "gtt_id": g.get("id"), "symbol": symbol, "exit_side": exit_side, "qty": qty,
            "sl_trigger": sl_trigger, "sl_limit": sl_limit,
            "tgt_trigger": tgt_trigger, "tgt_limit": tgt_limit,
        }

    def _delivery_open_trades_by_symbol(self) -> Dict[str, list]:
        """Index OPEN/PARTIAL delivery (CNC) trades by symbol via get_all_open_trades
        (which carries the ENTRY-leg product through its LEFT JOIN). DUPLICATE_SYMBOL
        guarantees ≤1 open trade per symbol, so each list is normally 0 or 1."""
        idx: Dict[str, list] = {}
        try:
            rows = self._store.get_all_open_trades() or []
        except Exception as exc:  # noqa: BLE001
            self._log.error("cnc_gtt_adoption: get_all_open_trades failed: %s", exc)
            return idx
        for t in rows:
            try:
                product = str(t["product"] or "").upper()
            except (KeyError, IndexError, TypeError):
                product = ""
            if PRODUCT_TO_INTENT.get(product, "") != "DELIVERY":
                continue
            idx.setdefault(t["symbol"], []).append(t)
        return idx

    def _warn_adopt_once(self, gid, label: str, title: str, body: str) -> str:
        """WARN about an unadoptable orphan GTT ONCE per gtt_id per process (the
        prepass runs every cycle — this prevents alert spam, esp. for benign human
        GTTs that match the 0-trades case). Always returns the action label."""
        if gid not in self._adopt_warned:
            self._adopt_warned.add(gid)
            self._alert("WARNING", title, body, source="cnc_gtt_adoption")
        return label

    # ── Step 5: leak / 50-cap (GTTs are EXEMPT from order-cancellation sweeps) ──
    def _orphan_sweep_and_cap(self, broker_gtts: Dict[str, dict],
                              active_rows: list) -> List[str]:
        """Forensic-log-then-delete a LEAKED system GTT (still ACTIVE at the broker but
        its gtt_state row is non-ACTIVE / the trade is closed); never touch a
        human/external GTT (no gtt_state row — FIX-182 discipline) nor a still-ACTIVE
        system GTT (the per-row ladder owns it). WARNING as the active-GTT count nears
        the 50 broker cap. A gtt_state GTT is a LIVE protective leg — only delete_gtt
        (deliberate close / dedupe / confirmed orphan) ever removes one; NO sweep does."""
        out: List[str] = []
        active_ids = {str(r["gtt_id"]) for r in active_rows}
        active_broker = {
            gid: g for gid, g in broker_gtts.items()
            if str((g.get("status", "") if isinstance(g, dict) else "")).lower() == "active"
        }

        n = len(active_broker)
        if n >= _GTT_CAP_WARN:
            sev = "WARNING" if n < _GTT_CAP_MAX else "CRITICAL"
            self._alert(sev, "GTT count approaching broker cap",
                        f"{n} active GTTs at the broker (cap {_GTT_CAP_MAX}).")
            out.append(f"gtt_cap:{n}")

        for gid, g in active_broker.items():
            if gid in active_ids:
                continue  # a healthy system GTT — the per-row ladder owns it
            row = self._store.get_gtt_state_by_id(gid)
            if row is None:
                # unknown GTT: human/external (or a persist-failed system GTT) ->
                # NEVER delete (protect human GTTs + don't strip protection); log only.
                self._forensic_log_gtt("unknown_gtt_left_alone", gid, g,
                                       "no gtt_state row -> treated as human/external")
                out.append(f"unknown_gtt:{gid}")
                continue
            # a SYSTEM GTT we believe is finished (non-ACTIVE row) yet still ACTIVE at
            # the broker -> a LEAK -> forensic-log FIRST, then delete (Step 5 ordering).
            self._forensic_log_gtt(
                "orphan_gtt_leak", gid, g,
                f"gtt_state status={row['status']} trade={row['trade_id']} but still "
                f"ACTIVE at broker -> delete")
            self._alert("WARNING", f"Orphan GTT cleaned — {row['symbol']}",
                        f"GTT {gid} (trade {row['trade_id']}, row status {row['status']}) "
                        f"was still live at the broker with no ACTIVE state -> deleted.")
            self._safe_delete_gtt(gid)
            out.append(f"orphan_deleted:{gid}")
        return out

    # ── gather (Y4-safe) ──────────────────────────────────────────────────────
    # ── the paper blind spot (26-Jul-2026) ────────────────────────────────────
    def _announce_paper_carry_blind_spot(self, rows) -> List[str]:
        """PAPER ONLY. Announce that this pass cannot prove the overnight carry —
        because it is about to emit artefacts indistinguishable from proof that it did.

        THE MECHANISM, measured. Paper's positions, holdings and GTTs are in-memory
        dicts on the adapter (zerodha_adapter.py:424/429/432) and the nightly restart
        erases all three; ``gtt_state`` is a real table and survives, by design. So on
        the morning after a paper CNC carry ``_gather()`` returns empty/empty/empty
        against a live ACTIVE row, ``_handle_row`` falls past rungs 1-3 to rung 4's
        "GTT gone + flat -> the GTT did its job" branch, and ``_finalize_gtt_exit``
        books a P&L at today's LTP (paper ``get_trades()`` is ``[]``, so the price
        resolves off the quote), releases the delivery reservation, publishes
        PositionClosed and marks the row CLEANED.

        WHY IT IS WORTH SAYING OUT LOUD: every one of those artefacts is identical to
        a real carried exit. That is not weak evidence, it is ACTIVE MISINFORMATION
        about the one path that holds capital overnight — and silence is the dangerous
        version, because "paper-proven" is the literal gate on delivery going live. So
        the run states its own blind spot, and states it BEFORE the misleading
        GTT_EXIT rather than after it.

        LIVE IS UNREACHABLE BY CONSTRUCTION, and by a MODE check rather than a config
        flag somebody could flip: this returns immediately unless the process is a
        paper run. In live the premise is false anyway — the broker's holdings and
        GTTs are real and survive the restart, which is precisely what the Mon->Tue
        live pair exists to demonstrate.

        ⛔ It does NOT change what the reconcile then does. Making paper behave
        differently from live here would be its own parity lie; the fix for the gap is
        a live pair, not a settlement model.
        """
        if self._mode != "PAPER":
            return []
        today = now_ist().date().isoformat()
        carried = []
        for r in rows:
            stamp = str(r["created_at"] or "")[:10]
            # Fail CLOSED on an unreadable stamp: only a well-formed date STRICTLY
            # older than today counts, so a blank never reads as "carried".
            if len(stamp) == 10 and stamp < today:
                carried.append(r)
        if not carried:
            return []
        if today in self._paper_carry_warned:
            return [f"paper_carry_blind_spot_seen:{len(carried)}"]
        self._paper_carry_warned.add(today)

        symbols = ", ".join(sorted({str(r["symbol"]) for r in carried}))
        oldest = min(str(r["created_at"])[:10] for r in carried)
        body = (
            f"{len(carried)} delivery GTT row(s) written on or before {oldest} "
            f"({symbols}) survived this restart in the DATABASE — but the paper "
            f"broker's holdings, positions and GTTs did not. They are in-memory and "
            f"died with the process.\n\n"
            f"⛔ WHATEVER THIS RECONCILE DOES WITH THEM NEXT IS NOT EVIDENCE ABOUT "
            f"THE OVERNIGHT CARRY. Expect a clean-looking GTT_EXIT — trade closed, "
            f"P&L booked at today's LTP, capital released — for a position that was "
            f"never held and a GTT that was never there. It will be indistinguishable "
            f"from a real carried exit.\n\n"
            f"The overnight path is LIVE-ONLY. Paper can model a carry's end state "
            f"(injected) but never the transition into it: nothing in production "
            f"promotes a filled paper CNC position into a holding. Proving it needs a "
            f"Monday->Tuesday live pair, whose PASS is the first Tuesday reconcile "
            f"reporting healthy:<symbol> and finalising NOTHING. See "
            f"docs/audit/paper_overnight_carry_26jul2026.md."
        )
        self._alert("CRITICAL", "PAPER CANNOT PROVE THE OVERNIGHT CARRY", body)
        self._log.critical("cnc_gtt_monitor.paper_carry_blind_spot", extra={
            "rows": len(carried), "symbols": symbols, "oldest_created_at": oldest,
            "gtt_ids": [r["gtt_id"] for r in carried]})
        return [f"paper_carry_blind_spot:{len(carried)}"]

    def _gather(self):
        """Return (broker_gtts_by_id, held_qty_by_symbol) or None on a broker failure
        (Y4: defer + alert, never treat no-data as no-positions)."""
        try:
            gtts = self._adapter.get_gtts() or []
            holdings = self._adapter.get_holdings() or []
            positions = self._adapter.get_positions() or []
        except Exception as exc:  # noqa: BLE001 — broker unavailable
            self._log.error("cnc_gtt_monitor: broker gather failed: %s", exc)
            self._alert("WARNING", "GTT reconcile deferred",
                        f"Broker gather failed ({exc}); deferring this cycle (no action taken).")
            return None

        broker_gtts: Dict[str, dict] = {}
        for g in gtts:
            gid = g.get("id") if isinstance(g, dict) else getattr(g, "id", None)
            if gid is not None:
                broker_gtts[str(gid)] = g

        held: Dict[str, int] = {}
        for h in holdings:
            sym = getattr(h, "symbol", None) or (h.get("symbol") if isinstance(h, dict) else None)
            qty = getattr(h, "qty", 0) or (h.get("qty", 0) if isinstance(h, dict) else 0)
            if sym:
                held[sym] = held.get(sym, 0) + int(qty)
        for p in positions:
            product = getattr(p, "product", "") or (p.get("product", "") if isinstance(p, dict) else "")
            if str(product).upper() != "CNC":
                continue
            sym = getattr(p, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else None)
            qty = getattr(p, "qty", 0) or (p.get("qty", 0) if isinstance(p, dict) else 0)
            if sym:
                held[sym] = held.get(sym, 0) + abs(int(qty))
        return broker_gtts, held

    # ── per-row K6 ladder ─────────────────────────────────────────────────────
    def _handle_row(self, r, broker_gtts: Dict[str, dict], held_qty: Dict[str, int],
                    in_hours: bool) -> str:
        gid = str(r["gtt_id"])
        symbol = r["symbol"]
        row_qty = int(r["qty"])
        held = int(held_qty.get(symbol, 0))

        # Y2: an anomaly already flagged for manual review -> stand down (no re-alert,
        # no auto-act) until the operator clears it or the state changes.
        if r["needs_review"]:
            return f"needs_review:{symbol}"

        bg = broker_gtts.get(gid)
        bg_status = (str(bg.get("status", "")).lower() if isinstance(bg, dict) else "") if bg else ""
        triggered = bg is not None and bg_status == "triggered"
        present_active = bg is not None and bg_status == "active"

        # 1) GTT fired.
        if triggered:
            if held == 0:
                return self._finalize_gtt_exit(r, reason="GTT_EXIT")          # clean exit
            return self._reprotect(r, held, why="F6: GTT triggered but holding still > 0")

        # 2) Healthy.
        if present_active and held == row_qty:
            self._store.touch_gtt_state_verified(r["gtt_id"], self._now())
            return f"healthy:{symbol}"

        # 3) QTY MISMATCH (held > 0 and != row.qty) — CRITICAL once + cancel + no recreate.
        if held > 0 and held != row_qty:
            return self._qty_mismatch(r, held, gtt_present=present_active)

        # 4) Holding FLAT.
        if held == 0:
            if present_active:
                # orphan: GTT still resting but position gone (external close) ->
                # forensic-log + delete the now-pointless GTT + finalise the trade.
                self._forensic_log("orphan_active_gtt_flat", r,
                                   "GTT active but holding flat (external close)")
                self._safe_delete_gtt(r["gtt_id"])
                return self._finalize_gtt_exit(r, reason="GTT_EXIT")
            # GTT gone + flat -> the GTT did its job (fired + aged out) / was removed.
            return self._finalize_gtt_exit(r, reason="GTT_EXIT")

        # 5) GTT missing + holding intact + qty match -> recreate (in-hours) / queue.
        if held == row_qty:
            if in_hours:
                return self._recreate(r, held, old_status="EXPIRED",
                                      why="GTT missing; holding intact")
            self._queue_preopen(r, held)
            self._alert("WARNING", f"GTT missing pre-open — {symbol}",
                        f"GTT {gid} missing for held {symbol} qty={held}; queued to "
                        f"recreate on the first in-hours cycle.")
            return f"queued_preopen:{symbol}"

        return f"noop:{symbol}"

    # ── actions ───────────────────────────────────────────────────────────────
    def _finalize_gtt_exit(self, r, *, reason: str) -> str:
        """3.5c: finalise a delivery trade closed by its GTT. Idempotent (3.5e): the
        OPEN/PARTIAL->CLOSED transition is the single gate; a duplicate observer gets
        False and does NOT double-release capital. Releases the delivery-bucket
        reservation, records financials, publishes PositionClosed (SYSTEM-OWNED), and
        marks the gtt_state row CLEANED."""
        trade_id = r["trade_id"]
        symbol = r["symbol"]
        if not self._store.mark_trade_closed_gtt(trade_id, exit_reason=reason):
            # already finalised by another observer (or already terminal) — just
            # ensure the gtt_state row is cleaned so it stops being reconciled.
            self._store.set_gtt_state_status(r["gtt_id"], "CLEANED", self._now())
            return f"gtt_exit_dup:{symbol}"

        trade = self._store.fetch_one(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,))
        entry_price = float(trade["entry_actual_price"] or 0.0) if trade else 0.0
        qty = int(trade["qty_filled"] or 0) if trade else int(r["qty"])
        direction = (trade["direction"] if trade else "LONG") or "LONG"
        exit_side = r["exit_side"]
        exit_price = self._resolve_exit_price(symbol, exit_side, entry_price)

        pnl = 0.0
        if entry_price > 0 and qty > 0:
            # E4 (2026-07-17): real CNC round-trip costs (was hardcoded 0.0 —
            # no CostCalculator was wired here) so this close's pnl_delta is
            # NET like every other row. product is CNC by construction: this
            # path only ever handles delivery GTT exits.
            charges = round_trip_costs_or_zero(
                self._cost_calculator,
                qty=qty,
                entry_price=float(entry_price),
                exit_price=float(exit_price),
                product="CNC",
                logger=self._log,
                context=f"gtt_exit trade_id={trade_id}",
            )
            try:
                rr = self._fm.release_used(
                    symbol=symbol, exit_price=float(exit_price), exit_qty=qty,
                    intent=PRODUCT_TO_INTENT.get("CNC", "DELIVERY"),
                    entry_price=float(entry_price), direction=direction,
                    costs=charges,
                    trade_id=trade_id)   # M-C7: reverse the persisted committed margin
                pnl = float(rr.pnl_delta)
                # E4: pnl is NET now, so gross/charges must be passed explicitly
                # or the row would claim gross==net and charges==0 falsely.
                self._store.record_gtt_close_financials(
                    trade_id=trade_id, exit_price=float(exit_price), net_pnl=pnl,
                    gross_pnl=pnl + charges, charges=charges)
            except Exception as exc:  # noqa: BLE001
                self._log.error("cnc_gtt_monitor: capital release failed for %s: %s",
                                trade_id, exc)
        try:
            self._bus.publish(PositionClosed(
                source_module="cnc_gtt_monitor", symbol=symbol, trade_id=trade_id,
                signal_id=(trade["signal_id"] if trade else "") or "",
                exit_price=float(exit_price), realized_pnl=pnl))
        except Exception as exc:  # noqa: BLE001
            self._log.error("cnc_gtt_monitor: PositionClosed publish failed: %s", exc)

        self._store.set_gtt_state_status(r["gtt_id"], "CLEANED", self._now())
        self._placer.forget(trade_id)
        self._log.info("cnc_gtt_monitor.gtt_exit", extra={
            "trade_id": trade_id, "symbol": symbol, "gtt_id": r["gtt_id"],
            "exit_price": exit_price, "pnl": pnl, "reason": reason})
        self._alert("WARNING", f"GTT exit — {symbol}",
                    f"Delivery position {symbol} closed via GTT (trade {trade_id}); "
                    f"exit={exit_price:.2f} pnl={pnl:+.2f}. Capital released.")
        return f"gtt_exit:{symbol}"

    def _reprotect(self, r, held: int, *, why: str) -> str:
        """F6: the GTT triggered but the holding is NOT flat (partial / no fill / gap
        through). Re-protect the REMAINING qty + CRITICAL. Do NOT close the trade."""
        self._alert("CRITICAL", f"GTT partial/no-fill re-protect — {r['symbol']}",
                    f"{why}: held={held} qty={r['qty']} (trade {r['trade_id']}). "
                    f"Re-protecting the remaining {held}.")
        return self._recreate(r, held, old_status="TRIGGERED", why=why, critical=True)

    def _recreate(self, r, held: int, *, old_status: str, why: str,
                  critical: bool = False) -> str:
        """T1: mark the old row done, drop the placer cache, and place a FRESH GTT for
        the held qty (Y3: place_for_fill fetches a fresh LTP). On failure -> CRITICAL +
        needs_review (do not crash, do not leave it silently unprotected)."""
        trade_id = r["trade_id"]
        symbol = r["symbol"]
        try:
            self._store.set_gtt_state_status(r["gtt_id"], old_status, self._now())
            self._placer.forget(trade_id)
            res = self._placer.place_for_fill(
                symbol=symbol, exit_side=r["exit_side"], qty=held,
                sl_price=float(r["sl_trigger"]), tgt_price=float(r["tgt_trigger"]),
                trade_id=trade_id, tag="recreate")
            self._log.info("cnc_gtt_monitor.recreated", extra={
                "trade_id": trade_id, "symbol": symbol, "new_gtt_id": res.gtt_id,
                "qty": held, "why": why})
            if not critical:
                self._alert("WARNING", f"GTT recreated — {symbol}",
                            f"{why}: recreated GTT for {symbol} qty={held} "
                            f"(trade {trade_id}); new gtt_id={res.gtt_id}.")
            return f"recreated:{symbol}"
        except Exception as exc:  # noqa: BLE001
            self._store.set_gtt_state_needs_review(r["gtt_id"], 1, self._now())
            self._alert("CRITICAL", f"GTT re-protect FAILED — {symbol}",
                        f"Could not recreate GTT for {symbol} qty={held} "
                        f"(trade {trade_id}): {exc}. Manual intervention required.")
            return f"recreate_failed:{symbol}"

    def _qty_mismatch(self, r, held: int, *, gtt_present: bool) -> str:
        """held > 0 and != row.qty, not explained by a clean exit. CRITICAL ONCE (Y2
        needs_review) + cancel the wrong-qty GTT (forensic-log first) + NO recreate +
        NO soft-kill + intraday UNAFFECTED."""
        symbol = r["symbol"]
        self._forensic_log("qty_mismatch", r,
                           f"held={held} != gtt_state.qty={r['qty']} (gtt_present={gtt_present})")
        if gtt_present:
            self._safe_delete_gtt(r["gtt_id"])  # a wrong-qty GTT would mis-sell on trigger
        self._store.set_gtt_state_needs_review(r["gtt_id"], 1, self._now())
        self._alert("CRITICAL", f"GTT qty mismatch — {symbol}",
                    f"Holding {held} != protected qty {r['qty']} for {symbol} "
                    f"(trade {r['trade_id']}). Wrong-qty GTT cancelled; flagged for "
                    f"manual review. NO auto-recreate, intraday unaffected.")
        return f"qty_mismatch:{symbol}"

    def _queue_preopen(self, r, held: int) -> None:
        with self._lock:
            self._preopen_queue[r["trade_id"]] = {
                "gtt_id": r["gtt_id"], "symbol": r["symbol"], "exit_side": r["exit_side"],
                "sl_trigger": float(r["sl_trigger"]), "tgt_trigger": float(r["tgt_trigger"]),
                "qty": held}

    def _drain_preopen(self) -> List[str]:
        """Y1: recreate every queued pre-open GTT on the first in-hours cycle, fetching
        a fresh row each time (state may have changed since queuing)."""
        with self._lock:
            queued = list(self._preopen_queue.items())
            self._preopen_queue.clear()
        out: List[str] = []
        for trade_id, spec in queued:
            r = self._store.get_active_gtt_for_trade(trade_id)
            if r is None:
                out.append(f"preopen_skip:{spec['symbol']}")  # row gone — nothing to do
                continue
            out.append(self._recreate(r, int(spec["qty"]), old_status="EXPIRED",
                                      why="Y1 pre-open queue drain"))
        return out

    # ── helpers ─────────────────────────────────────────────────────────────
    def _resolve_exit_price(self, symbol: str, exit_side: str, entry_price: float) -> float:
        """Best-effort exit price: broker trades (matching the exit side) -> LTP ->
        entry proxy. Mirrors the reconciler's CHECK1 resolution."""
        try:
            for t in reversed(self._adapter.get_trades() or []):
                if (t.get("tradingsymbol") == symbol
                        and t.get("transaction_type") == exit_side
                        and float(t.get("quantity", 0)) > 0
                        and float(t.get("average_price", 0)) > 0):
                    return float(t["average_price"])
        except Exception:  # noqa: BLE001
            pass
        try:
            q = self._adapter.get_quote([symbol]) or {}
            v = q.get(symbol)
            ltp = getattr(v, "last_price", None) if v is not None else None
            if ltp and float(ltp) > 0:
                return float(ltp)
        except Exception:  # noqa: BLE001
            pass
        return float(entry_price or 0.0)

    def _safe_delete_gtt(self, gtt_id) -> None:
        try:
            self._adapter.delete_gtt(gtt_id)
        except Exception as exc:  # noqa: BLE001
            self._log.error("cnc_gtt_monitor: delete_gtt(%s) failed: %s", gtt_id, exc)

    def _soft_kill(self, reason: str) -> str:
        self._log.critical("cnc_gtt_monitor SOFT_KILL: %s", reason)
        try:
            self._ks.soft_kill(reason=f"cnc_gtt_monitor: {reason}",
                               triggered_by="cnc_gtt_monitor")
        except Exception as exc:  # noqa: BLE001
            self._log.error("cnc_gtt_monitor: soft_kill failed: %s", exc)
        self._alert("CRITICAL", "GTT ownership ambiguity — SOFT_KILL", reason)
        return f"soft_kill:{reason[:40]}"

    def _forensic_log(self, event: str, r, detail: str) -> None:
        """Forensic record BEFORE any GTT deletion (Step 5 ordering): who/what/why."""
        self._log.warning("cnc_gtt_monitor.forensic", extra={
            "event": event, "trade_id": r["trade_id"], "symbol": r["symbol"],
            "gtt_id": r["gtt_id"], "qty": r["qty"], "detail": detail})

    def _forensic_log_gtt(self, event: str, gid: str, bg, detail: str) -> None:
        """Forensic record for a broker GTT (no gtt_state row in hand) BEFORE deletion."""
        sym = (bg.get("condition") or {}).get("tradingsymbol", "") if isinstance(bg, dict) else ""
        self._log.warning("cnc_gtt_monitor.forensic", extra={
            "event": event, "gtt_id": gid, "symbol": sym, "detail": detail})

    def _alert(self, severity: str, title: str, body: str,
               *, source: str = "cnc_gtt_monitor") -> None:
        if self._notifier is None:
            return
        try:
            self._notifier.send(severity=severity, title=f"[{self._mode}] {title}",
                                body=body, source_module=source)
        except Exception as exc:  # noqa: BLE001
            self._log.error("cnc_gtt_monitor: notifier.send failed: %s", exc)

    def _now(self) -> str:
        return now_ist().isoformat()
