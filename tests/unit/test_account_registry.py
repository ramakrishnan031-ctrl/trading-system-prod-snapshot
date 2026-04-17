"""
tests/unit/test_account_registry.py

Validates core/account_registry.py against AR1-AR9 locked decisions.

Run: python -m pytest tests/unit/test_account_registry.py -v
Or:  python tests/unit/test_account_registry.py  (standalone)
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.exceptions import ConfigMissingError, ConfigSchemaError
from core.account_registry import AccountRegistry, AccountRow


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_HDR = "account_id,broker,label,is_primary,api_key_env,api_secret_env,totp_secret_env,paper_capital,capital_share_pct,enabled"

_VALID_CSV = f"""\
{_HDR}
primary,zerodha,Primary Trading Account,true,ZD_KEY,ZD_SECRET,ZD_TOTP,5000000,1.0,true
"""

_TWO_ACCOUNTS_CSV = f"""\
{_HDR}
primary,zerodha,Primary Trading Account,true,ZD_KEY,ZD_SECRET,ZD_TOTP,5000000,1.0,true
secondary,zerodha,Secondary Account,false,ZD_KEY2,ZD_SECRET2,ZD_TOTP2,5000000,0.0,false
"""

_NO_PRIMARY_CSV = f"""\
{_HDR}
primary,zerodha,Primary Trading Account,false,ZD_KEY,ZD_SECRET,ZD_TOTP,5000000,1.0,true
"""

_TWO_PRIMARY_CSV = f"""\
{_HDR}
acct1,zerodha,Account One,true,ZD_KEY1,ZD_SEC1,ZD_TOTP1,5000000,0.5,true
acct2,zerodha,Account Two,true,ZD_KEY2,ZD_SEC2,ZD_TOTP2,5000000,0.5,true
"""


def _write_csv(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "accounts.csv"
    p.write_text(content, encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# AR6: load() tests
# ─────────────────────────────────────────────────────────────────────────────

def test_load_valid_csv(tmp_path: Path) -> None:
    """load() with valid single-account CSV returns populated registry (AR6)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    registry = AccountRegistry.load(p)
    assert registry.count() == 1
    print("  OK load() valid CSV -> 1 row")


def test_load_missing_file_raises(tmp_path: Path) -> None:
    """load() on absent file raises ConfigMissingError (AR6)."""
    import pytest
    with pytest.raises(ConfigMissingError):
        AccountRegistry.load(tmp_path / "no_such_file.csv")
    print("  OK load() missing file -> ConfigMissingError")


def test_load_missing_column_raises(tmp_path: Path) -> None:
    """Missing required column -> ConfigSchemaError (AR6)."""
    import pytest
    bad = "account_id,broker\nprimary,zerodha\n"
    p = _write_csv(tmp_path, bad)
    with pytest.raises(ConfigSchemaError):
        AccountRegistry.load(p)
    print("  OK load() missing columns -> ConfigSchemaError")


def test_load_empty_account_id_raises(tmp_path: Path) -> None:
    """Empty account_id -> ConfigSchemaError (AR6)."""
    import pytest
    bad = f"{_HDR}\n,zerodha,Some Account,true,K,S,T,5000000,1.0,true\n"
    p = _write_csv(tmp_path, bad)
    with pytest.raises(ConfigSchemaError):
        AccountRegistry.load(p)
    print("  OK load() empty account_id -> ConfigSchemaError")


def test_load_no_primary_raises(tmp_path: Path) -> None:
    """No is_primary=true row -> ConfigSchemaError (AR3 + AR6)."""
    import pytest
    p = _write_csv(tmp_path, _NO_PRIMARY_CSV)
    with pytest.raises(ConfigSchemaError):
        AccountRegistry.load(p)
    print("  OK load() no primary -> ConfigSchemaError")


def test_load_two_primaries_raises(tmp_path: Path) -> None:
    """More than one is_primary=true row -> ConfigSchemaError (AR3 + AR6)."""
    import pytest
    p = _write_csv(tmp_path, _TWO_PRIMARY_CSV)
    with pytest.raises(ConfigSchemaError):
        AccountRegistry.load(p)
    print("  OK load() two primaries -> ConfigSchemaError")


