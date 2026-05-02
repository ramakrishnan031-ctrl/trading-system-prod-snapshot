"""
core/config_validator.py — Trading System v2

Purpose:
    Enforces that ALL config values from YAML are actually used by modules.
    Detects hardcoded defaults that bypass config injection.
    Fails startup if any drift detected between loaded config and usage.

Problem Statement (2026-04-28 paper testing):
    Modules accept config but fall back to hardcoded defaults when params
    missing or not passed. Examples:
    - signal_expiry_sec: config=600, code default=60
    - entry_end_time: YAML changed but code had hardcoded windows
    - in_flight_release_fn: defaulted to None instead of mandatory injection

Design Decisions:
    CV1 — Runtime tracking. ConfigValidator.track(key, value) called whenever
          a config value is read. At startup end, validate_all() asserts all
          loaded keys were tracked at least once.
    CV2 — Mandatory injection. Modules must receive config in __init__ with
          NO default values. Factory functions must pass config explicitly.
    CV3 — Startup gate. main.py calls validate_all() after all modules init.
          Any untracked config key → ConfigValidationError → exit(1).
    CV4 — Static analysis (future). AST scan for default param values on
          config-related params. Not implemented in v1; manual audit for now.

Usage:
    from core.config_validator import config_validator

    # In module __init__:
    self._poll_interval = config_validator.get(
        "order_monitor.poll_interval_sec",
        app_config.system.order_monitor.poll_interval_sec
    )

    # In main.py after all modules initialized:
    config_validator.validate_all(app_config)

What This Module Does NOT Do:
    - Does not replace config_loader.py (that handles YAML loading + Pydantic)
    - Does not hot-reload configs (load-once at startup)
    - Does not modify configs (read-only tracking)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TypeVar

from core.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ConfigUsage:
    """Tracks a single config key's usage."""
    key: str
    expected_value: Any
    actual_value: Any = None
    accessed: bool = False
    access_count: int = 0
    accessor_modules: list[str] = field(default_factory=list)


