"""
core/account_registry.py -- Trading System v2

Purpose:
    In-memory registry of trading accounts loaded from config/accounts.csv.
    v2 supports a single primary Zerodha account; multi-account is v2.1.
    Provides the primary account lookup used by ZerodhaAdapter.

Locked Design Decisions:
    AR1  -- Load from config/accounts.csv (CSV).
             Columns: account_id, broker, label, is_primary, api_key_env,
             api_secret_env, totp_secret_env, paper_capital, capital_share_pct,
             enabled.
    AR2  -- AccountRow: frozen dataclass with all columns typed.
    AR3  -- primary() -> AccountRow: return the row where is_primary=true.
             Raises ConfigSchemaError if no primary row found.
    AR4  -- get(account_id) -> AccountRow: raises KeyError on miss.
    AR5  -- all_accounts() -> list[AccountRow] in load order.
    AR6  -- load(path) classmethod; raises ConfigMissingError if file absent.
    AR7  -- Layer 0-1 (core/). Imports: stdlib + core.exceptions only.
    AR8  -- count() -> int.
    AR9  -- v2: single account only; multi-account wiring deferred to v2.1.
    AR10 -- get_enabled_accounts() -> list[AccountRow]: only enabled=True rows.
    AR11 -- paper_capital > 0 for enabled accounts (validation at load time).

What This Module Does NOT Do:
    - Does not store raw broker credentials (those come from env vars named in
      api_key_env / api_secret_env / totp_secret_env)
    - Does not manage session tokens or auth flows
    - Does not make broker API calls
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core.exceptions import ConfigMissingError, ConfigSchemaError


# ─────────────────────────────────────────────────────────────────────────────
# AccountRow
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AccountRow:
    """
    Immutable snapshot of one row from accounts.csv (AR2).

    api_key_env / api_secret_env / totp_secret_env store the NAME of the
    environment variable that holds the credential — not the credential itself.
    """
    account_id:       str    # Unique identifier e.g. "LFL836"
    broker:           str    # Broker name e.g. "zerodha"
    label:            str    # Human-readable label e.g. "Kandasamy"
    is_primary:       bool   # True for exactly one row
    api_key_env:      str    # Env var name for broker API key
    api_secret_env:   str    # Env var name for broker API secret
    totp_secret_env:  str    # Env var name for TOTP secret. UNUSED in v2:
                             # the live login flow (scripts/zerodha_login.py)
                             # uses the request_token OAuth path, not TOTP.
                             # Reserved for v2.1 headless automation. ACC-2.
    paper_capital:    float  # Capital (INR) used in paper mode
    capital_share_pct: float # Fraction of pooled capital (v2.1 multi-account)
    enabled:          bool   # Gates inclusion in account selector


# ─────────────────────────────────────────────────────────────────────────────
# AccountRegistry
# ─────────────────────────────────────────────────────────────────────────────

class AccountRegistry:
    """
    In-memory registry of trading accounts (AR1-AR11).

    Typical usage::
        registry = AccountRegistry.load(Path("config/accounts.csv"))
        primary_acct = registry.primary()
    """

    def __init__(self, rows: List[AccountRow]) -> None:
        self._rows: List[AccountRow] = list(rows)
        self._by_id: Dict[str, AccountRow] = {r.account_id: r for r in rows}

    # ── classmethod constructor ───────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "AccountRegistry":
        """
        Parse accounts.csv and return a populated registry (AR6).

        Args:
            path: Absolute or relative path to accounts.csv.

        Raises:
            ConfigMissingError: file does not exist.
            ConfigSchemaError: required column missing, no primary account,
                               more than one primary account, or paper_capital
                               <= 0 for an enabled account (AR11).
        """
        if not path.exists():
            raise ConfigMissingError(
                f"accounts.csv not found at {path}",
                path=str(path),
            )

        required_columns = {
            "account_id", "broker", "label", "is_primary",
            "api_key_env", "api_secret_env", "totp_secret_env",
            "paper_capital", "capital_share_pct", "enabled",
        }
        rows: List[AccountRow] = []

        try:
            with open(path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames is None:
                    raise ConfigSchemaError(
                        "accounts.csv is empty or has no header row",
                        path=str(path),
                    )
                missing = required_columns - set(reader.fieldnames)
                if missing:
                    raise ConfigSchemaError(
                        f"accounts.csv missing required columns: {sorted(missing)}",
                        path=str(path),
                        missing_columns=sorted(missing),
                    )

                for lineno, raw in enumerate(reader, start=2):
                    try:
                        enabled = raw["enabled"].strip().lower() in ("true", "1", "yes")
                        row = AccountRow(
                            account_id=raw["account_id"].strip(),
                            broker=raw["broker"].strip(),
                            label=raw["label"].strip(),
                            is_primary=raw["is_primary"].strip().lower() in ("true", "1", "yes"),
                            api_key_env=raw["api_key_env"].strip(),
                            api_secret_env=raw["api_secret_env"].strip(),
                            totp_secret_env=raw["totp_secret_env"].strip(),
                            paper_capital=float(raw["paper_capital"].strip()),
                            capital_share_pct=float(raw["capital_share_pct"].strip()),
                            enabled=enabled,
                        )
                    except (KeyError, ValueError) as exc:
                        raise ConfigSchemaError(
                            f"accounts.csv line {lineno}: {exc}",
                            path=str(path),
                            line=lineno,
                        ) from exc

                    if not row.account_id:
                        raise ConfigSchemaError(
                            f"accounts.csv line {lineno}: account_id is empty",
                            path=str(path),
                            line=lineno,
                        )

                    # AR11: enabled accounts must have paper_capital > 0
                    if row.enabled and row.paper_capital <= 0:
                        raise ConfigSchemaError(
                            f"accounts.csv line {lineno}: enabled account "
                            f"{row.account_id!r} has paper_capital <= 0",
                            path=str(path),
                            line=lineno,
                        )

                    rows.append(row)

        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigMissingError(
                f"Could not read accounts.csv at {path}: {exc}",
                path=str(path),
            ) from exc

        # Validate exactly one primary account
        primary_rows = [r for r in rows if r.is_primary]
        if not primary_rows:
            raise ConfigSchemaError(
                "accounts.csv has no row with is_primary=true",
                path=str(path),
            )
        if len(primary_rows) > 1:
            raise ConfigSchemaError(
                f"accounts.csv has {len(primary_rows)} rows with is_primary=true; "
                f"exactly one is required",
                path=str(path),
                primary_count=len(primary_rows),
            )

        registry = cls(rows)

        # v2.1 readiness: capital_share_pct must sum to 1.0 across enabled
        # accounts when more than one is enabled.
        enabled = registry.get_enabled_accounts()
        if len(enabled) > 1:
            total_share = sum(a.capital_share_pct for a in enabled)
            if abs(total_share - 1.0) > 1e-6:
                raise ConfigSchemaError(f"capital_share_pct sum = {total_share}, expected 1.0")
            # FIX-133 Item 29: warn about untested multi-account mode
            import logging
            logging.getLogger(__name__).warning(
                "multi_account.detected: %d enabled accounts. "
                "Multi-account mode not fully tested — use single account for live.",
                len(enabled),
            )

        return registry

    # ── public API ─────────────────────────────────────────────────────────────

    def primary(self) -> AccountRow:
        """Return the primary account row (AR3). Always succeeds after load()."""
        for row in self._rows:
            if row.is_primary:
                return row
        raise ConfigSchemaError(
            "No primary account found (post-load check failed)",
        )

    def get(self, account_id: str) -> AccountRow:
        """
        Return the AccountRow for the given account_id (AR4).

        Raises:
            KeyError: account_id not found.
        """
        row = self._by_id.get(account_id)
        if row is None:
            raise KeyError(f"Account {account_id!r} not found in registry")
        return row

    def all_accounts(self) -> List[AccountRow]:
        """Return all accounts in load order (AR5). Returns a copy."""
        return list(self._rows)

    def get_enabled_accounts(self) -> List[AccountRow]:
        """Return only enabled accounts in load order (AR10)."""
        return [r for r in self._rows if r.enabled]

    def count(self) -> int:
        """Return the number of accounts loaded (AR8)."""
        return len(self._rows)