# ─────────────────────────────────────────────────────────────────────────────
# AR2: AccountRow type correctness
# ─────────────────────────────────────────────────────────────────────────────

def test_row_types_correct(tmp_path: Path) -> None:
    """AccountRow fields have correct Python types after load (AR2)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    registry = AccountRegistry.load(p)
    row = registry.primary()
    assert isinstance(row.account_id, str)
    assert isinstance(row.broker, str)
    assert isinstance(row.label, str)
    assert isinstance(row.is_primary, bool)
    assert isinstance(row.api_key_env, str)
    assert isinstance(row.paper_capital, float)
    assert isinstance(row.enabled, bool)
    print("  OK AccountRow field types correct (AR2)")


def test_row_is_frozen(tmp_path: Path) -> None:
    """AccountRow is frozen; mutation raises FrozenInstanceError (AR2)."""
    import pytest
    from dataclasses import FrozenInstanceError
    p = _write_csv(tmp_path, _VALID_CSV)
    registry = AccountRegistry.load(p)
    row = registry.primary()
    with pytest.raises(FrozenInstanceError):
        row.broker = "other"  # type: ignore[misc]
    print("  OK AccountRow is frozen (AR2)")


def test_is_primary_true_parsed(tmp_path: Path) -> None:
    """is_primary='true' parses to True (AR2)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    registry = AccountRegistry.load(p)
    assert registry.primary().is_primary is True
    print("  OK is_primary=true parses to True (AR2)")


def test_is_primary_false_parsed(tmp_path: Path) -> None:
    """is_primary='false' parses to False (AR2)."""
    p = _write_csv(tmp_path, _TWO_ACCOUNTS_CSV)
    registry = AccountRegistry.load(p)
    secondary = registry.get("secondary")
    assert secondary.is_primary is False
    print("  OK is_primary=false parses to False (AR2)")


# ─────────────────────────────────────────────────────────────────────────────
# AR3: primary()
# ─────────────────────────────────────────────────────────────────────────────

def test_primary_returns_correct_row(tmp_path: Path) -> None:
    """primary() returns the row with is_primary=true (AR3)."""
    p = _write_csv(tmp_path, _TWO_ACCOUNTS_CSV)
    registry = AccountRegistry.load(p)
    acct = registry.primary()
    assert acct.account_id == "primary"
    assert acct.broker == "zerodha"
    assert acct.label == "Primary Trading Account"
    assert acct.is_primary is True
    print("  OK primary() returns correct row (AR3)")


def test_primary_field_values(tmp_path: Path) -> None:
    """primary() row has exact field values from CSV (AR3)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    registry = AccountRegistry.load(p)
    acct = registry.primary()
    assert acct.account_id == "primary"
    assert acct.broker == "zerodha"
    assert acct.label == "Primary Trading Account"
    print("  OK primary() field values match CSV (AR3)")


# ─────────────────────────────────────────────────────────────────────────────
# AR4: get()
# ─────────────────────────────────────────────────────────────────────────────

def test_get_known_account_id(tmp_path: Path) -> None:
    """get() returns correct row for known account_id (AR4)."""
    p = _write_csv(tmp_path, _TWO_ACCOUNTS_CSV)
    registry = AccountRegistry.load(p)
    row = registry.get("secondary")
    assert row.account_id == "secondary"
    assert row.is_primary is False
    print("  OK get() returns correct row (AR4)")


def test_get_unknown_account_id_raises(tmp_path: Path) -> None:
    """get() unknown account_id raises KeyError (AR4)."""
    import pytest
    p = _write_csv(tmp_path, _VALID_CSV)
    registry = AccountRegistry.load(p)
    with pytest.raises(KeyError):
        registry.get("nonexistent_account")
    print("  OK get() unknown account_id -> KeyError (AR4)")


# ─────────────────────────────────────────────────────────────────────────────
# AR5: all_accounts()
# ─────────────────────────────────────────────────────────────────────────────

def test_all_accounts_load_order(tmp_path: Path) -> None:
    """all_accounts() returns rows in CSV load order (AR5)."""
    p = _write_csv(tmp_path, _TWO_ACCOUNTS_CSV)
    registry = AccountRegistry.load(p)
    accounts = registry.all_accounts()
    assert len(accounts) == 2
    assert accounts[0].account_id == "primary"
    assert accounts[1].account_id == "secondary"
    print("  OK all_accounts() preserves load order (AR5)")


def test_all_accounts_returns_copy(tmp_path: Path) -> None:
    """all_accounts() returns a copy; mutating it doesn't affect registry (AR5)."""
    p = _write_csv(tmp_path, _TWO_ACCOUNTS_CSV)
    registry = AccountRegistry.load(p)
    accounts = registry.all_accounts()
    accounts.clear()
    assert registry.count() == 2  # original unaffected
    print("  OK all_accounts() returns copy (AR5)")


