"""
strategies/loader.py  -  Strategy YAML loader (S9-S11)

Layer 2 (strategies/). Imports: yaml, stdlib, core.exceptions,
strategies.schema. No state_store, no broker, no data layer.

Locked decisions: S9-S11
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml

from core.exceptions import ConfigMissingError, ConfigSchemaError
from strategies.schema import StrategyConfig, validate_strategy


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
    ) -> Dict[str, StrategyConfig]:
        """S9: Load ALL .yaml files from strategies_dir.

        Returns dict keyed by strategy name (StrategyConfig.name).
        ANY invalid file raises ConfigSchemaError immediately — no partial loads.
        Optionally cross-validates scan_webhook_map_path (S10).
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
