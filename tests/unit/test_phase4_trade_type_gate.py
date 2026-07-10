"""
tests/unit/test_phase4_trade_type_gate.py — SLICE2.5-PHASE-4.

The trade_type reject-by-intent gate ALREADY EXISTS (Slice 2 strategy_will_trade
LAYER 1×2, wired into both signal_processor paths before sizing). Phase 4 is
CONFIRM + HARDEN: (Step 2) split a trade_type×intent mismatch into its OWN reject
label "TRADE_TYPE" via a machine-readable Verdict.cause (no string-matching);
(Step 3) lock the gate behaviour for EVERY go-live config + the dormant no-op +
the label-bleed regression + the contradictory-combo Auditor BLOCK.

Option A (10-Jul-2026, BUILT): declared-intent gating is now LIVE — the loader's
load-time intent rewrite was removed, so the resolver segregates on the DECLARED intent.
Under the default (trade_type=INTRADAY + force on) the 3 positional_* DELIVERY strategies
are DORMANT (12 WILL / 3 WON'T). This was the deliberately-approved behavior change;
MIS-only is guaranteed at the broker product chokepoint (test_zerodha_adapter Option A).
"""
from __future__ import annotations

from types import SimpleNamespace

from strategies.control import (
    strategy_will_trade,
    CAUSE_OK,
    CAUSE_DISABLED,
    CAUSE_FORCE_BREAKER,
    CAUSE_TRADE_TYPE,
)
from tests.unit.test_signal_processor import (
    _MockStrategy,
    _assert_rejected,
    _insert_queued_signal,
    _make_proc,
    _make_store,
    _now_tup,
    _run_one,
)


def _s(intent, enabled=True):
    """A minimal strategy stub — strategy_will_trade reads only .intent + .enabled."""
    return SimpleNamespace(intent=intent, enabled=enabled, name="strat")


# ════════════════════════════════════════════════════════════════════════════════
# control.py — machine-readable cause per layer (foundation of the label split)
# ════════════════════════════════════════════════════════════════════════════════
def test_cause_codes_per_layer():
    # will-trade paths -> OK
    assert strategy_will_trade(_s("INTRADAY"), trade_type="INTRADAY", force_intraday_only=False).cause == CAUSE_OK
    assert strategy_will_trade(_s("DELIVERY"), trade_type="DELIVERY", force_intraday_only=False).cause == CAUSE_OK
    assert strategy_will_trade(_s("INTRADAY"), trade_type="BOTH", force_intraday_only=False).cause == CAUSE_OK
    # LAYER 1×2 trade_type mismatch -> TRADE_TYPE (the ONLY one promoted to a label)
    assert strategy_will_trade(_s("DELIVERY"), trade_type="INTRADAY", force_intraday_only=False).cause == CAUSE_TRADE_TYPE
    assert strategy_will_trade(_s("INTRADAY"), trade_type="DELIVERY", force_intraday_only=False).cause == CAUSE_TRADE_TYPE
    # LAYER 3 switch off -> DISABLED ; LAYER 0 breaker -> FORCE_BREAKER
    assert strategy_will_trade(_s("INTRADAY", enabled=False), trade_type="INTRADAY", force_intraday_only=False).cause == CAUSE_DISABLED
    assert strategy_will_trade(_s("DELIVERY"), trade_type="BOTH", force_intraday_only=True).cause == CAUSE_FORCE_BREAKER


def test_reason_strings_unchanged():
    # GUARD: Phase 4 must NOT change the human reason strings (status table + Slice-2
    # tests read them). Only the cause field was added.
    assert "master INTRADAY" in strategy_will_trade(_s("DELIVERY"), trade_type="INTRADAY", force_intraday_only=False).reason
    assert "master DELIVERY" in strategy_will_trade(_s("INTRADAY"), trade_type="DELIVERY", force_intraday_only=False).reason
    assert "switch disabled" in strategy_will_trade(_s("X", enabled=False), trade_type="INTRADAY", force_intraday_only=False).reason
    assert "breaker" in strategy_will_trade(_s("DELIVERY"), trade_type="BOTH", force_intraday_only=True).reason.lower()


# ════════════════════════════════════════════════════════════════════════════════
# Step 3 — go-live scenario matrix (the gate decision, per config)
# ════════════════════════════════════════════════════════════════════════════════
def test_scenario_intraday_force_false():
    assert strategy_will_trade(_s("INTRADAY"), trade_type="INTRADAY", force_intraday_only=False).will_trade
    v = strategy_will_trade(_s("DELIVERY"), trade_type="INTRADAY", force_intraday_only=False)
    assert not v.will_trade and v.cause == CAUSE_TRADE_TYPE


