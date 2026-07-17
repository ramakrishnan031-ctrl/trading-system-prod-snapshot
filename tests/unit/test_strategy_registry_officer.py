"""Strategy-direction registry — the daily officer (detect/register/confirm/conflict/notify),
the canonical accessor, and the behaviour-neutral consumer routing.

The whole feature is additive: on the pre-change tree the officer/module do not exist, so
every detect/confirm/conflict assertion here is RED-on-old by absence. The behaviour-NEUTRAL
routing (GUI _family_of, eod_squareoff direction_map) is proven to produce the SAME output as
before.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core import db_connect
from core import strategy_direction as sd
import scripts.strategy_registry_officer as officer


# ── canonical accessor ──────────────────────────────────────────────────────────
def test_build_direction_map_reads_declared_field_not_name():
    m = sd.build_direction_map("config")
    # every current strategy resolves to its DECLARED direction
    assert m["gap_fade_long"] == "LONG"
    assert m["gap_fade_short"] == "SHORT"
    assert m["positional_sector_rotation"] == "LONG"   # no _long/_short suffix — proves it's the field, not the name
    assert m["vwap_rejection_short"] == "SHORT"
    assert len(m) >= 16

def test_canonical_direction_unknown_is_none():
    assert sd.canonical_direction("no_such_strategy", "config") is None


# ── the pure reconcile step ─────────────────────────────────────────────────────
_DMAP = {"gap_fade_long": "LONG", "gap_fade_short": "SHORT", "vwap_bounce_long": "LONG"}

def test_detect_registers_new_pending():
    reg, new, conf, conflict = officer.reconcile(
        registry={}, direction_map=_DMAP, signal_names=set(), filled=set(),
        realized={}, today_iso="2026-07-17",
    )
    assert set(new) == {"gap_fade_long", "gap_fade_short", "vwap_bounce_long"}
    assert reg["gap_fade_long"]["registration_status"] == "PENDING"
    assert reg["gap_fade_long"]["direction"] == "LONG"
    assert reg["gap_fade_long"]["health"] == "OK"
    assert conf == [] and conflict == []

def test_no_new_when_all_registered():
    seed = {n: {"direction": d, "registration_status": "PENDING", "health": "OK"}
            for n, d in _DMAP.items()}
    reg, new, conf, conflict = officer.reconcile(seed, _DMAP, set(), set(), {}, "2026-07-17")
    assert new == []

def test_confirm_on_first_filled_trade():
    seed = {"vwap_bounce_long": {"direction": "LONG", "registration_status": "PENDING", "health": "OK"}}
    reg, new, conf, conflict = officer.reconcile(
        seed, {"vwap_bounce_long": "LONG"}, set(), filled={"vwap_bounce_long"},
        realized={"vwap_bounce_long": {"LONG"}}, today_iso="2026-07-17",
    )
    assert conf == ["vwap_bounce_long"]
    assert reg["vwap_bounce_long"]["registration_status"] == "CONFIRMED"
    assert conflict == []

def test_direction_conflict_flags_and_keeps_declared():
    seed = {"gap_fade_long": {"direction": "LONG", "registration_status": "CONFIRMED", "health": "OK"}}
    reg, new, conf, conflict = officer.reconcile(
        seed, {"gap_fade_long": "LONG"}, set(), {"gap_fade_long"},
        realized={"gap_fade_long": {"LONG", "SHORT"}},  # a realized SHORT contradicts declared LONG
        today_iso="2026-07-17",
    )
    assert conflict == ["gap_fade_long"]
    assert reg["gap_fade_long"]["health"] == "DIRECTION_CONFLICT"
    assert reg["gap_fade_long"]["direction"] == "LONG", "declared value MUST be kept, never overwritten"

def test_conflict_not_reflagged_when_already_conflicted():
    seed = {"x": {"direction": "LONG", "registration_status": "CONFIRMED", "health": "DIRECTION_CONFLICT"}}
    reg, new, conf, conflict = officer.reconcile(
        seed, {"x": "LONG"}, set(), {"x"}, {"x": {"SHORT"}}, "2026-07-17")
    assert conflict == [], "notify only on the OK->CONFLICT transition, not every run"
    assert reg["x"]["health"] == "DIRECTION_CONFLICT"

def test_conflict_self_heals_to_ok_when_realized_matches():
    seed = {"x": {"direction": "LONG", "registration_status": "CONFIRMED", "health": "DIRECTION_CONFLICT"}}
    reg, *_ = officer.reconcile(seed, {"x": "LONG"}, set(), {"x"}, {"x": {"LONG"}}, "2026-07-17")
    assert reg["x"]["health"] == "OK"

def test_signal_only_strategy_with_single_realized_side_registers():
    # a scanner fired (in signals) but has no YAML; it traded LONG only -> registered LONG
    reg, new, *_ = officer.reconcile(
        {}, direction_map={}, signal_names={"mystery_scanner"}, filled={"mystery_scanner"},
        realized={"mystery_scanner": {"LONG"}}, today_iso="2026-07-17")
    assert "mystery_scanner" in new
    assert reg["mystery_scanner"]["direction"] == "LONG"
    assert reg["mystery_scanner"]["evidence"]["source_yaml"] == "signals (no YAML)"


# ── end-to-end run() with a temp DB + temp registry ─────────────────────────────
def _mk_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE signals (signal_id TEXT, scanner TEXT, strategy TEXT)")
    conn.execute("CREATE TABLE trades (trade_id TEXT, strategy TEXT, direction TEXT, qty_filled INTEGER)")
    conn.execute("INSERT INTO signals VALUES ('s1','vwap_bounce_long','vwap_bounce_long')")
    conn.execute("INSERT INTO trades VALUES ('t1','vwap_bounce_long','LONG',10)")   # filled -> confirm
    conn.commit(); conn.close()

def test_run_writes_registry_and_notifies(tmp_path, monkeypatch):
    _mk_db(tmp_path / "trading_system.db")
    db_connect.init_analytics_schema(tmp_path / "trading_system.db")
    reg_path = tmp_path / "reg.yaml"      # empty -> everything is new
    calls = {}
    def _capture(new, conflict, registry, config_dir):
        calls["new"], calls["conflict"] = list(new), list(conflict)
    summary = officer.run(Path("config"), tmp_path / "trading_system.db", reg_path,
                          "2026-07-17", dry_run=False, notify_fn=_capture)
    assert reg_path.exists(), "registry written"
    written = sd.load_registry(reg_path)
    assert written["vwap_bounce_long"]["registration_status"] == "CONFIRMED"  # had a filled trade
    assert "vwap_bounce_long" in calls["new"]        # newly registered -> notified
    assert calls["conflict"] == []
    assert summary["changed"] is True

def test_run_dry_run_writes_nothing(tmp_path):
    _mk_db(tmp_path / "trading_system.db")
    db_connect.init_analytics_schema(tmp_path / "trading_system.db")
    reg_path = tmp_path / "reg.yaml"
    officer.run(Path("config"), tmp_path / "trading_system.db", reg_path, "2026-07-17", dry_run=True)
    assert not reg_path.exists(), "dry-run must not write"


# ── the seeded registry is self-consistent with the config ──────────────────────
def test_committed_seed_matches_config_directions():
    reg = sd.load_registry("config/strategy_direction_registry.yaml")
    dmap = sd.build_direction_map("config")
    assert set(reg) == set(dmap), "the seed must list exactly the configured strategies"
    for name, d in dmap.items():
        assert reg[name]["direction"] == d, f"{name}: seed direction must mirror StrategyConfig.direction"
