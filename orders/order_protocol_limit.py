"""
orders/order_protocol_limit.py — Trading System v2

Purpose:
    LIMIT_TRIPLE order protocol. Two-phase placement:
      Phase 1 (execute):   ENTRY LIMIT at entry_price.
      Phase 2 (place_exits): SL + TGT on the exit side, sized to the ACTUAL
                             filled qty. Called by OrderPlacer on ENTRY fill.

    This is the P8/P13 fallback protocol used when Cover Orders are not
    available for a stock, or as the v2 default before CO is validated.

Naked-Short Fix (2026-04-24 / Audit finding 2.1):
    Pre-fix the protocol placed ENTRY + SL + TGT in sequence at FULL qty
    without waiting for ENTRY fill. If ENTRY partial-filled (e.g. 100/1000)
    and price spiked through TGT, TGT executed at qty=1000 → naked short
    of 900. Post-fix the protocol places ENTRY only; exits are placed by
    OrderPlacer on ENTRY fill using event.filled_qty. Partial fills are
    safe because SL/TGT are sized to the filled qty.

SL-M Removal — P0 (2026-06-15, first live day):
    Zerodha rejects SL-M orders via the API entirely (not just for CNC).
    place_exits now places EVERY SL leg as order_type="SL" (stop-limit) for
    both INTRADAY and DELIVERY, with a limit price offset past the trigger
    (capital.sl_limit_offset_pct, default 0.5%) so a triggered stop fills like
    a market order. See orders/price_math.calc_sl_limit_price.

Locked Design Decisions:
    OPL1 -- Two-phase placement. execute() = ENTRY. place_exits() = SL+TGT.
    OPL2 -- ENTRY side = signal side. SL/TGT side = opposite of signal side
            (always closing orders).
    OPL3 -- execute() failure: raise immediately; no cleanup needed (nothing
            else placed). place_exits() failure: SL-first, so if SL fails
            NO TGT is attempted — caller must escalate (position is live
            with no SL). If TGT fails after SL: SL still stands; raise.
    OPL4 -- tag = trade_id passed to zerodha_adapter for all orders.
    OPL5 -- Layer 5 (orders/). Imports broker/zerodha_adapter, orders/entry_engine.
    OPL6 -- order_protocol = "LIMIT_TRIPLE" on the EntryResult.
    OPL7 -- ALL SL legs use order_type="SL" (stop-limit) with a limit price
            offset past the trigger by sl_limit_offset_pct (default 0.5%).
            Rationale: Zerodha rejects SL-M via the API (P0 2026-06-15).
            Applies to both INTRADAY and DELIVERY.

What This Module Does NOT Do:
    - Does not manage DB rows (order_placer's job)
    - Does not monitor fills (order_monitor's job)
    - Does not cancel/timeout orders (order_timeout's job)
    - Does not subscribe to events (OrderPlacer owns the fill→exits flow)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from broker.zerodha_adapter import ZerodhaAdapter
from core.effect_telemetry import handle as _effect_handle
from core.exceptions import BrokerError, OrderRejectedError, SLUnplaceableError
from core.ids import truncate_tag_for_broker
from core.logger import log_exception
from orders.entry_engine import EntryEngine, EntryResult
from orders.price_math import (
    DEFAULT_SL_LIMIT_OFFSET_PCT,
    calc_sl_limit_price,
    clamp_exit_into_band,
)


# ─────────────────────────────────────────────────────────────────────────────
# ExitLegsResult — return type for place_exits (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExitLegsResult:
    """
    Outcome of LimitTripleProtocol.place_exits().

    On full success both legs are present and ``tgt_placed`` is True.

    FIX-190 (Bug C): if the SL is placed but the TGT is rejected (e.g. the
    target price is outside the circuit band), the protocol no longer raises —
    the position is STILL protected by the live stop. It returns a partial
    result with ``tgt_placed=False`` and the tgt_* fields None. The caller keeps
    the SL and must NOT treat this as POSITION_UNPROTECTED (raising used to make
    the caller HARD_KILL the whole book — the 19-Jun incident). A genuine SL
    failure (position truly unprotected) still raises.
    """
    sl_broker_order_id: str
    sl_internal_id: str
    sl_order_type: str          # always "SL" (stop-limit) post-P0 2026-06-15
    sl_trigger_price: float
    sl_price: float             # limit price = trigger ± sl_limit_offset_pct
    tgt_broker_order_id: Optional[str] = None
    tgt_internal_id: Optional[str] = None
    tgt_price: Optional[float] = None
    tgt_placed: bool = True     # FIX-190 Bug C: False = SL live, TGT not placed
    # NOCIL after-check (24-Jun): record whether the circuit-band clamp moved each
    # leg, so the Slice-1 SL/TGT after-check can tell a LEGITIMATE band clamp
    # (placed == band ceiling) from a real mismatch instead of false-flagging it.
    # When True, the clamped band ceiling is the placed price (sl_trigger_price /
    # tgt_price respectively).
    sl_clamped: bool = False
    tgt_clamped: bool = False
    # SLICE2.5-P1: when True, this trade's overnight protection is a single broker-side
    # OCO-GTT (CNC), NOT two day-validity legs. gtt_id is the Kite trigger id (or a
    # paper mock). order_placer detects is_gtt and finalizes via _finalize_cnc_gtt
    # (logs the gtt_id; no day-leg persist / _fill_map OCO / day-leg after-check).
    is_gtt: bool = False
    gtt_id: Optional[str] = None


@dataclass(frozen=True)
class TgtOnlyResult:
    """Outcome of LimitTripleProtocol.place_tgt_only() (TGT retry, Task 2026-06-19).

    placed=True with ids/price set on success; placed=False (ids None) when the
    broker rejected the TGT or returned no id (e.g. still outside the circuit
    band) — the method NEVER raises, so the caller can schedule another retry.
    The SL is never touched. ``clamped`` records whether the circuit-band clamp
    moved the target (Bug D)."""
    placed: bool
    tgt_broker_order_id: Optional[str] = None
    tgt_internal_id: Optional[str] = None
    tgt_price: Optional[float] = None
    clamped: bool = False
    unplaceable: bool = False    # True = placeability gate refused (wrong-side of
                                 # fill / band too tight), NOT a broker reject ->
                                 # caller keeps retrying ("skipped_unplaceable")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _exit_side(entry_side: str) -> str:
    """Return the closing side for a position."""
    return "SELL" if entry_side == "BUY" else "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# LimitTripleProtocol
# ─────────────────────────────────────────────────────────────────────────────

class LimitTripleProtocol(EntryEngine):
    """
    LIMIT_TRIPLE entry protocol (P8/P13 fallback).

    Two-phase:
      Phase 1: execute() places ENTRY LIMIT only.
      Phase 2: place_exits() places SL + TGT on ENTRY fill.
    """

    def __init__(
        self,
        adapter: ZerodhaAdapter,
        logger: logging.Logger,
        sl_limit_offset_pct: float = DEFAULT_SL_LIMIT_OFFSET_PCT,
    ) -> None:
        self._adapter = adapter
        self._log = logger
        # effect-telemetry (ledger #1, frozen contract A2.1): one handle,
        # resolved once — a LIMIT_TRIPLE entry sequence executed.
        self._fx_execute = _effect_handle("limit_protocol")
        # P0 (2026-06-15): SL legs are placed as SL (stop-limit), never SL-M
        # (Zerodha rejects SL-M via API). This is the offset of the limit price
        # past the trigger so a triggered stop fills reliably. See calc_sl_limit_price.
        self._sl_limit_offset_pct = sl_limit_offset_pct

    # ── Phase 1: ENTRY ────────────────────────────────────────────────────────

    def execute(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        sl_price: float,
        tgt_price: float,
        intent: str,
        trade_id: str,
        tag: str = "",
        entry_order_type: str = "LIMIT",   # SNR-V2: "MARKET" for the retest entry
    ) -> EntryResult:
        """
        Phase 1: place ENTRY LIMIT only (OPL1/OPL2/OPL6).

        SL and TGT are deferred to place_exits(), called by OrderPlacer on
        ENTRY fill. The EntryResult has sl_* / tgt_* fields empty by design;
        see module docstring for the naked-short fix rationale.

        `sl_price` and `tgt_price` are accepted here for signature symmetry
        with CoPlusTgtProtocol but are NOT used — the caller must forward
        them to place_exits() at fill time.
        """
        # effect-telemetry (frozen A2.1): an entry sequence executed —
        # counted at protocol dispatch (the protocol's consequential act).
        self._fx_execute.inc()
        # FIX-093: Truncate tag to 16 chars for Kite API compliance
        order_tag = truncate_tag_for_broker(tag or trade_id)
        self._log.debug(
            "limit_triple.order_tag_truncated",
            extra={"trade_id": trade_id, "order_tag": order_tag},
        )

        try:
            # SNR-V2: a MARKET entry (the retest-confirm path) passes price=0 — kite
            # and the tick-snap both ignore price for MARKET, and the fill (live or
            # paper synth) lands at LTP. The default LIMIT path is byte-unchanged.
            _entry_order_price = 0.0 if entry_order_type == "MARKET" else entry_price
            entry_placed = self._adapter.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=_entry_order_price,
                order_type=entry_order_type,
                intent=intent,
                tag=order_tag,
            )
        except BrokerError:
            raise  # OPL3: entry failure → propagate immediately

        # OP-LM3: empty broker_order_id is a silent broker failure
        if not entry_placed.broker_order_id:
            raise OrderRejectedError(
                "adapter returned empty broker_order_id for ENTRY order",
                symbol=symbol, trade_id=trade_id, leg="ENTRY",
            )

        self._log.info(
            "limit_triple.entry_placed",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": entry_placed.broker_order_id,
                "price": entry_price,
            },
        )

        return EntryResult(
            success=True,
            entry_broker_order_id=entry_placed.broker_order_id,
            sl_broker_order_id="",    # deferred to place_exits()
            tgt_broker_order_id="",   # deferred to place_exits()
            entry_internal_id=entry_placed.internal_order_id,
            sl_internal_id="",
            tgt_internal_id="",
            order_protocol="LIMIT_TRIPLE",
        )

    # ── Phase 2: SL + TGT ─────────────────────────────────────────────────────

    def _circuit_limits(self, symbol: str):
        """FIX-190 (Bug D): best-effort (upper, lower) circuit limits from a quote.
        Returns (None, None) if unavailable so the caller places prices as-is."""
        try:
            quotes = self._adapter.get_quote([symbol])
            q = quotes.get(symbol) if quotes else None
            if q is not None:
                return (
                    getattr(q, "upper_circuit", None),
                    getattr(q, "lower_circuit", None),
                )
        except Exception as exc:
            self._log.debug(
                "limit_triple.circuit_limits_fetch_failed",
                extra={"symbol": symbol, "error": str(exc)},
            )
        return (None, None)

    def place_exits(
        self,
        *,
        symbol: str,
        entry_side: str,        # ENTRY side; SL/TGT placed on the opposite side
        qty: int,               # the filled qty — sized to ACTUAL fill, not requested
        sl_price: float,
        tgt_price: float,
        intent: str,
        trade_id: str,
        tag: str = "",
        entry_fill: float = 0.0,  # actual entry fill — the placeability gate's
                                  # reference (0.0 -> fail open, no polarity check)
    ) -> ExitLegsResult:
        """
        Phase 2: place SL + TGT on exit side at the ACTUAL filled qty (OPL1/OPL7).

        Called by OrderPlacer on ENTRY OrderFilled. SL-first order matters:
        if SL fails we do NOT attempt TGT (better to raise with no TGT than
        leave a naked TGT that could execute into a missing SL bracket).

        Args:
            qty: the actual filled qty from the OrderFilled event. Sizing
                 SL/TGT to qty ≤ entry_qty is the naked-short fix (2.1).

        Raises:
            BrokerError on any leg failure. Caller is responsible for
            escalating (position is live with no protection if SL failed;
            position has only SL if TGT failed).
        """
        # FIX-093: Truncate tag to 16 chars for Kite API compliance
        order_tag = truncate_tag_for_broker(tag or trade_id)
        exit_side = _exit_side(entry_side)

        # Circuit-band PLACEABILITY GATE (NOCIL fix, 22-Jun). FIX-190 (Bug D)
        # clamped SL/TGT into the band so the broker could not reject them, but
        # the clamp was side-agnostic: a LONG TGT recalc'd above the upper
        # circuit was clamped DOWN to upper*(1-margin), which for NOCIL landed
        # BELOW the entry fill -> an instantly-marketable SELL that scratched the
        # trade. clamp_exit_into_band now also verifies each leg lands on the
        # correct side of the fill:
        #   - SL unplaceable (clamp to/through fill = instant stop-out) -> the
        #     position cannot be stopped -> raise SLUnplaceableError; OrderPlacer
        #     emergency-closes + hard_kills (TGT not attempted; SL-first).
        #   - TGT unplaceable (clamp to wrong side of fill) -> benign, the SL
        #     still protects -> place SL only + hand the TGT to TGTRetryManager
        #     (FIX-190 Bug C path). *** THIS IS THE NOCIL FIX. ***
        # Best-effort on the band fetch: no circuit data -> place as-is (gate
        # fails open) and Bug C still handles any broker-side TGT rejection.
        upper_c, lower_c = self._circuit_limits(symbol)
        tgt_unplaceable = False
        sl_was_clamped = False
        tgt_was_clamped = False
        if upper_c or lower_c:
            sl_res = clamp_exit_into_band(
                sl_price, leg="SL", direction=entry_side, entry_fill=entry_fill,
                upper_circuit=upper_c, lower_circuit=lower_c,
            )
            if not sl_res.placeable:
                self._log.critical(
                    "limit_triple.sl_unplaceable_wrong_side",
                    extra={
                        "trade_id": trade_id, "symbol": symbol,
                        "sl_price": sl_price, "entry_fill": entry_fill,
                        "upper_circuit": upper_c, "lower_circuit": lower_c,
                        "reason": sl_res.reason,
                        "detail": "ENTRY filled; SL cannot be placed on the "
                                  "protective side of the fill; escalating to "
                                  "emergency close + hard_kill (no TGT attempted)",
                    },
                )
                raise SLUnplaceableError(
                    "SL clamp lands on the wrong side of the entry fill",
                    trade_id=trade_id, symbol=symbol, sl_price=sl_price,
                    entry_fill=entry_fill, reason=sl_res.reason,
                )
            tgt_res = clamp_exit_into_band(
                tgt_price, leg="TGT", direction=entry_side, entry_fill=entry_fill,
                upper_circuit=upper_c, lower_circuit=lower_c,
            )
            sl_price = sl_res.price
            sl_was_clamped = sl_res.was_clamped
            tgt_unplaceable = not tgt_res.placeable
            if not tgt_unplaceable:
                tgt_price = tgt_res.price
                tgt_was_clamped = tgt_res.was_clamped
            if sl_res.was_clamped or tgt_res.was_clamped or tgt_unplaceable:
                self._log.warning(
                    "limit_triple.exit_price_clamped_to_band",
                    extra={
                        "trade_id": trade_id, "symbol": symbol,
                        "upper_circuit": upper_c, "lower_circuit": lower_c,
                        "sl_clamped": sl_res.was_clamped,
                        "tgt_clamped": tgt_res.was_clamped,
                        "tgt_unplaceable": tgt_unplaceable,
                        "sl_price": sl_price, "tgt_price": tgt_price,
                    },
                )

        # ── Step 1: SL (stop-limit) for BOTH INTRADAY and DELIVERY ─────────
        # P0 (2026-06-15): Zerodha rejects SL-M orders via the API entirely,
        # not just for CNC. Every SL leg is now order_type="SL" (stop-limit)
        # with a limit price offset past the trigger so a triggered stop still
        # fills in a fast move. See calc_sl_limit_price / OPL7.
        sl_order_type = "SL"
        sl_limit_price = calc_sl_limit_price(
            exit_side=exit_side,
            trigger_price=sl_price,
            offset_pct=self._sl_limit_offset_pct,
        )

        try:
            sl_placed = self._adapter.place_order(
                symbol=symbol,
                side=exit_side,
                qty=qty,
                price=sl_limit_price,
                order_type=sl_order_type,
                intent=intent,
                tag=order_tag,
                trigger_price=sl_price,
            )
        except BrokerError as exc:
            # OPL3: SL failed. Caller must escalate — position is live, unprotected.
            self._log.critical(
                "limit_triple.sl_failed_position_unprotected",
                extra={
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "qty": qty,
                    "sl_price": sl_price,
                    "sl_order_type": sl_order_type,
                    "intent": intent,
                    "detail": "ENTRY already filled; SL placement failed; "
                              "TGT not attempted; caller must hard_kill and cancel any live position",
                },
            )
            log_exception(self._log, exc)
            raise

        if not sl_placed.broker_order_id:
            raise OrderRejectedError(
                "adapter returned empty broker_order_id for SL order",
                symbol=symbol, trade_id=trade_id, leg="SL",
            )

        self._log.info(
            "limit_triple.sl_placed",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": sl_placed.broker_order_id,
                "order_type": sl_order_type,
                "trigger_price": sl_price,
                "qty": qty,
                "intent": intent,
            },
        )

        # ── Step 2: TGT LIMIT ─────────────────────────────────────────────
        # FIX-190 (Bug C): a TGT-only failure with the SL standing is NOT a
        # protection breach — the position has a live stop. Return a partial
        # result (tgt_placed=False) instead of raising, so the caller keeps the
        # SL and does NOT emergency-exit / HARD_KILL the entire book.
        def _partial_sl_only() -> ExitLegsResult:
            return ExitLegsResult(
                sl_broker_order_id=sl_placed.broker_order_id,
                sl_internal_id=sl_placed.internal_order_id,
                sl_order_type=sl_order_type,
                sl_trigger_price=sl_price,
                sl_price=sl_limit_price,
                tgt_broker_order_id=None,
                tgt_internal_id=None,
                tgt_price=None,
                tgt_placed=False,
                sl_clamped=sl_was_clamped,   # SL still placed (may be clamped)
                tgt_clamped=False,           # no TGT placed in the SL-only path
            )

        # NOCIL fix: the placeability gate found the clamped TGT would sit on the
        # wrong side of the fill (instantly marketable). Do NOT place it — the SL
        # is live, so return SL-only; OrderPlacer routes the TGT to TGTRetryManager
        # (FIX-190 Bug C), which re-clamps against the band when it relaxes.
        if tgt_unplaceable:
            self._log.warning(
                "limit_triple.tgt_unplaceable_sl_standing",
                extra={
                    "trade_id": trade_id, "symbol": symbol, "qty": qty,
                    "sl_still_standing": sl_placed.broker_order_id,
                    "tgt_price": tgt_price, "entry_fill": entry_fill,
                    "detail": "clamped TGT would be wrong-side of the entry fill; "
                              "placing SL only; TGT deferred to retry",
                },
            )
            return _partial_sl_only()

        try:
            tgt_placed = self._adapter.place_order(
                symbol=symbol,
                side=exit_side,
                qty=qty,
                price=tgt_price,
                order_type="LIMIT",
                intent=intent,
                tag=order_tag,
            )
        except BrokerError as exc:
            self._log.critical(
                "limit_triple.tgt_failed_sl_standing",
                extra={
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "qty": qty,
                    "sl_still_standing": sl_placed.broker_order_id,
                    "tgt_price": tgt_price,
                    "detail": "TGT rejected; SL is live so the position remains "
                              "protected; NOT escalating (FIX-190 Bug C)",
                    "error": str(exc),
                },
            )
            log_exception(self._log, exc)
            return _partial_sl_only()

        if not tgt_placed.broker_order_id:
            self._log.critical(
                "limit_triple.tgt_failed_sl_standing",
                extra={
                    "trade_id": trade_id, "symbol": symbol, "qty": qty,
                    "sl_still_standing": sl_placed.broker_order_id,
                    "detail": "adapter returned empty broker_order_id for TGT; "
                              "SL live, position protected; NOT escalating (FIX-190 Bug C)",
                },
            )
            return _partial_sl_only()

        self._log.info(
            "limit_triple.tgt_placed",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": tgt_placed.broker_order_id,
                "price": tgt_price,
                "qty": qty,
            },
        )

        return ExitLegsResult(
            sl_broker_order_id=sl_placed.broker_order_id,
            sl_internal_id=sl_placed.internal_order_id,
            sl_order_type=sl_order_type,
            sl_trigger_price=sl_price,
            sl_price=sl_limit_price,
            tgt_broker_order_id=tgt_placed.broker_order_id,
            tgt_internal_id=tgt_placed.internal_order_id,
            tgt_price=tgt_price,
            sl_clamped=sl_was_clamped,
            tgt_clamped=tgt_was_clamped,
        )

    def place_tgt_only(
        self,
        *,
        symbol: str,
        entry_side: str,        # ENTRY side; TGT placed on the opposite side
        qty: int,
        tgt_price: float,
        intent: str,
        trade_id: str,
        tag: str = "",
        entry_fill: float = 0.0,  # actual entry fill — placeability-gate reference
    ) -> TgtOnlyResult:
        """
        TGT retry (Task 2026-06-19): place ONLY the TGT LIMIT leg for a trade
        whose SL is already standing (FIX-190 Bug C left it SL-only when the TGT
        could not be placed). Clamps the TGT into the circuit band (Bug D) —
        circuit limits change intraday, so a retry can succeed once the band
        relaxes. NEVER raises and NEVER touches the SL: a broker reject / empty
        broker_order_id returns ``placed=False`` so TGTRetryManager can schedule
        another attempt. The caller (OrderPlacer) owns DB persistence + fill_map
        registration so a filled retry-TGT triggers the software OCO.
        """
        order_tag = truncate_tag_for_broker(tag or trade_id)
        exit_side = _exit_side(entry_side)

        # Bug D: re-clamp on every retry against the CURRENT circuit band, and
        # gate against the fill (NOCIL fix). A clamp that lands on the wrong side
        # of the entry fill is UNPLACEABLE (the band is still too tight) -> return
        # placed=False so TGTRetryManager schedules another attempt; NEVER place a
        # wrong-side, instantly-marketable target.
        upper_c, lower_c = self._circuit_limits(symbol)
        clamped = False
        if upper_c or lower_c:
            res = clamp_exit_into_band(
                tgt_price, leg="TGT", direction=entry_side, entry_fill=entry_fill,
                upper_circuit=upper_c, lower_circuit=lower_c,
            )
            if not res.placeable:
                self._log.warning(
                    "limit_triple.tgt_retry_unplaceable_wrong_side",
                    extra={
                        "trade_id": trade_id, "symbol": symbol, "qty": qty,
                        "tgt_price": tgt_price, "entry_fill": entry_fill,
                        "reason": res.reason,
                        "detail": "clamped TGT still wrong-side of fill; "
                                  "not placing; will retry when band relaxes",
                    },
                )
                return TgtOnlyResult(placed=False, clamped=True, unplaceable=True)
            tgt_price = res.price
            clamped = res.was_clamped

        try:
            placed = self._adapter.place_order(
                symbol=symbol,
                side=exit_side,
                qty=qty,
                price=tgt_price,
                order_type="LIMIT",
                intent=intent,
                tag=order_tag,
            )
        except BrokerError as exc:
            self._log.warning(
                "limit_triple.tgt_retry_place_failed",
                extra={
                    "trade_id": trade_id, "symbol": symbol, "qty": qty,
                    "tgt_price": tgt_price, "error": str(exc),
                    "detail": "TGT retry rejected; SL untouched; will retry on schedule",
                },
            )
            log_exception(self._log, exc)
            return TgtOnlyResult(placed=False, clamped=clamped)

        if not placed.broker_order_id:
            self._log.warning(
                "limit_triple.tgt_retry_empty_broker_id",
                extra={"trade_id": trade_id, "symbol": symbol, "qty": qty},
            )
            return TgtOnlyResult(placed=False, clamped=clamped)

        self._log.info(
            "limit_triple.tgt_retry_placed",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": placed.broker_order_id,
                "price": tgt_price, "qty": qty,
            },
        )
        return TgtOnlyResult(
            placed=True,
            tgt_broker_order_id=placed.broker_order_id,
            tgt_internal_id=placed.internal_order_id,
            tgt_price=tgt_price,
            clamped=clamped,
        )