def test_scenario_delivery_force_false():
    assert strategy_will_trade(_s("DELIVERY"), trade_type="DELIVERY", force_intraday_only=False).will_trade
    v = strategy_will_trade(_s("INTRADAY"), trade_type="DELIVERY", force_intraday_only=False)
    assert not v.will_trade and v.cause == CAUSE_TRADE_TYPE


def test_scenario_both_force_false():
    assert strategy_will_trade(_s("INTRADAY"), trade_type="BOTH", force_intraday_only=False).will_trade
    assert strategy_will_trade(_s("DELIVERY"), trade_type="BOTH", force_intraday_only=False).will_trade


def test_current_config_intraday_trades_delivery_dormant():
    """★ CURRENT live config (trade_type=INTRADAY + force_intraday_only=true). Option A:
    an INTRADAY strategy WILL TRADE (cause OK); a DELIVERY strategy is DORMANT (cause
    FORCE_BREAKER) — the loader no longer rewrites intent. (The full loader+gate proof
    across all 15 real strategies is in test_slice2_strategy_control.)"""
    v = strategy_will_trade(_s("INTRADAY"), trade_type="INTRADAY", force_intraday_only=True)
    assert v.will_trade and v.cause == CAUSE_OK
    d = strategy_will_trade(_s("DELIVERY"), trade_type="INTRADAY", force_intraday_only=True)
    assert not d.will_trade and d.cause == CAUSE_FORCE_BREAKER


# ════════════════════════════════════════════════════════════════════════════════
# Step 2 — the live-path label split at the signal_processor gate (direct path :620;
# the resume path :1317 uses byte-identical mapping)
# ════════════════════════════════════════════════════════════════════════════════
def test_signal_processor_trade_type_mismatch_gets_distinct_label():
    store, _ = _make_store()
    _insert_queued_signal(store, "sig_tt", scanner="deliv_scan")
    strat = _MockStrategy(name="deliv_strat", intent="DELIVERY")
    proc, _, _ = _make_proc(
        store=store, trade_type="INTRADAY", force_intraday_only=False,
        strategies={"deliv_strat": strat},
        scan_webhook_map={"deliv_scan": {"strategy": "deliv_strat"}},
    )
    _run_one(proc, _now_tup("sig_tt", scanner="deliv_scan"), store=store)
    _assert_rejected(store, "sig_tt", "REJECTED_TRADE_TYPE")


def test_signal_processor_label_bleed_disabled_stays_strategy_control():
    """★ LABEL-BLEED REGRESSION: a NON-trade_type strategy-control reject (the
    per-strategy switch off) must STILL be STRATEGY_CONTROL, not TRADE_TYPE."""
    store, _ = _make_store()
    _insert_queued_signal(store, "sig_dis", scanner="dis_scan")
    strat = _MockStrategy(name="dis_strat", intent="INTRADAY")
    strat.enabled = False   # LAYER 3 -> cause DISABLED -> STRATEGY_CONTROL
    proc, _, _ = _make_proc(
        store=store, trade_type="INTRADAY", force_intraday_only=False,
        strategies={"dis_strat": strat},
        scan_webhook_map={"dis_scan": {"strategy": "dis_strat"}},
    )
    _run_one(proc, _now_tup("sig_dis", scanner="dis_scan"), store=store)
    row = _assert_rejected(store, "sig_dis", "REJECTED_STRATEGY_CONTROL")
    assert "TRADE_TYPE" not in row["status"]   # the split did not bleed


# ════════════════════════════════════════════════════════════════════════════════
# Step 3 — contradictory combo is a startup BLOCK (confirm, don't re-implement)
# ════════════════════════════════════════════════════════════════════════════════
def test_contradictory_delivery_plus_force_blocks_via_auditor():
    """trade_type=DELIVERY + force_intraday_only=true would silence the whole book;
    Config Auditor group A BLOCKs it. The full startup fail-fast (ValidationError) is
    locked in test_config_auditor — here we just confirm the group-A BLOCK fires for
    this composition (the gate + breaker compose safely)."""
    from core.config_auditor import audit
    rep = audit(SimpleNamespace(force_intraday_only=True, trade_type="DELIVERY"),
                groups="A")
    assert rep.blocks and "CONTRADICTORY CONFIG" in rep.blocks[0].message