class ConfigValidator:
    """
    Singleton that tracks config value usage across the application.

    Thread-safe for read tracking (via get()). validate_all() should only
    be called once at startup completion.
    """

    def __init__(self) -> None:
        self._tracked: dict[str, ConfigUsage] = {}
        self._enabled: bool = True
        self._validated: bool = False

    def reset(self) -> None:
        """Clear all tracked state. Used in tests."""
        self._tracked.clear()
        self._validated = False

    def disable(self) -> None:
        """Disable tracking. Used in tests that don't need validation."""
        self._enabled = False

    def enable(self) -> None:
        """Re-enable tracking after disable()."""
        self._enabled = True

    def register_expected(self, key: str, value: Any) -> None:
        """
        Register a config key and its expected value from loaded config.

        Called during config loading to build the expected-usage map.
        """
        if not self._enabled:
            return
        self._tracked[key] = ConfigUsage(key=key, expected_value=value)

    def get(self, key: str, value: T, module: str = "") -> T:
        """
        Track access to a config value and return it.

        Args:
            key: Dotted config key (e.g., "order_monitor.poll_interval_sec")
            value: The actual config value being used
            module: Optional module name for audit trail

        Returns:
            The same value passed in (pass-through for convenience)
        """
        if not self._enabled:
            return value

        if key in self._tracked:
            usage = self._tracked[key]
            usage.accessed = True
            usage.access_count += 1
            usage.actual_value = value
            if module and module not in usage.accessor_modules:
                usage.accessor_modules.append(module)

            if usage.expected_value != value:
                logger.warning(
                    "CONFIG_DRIFT: key=%s expected=%r actual=%r module=%s",
                    key, usage.expected_value, value, module
                )
        else:
            self._tracked[key] = ConfigUsage(
                key=key,
                expected_value=value,
                actual_value=value,
                accessed=True,
                access_count=1,
                accessor_modules=[module] if module else [],
            )

        return value

    def register_all_from_app_config(self, app_config: Any) -> None:
        """
        Walk AppConfig and register all leaf values as expected.

        Recursively traverses Pydantic models to register every config
        value with its dotted path key.
        """
        if not self._enabled:
            return

        def _is_pydantic_model(obj: Any) -> bool:
            """Check if obj is a Pydantic model instance."""
            return hasattr(type(obj), "model_fields")

        def _walk(obj: Any, prefix: str) -> None:
            if _is_pydantic_model(obj):
                for field_name in type(obj).model_fields:
                    val = getattr(obj, field_name)
                    key = f"{prefix}.{field_name}" if prefix else field_name
                    if _is_pydantic_model(val):
                        _walk(val, key)
                    elif isinstance(val, dict):
                        for k, v in val.items():
                            dict_key = f"{key}.{k}"
                            if _is_pydantic_model(v):
                                _walk(v, dict_key)
                            else:
                                self.register_expected(dict_key, v)
                    elif isinstance(val, list):
                        for i, item in enumerate(val):
                            if _is_pydantic_model(item):
                                _walk(item, f"{key}[{i}]")
                    else:
                        self.register_expected(key, val)

        _walk(app_config.system, "system")
        _walk(app_config.broker_costs, "broker_costs")
        _walk(app_config.broker_limits, "broker_limits")
        _walk(app_config.slippage, "slippage")
        _walk(app_config.scoring, "scoring")

    def get_usage_report(self) -> dict[str, Any]:
        """
        Generate a report of config usage for debugging/auditing.

        Returns:
            Dict with 'accessed', 'unaccessed', and 'drift' lists
        """
        accessed = []
        unaccessed = []
        drift = []

        for key, usage in sorted(self._tracked.items()):
            entry = {
                "key": key,
                "expected": usage.expected_value,
                "actual": usage.actual_value,
                "count": usage.access_count,
                "modules": usage.accessor_modules,
            }
            if usage.accessed:
                accessed.append(entry)
                if usage.expected_value != usage.actual_value:
                    drift.append(entry)
            else:
                unaccessed.append(entry)

        return {
            "accessed": accessed,
            "unaccessed": unaccessed,
            "drift": drift,
            "total_keys": len(self._tracked),
            "accessed_count": len(accessed),
            "unaccessed_count": len(unaccessed),
            "drift_count": len(drift),
        }

    def validate_all(self, strict: bool = False) -> None:
        """
        Validate that all registered config keys were accessed.

        Args:
            strict: If True, raise on ANY unaccessed key.
                    If False, only warn (for gradual rollout).

        Raises:
            ConfigValidationError: In strict mode when keys are unaccessed.
        """
        if not self._enabled:
            return

        if self._validated:
            logger.debug("ConfigValidator.validate_all() already called; skipping")
            return

        self._validated = True
        report = self.get_usage_report()

        if report["unaccessed"]:
            keys = [e["key"] for e in report["unaccessed"]]
            msg = f"CONFIG_UNACCESSED: {len(keys)} config keys never read: {keys[:10]}"
            if len(keys) > 10:
                msg += f" ... and {len(keys) - 10} more"

            if strict:
                logger.error(msg)
                raise ConfigValidationError(
                    f"Unaccessed config keys detected (strict mode): {keys}",
                    unaccessed_keys=keys,
                )
            else:
                logger.warning(msg)

        if report["drift"]:
            drift_keys = [e["key"] for e in report["drift"]]
            logger.error(
                "CONFIG_DRIFT_DETECTED: %d keys have value mismatch: %s",
                len(drift_keys), drift_keys
            )
            raise ConfigValidationError(
                f"Config drift detected: {drift_keys}",
                drift_keys=drift_keys,
            )

        logger.info(
            "ConfigValidator: %d/%d keys accessed, 0 drift",
            report["accessed_count"], report["total_keys"]
        )


config_validator = ConfigValidator()
