"""
tests/unit/test_fix133_angelone_stub.py

FIX-133 Item 28: AngelOne adapter stub.
  - Stub has same interface methods as ZerodhaAdapter
  - All methods raise NotImplementedError
  - Config model loads correctly
"""
from __future__ import annotations

import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.angelone_adapter import AngelOneAdapter


class TestAngelOneStub:

    def test_place_order_raises(self) -> None:
        adapter = AngelOneAdapter()
        with pytest.raises(NotImplementedError, match="stub"):
            adapter.place_order("RELIANCE", "BUY", 10, "LIMIT", price=2500.0)
        print("  OK: place_order raises NotImplementedError")

    def test_cancel_order_raises(self) -> None:
        adapter = AngelOneAdapter()
        with pytest.raises(NotImplementedError, match="stub"):
            adapter.cancel_order("ORDER_123")
        print("  OK: cancel_order raises NotImplementedError")

    def test_modify_order_raises(self) -> None:
        adapter = AngelOneAdapter()
        with pytest.raises(NotImplementedError, match="stub"):
            adapter.modify_order("ORDER_123", trigger_price=2400.0)
        print("  OK: modify_order raises NotImplementedError")

    def test_get_positions_raises(self) -> None:
        adapter = AngelOneAdapter()
        with pytest.raises(NotImplementedError, match="stub"):
            adapter.get_positions()
        print("  OK: get_positions raises NotImplementedError")

    def test_get_order_status_raises(self) -> None:
        adapter = AngelOneAdapter()
        with pytest.raises(NotImplementedError, match="stub"):
            adapter.get_order_status("ORDER_123")
        print("  OK: get_order_status raises NotImplementedError")

    def test_has_all_interface_methods(self) -> None:
        """Verify AngelOneAdapter has same core methods as expected."""
        adapter = AngelOneAdapter()
        required = ["place_order", "cancel_order", "modify_order",
                     "get_positions", "get_order_status", "get_margins"]
        for method in required:
            assert hasattr(adapter, method), f"Missing method: {method}"
        print(f"  OK: AngelOneAdapter has all {len(required)} interface methods")


class TestBrokerConfig:

    def test_broker_config_in_system_config(self) -> None:
        from core.config_loader import BrokerConfig
        cfg = BrokerConfig()
        assert cfg.primary == "zerodha"
        assert cfg.fallback == "angelone"
        assert cfg.fallback_enabled is False
        print("  OK: BrokerConfig defaults correct")


if __name__ == "__main__":
    tests = [
        TestAngelOneStub().test_place_order_raises,
        TestAngelOneStub().test_cancel_order_raises,
        TestAngelOneStub().test_modify_order_raises,
        TestAngelOneStub().test_get_positions_raises,
        TestAngelOneStub().test_get_order_status_raises,
        TestAngelOneStub().test_has_all_interface_methods,
        TestBrokerConfig().test_broker_config_in_system_config,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
