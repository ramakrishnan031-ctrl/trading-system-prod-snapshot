"""
tests/unit/test_fix133_multi_account.py

FIX-133 Item 29: Multi-account capital splitting (foundation).
  - Multi-account config detected -> warning logged
  - Single account -> no warning
  - accounts_multi_example.csv exists
"""
from __future__ import annotations

import csv
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.account_registry import AccountRegistry


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "account_id", "broker", "label", "is_primary",
        "api_key_env", "api_secret_env", "totp_secret_env",
        "paper_capital", "capital_share_pct", "enabled",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


class TestMultiAccountDetection:

    def test_multi_account_logs_warning(self, tmp_path, caplog) -> None:
        """Multiple enabled accounts -> warning logged."""
        csv_path = tmp_path / "accounts.csv"
        _write_csv(csv_path, [
            {"account_id": "A1", "broker": "zerodha", "label": "Primary",
             "is_primary": "TRUE", "api_key_env": "K1", "api_secret_env": "S1",
             "totp_secret_env": "T1", "paper_capital": "100000",
             "capital_share_pct": "0.6", "enabled": "TRUE"},
            {"account_id": "A2", "broker": "zerodha", "label": "Secondary",
             "is_primary": "FALSE", "api_key_env": "K2", "api_secret_env": "S2",
             "totp_secret_env": "T2", "paper_capital": "100000",
             "capital_share_pct": "0.4", "enabled": "TRUE"},
        ])

        with caplog.at_level(logging.WARNING):
            registry = AccountRegistry.load(csv_path)

        assert len(registry.get_enabled_accounts()) == 2
        assert any("multi_account.detected" in r.message for r in caplog.records), \
            "Expected multi_account.detected warning"
        print("  OK: multi-account -> warning logged")

    def test_single_account_no_warning(self, tmp_path, caplog) -> None:
        """Single enabled account -> no warning."""
        csv_path = tmp_path / "accounts.csv"
        _write_csv(csv_path, [
            {"account_id": "A1", "broker": "zerodha", "label": "Primary",
             "is_primary": "TRUE", "api_key_env": "K1", "api_secret_env": "S1",
             "totp_secret_env": "T1", "paper_capital": "100000",
             "capital_share_pct": "1", "enabled": "TRUE"},
        ])

        with caplog.at_level(logging.WARNING):
            registry = AccountRegistry.load(csv_path)

        assert len(registry.get_enabled_accounts()) == 1
        assert not any("multi_account" in r.message for r in caplog.records), \
            "No multi_account warning for single account"
        print("  OK: single account -> no warning")

    def test_example_csv_exists(self) -> None:
        """accounts_multi_example.csv file exists."""
        path = Path("config/accounts_multi_example.csv")
        assert path.exists(), f"{path} not found"

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) >= 2, "Example should have at least 2 accounts"
        print(f"  OK: accounts_multi_example.csv exists with {len(rows)} accounts")


if __name__ == "__main__":
    import tempfile
    tests = [
        ("example_csv", TestMultiAccountDetection().test_example_csv_exists),
    ]
    passed = 0
    for name, t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {name}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
