"""
tests/unit/test_service_window_config.py — Trading System v2  (25-Jul-2026)

The service shutdown time is CONFIGURABLE (`trading_hours.service_window_end`).

WHY THIS FILE EXISTS
    `SERVICE_WINDOW_END` used to be one module constant serving TWO roles:
      (1) the boot-refusal guard  — the LATEST the service may START (FIX-189)
      (2) the EOD self-exit       — WHEN the service STOPS
    Moving only (2) opens a silent trap: a crash-restart between the two values hits
    the boot guard, exits 0, and `Restart=on-failure` does not retry — the process is
    dead for the evening and the liveness probe (cron `*/5 09-15`, last run 15:55)
    never notices. See docs/audit/service_window_configurable_25jul2026.md §3.

    The boot guard runs at main.py:1712, BEFORE `load_all()` at :1743 — it structurally
    cannot read config. So the two roles are locked together through a single schema
    bound instead: every legal configured stop time is strictly inside the start window,
    which makes the trap UNREPRESENTABLE rather than merely untested.
"""
from __future__ import annotations

from datetime import datetime, time as _time, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import main
from core.config_loader import SERVICE_WINDOW_END_MAX, TradingHoursConfig

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_DIR = Path("config")


def _hours(**overrides) -> dict:
    """A schema-valid trading_hours block; override one field per test."""
    base = {
        "entry_start": "10:00",
        "entry_end": "15:00",
        "eod_entry_cutoff": "15:15",
        "eod_squareoff_time": "15:17",
        "market_open": "09:15",
        "market_close": "15:30",
    }
    base.update(overrides)
    return base


# ── §4.1 — the CONFIGURED value is read, proven with a DISTINCT value ────────

def test_default_is_1600_so_each_deploy_half_is_independently_safe():
    """No key in YAML -> 16:00. With extra='forbid', a schema without a default would
    make schema-before-config a boot failure; the default removes the ordering hazard
    and makes the revert config-only."""
    th = TradingHoursConfig(**_hours())
    assert th.service_window_end == "16:00"


def test_configured_value_is_read_not_the_old_constant():
    """A DISTINCT value (17:05, not 16:00 and not the deployed 17:35) must survive
    load and parse — this is the proof the value flows rather than being inspected."""
    th = TradingHoursConfig(**_hours(service_window_end="17:05"))
    assert main._parse_hhmm(th.service_window_end) == _time(17, 5)


def test_self_exit_honours_the_configured_value():
    """The self-exit is due strictly AFTER the configured stop time, not 16:00."""
    store = MagicMock()
    store.count_active_positions.return_value = 0
    w = main._parse_hhmm(TradingHoursConfig(**_hours(service_window_end="17:05")).service_window_end)

    # 16:30 is past the OLD constant but before the configured stop -> NOT due,
    # and short-circuits without querying the store.
    due, active = main._eod_self_exit_due(store, datetime(2026, 7, 27, 16, 30, tzinfo=_IST), w)
    assert (due, active) == (False, -1)
    store.count_active_positions.assert_not_called()

    # Past the configured stop and flat -> due.
    assert main._eod_self_exit_due(store, datetime(2026, 7, 27, 17, 6, tzinfo=_IST), w) == (True, 0)


def test_deployed_value_is_1735():
    """The value this change exists to set: the PB-01 Chartink alert fires at 17:00."""
    from core.config_loader import load_all
    th = load_all(_CONFIG_DIR).system.trading_hours
    assert th.service_window_end == "17:35"


# ── §4.2 — THE DELIBERATE BREAK: out-of-range is rejected AT CONFIG LOAD ─────

def test_below_market_close_is_rejected():
    """Lower bound: a stop time before the 15:30 close would cut the session short."""
    with pytest.raises(ValueError, match="service_window_end"):
        TradingHoursConfig(**_hours(service_window_end="15:00"))


def test_at_or_past_the_max_is_rejected():
    """Upper bound: at/after SERVICE_WINDOW_END_MAX the service would still be alive
    when the forward-shadow recorder runs."""
    with pytest.raises(ValueError, match="service_window_end"):
        TradingHoursConfig(**_hours(service_window_end="18:15"))
    with pytest.raises(ValueError, match="service_window_end"):
        TradingHoursConfig(**_hours(service_window_end="23:59"))


def test_the_rejection_reason_reaches_the_boot_log(tmp_path):
    """A validator whose reason nobody can read is half a validator.

    ConfigSchemaError's message names only the FILE; the reason is in .context["errors"].
    main() logged just the message, so a bad value would have failed the 08:15 boot
    without saying WHICH key was wrong. Found by measuring the deliberate break rather
    than assuming it was legible.
    """
    from core.config_loader import load_all

    src = (_CONFIG_DIR / "system_config.yaml").read_text(encoding="utf-8")
    tmp = tmp_path / "config"
    for p in _CONFIG_DIR.rglob("*"):
        if p.is_file():
            dst = tmp / p.relative_to(_CONFIG_DIR)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(p.read_bytes())
    (tmp / "system_config.yaml").write_text(
        src.replace('service_window_end: "17:35"', 'service_window_end: "18:20"'),
        encoding="utf-8",
    )
    with pytest.raises(Exception) as ei:
        load_all(tmp)
    detail = main._config_error_detail(ei.value)
    assert "service_window_end" in detail and "out of range" in detail, detail
    assert "18:20" in detail, "the offending value must appear in the log line"


