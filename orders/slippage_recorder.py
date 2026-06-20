"""
orders/slippage_recorder.py -- Trading System v2  (Slippage intelligence Phase 1)

Records RAW execution facts into the v31 data layer:
  * order_execution_log     -- one row per filled order/leg (on OrderFilled)
  * market_execution_context-- best-effort bid/ask/spread at fill (Priority-2)
  * trade_slippage_log      -- one row per completed trade (on PositionClosed),
                               with rr_damage_pct = how much execution ate the
                               planned risk budget (THE key metric).

SAFETY: this NEVER blocks or affects trade execution. It subscribes ASYNC
(dedicated background thread; EventBus logs but does not re-raise async-handler
exceptions), and every handler is additionally wrapped in try/except and every
insert is best-effort (returns bool, never raises). A recording failure can only
lose a log row, never a trade.

NO analytics here — band/strategy stats + tolerance recommendations are computed
ON-DEMAND in reports (Phase 2). This module writes raw facts only.
"""
from __future__ import annotations

from typing import Any, Optional

from core.events import OrderFilled, PositionClosed
from core.time_authority import now_ist

# Statuses that mean "won" / "lost" for trade_result.
_WIN, _LOSS, _BE = "WIN", "LOSS", "BREAKEVEN"


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers (unit-tested) — slippage sign convention: ADVERSE = positive.
# ─────────────────────────────────────────────────────────────────────────────

def _is_long(side: str) -> bool:
    return (side or "").upper() in ("LONG", "BUY")


def adverse_entry_slip(side: str, intended: float, actual: float) -> float:
    """Entry slippage, adverse = positive (paid worse). LONG: filled higher is
    bad; SHORT: filled lower is bad."""
    return (actual - intended) if _is_long(side) else (intended - actual)


def adverse_sl_slip(side: str, sl: float, fill: float) -> float:
    """SL-exit slippage, adverse = positive (exited worse / bigger loss). Exiting
    a LONG is a SELL (worse = filled below the stop); a SHORT is a BUY (worse =
    filled above the stop)."""
    return (sl - fill) if _is_long(side) else (fill - sl)


def favorable_tgt_slip(side: str, tgt: float, fill: float) -> float:
    """TGT-exit slippage, FAVOURABLE = positive (better fill, REDUCES rr_damage).
    Exiting a LONG is a SELL (better = filled above target); a SHORT is a BUY
    (better = filled below target)."""
    return (fill - tgt) if _is_long(side) else (tgt - fill)


def calc_rr_damage_pct(entry_slip_rs: Optional[float], sl_slip_rs: Optional[float],
                       tgt_slip_rs: Optional[float],
                       planned_sl_distance: Optional[float]) -> Optional[float]:
    """% of the planned risk budget (SL distance) destroyed by execution:
        (entry_adverse + sl_adverse - tgt_favourable) / planned_sl_distance × 100.
    Returns None if the SL distance is unknown/zero."""
    if not planned_sl_distance:
        return None
    damage = (entry_slip_rs or 0.0) + (sl_slip_rs or 0.0) - (tgt_slip_rs or 0.0)
    return damage / planned_sl_distance * 100.0


def get_price_band(price: float, bands: list[str]) -> Optional[str]:
    """Return the band label for `price`. Bands are "LO-HI" (HI exclusive) or
    "Nnn+" (open-ended top). Returns None if no band matches / bands empty."""
    if not bands or price is None:
        return None
    for b in bands:
        try:
            if b.endswith("+"):
                if price >= float(b[:-1]):
                    return b
            else:
                lo, hi = b.split("-", 1)
                if float(lo) <= price < float(hi):
                    return b
        except (ValueError, AttributeError):
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Recorder
# ─────────────────────────────────────────────────────────────────────────────

