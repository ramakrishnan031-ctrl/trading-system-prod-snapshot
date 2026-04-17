"""
tests/unit/test_cost_calculator.py

Validates broker/cost_calculator.py against CC1-CC12 locked decisions:
  - MIS BUY at 100 qty, 500 price -> known itemized breakdown (CC1-CC10)
  - MIS SELL at same -> STT applies on sell; stamp_duty = 0 (CC4, CC8)
  - CNC BUY -> brokerage=0; STT = 0.1%; stamp_duty = 0.015% (CC3, CC4, CC8)
  - CNC SELL -> brokerage applies; STT = 0.1%; stamp_duty = 0 (CC3, CC4, CC8)
  - Brokerage cap: large order -> brokerage = 20.00 flat (CC3)
  - Brokerage proportional: tiny order -> min is 0.03% of turnover (CC3)
  - GST = 18% on (brokerage + exchange_txn + sebi) only (CC6)
  - total = sum of rounded components, not round(raw_sum) (CC10)
  - total_round_trip_cost: known input -> known output (CC12)
  - Unknown exchange raises ValueError (CC2, CC5)
  - Unknown side raises ValueError (CC2)
  - Unknown product raises ValueError (CC2)
  - Rates pulled from injected BrokerCostsConfig; mutating config changes output (CC9)

All tests build a mock BrokerCostsConfig with the same values as the real stub
(broker_costs.yaml) so results are deterministic regardless of file changes.

Run: python tests/unit/test_cost_calculator.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.cost_calculator import CostBreakdown, CostCalculator


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — build mock BrokerCostsConfig with standard stub rates
# ─────────────────────────────────────────────────────────────────────────────

def _make_zerodha(
    brokerage_flat_intraday: float = 20.0,
    brokerage_pct_intraday:  float = 0.03,
    stt_sell_pct:            float = 0.025,
    stt_cnc_pct:             float = 0.1,
    exchange_txn_pct:        float = 0.00297,
    gst_pct:                 float = 18.0,
    sebi_pct:                float = 0.0001,
    stamp_duty_mis_buy_pct:  float = 0.003,
    stamp_duty_cnc_buy_pct:  float = 0.015,
) -> MagicMock:
    z = MagicMock()
    z.brokerage_flat_intraday = brokerage_flat_intraday
    z.brokerage_pct_intraday  = brokerage_pct_intraday
    z.stt_sell_pct            = stt_sell_pct
    z.stt_cnc_pct             = stt_cnc_pct
    z.exchange_txn_pct        = exchange_txn_pct
    z.gst_pct                 = gst_pct
    z.sebi_pct                = sebi_pct
    z.stamp_duty_mis_buy_pct  = stamp_duty_mis_buy_pct
    z.stamp_duty_cnc_buy_pct  = stamp_duty_cnc_buy_pct
    return z


def _make_calc(**zerodha_overrides) -> CostCalculator:
    costs = MagicMock()
    costs.zerodha = _make_zerodha(**zerodha_overrides)
    return CostCalculator(costs)


# ─────────────────────────────────────────────────────────────────────────────
# Tests — MIS BUY (CC1-CC10)
# Pre-computed reference: 100 qty @ 500, MIS BUY, NSE
#   turnover = 50000
#   brokerage = min(20, 0.03/100*50000) = min(20, 15) = 15.00
#   stt = 0  (MIS BUY)
#   exchange_txn = 0.00297/100*50000 = 1.485 -> 1.49
#   sebi = 0.0001/100*50000 = 0.05
#   gst = 18/100*(15.00+1.49+0.05) = 18%*16.54 = 2.9772 -> 2.98
#   stamp_duty = 0.003/100*50000 = 1.50
#   total = 15.00+0.00+1.49+0.05+2.98+1.50 = 21.02
# ─────────────────────────────────────────────────────────────────────────────

def test_mis_buy_breakdown() -> None:
    calc = _make_calc()
    b = calc.calculate_cost("BUY", 100, 500.0, "MIS")
    assert isinstance(b, CostBreakdown)
    assert b.turnover     == 50000.0
    assert b.brokerage    == 15.00
    assert b.stt          == 0.00
    assert b.exchange_txn == 1.49
    assert b.sebi         == 0.05
    assert b.gst          == 2.98
    assert b.stamp_duty   == 1.50
    assert b.total        == 21.02
    print("  OK MIS BUY 100@500: full breakdown verified (CC1-CC10)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — MIS SELL (CC4, CC8)
# Pre-computed: 100 qty @ 500, MIS SELL
#   brokerage = min(20, 15) = 15.00
#   stt = 0.025/100*50000 = 12.50  (SELL side taxed for MIS)
#   exchange_txn = 1.49
#   sebi = 0.05
#   gst = 18%*(15.00+1.49+0.05) = 2.98
#   stamp_duty = 0  (SELL side)
#   total = 15.00+12.50+1.49+0.05+2.98+0.00 = 32.02
# ─────────────────────────────────────────────────────────────────────────────

def test_mis_sell_breakdown() -> None:
    calc = _make_calc()
    b = calc.calculate_cost("SELL", 100, 500.0, "MIS")
    assert b.brokerage    == 15.00
    assert b.stt          == 12.50,  f"STT should be 12.50 for MIS SELL, got {b.stt}"
    assert b.exchange_txn == 1.49
    assert b.sebi         == 0.05
    assert b.gst          == 2.98
    assert b.stamp_duty   == 0.00,   f"stamp_duty must be 0 on SELL, got {b.stamp_duty}"
    assert b.total        == 32.02
    print("  OK MIS SELL 100@500: STT on sell; stamp_duty=0 (CC4, CC8)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — CNC BUY (CC3, CC4, CC8)
# Pre-computed: 100 qty @ 500, CNC BUY
#   brokerage = 0 (CNC BUY is free)
#   stt = 0.1/100*50000 = 50.00
#   exchange_txn = 1.49
#   sebi = 0.05
#   gst = 18%*(0+1.49+0.05) = 18%*1.54 = 0.2772 -> 0.28
#   stamp_duty = 0.015/100*50000 = 7.50
#   total = 0.00+50.00+1.49+0.05+0.28+7.50 = 59.32
# ─────────────────────────────────────────────────────────────────────────────

def test_cnc_buy_breakdown() -> None:
    calc = _make_calc()
    b = calc.calculate_cost("BUY", 100, 500.0, "CNC")
    assert b.brokerage    == 0.00,  f"CNC BUY brokerage must be 0, got {b.brokerage}"
    assert b.stt          == 50.00, f"CNC BUY STT=0.1% of 50000=50, got {b.stt}"
    assert b.exchange_txn == 1.49
    assert b.sebi         == 0.05
    assert b.gst          == 0.28,  f"GST on CNC BUY: 18%*(0+1.49+0.05)=0.28, got {b.gst}"
    assert b.stamp_duty   == 7.50,  f"CNC BUY stamp=0.015%*50000=7.50, got {b.stamp_duty}"
    assert b.total        == 59.32
    print("  OK CNC BUY 100@500: brokerage=0; STT=0.1%; stamp_duty=0.015% (CC3, CC4, CC8)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — CNC SELL (CC3, CC4, CC8)
# Pre-computed: 100 qty @ 500, CNC SELL
#   brokerage = min(20, 15) = 15.00 (CNC SELL pays brokerage)
#   stt = 0.1/100*50000 = 50.00
#   exchange_txn = 1.49
#   sebi = 0.05
#   gst = 18%*(15.00+1.49+0.05) = 2.98
#   stamp_duty = 0 (SELL side)
#   total = 15.00+50.00+1.49+0.05+2.98+0.00 = 69.52
# ─────────────────────────────────────────────────────────────────────────────

def test_cnc_sell_breakdown() -> None:
    calc = _make_calc()
    b = calc.calculate_cost("SELL", 100, 500.0, "CNC")
    assert b.brokerage    == 15.00
    assert b.stt          == 50.00
    assert b.exchange_txn == 1.49
    assert b.sebi         == 0.05
    assert b.gst          == 2.98
    assert b.stamp_duty   == 0.00
    assert b.total        == 69.52
    print("  OK CNC SELL 100@500: brokerage applies; STT=0.1%; stamp_duty=0 (CC3, CC4, CC8)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — CO product treated same as MIS (CC3)
# ─────────────────────────────────────────────────────────────────────────────

def test_co_buy_brokerage_same_as_mis() -> None:
    calc = _make_calc()
    mis = calc.calculate_cost("BUY", 100, 500.0, "MIS")
    co  = calc.calculate_cost("BUY", 100, 500.0, "CO")
    assert co.brokerage   == mis.brokerage
    assert co.stt         == mis.stt
    assert co.stamp_duty  == mis.stamp_duty
    assert co.total       == mis.total
    print("  OK CO product has identical cost structure to MIS (CC3)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Brokerage cap (CC3)
# 100,000 qty @ 1,000: turnover = 100,000,000
#   proportional = 0.03/100 * 1e8 = 30,000 -> capped at 20.00
# ─────────────────────────────────────────────────────────────────────────────

def test_brokerage_capped_at_flat_rate() -> None:
    calc = _make_calc()
    b = calc.calculate_cost("BUY", 100_000, 1000.0, "MIS")
    assert b.brokerage == 20.00, (
        f"Brokerage must be capped at 20 flat for large orders, got {b.brokerage}"
    )
    print("  OK Brokerage capped at 20 for 100,000 qty @ 1,000 (CC3)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Brokerage proportional (CC3)
# 1 qty @ 100: turnover = 100
#   proportional = 0.03/100 * 100 = 0.03 < 20 -> brokerage = 0.03
# ─────────────────────────────────────────────────────────────────────────────

def test_brokerage_proportional_below_flat_cap() -> None:
    calc = _make_calc()
    b = calc.calculate_cost("BUY", 1, 100.0, "MIS")
    assert b.brokerage == 0.03, (
        f"Tiny order: brokerage = 0.03%*100 = 0.03, got {b.brokerage}"
    )
    print("  OK Brokerage = 0.03% of turnover when below 20 flat cap (CC3)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — GST only on brokerage + exchange_txn + sebi (CC6)
# ─────────────────────────────────────────────────────────────────────────────

def test_gst_computed_on_correct_base() -> None:
    calc = _make_calc()
    b = calc.calculate_cost("BUY", 100, 500.0, "MIS")
    # GST base = brokerage + exchange_txn + sebi (NOT stt, NOT stamp_duty)
    expected_gst_base = b.brokerage + b.exchange_txn + b.sebi  # 15.00 + 1.49 + 0.05 = 16.54
    expected_gst = round(expected_gst_base * 0.18, 2)          # 2.9772 -> 2.98
    assert b.gst == expected_gst, (
        f"GST should be 18% of (brokerage+exch+sebi)={expected_gst_base}, "
        f"expected {expected_gst}, got {b.gst}"
    )
    # STT is NOT part of the GST base
    assert b.stt == 0.0  # confirm STT is 0 for this case
    print(f"  OK GST = 18% of (brokerage={b.brokerage}+exch={b.exchange_txn}+sebi={b.sebi}) = {b.gst} (CC6)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — total = sum of rounded components (CC10)
# ─────────────────────────────────────────────────────────────────────────────

def test_total_equals_sum_of_rounded_components() -> None:
    calc = _make_calc()
    for side in ("BUY", "SELL"):
        for product in ("MIS", "CNC"):
            b = calc.calculate_cost(side, 100, 500.0, product)
            component_sum = round(
                b.brokerage + b.stt + b.exchange_txn + b.sebi + b.gst + b.stamp_duty,
                10  # high precision to detect floating-point drift
            )
            assert abs(b.total - component_sum) < 1e-9, (
                f"{side} {product}: total={b.total} != component_sum={component_sum}"
            )
    print("  OK total == sum of rounded components for all 4 side/product combinations (CC10)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — total_round_trip_cost (CC12)
# Pre-computed: 100@500 BUY + 100@510 SELL, MIS
#   BUY total  = 21.02
#   SELL total = 32.64  (turnover=51000, brokerage=15.30, stt=12.75,
#                        exch=1.51, sebi=0.05, gst=3.03, stamp=0)
#   round_trip = 53.66
# ─────────────────────────────────────────────────────────────────────────────

def test_total_round_trip_cost_known_value() -> None:
    calc = _make_calc()
    rt = calc.total_round_trip_cost(100, 500.0, 510.0, "MIS")
    assert abs(rt - 53.66) < 1e-9, f"Expected round-trip=53.66, got {rt}"
    print("  OK total_round_trip_cost(100, 500->510, MIS) = 53.66 (CC12)")


def test_total_round_trip_equals_buy_plus_sell() -> None:
    calc = _make_calc()
    buy  = calc.calculate_cost("BUY",  100, 500.0, "CNC")
    sell = calc.calculate_cost("SELL", 100, 510.0, "CNC")
    rt   = calc.total_round_trip_cost(100, 500.0, 510.0, "CNC")
    assert abs(rt - (buy.total + sell.total)) < 1e-9
    print("  OK total_round_trip_cost == buy.total + sell.total for CNC (CC12)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — invalid inputs raise ValueError (CC2, CC5)
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_exchange_raises_value_error() -> None:
    calc = _make_calc()
    raised = False
    try:
        calc.calculate_cost("BUY", 100, 500.0, "MIS", exchange="BSE")
    except ValueError as exc:
        raised = True
        assert "BSE" in str(exc)
    assert raised, "Expected ValueError for exchange='BSE'"
    print("  OK Unknown exchange 'BSE' raises ValueError (CC2, CC5)")


def test_unknown_side_raises_value_error() -> None:
    calc = _make_calc()
    raised = False
    try:
        calc.calculate_cost("LONG", 100, 500.0, "MIS")
    except ValueError:
        raised = True
    assert raised, "Expected ValueError for side='LONG'"
    print("  OK Unknown side 'LONG' raises ValueError (CC2)")


def test_unknown_product_raises_value_error() -> None:
    calc = _make_calc()
    raised = False
    try:
        calc.calculate_cost("BUY", 100, 500.0, "NRML")
    except ValueError:
        raised = True
    assert raised, "Expected ValueError for product='NRML'"
    print("  OK Unknown product 'NRML' raises ValueError (CC2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — rates from config, not hardcoded (CC9)
# Mutate exchange_txn_pct from 0.00297 -> 0.00594 (double)
# Expected exchange_txn changes from 1.49 -> 2.97 for 100@500 MIS BUY
# Expected GST changes: 18%*(15.00+2.97+0.05) = 18%*18.02 = 3.2436 -> 3.24
# Expected total: 15.00+0.00+2.97+0.05+3.24+1.50 = 22.76
# ─────────────────────────────────────────────────────────────────────────────

def test_rates_from_injected_config_not_hardcoded() -> None:
    # Standard config
    calc_std = _make_calc()
    b_std = calc_std.calculate_cost("BUY", 100, 500.0, "MIS")
    assert b_std.exchange_txn == 1.49
    assert b_std.total == 21.02

    # Mutated config: exchange_txn_pct doubled
    calc_mut = _make_calc(exchange_txn_pct=0.00594)
    b_mut = calc_mut.calculate_cost("BUY", 100, 500.0, "MIS")
    assert b_mut.exchange_txn == 2.97, f"Expected 2.97, got {b_mut.exchange_txn}"
    assert b_mut.gst          == 3.24, f"Expected GST 3.24, got {b_mut.gst}"
    assert b_mut.total        == 22.76, f"Expected total 22.76, got {b_mut.total}"

    assert b_mut.total != b_std.total, "Mutated config must produce different total"
    print("  OK Mutating exchange_txn_pct changes output; rates are not hardcoded (CC9)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_mis_buy_breakdown,
        test_mis_sell_breakdown,
        test_cnc_buy_breakdown,
        test_cnc_sell_breakdown,
        test_co_buy_brokerage_same_as_mis,
        test_brokerage_capped_at_flat_rate,
        test_brokerage_proportional_below_flat_cap,
        test_gst_computed_on_correct_base,
        test_total_equals_sum_of_rounded_components,
        test_total_round_trip_cost_known_value,
        test_total_round_trip_equals_buy_plus_sell,
        test_unknown_exchange_raises_value_error,
        test_unknown_side_raises_value_error,
        test_unknown_product_raises_value_error,
        test_rates_from_injected_config_not_hardcoded,
    ]

    print("=" * 70)
    print("cost_calculator.py — Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