# ─────────────────────────────────────────────────────────────────────────────
# AR8: count()
# ─────────────────────────────────────────────────────────────────────────────

def test_count_single_account(tmp_path: Path) -> None:
    """count() returns 1 for single-account CSV (AR8)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    registry = AccountRegistry.load(p)
    assert registry.count() == 1
    print("  OK count() == 1 (AR8)")


def test_count_two_accounts(tmp_path: Path) -> None:
    """count() returns 2 for two-account CSV (AR8)."""
    p = _write_csv(tmp_path, _TWO_ACCOUNTS_CSV)
    registry = AccountRegistry.load(p)
    assert registry.count() == 2
    print("  OK count() == 2 (AR8)")


# ─────────────────────────────────────────────────────────────────────────────
# AR9: v2 single primary; is_primary variants
# ─────────────────────────────────────────────────────────────────────────────

def test_is_primary_variants(tmp_path: Path) -> None:
    """is_primary accepts 'true', '1', 'yes' (case-insensitive) as True (AR9)."""
    csv = (
        f"{_HDR}\n"
        "acct_yes,zerodha,Acct Yes,yes,K,S,T,5000000,1.0,true\n"
    )
    p = _write_csv(tmp_path, csv)
    registry = AccountRegistry.load(p)
    assert registry.primary().is_primary is True
    print("  OK is_primary='yes' parses to True (AR9)")


def test_is_primary_case_insensitive(tmp_path: Path) -> None:
    """is_primary='TRUE' (uppercase) parses correctly (AR9)."""
    csv = (
        f"{_HDR}\n"
        "acct1,zerodha,Account One,TRUE,K,S,T,5000000,1.0,true\n"
    )
    p = _write_csv(tmp_path, csv)
    registry = AccountRegistry.load(p)
    assert registry.primary().account_id == "acct1"
    print("  OK is_primary='TRUE' parses to True (AR9)")


# ─────────────────────────────────────────────────────────────────────────────
# New columns (SU2, SU3, AR10, AR11)
# ─────────────────────────────────────────────────────────────────────────────

def test_new_columns_parsed_correctly(tmp_path: Path) -> None:
    """api_key_env, paper_capital, enabled parsed from CSV (SU3)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    registry = AccountRegistry.load(p)
    row = registry.primary()
    assert row.api_key_env == "ZD_KEY"
    assert row.api_secret_env == "ZD_SECRET"
    assert row.totp_secret_env == "ZD_TOTP"
    assert row.paper_capital == 5_000_000.0
    assert row.capital_share_pct == 1.0
    assert row.enabled is True
    print("  OK new columns parsed correctly (SU3)")


def test_paper_capital_is_float(tmp_path: Path) -> None:
    """paper_capital field is a float after load (SU3)."""
    p = _write_csv(tmp_path, _VALID_CSV)
    row = AccountRegistry.load(p).primary()
    assert isinstance(row.paper_capital, float)
    print("  OK paper_capital is float (SU3)")