class SlippageRecorder:
    """Subscribes (async) to OrderFilled + PositionClosed and writes the raw
    slippage facts. Construct once at startup AFTER the store + bus exist."""

    def __init__(self, store: Any, bus: Any, logger: Any, *,
                 price_bands: Optional[list[str]] = None,
                 adapter: Optional[Any] = None) -> None:
        self._store = store
        self._bus = bus
        self._log = logger
        self._bands = list(price_bands or [])
        self._adapter = adapter   # optional: best-effort bid/ask quote source
        # Async dispatch: handler runs on its own thread; exceptions are logged
        # but NEVER re-raised into the publisher (the order path).
        bus.subscribe(OrderFilled, self._on_order_filled, async_dispatch=True)
        bus.subscribe(PositionClosed, self._on_position_closed, async_dispatch=True)

    # -- OrderFilled -> order_execution_log + market_execution_context ---------
    def _on_order_filled(self, ev: OrderFilled) -> None:
        try:
            order_id = ev.internal_order_id or ev.order_id or None
            leg, order_type, qty_req, strategy = self._enrich_order(order_id, ev.trade_id)
            intended = ev.expected_price or None
            actual = ev.avg_fill_price or None
            slip_rs = (adverse_entry_slip(ev.side, intended, actual)
                       if (intended and actual) else None)
            row = {
                "order_id": order_id,
                "parent_trade_id": ev.trade_id or None,
                "signal_id": ev.signal_id or None,
                "symbol": ev.symbol,
                "strategy_name": strategy,
                "leg": leg or "ENTRY",
                "order_type": order_type,
                "side": ev.side or None,
                "intended_price": intended,
                "actual_price": actual,
                "slippage_rs": (round(slip_rs, 4) if slip_rs is not None else None),
                "slippage_pct": (round(ev.slippage_pct, 4) if ev.slippage_pct else None),
                "qty": qty_req,
                "filled_qty": ev.filled_qty or None,
                "is_partial": 1 if (qty_req and ev.filled_qty and ev.filled_qty < qty_req) else 0,
                "status": "COMPLETE",
                "fill_timestamp": ev.filled_at or None,
            }
            self._store.insert_order_execution_log(row)
            self._record_context(ev.trade_id, order_id, ev.symbol, leg, actual)
        except Exception as exc:  # noqa: BLE001 — recording must never raise
            self._log.warning("slippage_recorder: order-filled record failed: %s", exc)

    def _record_context(self, trade_id, order_id, symbol, leg, ltp) -> None:
        """Best-effort market context (Priority-2). Missing bid/ask -> NULLs."""
        bid = ask = spread_rs = spread_pct = bid_qty = ask_qty = volume = None
        try:
            if self._adapter is not None and hasattr(self._adapter, "get_quote_raw"):
                q = (self._adapter.get_quote_raw([f"NSE:{symbol}"]) or {}).get(f"NSE:{symbol}")
                if q:
                    depth = q.get("depth") or {}
                    buy, sell = (depth.get("buy") or []), (depth.get("sell") or [])
                    if buy:
                        bid = buy[0].get("price"); bid_qty = buy[0].get("quantity")
                    if sell:
                        ask = sell[0].get("price"); ask_qty = sell[0].get("quantity")
                    volume = q.get("volume") or q.get("volume_traded")
                    if bid and ask and bid > 0:
                        spread_rs = ask - bid
                        spread_pct = spread_rs / bid * 100.0
                    if ltp is None:
                        ltp = q.get("last_price")
        except Exception:
            pass  # Priority-2: never fail / never block
        self._store.insert_market_execution_context({
            "trade_id": trade_id or None, "order_id": order_id, "symbol": symbol,
            "leg": leg, "captured_at": now_ist().isoformat(), "ltp": ltp,
            "bid_price": bid, "ask_price": ask,
            "spread_rs": (round(spread_rs, 4) if spread_rs is not None else None),
            "spread_pct": (round(spread_pct, 4) if spread_pct is not None else None),
            "bid_qty": bid_qty, "ask_qty": ask_qty, "volume_traded": volume,
        })

    # -- PositionClosed -> trade_slippage_log roll-up -------------------------
    def _on_position_closed(self, ev: PositionClosed) -> None:
        try:
            t = self._fetch_trade(ev.trade_id)
            if not t:
                return
            self._store.insert_trade_slippage_log(self.build_trade_slippage_row(t, self._bands))
        except Exception as exc:  # noqa: BLE001
            self._log.warning("slippage_recorder: position-closed roll-up failed: %s", exc)

    @staticmethod
    def build_trade_slippage_row(t: dict, bands: list[str]) -> dict:
        """Pure roll-up from a trade row -> a trade_slippage_log row (testable)."""
        side = t.get("direction") or ""
        e_sig, e_fill = t.get("entry_target_price"), t.get("entry_actual_price")
        sl0, tgt0 = t.get("sl_initial"), t.get("tgt_initial")
        exit_px, exit_reason = t.get("exit_price"), (t.get("exit_reason") or "")
        net = t.get("net_pnl")

        entry_slip = adverse_entry_slip(side, e_sig, e_fill) if (e_sig and e_fill) else None
        planned_dist = abs(e_sig - sl0) if (e_sig and sl0) else None
        sl_fill = exit_px if exit_reason == "SL_HIT" else None
        tgt_fill = exit_px if exit_reason == "TGT_HIT" else None
        sl_slip = adverse_sl_slip(side, sl0, sl_fill) if (sl0 and sl_fill) else None
        tgt_slip = favorable_tgt_slip(side, tgt0, tgt_fill) if (tgt0 and tgt_fill) else None

        planned_rr = (abs(tgt0 - e_sig) / planned_dist
                      if (tgt0 and e_sig and planned_dist) else None)
        actual_rr = None
        if e_fill and sl0 and exit_px:
            risk = abs(e_fill - sl0)
            if risk:
                move = (exit_px - e_fill) if _is_long(side) else (e_fill - exit_px)
                actual_rr = move / risk
        result = (_WIN if (net or 0) > 0 else _LOSS if (net or 0) < 0 else _BE) if net is not None else None

        def _pct(rs, base):
            return round(rs / base * 100.0, 4) if (rs is not None and base) else None

        return {
            "trade_id": t.get("trade_id"),
            "trade_date": (t.get("created_at") or "")[:10] or now_ist().date().isoformat(),
            "symbol": t.get("symbol"), "strategy_name": t.get("strategy") or "?",
            "side": side or "?", "qty": t.get("qty_filled"),
            "price_band": get_price_band(e_sig, bands) if e_sig else None,
            "entry_signal_price": e_sig, "entry_fill_price": e_fill,
            "entry_slippage_rs": (round(entry_slip, 4) if entry_slip is not None else None),
            "entry_slippage_pct": _pct(entry_slip, e_sig),
            "sl_trigger_price": sl0, "sl_fill_price": sl_fill,
            "sl_slippage_rs": (round(sl_slip, 4) if sl_slip is not None else None),
            "sl_slippage_pct": _pct(sl_slip, sl0),
            "tgt_price": tgt0, "tgt_fill_price": tgt_fill,
            "tgt_slippage_rs": (round(tgt_slip, 4) if tgt_slip is not None else None),
            "tgt_slippage_pct": _pct(tgt_slip, tgt0),
            "planned_sl_distance": (round(planned_dist, 4) if planned_dist else None),
            "planned_rr": (round(planned_rr, 4) if planned_rr is not None else None),
            "actual_rr": (round(actual_rr, 4) if actual_rr is not None else None),
            "rr_damage_pct": (lambda d: round(d, 4) if d is not None else None)(
                calc_rr_damage_pct(entry_slip, sl_slip, tgt_slip, planned_dist)),
            "trade_result": result, "exit_reason": exit_reason or None,
        }

    # -- enrichment (best-effort DB reads) ------------------------------------
    def _enrich_order(self, order_id, trade_id):
        """(leg, order_type, qty_requested, strategy) from orders + trades; all
        None on any miss — never raises."""
        leg = order_type = qty_req = strategy = None
        try:
            if order_id:
                r = self._store.fetch_one(
                    "SELECT leg, order_type, qty_requested FROM orders WHERE order_id=?",
                    (order_id,))
                if r:
                    leg, order_type, qty_req = r["leg"], r["order_type"], r["qty_requested"]
            if trade_id:
                r = self._store.fetch_one("SELECT strategy FROM trades WHERE trade_id=?", (trade_id,))
                if r:
                    strategy = r["strategy"]
        except Exception:
            pass
        return leg, order_type, qty_req, strategy

    def _fetch_trade(self, trade_id) -> Optional[dict]:
        if not trade_id:
            return None
        try:
            r = self._store.fetch_one(
                "SELECT trade_id, symbol, strategy, direction, qty_filled, created_at, "
                "entry_target_price, entry_actual_price, sl_initial, tgt_initial, "
                "exit_price, exit_reason, net_pnl FROM trades WHERE trade_id=?",
                (trade_id,))
            return dict(r) if r else None
        except Exception:
            return None
