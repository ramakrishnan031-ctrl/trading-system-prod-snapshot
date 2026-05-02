"""
tests/core/test_config_validator.py — ConfigValidator unit tests

Tests CV1-CV4 design decisions:
    CV1 — Runtime tracking via get()
    CV2 — Mandatory injection (module design, not tested here)
    CV3 — Startup gate via validate_all()
    CV4 — Static analysis (future, not tested here)
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from core.config_validator import ConfigValidator, config_validator
from core.exceptions import ConfigValidationError


class TestConfigValidatorBasics:
    """CV1: Basic tracking functionality."""

    def test_register_and_get_tracks_access(self) -> None:
        """get() marks key as accessed and returns value."""
        cv = ConfigValidator()
        cv.register_expected("test.key", 42)

        result = cv.get("test.key", 42, module="test_module")

        assert result == 42
        report = cv.get_usage_report()
        assert report["accessed_count"] == 1
        assert report["unaccessed_count"] == 0

    def test_unaccessed_key_in_report(self) -> None:
        """Keys registered but never get() appear in unaccessed."""
        cv = ConfigValidator()
        cv.register_expected("never.read", "value")

        report = cv.get_usage_report()

        assert report["unaccessed_count"] == 1
        assert report["unaccessed"][0]["key"] == "never.read"

    def test_get_without_register_still_tracks(self) -> None:
        """get() on unregistered key adds it to tracking."""
        cv = ConfigValidator()

        result = cv.get("dynamic.key", 100, module="dynamic_module")

        assert result == 100
        report = cv.get_usage_report()
        assert report["accessed_count"] == 1

    def test_access_count_increments(self) -> None:
        """Multiple get() calls increment access_count."""
        cv = ConfigValidator()
        cv.register_expected("freq.key", "val")

        cv.get("freq.key", "val", module="mod1")
        cv.get("freq.key", "val", module="mod2")
        cv.get("freq.key", "val", module="mod1")

        report = cv.get_usage_report()
        accessed = [e for e in report["accessed"] if e["key"] == "freq.key"][0]
        assert accessed["count"] == 3
        assert set(accessed["modules"]) == {"mod1", "mod2"}

    def test_disable_skips_tracking(self) -> None:
        """disable() prevents any tracking."""
        cv = ConfigValidator()
        cv.disable()
        cv.register_expected("ignored.key", 1)
        cv.get("ignored.key", 1)

        report = cv.get_usage_report()
        assert report["total_keys"] == 0

    def test_reset_clears_state(self) -> None:
        """reset() clears all tracked data."""
        cv = ConfigValidator()
        cv.register_expected("to.clear", 1)
        cv.get("to.clear", 1)

        cv.reset()

        report = cv.get_usage_report()
        assert report["total_keys"] == 0


class TestConfigValidatorDrift:
    """CV1: Value drift detection."""

    def test_drift_detected_when_values_differ(self) -> None:
        """get() with different value than expected is flagged as drift."""
        cv = ConfigValidator()
        cv.register_expected("drift.key", 100)

        cv.get("drift.key", 200, module="drifter")

        report = cv.get_usage_report()
        assert report["drift_count"] == 1
        assert report["drift"][0]["expected"] == 100
        assert report["drift"][0]["actual"] == 200

    def test_no_drift_when_values_match(self) -> None:
        """get() with matching value has no drift."""
        cv = ConfigValidator()
        cv.register_expected("no.drift", "same")

        cv.get("no.drift", "same")

        report = cv.get_usage_report()
        assert report["drift_count"] == 0


class TestValidateAll:
    """CV3: Startup validation gate."""

    def test_validate_all_passes_when_all_accessed(self) -> None:
        """validate_all() succeeds when all keys accessed."""
        cv = ConfigValidator()
        cv.register_expected("a.key", 1)
        cv.register_expected("b.key", 2)
        cv.get("a.key", 1)
        cv.get("b.key", 2)

        cv.validate_all(strict=True)

    def test_validate_all_strict_raises_on_unaccessed(self) -> None:
        """validate_all(strict=True) raises ConfigValidationError."""
        cv = ConfigValidator()
        cv.register_expected("accessed.key", 1)
        cv.register_expected("unaccessed.key", 2)
        cv.get("accessed.key", 1)

        with pytest.raises(ConfigValidationError) as exc_info:
            cv.validate_all(strict=True)

        assert "unaccessed.key" in str(exc_info.value)

    def test_validate_all_non_strict_warns_only(self) -> None:
        """validate_all(strict=False) warns but doesn't raise for unaccessed."""
        cv = ConfigValidator()
        cv.register_expected("unaccessed.key", 1)

        cv.validate_all(strict=False)

    def test_validate_all_raises_on_drift(self) -> None:
        """validate_all() raises on drift regardless of strict flag."""
        cv = ConfigValidator()
        cv.register_expected("drift.key", 100)
        cv.get("drift.key", 999)

        with pytest.raises(ConfigValidationError) as exc_info:
            cv.validate_all(strict=False)

        assert "drift" in str(exc_info.value).lower()

    def test_validate_all_idempotent(self) -> None:
        """Second validate_all() call is a no-op."""
        cv = ConfigValidator()
        cv.register_expected("key", 1)
        cv.get("key", 1)

        cv.validate_all()
        cv.validate_all()

    def test_validate_all_disabled_is_noop(self) -> None:
        """validate_all() on disabled validator does nothing."""
        cv = ConfigValidator()
        cv.disable()
        cv.register_expected("key", 1)

        cv.validate_all(strict=True)


class TestRegisterFromAppConfig:
    """CV1: Automatic registration from Pydantic models."""

    def test_register_from_simple_model(self) -> None:
        """register_all_from_app_config walks Pydantic models."""

        class InnerConfig(BaseModel):
            model_config = ConfigDict(extra="forbid")
            value: int = 42

        class SystemConfig(BaseModel):
            model_config = ConfigDict(extra="forbid")
            inner: InnerConfig

        class EmptyConfig(BaseModel):
            model_config = ConfigDict(extra="forbid")

        class MockAppConfig(BaseModel):
            model_config = ConfigDict(extra="forbid")
            system: SystemConfig
            broker_costs: EmptyConfig
            broker_limits: EmptyConfig
            slippage: EmptyConfig
            scoring: EmptyConfig

        cv = ConfigValidator()
        app = MockAppConfig(
            system=SystemConfig(inner=InnerConfig()),
            broker_costs=EmptyConfig(),
            broker_limits=EmptyConfig(),
            slippage=EmptyConfig(),
            scoring=EmptyConfig(),
        )

        cv.register_all_from_app_config(app)

        report = cv.get_usage_report()
        keys = [e["key"] for e in report["unaccessed"]]
        assert "system.inner.value" in keys


class TestGlobalSingleton:
    """Verify global singleton behavior."""

    def test_singleton_available(self) -> None:
        """config_validator singleton is importable."""
        assert config_validator is not None

    def test_singleton_reset_works(self) -> None:
        """reset() clears singleton state."""
        config_validator.reset()
        config_validator.register_expected("singleton.key", 1)

        assert config_validator.get_usage_report()["total_keys"] == 1

        config_validator.reset()

        assert config_validator.get_usage_report()["total_keys"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
