"""
broker/cost_calculator.py — Trading System v2

Purpose:
    Compute itemized transaction costs for a single order leg.
    Used by the live engine (every order) and the paper engine (P&L simulation).

Locked Design Decisions:
    CC1  — Scope: brokerage + STT + exchange_txn + GST + SEBI + stamp_duty.
           Returns CostBreakdown (frozen dataclass). No P&L, no slippage.
    CC2  — API: calculate_cost(side, qty, price, product, exchange='NSE') -> CostBreakdown.
           CostBreakdown fields: brokerage, stt, exchange_txn, gst, sebi,
           stamp_duty, total, turnover (all float, INR).
           Only exchange="NSE" supported; ValueError for others (CC5).
    CC3  — Brokerage: MIS/CO → min(flat, pct/100*turnover); CNC BUY → 0;
           CNC SELL → min(flat, pct/100*turnover).
    CC4  — STT: MIS/CO SELL → stt_sell_pct/100*turnover; MIS/CO BUY → 0;
           CNC both sides → stt_cnc_pct/100*turnover.
    CC5  — Exchange txn: exchange_txn_pct/100*turnover (NSE only).
    CC6  — GST: gst_pct/100 * (brokerage + exchange_txn + sebi).
    CC7  — SEBI: sebi_pct/100 * turnover.
    CC8  — Stamp duty: SELL → 0; MIS/CO BUY → stamp_duty_mis_buy_pct/100*turnover;
           CNC BUY → stamp_duty_cnc_buy_pct/100*turnover.
    CC9  — All rates from injected BrokerCostsConfig. No hardcoded rates.
    CC10 — Each component rounded to 2dp via ROUND_HALF_UP (Decimal via str()).
           total = sum of rounded components (not round(raw_sum)).
    CC11 — Layer 2 (broker/). Stdlib: decimal, dataclasses. No new exception class.
    CC12 — total_round_trip_cost(qty, entry_price, exit_price, product) -> float
           convenience: calculate_cost("BUY") + calculate_cost("SELL").

What This Module Does NOT Do:
    - Does not compute slippage (handled by slippage_model consumers)
    - Does not compute P&L (paper_engine's job)
    - Does not read config files — caller injects BrokerCostsConfig
    - Does not support BSE or other exchanges (deferred to v2.1)
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from core.config_loader import BrokerCostsConfig


# ─────────────────────────────────────────────────────────────────────────────
# Result type (CC2)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CostBreakdown:
    """
    Itemized transaction cost for a single order leg. All amounts in INR.

    Fields:
        brokerage    — broker commission (CC3)
        stt          — Securities Transaction Tax (CC4)
        exchange_txn — exchange transaction charge (CC5)
        gst          — GST on (brokerage + exchange_txn + sebi) (CC6)
        sebi         — SEBI turnover fee (CC7)
        stamp_duty   — stamp duty on buy-side (CC8)
        total        — sum of all rounded components (CC10)
        turnover     — qty * price (gross trade value)
    """
    brokerage:    float
    stt:          float
    exchange_txn: float
    gst:          float
    sebi:         float
    stamp_duty:   float
    total:        float
    turnover:     float


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

_TWO_DP = Decimal("0.01")


def _d(value: float) -> Decimal:
    """Convert float → Decimal via str() to avoid floating-point representation error."""
    return Decimal(str(value))


def _r2(d: Decimal) -> Decimal:
    """Round to 2 decimal places using ROUND_HALF_UP (CC10)."""
    return d.quantize(_TWO_DP, rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

class CostCalculator:
    """
    Computes itemized Zerodha transaction costs per order leg.

    Instantiated once at startup with the loaded BrokerCostsConfig (CC9).
    All rate fields are read from the injected config — no rates are hardcoded.

    Usage::
        calc = CostCalculator(cfg.broker_costs)
        breakdown = calc.calculate_cost("BUY", qty=100, price=500.0, product="MIS")
        round_trip = calc.total_round_trip_cost(100, 500.0, 510.0, "MIS")
    """

    def __init__(self, costs: BrokerCostsConfig) -> None:
        self._z = costs.zerodha   # ZerodhaRatesConfig

    def calculate_cost(
        self,
        side: str,
        qty: int,
        price: float,
        product: str,
        exchange: str = "NSE",
        is_fno: bool = False,
        fno_kind: str = "FUTURES",
    ) -> CostBreakdown:
        """
        Compute itemized transaction cost for one order leg.

        Args:
            side:     "BUY" or "SELL"
            qty:      number of shares (must be > 0)
            price:    execution price per share (must be > 0)
            product:  "MIS" | "CO" | "CNC"
            exchange: only "NSE" is supported (CC5)
            is_fno:   Audit #13 — when True, STT/stamp-duty use FNO rates
                      instead of the cash-equity rates. Prevents phantom
                      equity taxes on options/futures that would otherwise
                      trip invariant checks (HARD_KILL).
            fno_kind: "FUTURES" (default) or "OPTIONS". Only consulted when
                      is_fno=True. Options STT is on premium, futures on
                      notional.

        Returns:
            CostBreakdown with all components rounded to 2dp and total = sum of components.

        Raises:
            ValueError: unknown side, product, exchange, or fno_kind.
        """
        if side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {side!r}. Must be 'BUY' or 'SELL'.")
        if product not in ("MIS", "CO", "CNC"):
            raise ValueError(f"Invalid product: {product!r}. Must be 'MIS', 'CO', or 'CNC'.")
        if exchange != "NSE":
            raise ValueError(
                f"Unsupported exchange: {exchange!r}. "
                "Only 'NSE' is currently supported (BSE deferred to v2.1)."
            )
        # FIX-112: Validate FNO segment is only available on NSE
        if is_fno and exchange != "NSE":
            raise ValueError(
                f"FNO segment (is_fno=True) requires exchange='NSE', got {exchange!r}. "
                "BSE does not support Futures & Options."
            )
        if is_fno and fno_kind not in ("FUTURES", "OPTIONS"):
            raise ValueError(
                f"Invalid fno_kind: {fno_kind!r}. Must be 'FUTURES' or 'OPTIONS'."
            )

        z = self._z
        turnover_d = _d(qty) * _d(price)

        # ── Brokerage (CC3) ───────────────────────────────────────────────────
        if product in ("MIS", "CO") or (product == "CNC" and side == "SELL"):
            proportional = _d(z.brokerage_pct_intraday) / _d("100") * turnover_d
            brokerage_d = _r2(min(_d(z.brokerage_flat_intraday), proportional))
        else:
            # CNC BUY → free (CC3)
            brokerage_d = _r2(Decimal("0"))

        # ── STT (CC4 + FIX-027) ───────────────────────────────────────────────
        if is_fno:
            if fno_kind == "FUTURES":
                # FIX-027: Futures STT applies to both BUY and SELL
                stt_d = _r2(_d(z.futures.stt_pct) / _d("100") * turnover_d)
            elif fno_kind == "OPTIONS":
                if side == "SELL":
                    # FIX-027: Options STT only on SELL side
                    stt_d = _r2(_d(z.options_sell.stt_pct) / _d("100") * turnover_d)
                else:
                    # FIX-027: Options BUY has zero STT
                    stt_d = _r2(_d(z.options_buy.stt_pct) / _d("100") * turnover_d)
            else:
                stt_d = _r2(Decimal("0"))
        elif product in ("MIS", "CO"):
            stt_d = _r2(
                _d(z.stt_sell_pct) / _d("100") * turnover_d
                if side == "SELL"
                else Decimal("0")
            )
        else:
            # CNC: both sides taxed at stt_cnc_pct (CC4)
            stt_d = _r2(_d(z.stt_cnc_pct) / _d("100") * turnover_d)

        # ── Exchange transaction charge (CC5) ─────────────────────────────────
        exchange_txn_d = _r2(_d(z.exchange_txn_pct) / _d("100") * turnover_d)

        # ── SEBI turnover fee (CC7) ───────────────────────────────────────────
        sebi_d = _r2(_d(z.sebi_pct) / _d("100") * turnover_d)

        # ── GST on (brokerage + exchange_txn + sebi) (CC6) ───────────────────
        gst_d = _r2(
            _d(z.gst_pct) / _d("100") * (brokerage_d + exchange_txn_d + sebi_d)
        )

        # ── Stamp duty — BUY side only (CC8 + FIX-027) ───────────────────────
        if side == "SELL":
            if is_fno and fno_kind == "OPTIONS":
                # FIX-027: Options sell has zero stamp duty
                stamp_duty_d = _r2(_d(z.options_sell.stamp_duty_pct) / _d("100") * turnover_d)
            else:
                stamp_duty_d = _r2(Decimal("0"))
        elif is_fno:
            # FIX-027: FNO BUY stamp duty
            if fno_kind == "FUTURES":
                stamp_duty_d = _r2(_d(z.futures.stamp_duty_buy_pct) / _d("100") * turnover_d)
            else:  # OPTIONS
                stamp_duty_d = _r2(_d(z.options_buy.stamp_duty_pct) / _d("100") * turnover_d)
        elif product in ("MIS", "CO"):
            stamp_duty_d = _r2(_d(z.stamp_duty_mis_buy_pct) / _d("100") * turnover_d)
        else:
            # CNC BUY
            stamp_duty_d = _r2(_d(z.stamp_duty_cnc_buy_pct) / _d("100") * turnover_d)

        # ── Total: sum of rounded components, not round(raw_sum) (CC10) ──────
        total_d = brokerage_d + stt_d + exchange_txn_d + sebi_d + gst_d + stamp_duty_d

        return CostBreakdown(
            brokerage=float(brokerage_d),
            stt=float(stt_d),
            exchange_txn=float(exchange_txn_d),
            gst=float(gst_d),
            sebi=float(sebi_d),
            stamp_duty=float(stamp_duty_d),
            total=float(total_d),
            turnover=float(turnover_d),
        )

    def total_round_trip_cost(
        self,
        qty: int,
        entry_price: float,
        exit_price: float,
        product: str,
        exchange: str = "NSE",
        is_fno: bool = False,
        fno_kind: str = "FUTURES",
    ) -> float:
        """
        Total cost of a complete trade: BUY entry + SELL exit (CC12).

        Used by the paper engine for realistic P&L accounting (Project Rule 15).

        Args:
            is_fno / fno_kind: Audit #13 — propagated to both legs so paper
                P&L reflects FNO statutory rates when trading derivatives.

        Returns:
            Sum of calculate_cost("BUY", ...).total + calculate_cost("SELL", ...).total
        """
        bd = self.round_trip_breakdown(
            qty, entry_price, exit_price, product, exchange,
            is_fno=is_fno, fno_kind=fno_kind,
        )
        return bd.total

    def round_trip_breakdown(
        self,
        qty: int,
        entry_price: float,
        exit_price: float,
        product: str,
        exchange: str = "NSE",
        is_fno: bool = False,
        fno_kind: str = "FUTURES",
    ) -> CostBreakdown:
        """Combined CostBreakdown for BUY entry + SELL exit (v14)."""
        buy = self.calculate_cost(
            "BUY", qty, entry_price, product, exchange,
            is_fno=is_fno, fno_kind=fno_kind,
        )
        sell = self.calculate_cost(
            "SELL", qty, exit_price, product, exchange,
            is_fno=is_fno, fno_kind=fno_kind,
        )
        return CostBreakdown(
            brokerage=round(buy.brokerage + sell.brokerage, 2),
            stt=round(buy.stt + sell.stt, 2),
            exchange_txn=round(buy.exchange_txn + sell.exchange_txn, 2),
            gst=round(buy.gst + sell.gst, 2),
            sebi=round(buy.sebi + sell.sebi, 2),
            stamp_duty=round(buy.stamp_duty + sell.stamp_duty, 2),
            total=round(buy.total + sell.total, 2),
            turnover=round(buy.turnover + sell.turnover, 2),
        )


def round_trip_costs_or_zero(
    calculator: Optional[CostCalculator],
    *,
    qty: int,
    entry_price: float,
    exit_price: float,
    product: str,
    logger,
    context: str,
) -> float:
    """E4 (2026-07-17): round-trip costs for a backstop close path, fail-OPEN.

    The backstop close paths (order_reconciler CHECK1/CHECK4, cnc_gtt_monitor)
    hardcoded ``costs=0.0`` because they were never wired a CostCalculator — so
    their fm_ledger.pnl_delta was written GROSS while every other row was NET.
    This is the single wiring point that closes that gap.

    FAIL-OPEN BY DESIGN: a cost-calculation failure must NEVER block a capital
    release. On the backstop paths a raised exception would leave the closed
    position's margin locked in ``used`` for the rest of the session (starving
    sizing) — strictly worse than booking a slightly-wrong cost. So a failure
    (or a missing calculator) degrades to 0.0, which reverts that ONE row to
    the pre-fix gross behaviour, and is logged LOUDLY so it is attributable
    rather than silent. Mirrors order_placer.py's existing policy on the normal
    exit path deliberately — one policy, not two.

    Returns the round-trip total (CC12), or 0.0 if it could not be computed.
    """
    if calculator is None:
        logger.error(
            "cost_calc_unavailable: no CostCalculator wired; costs=0.0 "
            "(pnl_delta for this close is GROSS) context=%s",
            context,
        )
        return 0.0
    try:
        return float(
            calculator.total_round_trip_cost(
                qty=int(qty),
                entry_price=float(entry_price),
                exit_price=float(exit_price),
                product=product,
            )
        )
    except Exception as exc:  # noqa: BLE001 — fail-open, see docstring
        logger.error(
            "cost_calc_failed: costs=0.0 (pnl_delta for this close is GROSS) "
            "context=%s product=%s: %s",
            context, product, exc,
        )
        return 0.0
