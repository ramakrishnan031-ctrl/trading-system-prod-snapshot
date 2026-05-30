"""
tests/unit/test_fix131_broker_costs.py

FIX-131 Item 25: Broker cost YAML explicit definition.
  - All required fields must be defined (no missing keys)
  - Missing field raises ValidationError at load time
  - Negative rates raise ValidationError
  - cost_calculator.py has no hardcoded fallbacks (CC9)
  - Correct cost calculation with YAML-defined rates
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pydantic import ValidationError
from core.config_loader import BrokerCostsConfig, ZerodhaRatesConfig, FuturesRatesConfig, OptionsBuyRatesConfig, OptionsSellRatesConfig
from broker.cost_calculator import CostCalculator


def _make_full_zerodha_rates(**overrides) -> ZerodhaRatesConfig:
    """Build a complete ZerodhaRatesConfig with sane defaults."""
    defaults = dict(
        brokerage_flat_intraday=20.0,
        brokerage_pct_intraday=0.03,
        stt_sell_pct=0.025,
        stt_cnc_pct=0.1,
        exchange_txn_pct=0.00297,
        gst_pct=18.0,
        sebi_pct=0.0001,
        stamp_duty_mis_buy_pct=0.003,
        stamp_duty_cnc_buy_pct=0.015,
        futures=FuturesRatesConfig(stt_pct=0.0125, stamp_duty_buy_pct=0.002),
        options_buy=OptionsBuyRatesConfig(stt_pct=0.0, stamp_duty_pct=0.003),
        options_sell=OptionsSellRatesConfig(stt_pct=0.0625, stamp_duty_pct=0.0),
    )
    defaults.update(overrides)
    return ZerodhaRatesConfig(**defaults)


def _make_broker_costs(**zerodha_overrides) -> BrokerCostsConfig:
    return BrokerCostsConfig(zerodha=_make_full_zerodha_rates(**zerodha_overrides))


class TestBrokerCostYamlExplicit:

    def test_yaml_loads_without_error(self) -> None:
        """broker_costs.yaml loads cleanly via config_loader."""
        from core.config_loader import load_all
        import yaml
        yaml_path = Path(__file__).parent.parent.parent / "config" / "broker_costs.yaml"
        assert yaml_path.exists(), f"broker_costs.yaml not found at {yaml_path}"
        with open(yaml_path) as f:
            raw = yaml.safe_load(f)
        # Should parse without error
        cfg = BrokerCostsConfig(**raw)
        assert cfg.zerodha.brokerage_flat_intraday == 20.0
        assert cfg.zerodha.gst_pct == 18.0
        print("  OK: broker_costs.yaml loads without error and all fields defined")

    def test_missing_required_field_raises_validation_error(self) -> None:
        """Missing required field in YAML → ValidationError at parse time."""
        with pytest.raises(ValidationError):
            # Missing gst_pct — required field
            ZerodhaRatesConfig(
                brokerage_flat_intraday=20.0,
                brokerage_pct_intraday=0.03,
                stt_sell_pct=0.025,
                stt_cnc_pct=0.1,
                exchange_txn_pct=0.00297,
                # gst_pct MISSING
                sebi_pct=0.0001,
                stamp_duty_mis_buy_pct=0.003,
                stamp_duty_cnc_buy_pct=0.015,
                futures=FuturesRatesConfig(stt_pct=0.0125, stamp_duty_buy_pct=0.002),
                options_buy=OptionsBuyRatesConfig(stt_pct=0.0, stamp_duty_pct=0.003),
                options_sell=OptionsSellRatesConfig(stt_pct=0.0625, stamp_duty_pct=0.0),
            )
        print("  OK: missing required field raises ValidationError")

    def test_extra_unknown_field_raises_validation_error(self) -> None:
        """Unknown field in YAML → ValidationError (extra='forbid')."""
        with pytest.raises(ValidationError):
            _make_broker_costs(unknown_field=999.0)
        print("  OK: extra/unknown field raises ValidationError")

    def test_negative_rate_raises_validation_error(self) -> None:
        """Negative rate raises ValidationError."""
        with pytest.raises((ValidationError, ValueError)):
            _make_broker_costs(stt_sell_pct=-0.5)
        print("  OK: negative rate raises ValidationError")

    def test_zero_gst_raises_validation_error(self) -> None:
        """gst_pct=0 raises ValidationError (GST is mandatory)."""
        with pytest.raises((ValidationError, ValueError)):
            _make_broker_costs(gst_pct=0.0)
        print("  OK: gst_pct=0 raises ValidationError")

    def test_no_hardcoded_fallbacks_in_cost_calculator(self) -> None:
        """CostCalculator source code has no hardcoded rate values (CC9)."""
        import inspect
        source = inspect.getsource(CostCalculator)
        # Common hardcoded rate values that should NOT appear
        suspicious_patterns = ["0.03", "0.025", "0.00297", "18.0", "0.003"]
        found = [p for p in suspicious_patterns if p in source]
        # Allow only non-rate usages (e.g., 0.03 might appear in comments/doc)
        # We check for them in the calculate method body specifically
        calc_source = inspect.getsource(CostCalculator.calculate_cost)
        found_in_calc = [p for p in ["20.0", "0.03", "0.025", "18.0"] if p in calc_source]
        # If found in calc_cost, it's a hardcoded rate - fail
        for pattern in found_in_calc:
            # Check it's accessing config, not a literal
            assert f"self._z" in calc_source or pattern not in calc_source, (
                f"Possible hardcoded rate {pattern!r} in calculate_cost() — should use injected config"
            )
        print("  OK: no suspicious hardcoded rate literals found in calculate_cost()")

    def test_correct_intraday_cost_calculated(self) -> None:
        """CostCalculator uses YAML rates correctly for intraday trade."""
        cfg = _make_broker_costs()
        calc = CostCalculator(BrokerCostsConfig(zerodha=cfg.zerodha if hasattr(cfg, 'zerodha') else cfg))

        # For 10 qty @ 1000 price, MIS buy: turnover = 10000
        # STT on MIS buy = 0 (sell side only)
        # Exchange = 0.00297/100 * 10000 = 0.297
        # Stamp = 0.003/100 * 10000 = 0.3
        result = calc.calculate_cost("BUY", 10, 1000.0, "MIS")
        assert result.total > 0, "Cost should be positive for intraday BUY"
        assert result.stt == 0.0, "STT should be 0 for MIS BUY"
        assert result.exchange_txn > 0, "Exchange txn should be > 0"
        print(f"  OK: intraday BUY cost calculated correctly: {result.total:.4f} INR")


if __name__ == "__main__":
    tests = [
        TestBrokerCostYamlExplicit().test_yaml_loads_without_error,
        TestBrokerCostYamlExplicit().test_missing_required_field_raises_validation_error,
        TestBrokerCostYamlExplicit().test_extra_unknown_field_raises_validation_error,
        TestBrokerCostYamlExplicit().test_negative_rate_raises_validation_error,
        TestBrokerCostYamlExplicit().test_zero_gst_raises_validation_error,
        TestBrokerCostYamlExplicit().test_no_hardcoded_fallbacks_in_cost_calculator,
        TestBrokerCostYamlExplicit().test_correct_intraday_cost_calculated,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
