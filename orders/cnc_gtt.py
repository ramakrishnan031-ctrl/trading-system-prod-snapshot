"""
orders/cnc_gtt.py — SLICE2.5-P1 (25-Jun-2026): CNC overnight protection via OCO-GTT.

A CNC (delivery) position is held overnight, but day-validity SL/TGT legs expire at
EOD → the position would be naked overnight. This module places ONE broker-side
two-leg OCO GTT (Good-Till-Triggered) right after the entry fill, so a single
persistent trigger protects the position entry→exit and survives a VM outage.

Phase 1 scope: place / modify ONE GTT per trade (in-memory map; durable cross-restart
persistence + a leak/health monitor are Phase 2). Static SL/TGT only — the delivery
strategies have smart_tgt_enabled=false, so no trailing. (Trailing would need
modify_gtt on each step — OUT OF SCOPE for P1; see Phase 2.)

Parity: the ONLY live/paper difference is the broker boundary (adapter.place_gtt /
modify_gtt — live calls Kite, paper returns a mock id + records identical params).
Everything here (the limit-price math, the C8 distance validation, the one-per-trade
logic) is identical in both engines.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from core.exceptions import BrokerError
from orders.price_math import DEFAULT_TICK, calc_gtt_limit_price, round_to_tick

# C8: Kite requires a GTT trigger to sit a minimum distance from the LTP. 0.25% is the
# conservative public figure; validate so we surface a clear error instead of a broker
# reject (and so an OCO whose SL/TGT no longer straddle the LTP is caught up-front).
_MIN_TRIGGER_DISTANCE_PCT = 0.0025


@dataclass(frozen=True)
class CncGttResult:
    """Outcome of CncGttPlacer.place_for_fill()."""
    gtt_id: str
    sl_trigger: float
    sl_limit: float
    tgt_trigger: float
    tgt_limit: float
    modified: bool          # True = modified an existing GTT (a later partial fill)


class CncGttPlacer:
    """Places/maintains the single OCO GTT that protects a CNC position overnight.

    Reuses the EXACT SL/TGT prices the LIMIT_TRIPLE path computes (mechanism +
    validity change only): SL trigger = the strategy SL (fill_entry.sl_price), TGT
    trigger = the fill-recalc'd target (calc_tgt_price, FIX-013). The SL leg's LIMIT
    sits a DEEP gtt_sl_limit_offset_pct (3%) below its trigger so an overnight gap-down
    still fills within the floor; the TGT leg's LIMIT sits a SMALL offset below its
    trigger so it fills at/just-below target.
    """

    def __init__(
        self,
        adapter: Any,
        *,
        gtt_sl_limit_offset_pct: float,
        gtt_tgt_limit_offset_pct: float,
        delivery_enabled: bool,
        logger: Any,
        quote_fn: Optional[Callable[[list], dict]] = None,
        tick_fn: Optional[Callable[[str], float]] = None,
    ) -> None:
        self._adapter = adapter
        self._gtt_sl_off = float(gtt_sl_limit_offset_pct)
        self._gtt_tgt_off = float(gtt_tgt_limit_offset_pct)
        self._delivery_enabled = bool(delivery_enabled)
        self._log = logger
        self._quote_fn = quote_fn or getattr(adapter, "get_quote", None)
        self._tick_fn = tick_fn or (lambda _s: DEFAULT_TICK)
        # P1: in-memory one-GTT-per-trade map (durable persistence = Phase 2).
        self._trade_gtts: Dict[str, str] = {}
        self._lock = threading.Lock()

    def gtt_for(self, trade_id: str) -> Optional[str]:
        with self._lock:
            return self._trade_gtts.get(trade_id)

    def place_for_fill(
        self,
        *,
        symbol: str,
        exit_side: str,          # both OCO legs use this side (SELL exits a long)
        qty: int,
        sl_price: float,
        tgt_price: float,
        trade_id: str,
        tag: str = "",
    ) -> CncGttResult:
        """Place (or modify, if a GTT already exists for this trade) the OCO GTT.
        Raises BrokerError on a missing LTP or a C8 distance/straddle violation."""
        tick = self._safe_tick(symbol)
        sl_trigger = round_to_tick(sl_price, tick, mode="nearest")
        tgt_trigger = round_to_tick(tgt_price, tick, mode="nearest")
        sl_limit = calc_gtt_limit_price(exit_side, sl_trigger, self._gtt_sl_off, tick)
        tgt_limit = calc_gtt_limit_price(exit_side, tgt_trigger, self._gtt_tgt_off, tick)

        last_price = self._fresh_ltp(symbol)
        self._validate_trigger_distance(symbol, last_price, sl_trigger, tgt_trigger)

        with self._lock:
            existing = self._trade_gtts.get(trade_id)

        common = dict(
            symbol=symbol, exit_side=exit_side, qty=int(qty),
            sl_trigger=sl_trigger, sl_limit=sl_limit,
            tgt_trigger=tgt_trigger, tgt_limit=tgt_limit, last_price=last_price,
        )
        if existing:
            # One GTT per trade (C3): a later partial fill MODIFIES the existing GTT to
            # the new cumulative qty rather than leaking a second trigger.
            gtt_id = self._adapter.modify_gtt(gtt_id=existing, **common)
            modified = True
        else:
            gtt_id = self._adapter.place_gtt(tag=tag, **common)
            modified = False

        with self._lock:
            self._trade_gtts[trade_id] = gtt_id

        self._log.info(
            "cnc_gtt.placed",
            extra={
                "trade_id": trade_id, "symbol": symbol, "gtt_id": gtt_id,
                "modified": modified, "qty": qty, "exit_side": exit_side,
                "sl_trigger": sl_trigger, "sl_limit": sl_limit,
                "tgt_trigger": tgt_trigger, "tgt_limit": tgt_limit,
                "last_price": last_price,
            },
        )
        return CncGttResult(
            gtt_id=str(gtt_id), sl_trigger=sl_trigger, sl_limit=sl_limit,
            tgt_trigger=tgt_trigger, tgt_limit=tgt_limit, modified=modified,
        )

    # ── internals ────────────────────────────────────────────────────────────
    def _safe_tick(self, symbol: str) -> float:
        try:
            t = float(self._tick_fn(symbol))
            return t if t > 0 else DEFAULT_TICK
        except Exception:  # noqa: BLE001 — fail-safe to the default tick
            return DEFAULT_TICK

    def _fresh_ltp(self, symbol: str) -> float:
        """Fresh LTP — MANDATORY for a GTT (Kite needs last_price)."""
        if self._quote_fn is not None:
            try:
                q = self._quote_fn([symbol]) or {}
                v = q.get(symbol)
                ltp = getattr(v, "last_price", None) if v is not None else None
                if ltp and float(ltp) > 0:
                    return float(ltp)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("cnc_gtt: LTP fetch failed for %s: %s", symbol, exc)
        raise BrokerError(
            f"cnc_gtt: no LTP for {symbol}; cannot place GTT (last_price is mandatory)")

    def _validate_trigger_distance(self, symbol, ltp, sl_trigger, tgt_trigger) -> None:
        """C8: SL must be below LTP, TGT above, each at least _MIN_TRIGGER_DISTANCE_PCT
        away. Raises a clear BrokerError otherwise (vs an opaque Kite reject)."""
        if not (sl_trigger < ltp < tgt_trigger):
            raise BrokerError(
                f"cnc_gtt: OCO triggers must straddle LTP for {symbol} "
                f"(need sl<{ltp}<tgt); got sl={sl_trigger}, tgt={tgt_trigger} "
                "(SL/TGT already breached at fill — handle via emergency exit)")
        if (abs(ltp - sl_trigger) / ltp < _MIN_TRIGGER_DISTANCE_PCT
                or abs(tgt_trigger - ltp) / ltp < _MIN_TRIGGER_DISTANCE_PCT):
            raise BrokerError(
                f"cnc_gtt: a trigger is within {_MIN_TRIGGER_DISTANCE_PCT:.2%} of LTP "
                f"{ltp} for {symbol} (sl={sl_trigger}, tgt={tgt_trigger}); Kite rejects")