def test_enabled_bool_variants(tmp_path: Path) -> None:
    """enabled='TRUE' and enabled='false' parse correctly."""
    csv = (
        f"{_HDR}\n"
        "a1,zerodha,Acct 1,true,K1,S1,T1,5000000,1.0,TRUE\n"
        "a2,zerodha,Acct 2,false,K2,S2,T2,5000000,0.0,false\n"
    )
    p = _write_csv(tmp_path, csv)
    reg = AccountRegistry.load(p)
    assert reg.get("a1").enabled is True
    assert reg.get("a2").enabled is False
    print("  OK enabled bool variants parsed (SU3)")


def test_paper_capital_zero_for_enabled_raises(tmp_path: Path) -> None:
    """Enabled account with paper_capital=0 raises ConfigSchemaError (AR11)."""
    import pytest
    csv = (
        f"{_HDR}\n"
        "a1,zerodha,Acct 1,true,K,S,T,0,1.0,true\n"
    )
    p = _write_csv(tmp_path, csv)
    with pytest.raises(ConfigSchemaError, match="paper_capital"):
        AccountRegistry.load(p)
    print("  OK paper_capital=0 for enabled -> ConfigSchemaError (AR11)")


def test_paper_capital_zero_for_disabled_ok(tmp_path: Path) -> None:
    """Disabled account with paper_capital=0 is acceptable (AR11 disabled path)."""
    csv = (
        f"{_HDR}\n"
        "a1,zerodha,Primary,true,K,S,T,5000000,1.0,true\n"
        "a2,zerodha,Disabled,false,K2,S2,T2,0,0.0,false\n"
    )
    p = _write_csv(tmp_path, csv)
    reg = AccountRegistry.load(p)
    assert reg.get("a2").paper_capital == 0.0
    print("  OK paper_capital=0 for disabled account is OK (AR11)")


def test_get_enabled_accounts_returns_only_enabled(tmp_path: Path) -> None:
    """get_enabled_accounts() returns only rows with enabled=True (AR10)."""
    p = _write_csv(tmp_path, _TWO_ACCOUNTS_CSV)
    reg = AccountRegistry.load(p)
    enabled = reg.get_enabled_accounts()
    assert len(enabled) == 1
    assert enabled[0].account_id == "primary"
    print("  OK get_enabled_accounts() filters correctly (AR10)")


def test_get_enabled_accounts_empty_if_all_disabled(tmp_path: Path) -> None:
    """get_enabled_accounts() returns [] when all are disabled (AR10)."""
    csv = (
        f"{_HDR}\n"
        "a1,zerodha,Primary,true,K,S,T,5000000,1.0,false\n"
    )
    p = _write_csv(tmp_path, csv)
    reg = AccountRegistry.load(p)
    assert reg.get_enabled_accounts() == []
    print("  OK get_enabled_accounts() returns [] when all disabled (AR10)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    tests = [
        test_load_valid_csv,
        test_load_missing_file_raises,
        test_load_missing_column_raises,
        test_load_empty_account_id_raises,
        test_load_no_primary_raises,
        test_load_two_primaries_raises,
        test_row_types_correct,
        test_row_is_frozen,
        test_is_primary_true_parsed,
        test_is_primary_false_parsed,
        test_primary_returns_correct_row,
        test_primary_field_values,
        test_get_known_account_id,
        test_get_unknown_account_id_raises,
        test_all_accounts_load_order,
        test_all_accounts_returns_copy,
        test_count_single_account,
        test_count_two_accounts,
        test_is_primary_variants,
        test_is_primary_case_insensitive,
        test_new_columns_parsed_correctly,
        test_paper_capital_is_float,
        test_enabled_bool_variants,
        test_paper_capital_zero_for_enabled_raises,
        test_paper_capital_zero_for_disabled_ok,
        test_get_enabled_accounts_returns_only_enabled,
        test_get_enabled_accounts_empty_if_all_disabled,
    ]

    passed = failed = 0
    for fn in tests:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                fn(Path(tmp))
                passed += 1
            except Exception:
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    if failed:
        sys.exit(1)