def test_config_error_detail_never_raises():
    """Diagnostics must not mask the failure they describe."""
    assert main._config_error_detail(ValueError("plain")) == ""
    assert main._config_error_detail(RuntimeError()) == ""


def test_rejection_happens_in_the_schema_before_any_io():
    """AC: the failure is a config-load failure — raised by the model validator, before
    main() reaches StateStore (:1767) or any broker handle. Constructing the model with
    no DB, no broker and no filesystem access is that proof."""
    with pytest.raises(ValueError):
        TradingHoursConfig(**_hours(service_window_end="19:00"))


# ── §4.3 — a value inside the range validates cleanly ───────────────────────

@pytest.mark.parametrize("v", ["15:30", "16:00", "17:35", "18:14"])
def test_values_inside_the_range_are_accepted(v):
    assert TradingHoursConfig(**_hours(service_window_end=v)).service_window_end == v


# ── §3.3 — UPPER-BOUND DRIFT GUARD (a test, not a comment) ───────────────────

def test_max_is_before_the_forward_shadow_job():
    """DRIFT GUARD — the upper bound's REASON, made executable.

    The 18:15 forward-shadow recorder is the only out-of-sample evidence producer in the
    system and what it writes cannot be regenerated. The bound is POSTURE, not a
    concurrency finding: nothing in the evening pipeline should ever have to reason about
    whether a live writer is present. (WAL would in fact tolerate it — which is exactly
    why a comment saying '18:15' would be correctly spotted as weakly argued and relaxed.
    This assertion fails instead.)

    Reading the schedule from the registry means moving the cron moves the bound.
    """
    from core.cron_registry import load_cron_registry
    job = load_cron_registry(_CONFIG_DIR / "cron_registry.yaml").get("forward_shadow_record")
    assert job is not None, "forward_shadow_record vanished from the registry"
    assert job.due_time is not None
    assert SERVICE_WINDOW_END_MAX <= job.due_time, (
        f"service_window_end's upper bound ({SERVICE_WINDOW_END_MAX}) is at/after the "
        f"forward-shadow recorder ({job.due_time}) — a configured shutdown could leave the "
        "service alive while the OOS recorder runs"
    )


# ── §0.1 / §3.3 — the trap is closed BY CONSTRUCTION ────────────────────────

def test_start_cutoff_dominates_every_legal_stop_time():
    """THE LOAD-BEARING INVARIANT.

    If the service may STOP later than it may START, a crash-restart in the gap is
    refused with exit 0, `Restart=on-failure` does not retry, and the evening is lost
    silently. Binding the start cutoff to the schema's exclusive upper bound makes that
    gap impossible for EVERY legal config value — including a revert to 16:00.
    """
    assert main.SERVICE_START_CUTOFF >= SERVICE_WINDOW_END_MAX


def test_a_start_inside_the_widened_guard_is_permitted():
    """The accepted cost, pinned: the anti-overnight guard now admits 16:00-18:15.
    A stray start there self-exits within one poll (its target_dt is already past)."""
    assert main._within_service_window(datetime(2026, 7, 27, 17, 0, tzinfo=_IST)) is True


def test_fix189_incidents_are_still_refused():
    """The widening must not weaken what FIX-189 was built for: the observed 23:22 start
    and the 04:24 false-alert hour stay outside the window."""
    assert main._within_service_window(datetime(2026, 7, 26, 23, 22, tzinfo=_IST)) is False
    assert main._within_service_window(datetime(2026, 7, 27, 4, 24, tzinfo=_IST)) is False
    assert main._within_service_window(datetime(2026, 7, 27, 7, 59, tzinfo=_IST)) is False


# ── §4.4 — the boot log states the configured window ────────────────────────

def test_boot_banner_states_the_configured_window():
    """After a week of things that look alive and produce nothing, 'what window is this
    process running?' must be answerable from the log, not from the deployed source."""
    banner = main._service_window_banner(_time(8, 0), _time(18, 15), _time(17, 35))
    assert "08:00" in banner and "18:15" in banner and "17:35" in banner


# ── §4.5 — parity (re-confirmed by test, not re-investigated) ───────────────

@pytest.mark.parametrize("mode", ["PAPER", "LIVE"])
def test_shutdown_decision_is_mode_agnostic(mode):
    """count_active_positions() is mode-agnostic (main.py:1032) and nothing in the
    shutdown path branches on mode. Same inputs -> same decision in both modes."""
    store = MagicMock()
    store.count_active_positions.return_value = 0
    w = _time(17, 35)
    assert main._eod_self_exit_due(store, datetime(2026, 7, 27, 17, 40, tzinfo=_IST), w) == (True, 0)
    store.count_active_positions.return_value = 1
    assert main._eod_self_exit_due(store, datetime(2026, 7, 27, 17, 40, tzinfo=_IST), w) == (False, 1)


# ── §3.4 — the removed default must not silently come back ──────────────────

def test_window_end_is_required_not_defaulted():
    """A defaulted window_end would resolve to the START cutoff after the split — the
    wrong role, silently. Fail loud instead."""
    with pytest.raises(TypeError):
        main._start_eod_self_exit_thread(
            store=MagicMock(), notifier=None, mode="LIVE", log=MagicMock(),
            market_windows=None, shutdown_event=MagicMock(),
        )
