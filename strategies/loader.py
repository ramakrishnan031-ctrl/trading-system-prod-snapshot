"""
strategies/loader.py  -  Strategy YAML loader (S9-S11)

Layer 2 (strategies/). Imports: yaml, stdlib, core.exceptions,
strategies.schema. No state_store, no broker, no data layer.

Locked decisions: S9-S11
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from pydantic import ValidationError

from core.exceptions import ConfigMissingError, ConfigSchemaError
from strategies.schema import StrategyConfig, validate_strategy

_log = logging.getLogger("strategies.loader")


# ── Config-error description scan (missing-direction alert, 18-Jul-2026) ─────────
#
# load_all_strategies() (S9) is fail-fast by design: the FIRST invalid YAML raises
# ConfigSchemaError and no strategy loads ("no partial loads"). That is the correct
# capital-safety behaviour — the system must NOT boot with a broken strategy set — but
# on its own it fails QUIETLY: today a strategy YAML that is missing `direction` (or has
# an invalid one, or fails any other schema rule) only aborts the boot with a single log
# line naming the first bad file, and Rama is never told LOUDLY. A forgotten
# `direction:` would then silently not-trade the strategy.
#
# scan_strategy_errors() is the additive, describe-only pass that feeds that alert. It
# does NOT change how strategies load or trade — it just names EVERY bad file so the
# boot-abort path can send one consolidated Telegram+email alert before it (still) fails.

def _describe_config_error(exc: Exception) -> str:
    """Render a concise, path-free reason for ONE failed strategy YAML.

    Prefers pydantic's structured errors (reached via ``ConfigSchemaError.__cause__``,
    which ``validate_strategy`` sets with ``raise ... from exc``) so the message names
    the offending field(s) — e.g. ``missing required field 'direction'`` or
    ``direction: direction must be LONG or SHORT, got 'FOO'``. Falls back to the raw
    exception text for non-schema failures (missing file, bad YAML syntax, non-mapping).
    """
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, ValidationError):
        parts: List[str] = []
        for err in cause.errors():
            loc = ".".join(str(x) for x in err.get("loc", ())) or "<root>"
            etype = err.get("type", "")
            msg = str(err.get("msg", "invalid"))
            if etype == "missing":
                parts.append("missing required field %r" % loc)
            elif etype == "extra_forbidden":
                parts.append("unknown field %r not permitted" % loc)
            else:
                # Custom-validator ValueErrors surface as "Value error, <detail>";
                # drop the boilerplate prefix so the detail reads cleanly.
                if msg.startswith("Value error, "):
                    msg = msg[len("Value error, "):]
                parts.append("%s: %s" % (loc, msg))
        if parts:
            return "; ".join(parts)
    return str(exc)


def scan_strategy_errors(strategies_dir: Path) -> List[Tuple[str, str]]:
    """Validate EVERY ``*.yaml`` in ``strategies_dir`` and return, for each file that
    fails, a ``(filename, reason)`` pair. An all-valid directory returns ``[]``.

    Unlike :meth:`StrategyLoader.load_all_strategies` (which raises on the FIRST invalid
    file — S9, "no partial loads"), this collects ALL bad files in one pass so a single
    consolidated alert can name every one. It NEVER raises: a per-file failure becomes a
    reason string, so it is safe to call on the boot's abort path purely to describe
    *why* the boot is failing. It changes NOTHING about how strategies load or trade —
    the actual fail-fast abort is still owned by ``load_all_strategies`` /
    ``check_strategy_configs``.
    """
    bad: List[Tuple[str, str]] = []
    for yaml_path in sorted(strategies_dir.glob("*.yaml")):
        try:
            validate_strategy(yaml_path)
        except Exception as exc:  # noqa: BLE001 — a describe pass must never itself raise
            bad.append((yaml_path.name, _describe_config_error(exc)))
    return bad


class StrategyLoader:
    """
    S9: Loads all strategy YAMLs at startup and provides a lookup API.
    Strategies are loaded ONCE; no hot reload.
    """

    def __init__(self) -> None:
        self._strategies: Dict[str, StrategyConfig] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def load_all_strategies(
        self,
        strategies_dir: Path,
        scan_webhook_map_path: Optional[Path] = None,
        force_intraday_only: bool = False,
    ) -> Dict[str, StrategyConfig]:
        """S9: Load ALL .yaml files from strategies_dir.

        Returns dict keyed by strategy name (StrategyConfig.name).
        ANY invalid file raises ConfigSchemaError immediately — no partial loads.
        Optionally cross-validates scan_webhook_map_path (S10).

        Option A (10-Jul-2026): the declared intent is PRESERVED for every strategy
        (the old Bug-D destructive rewrite DELIVERY->INTRADAY at load was REMOVED).
        ``force_intraday_only`` no longer mutates intent here; its safety role moves
        to the proper layers: strategies.control.strategy_will_trade DORMANTS a raw
        DELIVERY strategy while the breaker is on (LAYER 0) or trade_type=INTRADAY
        (LAYER 1x2), so it never reaches sizing/placement; and the broker chokepoint
        (zerodha_adapter.place_order) coerces the product to MIS under the breaker,
        with the delivery_lock (delivery_enabled=false) as an independent CNC backstop.
        Preserving the intent gives the resolver + status table a single source of
        truth for each strategy's true product type. The param is retained (callers +
        a per-strategy dormancy log); it just no longer rewrites.
        """
        strategies: Dict[str, StrategyConfig] = {}

        yaml_files = sorted(strategies_dir.glob("*.yaml"))
        if not yaml_files:
            raise ConfigSchemaError(
                "No strategy YAML files found in %s" % strategies_dir
            )

        for yaml_path in yaml_files:
            # validate_strategy raises ConfigSchemaError on any failure
            cfg = validate_strategy(yaml_path)
            # Option A (10-Jul-2026): NO load-time intent rewrite — the declared intent
            # is preserved. Under the breaker a DELIVERY strategy is DORMANTED at the
            # entry-gate resolver (it never places), and MIS-only is guaranteed at the
            # broker product chokepoint — so there is nothing to rewrite here. Log the
            # dormancy for visibility (was a WARNING overwrite; now an INFO notice).
            if force_intraday_only and cfg.intent != "INTRADAY":
                _log.info(
                    "force_intraday_only=true: %r keeps declared intent %s but is DORMANT "
                    "at the entry gate (no load-time rewrite; MIS-only enforced at the "
                    "broker product chokepoint)",
                    cfg.name, cfg.intent,
                )
            strategies[cfg.name] = cfg

        # S10: cross-validate against scan_webhook_map
        if scan_webhook_map_path is not None:
            self._validate_scan_webhook_map(scan_webhook_map_path, strategies)

        self._strategies = strategies
        return strategies

    def get_strategy(self, name: str) -> StrategyConfig:
        """S9: Lookup by strategy name. Raises ConfigMissingError if not found."""
        if name not in self._strategies:
            raise ConfigMissingError(
                "Strategy %r not found in loaded strategies. "
                "Available: %s" % (name, sorted(self._strategies.keys()))
            )
        return self._strategies[name]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _validate_scan_webhook_map(
        self,
        map_path: Path,
        strategies: Dict[str, StrategyConfig],
    ) -> None:
        """S10: Every strategy referenced in scan_webhook_map must have a YAML."""
        try:
            with open(map_path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
        except FileNotFoundError:
            raise ConfigMissingError(
                "scan_webhook_map not found: %s" % map_path
            )
        except yaml.YAMLError as exc:
            raise ConfigSchemaError(
                "Invalid YAML in scan_webhook_map %s: %s" % (map_path, exc)
            )

        if not isinstance(raw, dict):
            return

        scanners = raw.get("scanners") or {}
        if not isinstance(scanners, dict):
            return

        for scanner_name, entry in scanners.items():
            # S14 format: entry is a dict with "strategy" key
            if isinstance(entry, dict):
                strategy_name = entry.get("strategy")
            else:
                # Fallback: old format where entry is the strategy name directly
                strategy_name = entry

            if strategy_name is None:
                raise ConfigSchemaError(
                    "scan_webhook_map entry for scanner %r has no 'strategy' key"
                    % scanner_name
                )

            if strategy_name not in strategies:
                raise ConfigMissingError(
                    "scan_webhook_map references strategy %r (scanner: %r) "
                    "but no YAML was found in strategies dir"
                    % (strategy_name, scanner_name)
                )
