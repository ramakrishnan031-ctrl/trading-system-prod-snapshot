"""
tests/unit/test_daily_trade_review.py — Orders sheet (forensic master) generator.

Covers reports/daily_trade_review.py:
  * classify_closure() precedence (FLAG 2 — the highest-risk logic), incl. the
    reconciliation_log-wins-over-exit_reason collision cases;
  * the pure value helpers;
  * a DB-pure end-to-end build_records()+render on a synthetic StateStore (proves
    the multi-table join, the 69-column layout, freeze, and TOTALS).
"""
from __future__ import annotations

import json

import openpyxl
import pytest

from core.state_store import StateStore
from reports.daily_trade_review import (
    _COLSPECS, _SIGNAL_COLSPECS, _bucket_for, _build_t5_ranking, _day_summary, _fmt_time,
    _fmt_zone,
    _grade, _latency_ms, _minmax_norm, _minutes_between, _parse_dt, _signal_bucket,
    _signal_stage, _trade_bucket, _win_loss_pct, build_config_data, build_dashboard_data,
    build_reconciliation, build_records, build_signal_records, build_slippage_data,
    build_strategy_data, classify_closure, generate,
)

_TS = "2026-06-30T09:30:00+05:30"


# ── FLAG 2: closure-type precedence ───────────────────────────────────────────

def _recon(*names):
    return [{"check_name": n} for n in names]


def test_manual_close_alone_is_system_close_not_recon():
    # THE refinement (01-Jul): a bare MANUAL_CLOSE = the reconciler DETECTING an
    # external close (daily-EOD / operator / RMS). It must NOT be RECON_CLOSE
    # (over-claim) — it is the honest SYSTEM_CLOSE bucket, flagged for W8.
    c, ev = classify_closure({"exit_reason": "MANUAL", "status": "CLOSED_MANUAL"},
                             _recon("MANUAL_CLOSE"))
    assert c == "SYSTEM_CLOSE" and "W8" in ev


def test_daily_eod_signature_is_system_close():
    # The real ARVIND/NRBBEARING 30-Jun signature (exit_reason=MANUAL + MANUAL_CLOSE),
    # whether exit is 15:17 (EOD) or mid-session (manual) — honestly SYSTEM_CLOSE,
    # never RECON_CLOSE and never a time-guessed EOD.
    c, _ = classify_closure({"exit_reason": "MANUAL", "status": "CLOSED_MANUAL"},
                            _recon("MANUAL_CLOSE"))
    assert c == "SYSTEM_CLOSE"


def test_stuck_exiting_is_recon_close():
    # STUCK_EXITING = reconciler-INITIATED finalize → the ONLY true RECON_CLOSE.
    c, ev = classify_closure({"exit_reason": "MANUAL", "status": "CLOSED_MANUAL"},
                             _recon("STUCK_EXITING"))
    assert c == "RECON_CLOSE" and "STUCK_EXITING" in ev


def test_orphan_recovery():
    for name in ("ORPHAN_ORDER", "SYSTEM_OVERSELL", "INFLIGHT_ORPHAN_FLATTEN"):
        c, ev = classify_closure({"exit_reason": None, "status": "FAILED"}, _recon(name))
        assert c == "ORPHAN_RECOVERY" and name in ev


def test_positive_eod_marker_beats_manual_close_bookkeeping():
    # A positive EOD marker wins over a co-occurring MANUAL_CLOSE (which is EOD bookkeeping).
    c, ev = classify_closure({"exit_reason": "EOD_SQUAREOFF", "status": "CLOSED"},
                             _recon("MANUAL_CLOSE"))
    assert c == "EOD_SQUAREOFF" and "bookkeeping" in ev


def test_recon_eod_close_only_when_both():
    # RECON_EOD_CLOSE only when BOTH a reconciler-initiated close AND a positive EOD marker.
    c, _ = classify_closure({"exit_reason": "EOD_SQUAREOFF", "status": "CLOSED_MANUAL"},
                            _recon("STUCK_EXITING"))
    assert c == "RECON_EOD_CLOSE"


@pytest.mark.parametrize("er,status,recon,expected", [
    ("SL_HIT", "CLOSED", [], "SL"),
    ("EMERGENCY_SL_TICK", "CLOSED", [], "SL"),
    ("TGT_HIT", "CLOSED", [], "TGT"),
    ("EOD_SQUAREOFF", "CLOSED", [], "EOD_SQUAREOFF"),        # positive emergency-path EOD
    ("MANUAL_CLOSE_EOD", "CLOSED_MANUAL", [], "EOD_SQUAREOFF"),  # legacy '*EOD*' positive marker
    ("EOD_EXIT_FAILED", "OPEN", [], "—"),                   # failed EOD → trade stays OPEN (not a close)
    ("MANUAL", "CLOSED_MANUAL", [], "SYSTEM_CLOSE"),        # manual/EOD collapse, no recon row
    (None, "CLOSED_MANUAL", [], "SYSTEM_CLOSE"),
    (None, "REJECTED", [], "—"),
    (None, "CANCELLED", [], "—"),
    (None, "OPEN", [], "—"),
    (None, "FAILED", [], "UNKNOWN"),
    ("GTT_EXIT", "CLOSED", [], "UNKNOWN"),
])
def test_closure_branches(er, status, recon, expected):
    c, _ = classify_closure({"exit_reason": er, "status": status}, _recon(*recon))
    assert c == expected


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_helpers():
    assert _fmt_time("2026-06-30T09:30:15+05:30") == "09:30:15"
    assert _fmt_time(None) == ""
    assert _minutes_between("2026-06-30T09:30:00+05:30", "2026-06-30T10:00:00+05:30") == 30.0
    assert _parse_dt("bad") is None
    assert _grade(90) == "A" and _grade(10) == "F" and _grade(None) == ""
    z = _fmt_zone('{"band_low":100.0,"band_high":102.0,"confidence":"HIGH","timeframes":["day","60minute"]}',
                  1.25, "HIGH")
    assert "100.00-102.00" in z and "HIGH" in z and "day,60minute" in z and "1.25%" in z
    assert _fmt_zone(None, None, None).startswith("N/A")


def test_69_columns_and_groups():
    assert len(_COLSPECS) == 69
    groups = [g for g, *_ in _COLSPECS]
    assert set(groups) == set("ABCDEFGHI")
    # keys unique
    keys = [k for _g, _h, k, *_ in _COLSPECS]
    assert len(keys) == len(set(keys))


# ── DB-pure end-to-end on a synthetic store ───────────────────────────────────

def _seed(store: StateStore):
    with store.transaction() as cur:
        def sig(sid):
            cur.execute(
                "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
                "received_at,expires_at,status,fingerprint,fingerprint_date) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, "ACME", "scan1", "strat1", _TS, _TS, _TS, "TRADED", sid, "2026-06-30"))

        def trade(tid, sid, **kw):
            cols = dict(trade_id=tid, signal_id=sid, symbol="ACME", direction="LONG",
                        strategy="strat1", qty_planned=10, qty_filled=10,
                        entry_target_price=100.0, sl_initial=98.0, tgt_initial=104.0,
                        margin_reserved=200.0, risk_amount=20.0, created_at=_TS,
                        status="CLOSED", entry_mode="FULL", order_protocol="LIMIT_TRIPLE",
                        updated_at=_TS, mode="PAPER")
            cols.update(kw)
            keys = ",".join(cols); ph = ",".join("?" * len(cols))
            cur.execute(f"INSERT INTO trades ({keys}) VALUES ({ph})", tuple(cols.values()))

        def order(oid, tid, leg, placed):
            cur.execute(
                "INSERT INTO orders (order_id,trade_id,leg,leg_index,transaction_type,"
                "order_type,product,variety,qty_requested,status,placed_at,filled_at,"
                "avg_fill_price,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, tid, leg, 0, "BUY", "LIMIT", "MIS", "regular", 10, "COMPLETE",
                 placed, _TS, 100.1, _TS))

        # SL trade
        sig("s1"); trade("t1", "s1", exit_reason="SL_HIT", exit_price=98.0,
                          entry_actual_price=100.1, entry_time=_TS, exit_time=_TS,
                          gross_pnl=-19.0, charges=1.0, net_pnl=-20.0, cost_brokerage=0.4,
                          cost_stt=0.2, cost_exchange_txn=0.1, cost_sebi=0.1, cost_gst=0.1,
                          cost_stamp_duty=0.1, order_to_fill_ms=1500)
        order("o1", "t1", "ENTRY", _TS)
        # MANUAL close finalized by reconciler → RECON_CLOSE
        sig("s2"); trade("t2", "s2", status="CLOSED_MANUAL", exit_reason="MANUAL",
                         exit_price=101.0, entry_actual_price=100.0, entry_time=_TS,
                         exit_time=_TS, gross_pnl=10.0, charges=1.0, net_pnl=9.0)
        order("o2", "t2", "ENTRY", _TS)
        cur.execute(
            "INSERT INTO reconciliation_log (ts,check_name,tier,symbol,trade_id,"
            "description,action_taken,success) VALUES (?,?,?,?,?,?,?,?)",
            (_TS, "MANUAL_CLOSE", "RECOVERABLE", "ACME", "t2", "flat at broker", "closed", 1))


def test_build_and_render(tmp_path):
    store = StateStore(tmp_path / "tr.db")
    try:
        _seed(store)
        records, meta = build_records(store, "2026-06-30")
        assert meta["n"] == 2 and meta["mode"] == "PAPER"
        by_sym = {r["trade_id"]: r for r in records}
        t1, t2 = by_sym["t1"], by_sym["t2"]
        assert t1["closure_type"] == "SL" and t1["trade_result"] == "LOSS"
        assert t1["net"] == -20.0 and t1["broker_order_id"] == "o1"
        assert t1["entry_delay_s"] == 1.5              # order_to_fill_ms/1000
        assert t2["closure_type"] == "SYSTEM_CLOSE"    # bare MANUAL_CLOSE = external-close detect, NOT recon repair
        assert "W8" in t2["remarks"]
        # missing-data labels present (capture gaps)
        assert t1["broker_margin"].startswith("— pending")
        assert t1["exit_trigger"].startswith("— pending")
        assert t1["exchange_order_id"].startswith("— pending")
        # no config snapshot for the date → Min Score pending
        assert str(t1["min_score"]).startswith("—")

        out = generate(store, "2026-06-30", tmp_path / "out")
        assert out.exists()
        ws = openpyxl.load_workbook(out)["Orders"]
        assert ws.max_column == 69
        assert ws.freeze_panes == "F4"
        # TOTALS row present with the net sum (-20 + 9 = -11)
        totals_row = ws.max_row
        assert ws.cell(totals_row, 1).value == "TOTALS"
    finally:
        store.close()


# ── SIGNALS sheet (sheet 2) ───────────────────────────────────────────────────

def test_signal_bucket():
    assert _signal_bucket("REJECTED_SCORE_50") == "rejected"
    assert _signal_bucket("REJECTED_OPEN_POSITIONS") == "rejected"
    assert _signal_bucket("SKIPPED_QUOTE_UNAVAILABLE") == "skipped"
    assert _signal_bucket("PROCESSED") == "qualified"
    assert _signal_bucket("TRADED") == "qualified"
    assert _signal_bucket("QUEUED") == "other"
    assert _signal_bucket("SOME_FUTURE_STATUS") == "unmapped"   # no silent catch-all → FAIL-able


@pytest.mark.parametrize("status,has_scr,has_trade,expected", [
    ("PROCESSED", True, True, "ORDER_CREATED"),
    ("PLACEMENT_FAILED", True, False, "ORDERABLE"),
    ("REJECTED_OPEN_POSITIONS", True, False, "CAPITAL_CHECK"),
    ("REJECTED_SIZING_CONCENTRATION", True, False, "CAPITAL_CHECK"),
    ("REJECTED_ENTRY_THROTTLED", True, False, "CAPITAL_CHECK"),
    ("REJECTED_CIRCUIT_PROXIMITY", True, False, "SECONDARY_FILTER"),
    ("SKIPPED_QUOTE_UNAVAILABLE", False, False, "SECONDARY_FILTER"),
    ("REJECTED_SCORE_57", True, False, "SCORED"),
    ("REJECTED_SHADOW_INNING_ACTIVE", False, False, "RECEIVED"),
    ("QUEUED", False, False, "RECEIVED"),
])
def test_signal_stage(status, has_scr, has_trade, expected):
    assert _signal_stage(status, has_scr, has_trade) == expected


def test_latency_ms():
    assert _latency_ms('{"total_ms": 42}') == 42.0
    assert _latency_ms('{"a": 5, "b": 7}') == 12.0
    assert _latency_ms(None) is None
    assert _latency_ms("not json") is None


def _seed_signals(store: StateStore):
    with store.transaction() as cur:
        def sig(sid, status):
            cur.execute(
                "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
                "received_at,expires_at,status,fingerprint,fingerprint_date) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, "ACME", "scan1", "strat1", _TS, _TS, _TS, status, sid, "2026-06-30"))

        def screen(sid, score, status):
            cur.execute(
                "INSERT INTO screener_results (signal_id,score,tier,status,step_results,"
                "latencies,market_data_snapshot,ts) VALUES (?,?,?,?,?,?,?,?)",
                (sid, score, "HIGH", status, "{}", '{"total_ms":12}', "{}", _TS))

        sig("q1", "PROCESSED"); screen("q1", 70, "PASSED")            # qualified → ORDER_CREATED
        sig("r1", "REJECTED_SCORE_50"); screen("r1", 50, "REJECTED_SCORE_50")  # rejected @ SCORED
        sig("r2", "REJECTED_OPEN_POSITIONS"); screen("r2", 65, "PASSED")       # rejected @ CAPITAL_CHECK
        sig("s1", "SKIPPED_QUOTE_UNAVAILABLE"); screen("s1", 0, "SKIPPED_QUOTE_UNAVAILABLE")  # skipped
        sig("p1", "REJECTED_SHADOW_INNING_ACTIVE")                    # rejected pre-score → RECEIVED, no screener
        cur.execute(
            "INSERT INTO webhook_audit (ts,scanner_name,source_ip,payload_size_bytes,"
            "response_code,signals_accepted,signals_rejected,duration_ms) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (_TS, "scan1", "1.2.3.4", 100, 200, 5, 20, 3))  # received 25 = 5 stored + 20 dropped


def test_signals_build_and_totals(tmp_path):
    store = StateStore(tmp_path / "sig.db")
    try:
        _seed_signals(store)
        recs, meta = build_signal_records(store, "2026-06-30")
        assert meta["n"] == 5
        assert meta["qualified"] == 1
        assert meta["rejected"] == 3      # r1, r2, p1
        assert meta["skipped"] == 1
        # honest storage identity Δ = 0
        delta = meta["n"] - (meta["qualified"] + meta["rejected"] + meta["skipped"]
                             + meta["other"] + meta["dropped"])
        assert delta == 0
        # webhook context
        assert meta["wh_received"] == 25 and meta["wh_accepted"] == 5 and meta["wh_dropped"] == 20
        by = {r["signal_id"]: r for r in recs}
        assert by["q1"]["stage"] == "ORDER_CREATED" and by["q1"]["qualified"] == "Y"
        assert by["r1"]["stage"] == "SCORED" and by["r1"]["rejected"] == "Y"
        assert by["r2"]["stage"] == "CAPITAL_CHECK" and by["r2"]["capital_check"].startswith("REJECTED")
        assert by["p1"]["stage"] == "RECEIVED"      # pre-score reject, no screener
        assert str(by["q1"]["min_score"]).startswith("—")  # no config snapshot

        assert meta["unmapped"] == 0 and "traded" in meta   # partition FAIL-able; funnel meta present

        out = generate(store, "2026-06-30", tmp_path / "out")
        wb = openpyxl.load_workbook(out)
        assert wb.sheetnames == ["Dashboard", "Reconciliation", "Orders", "Signals",
                                 "Strategies", "Slippage", "Config"]
        ws = wb["Signals"]
        assert ws.max_column == len(_SIGNAL_COLSPECS) == 21
        assert ws.freeze_panes == "A18"
        assert ws.max_row == 17 + 5   # 17 header/totals rows + 5 data rows
    finally:
        store.close()


# ── RECONCILIATION sheet (sheet 3) ────────────────────────────────────────────

def test_trade_bucket():
    # FIX 3 (Phase-B.1): the status-partition class is "entered", NOT "filled" — the word
    # "filled" is reserved for the Dashboard's qty-based execution metric (qty_filled>0).
    assert _trade_bucket("CLOSED") == "entered"
    assert _trade_bucket("CLOSED_MANUAL") == "entered"
    assert _trade_bucket("OPEN") == "entered"
    assert _trade_bucket("CANCELLED") == "cancelled"
    assert _trade_bucket("FAILED") == "rejected_failed"
    assert _trade_bucket("REJECTED_PRICE_DRIFT") == "rejected_failed"
    assert _trade_bucket("PENDING_FILL") == "pending"
    assert _trade_bucket("SOME_FUTURE_STATUS") == "unmapped"   # FAIL-able (schema drift)


def _seed_ledger(store, rows):
    with store.transaction() as cur:
        for et, pnl, bal in rows:
            cur.execute(
                "INSERT INTO fm_ledger (ts,entry_type,amount,bucket,balance_before,"
                "balance_after,pnl_delta,margin_delta,costs) VALUES (?,?,?,?,?,?,?,?,?)",
                (_TS, et, 0.0, "both", 1000.0, bal, pnl, 0.0, 0.0))


def test_reconciliation_pass_then_fail_injection(tmp_path):
    store = StateStore(tmp_path / "rec.db")
    try:
        _seed(store)            # t1 CLOSED net -20 (o1), t2 CLOSED_MANUAL net 9 (o2), signals s1/s2 TRADED
        _seed_ledger(store, [("INIT", 0.0, 1000.0),
                             ("RELEASE_USED", -20.0, 980.0),
                             ("RELEASE_USED", 9.0, 989.0)])   # ledger realized -11 == Σtrades.net_pnl -11
        recs, _ = build_records(store, "2026-06-30")
        _srecs, sm = build_signal_records(store, "2026-06-30")
        blocks, meta = build_reconciliation(store, "2026-06-30", recs, sm)
        bd = {b["name"]: b for b in blocks}
        assert bd["1 · SIGNAL-STORAGE"]["status"] == "PASS"
        # M-R4: the tautological "2 · ORDER" block (RHS ≡ LHS → could never FAIL) was deleted
        # and the rest renumbered. Guard against re-introduction by IDENTITY (survives a
        # renumber), not just by name.
        assert not any("placement_failed" in b["identity"] for b in blocks), \
            "M-R4: the order-placement tautology block must not be re-introduced"
        assert bd["2 · TRADE"]["status"] == "PASS"
        assert bd["3 · CAPITAL"]["status"] == "PASS"
        assert bd["3 · CAPITAL"]["lhs"] == -11.0 and bd["3 · CAPITAL"]["rhs"] == -11.0
        assert bd["4 · BROKER"]["status"] == "PENDING_CAPTURE"
        assert meta["overall"] == "PASS — 1 pending capture"

        # FAIL injection: force capital drift beyond tolerance
        with store.transaction() as cur:
            cur.execute("UPDATE trades SET net_pnl = net_pnl + 100 WHERE trade_id='t1'")
        recs2, _ = build_records(store, "2026-06-30")
        blocks2, meta2 = build_reconciliation(store, "2026-06-30", recs2, sm)
        cap = next(b for b in blocks2 if b["name"].endswith("CAPITAL"))
        assert cap["status"] == "FAIL"        # ledger -11 vs trades +89 → drift 100 > ₹1
        assert meta2["overall"] == "FAIL"     # OVERALL flips to FAIL
    finally:
        store.close()


def test_mr3_capital_block_keys_trades_by_close_date_not_created(tmp_path):
    """M-R3: Block-3 CAPITAL must key trades_realized by the CLOSE date (to line up with the
    ledger's close-date RELEASE_USED), not created_at. An overnight trade created on D but
    closed on D+1 realises its P&L on D+1; keying by created_at summed 0 on D+1 -> a false
    'capital corruption' FAIL on the date seam. RED on pre-fix code (drift 11 -> FAIL)."""
    store = StateStore(tmp_path / "mr3.db")
    try:
        prev, day = "2026-06-29", "2026-06-30"
        prev_ts, day_ts = f"{prev}T14:00:00+05:30", f"{day}T09:45:00+05:30"
        with store.transaction() as cur:
            cur.execute(
                "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
                "received_at,expires_at,status,fingerprint,fingerprint_date) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("s_on", "ACME", "scan1", "strat1", prev_ts, prev_ts, prev_ts, "TRADED", "s_on", prev))
            cur.execute(
                "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,qty_planned,"
                "qty_filled,entry_target_price,sl_initial,tgt_initial,margin_reserved,risk_amount,"
                "created_at,status,order_protocol,updated_at,net_pnl,exit_time) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("t_on", "s_on", "ACME", "LONG", "strat1", 10, 10, 100.0, 98.0, 104.0, 200.0, 20.0,
                 prev_ts, "CLOSED", "LIMIT_TRIPLE", day_ts, -11.0, day_ts))  # created D, closed D+1
        # ledger INIT + RELEASE_USED both on the CLOSE day (_seed_ledger stamps ts=_TS=2026-06-30)
        _seed_ledger(store, [("INIT", 0.0, 1000.0), ("RELEASE_USED", -11.0, 989.0)])
        recs, _ = build_records(store, day)
        _sr, sm = build_signal_records(store, day)
        blocks, _meta = build_reconciliation(store, day, recs, sm)
        cap = next(b for b in blocks if b["name"].endswith("CAPITAL"))
        assert cap["status"] == "PASS", cap          # RED pre-fix: trades_realized=0 -> drift 11 -> FAIL
        assert cap["lhs"] == -11.0 and cap["rhs"] == -11.0
    finally:
        store.close()


def test_reconciliation_capital_pending_when_no_init(tmp_path):
    store = StateStore(tmp_path / "rec2.db")
    try:
        _seed(store)   # trades but NO fm_ledger INIT row
        recs, _ = build_records(store, "2026-06-30")
        _srecs, sm = build_signal_records(store, "2026-06-30")
        blocks, meta = build_reconciliation(store, "2026-06-30", recs, sm)
        cap = next(b for b in blocks if b["name"].endswith("CAPITAL"))
        assert cap["status"] == "PENDING_CAPTURE"   # no opening → pending, never a fake FAIL
        assert "FAIL" not in meta["overall"]
    finally:
        store.close()


# ── CONFIG sheet (sheet 4 — first consumer of W0 config_snapshots) ────────────

def _seed_config_snapshot(store, date_iso, cfg_dict, *, sid_account="TEST01", mode="LIVE"):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO config_snapshots (snapshot_date, snapshot_ts, account_id, mode, "
            "trade_type, config_hash, config_json) VALUES (?,?,?,?,?,?,?)",
            (date_iso, _TS, sid_account, mode, "INTRADAY", "deadbeef" * 8,
             json.dumps(cfg_dict)))


_CFG = {
    "system": {
        "trade_type": "INTRADAY", "force_intraday_only": True, "delivery_enabled": False,
        "trading_hours": {"entry_start": "10:00", "entry_end": "15:00", "eod_entry_cutoff": "15:15",
                          "eod_squareoff_time": "15:17", "market_open": "09:15", "market_close": "15:30"},
        "capital": {"leverage_map": {"INTRADAY": 5.0, "COVER_ORDER": 6.0,
                                     "BRACKET_ORDER": 5.0, "DELIVERY": 1.0}},
        "risk": {"max_open_positions": 5, "max_daily_trades": 10, "max_consecutive_losses": 5,
                 "daily_loss_limit_pct": 0.03, "max_sector_exposure_pct": 0.4},
        "position_sizing": {"max_position_value_pct": 0.4, "max_concentration_pct": 0.1,
                            "risk_per_trade_pct": 0.01},
        "entry_gate": {"max_entry_slippage_pct": 1.0, "slippage_buffer": 2.0},
        "slippage_bands": ["0-100", "100-200"],
    },
    "scoring": {"min_pass_score": 60, "high_score_threshold": 80, "medium_score_threshold": 65,
                "steps": {"price_action": 15, "volume_surge": 15, "atr_filter": 10}},
    "slippage": {"default_tier": "liquid",
                 "tiers": {"liquid": {"slippage_bps": 5}, "mid": {"slippage_bps": 15},
                           "small": {"slippage_bps": 30}}},
    "broker_costs": {"zerodha": {"brokerage_flat_intraday": 20.0, "gst_pct": 18.0,
                                 "stt_sell_pct": 0.025}},
}


def _cfg_val(sections, section_key, label):
    for sec in sections:
        if sec["key"] == section_key:
            for l, v, _ind in sec["rows"]:
                if l.strip() == label:
                    return v
    return "__NOT_FOUND__"


def test_config_no_snapshot_renders_pending_w0(tmp_path):
    store = StateStore(tmp_path / "cfg_none.db")
    try:
        sections, meta = build_config_data(store, "2020-01-01")
        assert meta["has_snapshot"] is False and sections == []
        # the sheet renders an honest placeholder, not a blank/absent sheet
        out = generate(store, "2020-01-01", tmp_path / "out")
        wb = openpyxl.load_workbook(out)
        assert "Config" in wb.sheetnames
        ws = wb["Config"]
        joined = " ".join(str(c.value) for row in ws.iter_rows(values_only=False)
                          for c in row if c.value)
        assert "pending W0" in joined
    finally:
        store.close()


def test_config_sections_and_spot_values(tmp_path):
    store = StateStore(tmp_path / "cfg.db")
    try:
        _seed_config_snapshot(store, "2026-06-30", _CFG)
        sections, meta = build_config_data(store, "2026-06-30")
        assert meta["has_snapshot"] is True
        assert meta["mode"] == "LIVE" and meta["account"] == "TEST01"
        # the 6 documented sections, in order
        assert [s["key"] for s in sections] == [
            "SYSTEM", "RISK", "SCORING", "SLIPPAGE", "BROKER COSTS", "STRATEGY"]
        # the build-gate spot-checks — each value renders faithfully from config_json
        assert _cfg_val(sections, "SCORING", "min_pass_score") == 60
        assert _cfg_val(sections, "RISK", "daily_loss_limit_pct") == 0.03
        assert _cfg_val(sections, "SYSTEM", "INTRADAY") == 5.0           # leverage_map sub-row
        assert _cfg_val(sections, "SYSTEM", "entry_end") == "15:00"
        assert _cfg_val(sections, "SYSTEM", "force_intraday_only") is True
        assert _cfg_val(sections, "SLIPPAGE", "liquid") == 5
        assert _cfg_val(sections, "BROKER COSTS", "gst_pct") == 18.0
        # STRATEGY is honestly deferred to W0.1
        assert "W0.1" in str(_cfg_val(sections, "STRATEGY", "per-strategy config"))
        # max_capital is not in config → honest runtime label, never a fake number
        assert "runtime" in str(_cfg_val(sections, "SYSTEM", "max_capital"))
    finally:
        store.close()


def test_capital_block_wording_references_w10_not_pollution(tmp_path):
    # HOUSEKEEPING (01-Jul): RESET_PNL is a by-design daily EOD reset, NOT "pollution".
    # The real finding is W10 (get_daily_realized_net_pnl double-subtracts costs).
    store = StateStore(tmp_path / "recw.db")
    try:
        _seed(store)
        _seed_ledger(store, [("INIT", 0.0, 1000.0),
                             ("RELEASE_USED", -20.0, 980.0),
                             ("RELEASE_USED", 9.0, 989.0)])
        recs, _ = build_records(store, "2026-06-30")
        _s, sm = build_signal_records(store, "2026-06-30")
        blocks, _ = build_reconciliation(store, "2026-06-30", recs, sm)
        cap = next(b for b in blocks if b["name"].endswith("CAPITAL"))
        d = cap["detail"].lower()
        assert "pollutes" not in d                   # the old claim ("RESET_PNL pollutes it") is gone
        assert "by-design daily eod reset, not pollution" in d   # corrected: by-design, explicitly not pollution
        assert "w10" in d                            # the real finding is referenced
    finally:
        store.close()


# ── STRATEGIES sheet (sheet 5 — first analytics sheet) ────────────────────────

def _rec(strategy, direction, net, *, gross=None, cap=100.0, entry="10:05:00",
         sl_pct=None, tgt_pct=None, filled_entry=None, filled_sl=None, filled_tgt=None,
         rr=None):
    """Build a minimal Orders-record dict (the keys build_strategy_data reads)."""
    return {"strategy": strategy, "direction": direction, "_net_raw": net,
            "gross": net if gross is None else gross, "capital_consumed": cap,
            "entry_filled": entry, "sl_pct": sl_pct, "tgt_pct": tgt_pct,
            "filled_entry": filled_entry, "filled_sl": filled_sl,
            "filled_tgt": filled_tgt, "rr_achieved": rr}


def test_strategy_t1_t2_t3_from_records(tmp_path):
    store = StateStore(tmp_path / "strat.db")   # empty DB → T5 empty; T1-T4 from records
    try:
        records = [
            _rec("A", "LONG", 10.0, gross=12.0, cap=100.0, entry="10:05:00"),
            _rec("A", "LONG", -5.0, gross=-4.0, cap=100.0, entry="10:20:00"),
            _rec("B", "SHORT", 3.0, gross=3.5, cap=50.0, entry="11:05:00"),
        ]
        srecords = [{"strategy": "A", "vm_receipt": "09:20:00"},
                    {"strategy": "A", "vm_receipt": "10:05:00"},
                    {"strategy": "B", "vm_receipt": "11:00:00"},
                    {"strategy": "C", "vm_receipt": "12:00:00"}]   # C = signal-only
        data = build_strategy_data(store, "2026-06-30", records, srecords, {"mode": "LIVE"})

        t1 = {r["strategy"]: r for r in data["t1"]}
        assert t1["A"]["trades"] == 2 and t1["A"]["wins"] == 1 and t1["A"]["losses"] == 1
        assert t1["A"]["net"] == 5.0 and t1["A"]["win_pct"] == 50.0 and t1["A"]["signals"] == 2
        assert t1["A"]["max_win"] == 10.0 and t1["A"]["max_loss"] == -5.0
        assert t1["A"]["roi_pct"] == 2.5                     # 5 / 200 * 100
        assert t1["C"]["trades"] == 0 and t1["C"]["signals"] == 1   # signal-only strategy present
        # T3 long/short
        t3 = {r["direction"]: r for r in data["t3"]}
        assert t3["LONG"]["trades"] == 2 and t3["SHORT"]["trades"] == 1
        assert t3["LONG"]["profit"] == 5.0 and t3["SHORT"]["profit"] == 3.0
        # T2 buckets by entry time (2 A trades in 10:00-10:30; B in 11:00-11:30)
        t2 = {r["bucket"]: r for r in data["t2"]}
        assert t2["10:00-10:30"]["trades"] == 2 and t2["11:00-11:30"]["trades"] == 1
    finally:
        store.close()


def test_strategy_t4_rr_planned_vs_actual(tmp_path):
    store = StateStore(tmp_path / "rr.db")
    try:
        records = [_rec("A", "LONG", 5.0, sl_pct=1.0, tgt_pct=2.0,
                        filled_entry=100.0, filled_sl=99.0, filled_tgt=102.0, rr=1.8)]
        data = build_strategy_data(store, "2026-06-30", records, [], {"mode": "LIVE"})
        t4 = {r["strategy"]: r for r in data["t4"]}["A"]
        assert t4["plan_sl_pct"] == 1.0 and t4["plan_tgt_pct"] == 2.0
        assert t4["act_sl_pct"] == 1.0 and t4["act_tgt_pct"] == 2.0   # |100-99|/100, |102-100|/100
        assert t4["avg_rr"] == 1.8
    finally:
        store.close()


def _seed_trade(store, tid, date, strategy, net, *, margin=100.0, direction="LONG",
                status="CLOSED"):
    ts = f"{date}T10:00:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,received_at,"
            "expires_at,status,fingerprint,fingerprint_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"sig_{tid}", "ACME", "scan", strategy, ts, ts, ts, "TRADED", f"fp_{tid}", date))
        cur.execute(
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,qty_planned,"
            "qty_filled,entry_target_price,sl_initial,tgt_initial,margin_reserved,risk_amount,"
            "created_at,status,order_protocol,updated_at,net_pnl,mode) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"sig_{tid}", "ACME", direction, strategy, 10, 10, 100.0, 98.0, 104.0,
             margin, 20.0, ts, status, "LIMIT_TRIPLE", ts, net, "PAPER"))


def test_strategy_t5_confidence_downweights_low_sample(tmp_path):
    """THE design goal: a 1-trade/100%-win strategy must NOT top a high-sample one —
    confidence (min(n/20,1)) suppresses it, even though it wins on raw normalized score."""
    store = StateStore(tmp_path / "t5.db")
    try:
        # HIGH: 15 trades over 5 sessions, ~53% win, moderate net
        for i in range(15):
            _seed_trade(store, f"h{i}", f"2026-06-{20 + (i % 5):02d}", "HIGH",
                        net=(4.0 if i % 15 < 8 else -3.0), margin=100.0)
        # LOW: 1 trade, 100% win, big net (would top on raw metrics)
        _seed_trade(store, "l0", "2026-06-24", "LOW", net=50.0, margin=100.0)

        t5 = _build_t5_ranking(store, "2026-06-30", n_sessions=20)
        rows = {r["strategy"]: r for r in t5["rows"]}
        assert rows["LOW"]["trades_n"] == 1 and rows["LOW"]["win_pct"] == 100.0
        assert rows["HIGH"]["trades_n"] == 15
        assert rows["LOW"]["confidence"] == 0.05 and rows["HIGH"]["confidence"] == 0.75
        # on RAW (pre-confidence) normalized score, LOW would win…
        raw_low = 0.40 * rows["LOW"]["win_norm"] + 0.40 * rows["LOW"]["roi_norm"] + 0.20 * rows["LOW"]["rr_norm"]
        raw_high = 0.40 * rows["HIGH"]["win_norm"] + 0.40 * rows["HIGH"]["roi_norm"] + 0.20 * rows["HIGH"]["rr_norm"]
        assert raw_low > raw_high
        # …but confidence flips it: HIGH's composite wins → HIGH ranks above LOW
        assert rows["HIGH"]["composite"] > rows["LOW"]["composite"]
        assert rows["HIGH"]["rank"] < rows["LOW"]["rank"]
    finally:
        store.close()


def test_strategy_helpers_pure():
    # min-max: all-equal (or all-None) → neutral 0.5, never fabricated spread
    assert _minmax_norm([5.0, 5.0, 5.0]) == [0.5, 0.5, 0.5]
    assert _minmax_norm([None, None]) == [0.5, 0.5]
    assert _minmax_norm([0.0, 10.0, 5.0]) == [0.0, 1.0, 0.5]
    # time buckets by entry time
    assert _bucket_for("10:05:00") == "10:00-10:30"
    assert _bucket_for("09:20:00") == "09:15-09:30"
    assert _bucket_for("15:10:00") == "15:00-15:15"
    assert _bucket_for("15:45:00") is None      # out of range
    assert _bucket_for("") is None


# ── SLIPPAGE sheet (sheet 6 — last analytics sheet) ───────────────────────────

def _seed_slip(store, tid, date, symbol, strategy, qty, entry_rs, entry_pct,
               *, sl_rs=None, tgt_rs=None, band="100-200"):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO trade_slippage_log (trade_id,trade_date,symbol,strategy_name,side,qty,"
            "price_band,entry_slippage_rs,entry_slippage_pct,sl_slippage_rs,tgt_slippage_rs) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tid, date, symbol, strategy, "LONG", qty, band, entry_rs, entry_pct, sl_rs, tgt_rs))


def test_slippage_decomposition_sums_to_total(tmp_path):
    """The build-gate identity: total slip cost = ENTRY + SL − TGT, components sum to total."""
    store = StateStore(tmp_path / "slip.db")
    try:
        # t1 SL-exit: entry 0.5*10=5, sl 1.0*10=10, tgt 0 → total 15
        _seed_slip(store, "t1", "2026-06-30", "AAA", "sA", 10, 0.5, 0.5, sl_rs=1.0)
        # t2 TGT-exit (favourable +0.3 → subtracts): entry 0.2*20=4, tgt -(0.3)*20=-6 → total -2
        _seed_slip(store, "t2", "2026-06-30", "BBB", "sB", 20, 0.2, 0.2, tgt_rs=0.3)
        records = [{"trade_id": "t1", "gross": 100.0, "slip_delta": 0.1},
                   {"trade_id": "t2", "gross": 50.0, "slip_delta": -0.05}]
        data = build_slippage_data(store, "2026-06-30", records, {"mode": "LIVE"})
        s = data["summary"]
        assert s["entry_total"] == 9.0 and s["sl_total"] == 10.0 and s["tgt_total"] == -6.0
        assert s["total_cost"] == 13.0 and s["sum_check"] == 13.0     # components sum to total
        assert s["gross_total"] == 150.0 and s["pct_of_gross"] == 8.67
        # worst-20 sorted by slip cost desc: t1 (15) before t2 (-2)
        assert data["worst"][0]["symbol"] == "AAA" and data["worst"][0]["total_cost"] == 15.0
        # band aggregation (both in 100-200): trades 2, avg bps = avg(50,20)=35, cost 13
        band = {b["key"]: b for b in data["bands"]}["100-200"]
        assert band["trades"] == 2 and band["avg_bps"] == 35.0 and band["total_cost"] == 13.0
    finally:
        store.close()


def test_slippage_empty_is_graceful(tmp_path):
    store = StateStore(tmp_path / "slip_e.db")
    try:
        data = build_slippage_data(store, "2026-06-30", [], {"mode": "LIVE"})
        assert data["summary"]["n"] == 0 and data["summary"]["total_cost"] == 0.0
        assert data["summary"]["pct_of_gross"] is None      # gross 0 → N/A, never a div-by-zero
        assert data["bands"] == [] and data["worst"] == [] and data["trend"] == []
    finally:
        store.close()


# ── DASHBOARD sheet (final sheet, placed first) ───────────────────────────────

def _drec(strategy, direction, net, symbol, *, closure="TGT", entry="10:05:00",
          exit="10:30:00", cap=100.0, qty=10):
    """Minimal Orders-record dict (keys build_dashboard_data / build_strategy_data read)."""
    return {"strategy": strategy, "direction": direction, "_net_raw": net, "gross": net,
            "capital_consumed": cap, "closure_type": closure, "entry_filled": entry,
            "exit_time": exit, "broker_order_id": "B", "qty_filled": qty, "symbol": symbol,
            "slip_pct": None, "total_charges": 0.5, "trade_status": "CLOSED"}


def _full_smeta(n, qualified, traded):
    """A partition-consistent Signals-meta (build_reconciliation reads all the buckets)."""
    return {"n": n, "qualified": qualified, "rejected": n - qualified, "skipped": 0,
            "other": 0, "dropped": 0, "traded": traded, "wh_received": n, "wh_accepted": n,
            "wh_dropped": 0}


def test_dashboard_summary_agrees_with_detail(tmp_path):
    store = StateStore(tmp_path / "dash.db")
    try:
        records = [_drec("A", "LONG", 10.0, "AAA", closure="TGT"),
                   _drec("A", "LONG", -5.0, "BBB", closure="SL"),
                   _drec("B", "SHORT", 2.0, "CCC", closure="TGT")]
        srecords = [{"strategy": "A", "vm_receipt": "09:20:00"} for _ in range(3)]
        meta = {"mode": "LIVE", "n": 3}
        smeta = _full_smeta(3, 3, 3)
        sdata = build_strategy_data(store, "2026-06-30", records, srecords, meta)
        slipdata = build_slippage_data(store, "2026-06-30", records, meta)
        _rb, rmeta = build_reconciliation(store, "2026-06-30", records, smeta)
        dash = build_dashboard_data(store, "2026-06-30", records, srecords, meta, smeta,
                                    sdata, slipdata, rmeta)
        # net agrees with Σ records net (the Orders TOTALS)
        assert dash["profit"]["net"] == 7.0 and dash["profit"]["gross"] == 7.0
        assert dash["profit"]["profit_factor"] == 2.4          # 12 gross-profit / 5 gross-loss
        assert dash["profit"]["win_rate"] == round(2 / 3 * 100, 2)
        # banner agrees with reconciliation OVERALL (cannot disagree)
        assert dash["banner"] == rmeta["overall"]
        # closure breakdown agrees with the records
        assert dash["trading"]["closure"] == {"TGT": 2, "SL": 1}
        # long/short agrees with Strategies T3
        assert dash["trading"]["long"]["trades"] == 2 and dash["trading"]["short"]["trades"] == 1
    finally:
        store.close()


def test_dashboard_max_concurrent_sweep():
    from reports.daily_trade_review import _max_concurrent
    recs = [{"entry_filled": "10:00:00", "exit_time": "10:30:00", "capital_consumed": 100.0},
            {"entry_filled": "10:15:00", "exit_time": "10:45:00", "capital_consumed": 50.0},
            {"entry_filled": "11:00:00", "exit_time": "11:10:00", "capital_consumed": 30.0}]
    n, cap = _max_concurrent(recs)
    assert n == 2 and cap == 150.0        # first two overlap 10:15–10:30; third is separate
    assert _max_concurrent([]) == (0, None)


def test_dashboard_highlights_high_slippage_and_skew(tmp_path):
    store = StateStore(tmp_path / "hi.db")
    try:
        records = [_drec("A", "LONG", 10.0, "AAA", closure="TGT"),
                   _drec("A", "LONG", 8.0, "BBB", closure="TGT"),
                   _drec("B", "SHORT", -5.0, "CCC", closure="SL"),
                   _drec("B", "SHORT", -3.0, "DDD", closure="SL")]
        srecords = [{"strategy": "A", "vm_receipt": "09:20:00"}]
        meta = {"mode": "LIVE", "n": 4}
        smeta = _full_smeta(4, 4, 4)
        sdata = build_strategy_data(store, "2026-06-30", records, srecords, meta)
        # fake slipdata: slip cost = 300% of gross (> 25% flag), driver AAA
        slipdata = {"summary": {"total_cost": 50.0, "pct_of_gross": 300.0, "n": 4},
                    "worst": [{"symbol": "AAA", "total_cost": 50.0}]}
        _rb, rmeta = build_reconciliation(store, "2026-06-30", records, smeta)
        dash = build_dashboard_data(store, "2026-06-30", records, srecords, meta, smeta,
                                    sdata, slipdata, rmeta)
        hl = " || ".join(dash["highlights"])
        assert "HIGH SLIPPAGE" in hl and "300.0% of gross" in hl and "AAA" in hl
        # LONG 100% vs SHORT 0% → skew, SHORT underperforms
        assert "DIRECTION SKEW" in hl and "SHORT underperforms" in hl
    finally:
        store.close()


def test_dashboard_prior_day_delta(tmp_path):
    store = StateStore(tmp_path / "pd.db")
    try:
        _seed_trade(store, "p1", "2026-06-29", "A", 10.0)     # prior day in the DB
        _seed_trade(store, "p2", "2026-06-29", "A", -4.0)
        records = [_drec("A", "LONG", 3.0, "AAA")]            # today (synthetic)
        srecords = [{"strategy": "A", "vm_receipt": "09:20:00"}]
        meta = {"mode": "LIVE", "n": 1}
        smeta = _full_smeta(1, 1, 1)
        sdata = build_strategy_data(store, "2026-06-30", records, srecords, meta)
        slipdata = build_slippage_data(store, "2026-06-30", records, meta)
        _rb, rmeta = build_reconciliation(store, "2026-06-30", records, smeta)
        dash = build_dashboard_data(store, "2026-06-30", records, srecords, meta, smeta,
                                    sdata, slipdata, rmeta)
        assert dash["prior_date"] == "2026-06-29"
        dm = {d["metric"]: d for d in dash["deltas"]}
        assert dm["Net Profit ₹"]["prior"] == 6.0 and dm["Net Profit ₹"]["today"] == 3.0
        assert dm["Net Profit ₹"]["delta"] == -3.0            # today − prior
    finally:
        store.close()


# ── Phase-B.1 fixes: win%-denominator, per-date coverage %, "filled" label ────────

def test_win_loss_pct_helper_decided_denominator():
    """FIX 1 unit: _win_loss_pct divides by DECIDED (wins+losses); sums to 100; N/A when
    no decided trade (never a misleading 0.0%)."""
    assert _win_loss_pct(3, 2) == (60.0, 40.0)          # 3/5, 2/5 → sum 100
    assert sum(_win_loss_pct(3, 2)) == 100.0
    assert _win_loss_pct(5, 0) == (100.0, 0.0)
    assert _win_loss_pct(0, 3) == (0.0, 100.0)
    assert _win_loss_pct(0, 0) == (None, None)          # no decided trade → honest N/A


def test_fix1_win_pct_uses_decided_denominator_not_attempts(tmp_path):
    """FIX 1 (fail-on-old): a strategy/direction with 1 win + 1 loss + 1 never-filled
    attempt must show Win% 50 (1/2 decided), NOT the old diluted 33.33 (1/3 attempts);
    win% + loss% == 100."""
    store = StateStore(tmp_path / "fix1.db")
    try:
        records = [
            _rec("A", "LONG", 8.0, gross=9.0, cap=100.0, entry="10:05:00"),    # win  (decided)
            _rec("A", "LONG", -4.0, gross=-3.0, cap=100.0, entry="10:20:00"),  # loss (decided)
            _rec("A", "LONG", None, cap=0.0, entry=None),                      # never-filled attempt
        ]
        data = build_strategy_data(store, "2026-06-30", records, [], {"mode": "LIVE"})
        a = {r["strategy"]: r for r in data["t1"]}["A"]
        assert a["trades"] == 3 and a["wins"] == 1 and a["losses"] == 1
        assert a["win_pct"] == 50.0 and a["loss_pct"] == 50.0          # 1/(1+1), NOT 1/3
        assert a["win_pct"] + a["loss_pct"] == 100.0
        t3 = {r["direction"]: r for r in data["t3"]}
        assert t3["LONG"]["trades"] == 3 and t3["LONG"]["win_pct"] == 50.0   # not 33.33
    finally:
        store.close()


def test_fix1_skew_skips_direction_with_no_decided_trade(tmp_path):
    """FIX 1: a direction whose only record never filled (0 decided) must NOT trigger a
    'SHORT underperforms' skew highlight, and its Win% renders N/A (None), not 0.0%."""
    store = StateStore(tmp_path / "skew.db")
    try:
        records = [_drec("A", "LONG", 10.0, "AAA"), _drec("A", "LONG", 8.0, "BBB"),
                   _drec("B", "SHORT", None, "CCC", qty=0)]      # never-filled short → 0 decided
        srecords = [{"strategy": "A", "vm_receipt": "09:20:00"}]
        meta = {"mode": "LIVE", "n": 3}
        smeta = _full_smeta(3, 3, 2)
        sdata = build_strategy_data(store, "2026-06-30", records, srecords, meta)
        slipdata = build_slippage_data(store, "2026-06-30", records, meta)
        _rb, rmeta = build_reconciliation(store, "2026-06-30", records, smeta)
        dash = build_dashboard_data(store, "2026-06-30", records, srecords, meta, smeta,
                                    sdata, slipdata, rmeta)
        hl = " || ".join(dash["highlights"])
        assert "underperforms" not in hl and "DIRECTION SKEW" not in hl
        t3 = {r["direction"]: r for r in sdata["t3"]}
        assert t3["SHORT"]["win_pct"] is None and t3["LONG"]["win_pct"] == 100.0
    finally:
        store.close()


def test_fix2_coverage_webhook_drop_pct_is_per_date(tmp_path):
    """FIX 2 (fail-on-old): the Signals coverage note computes the webhook drop % from
    smeta per-date (was a hardcoded '82%'). 23 dropped of 55 received → 41.8%."""
    store = StateStore(tmp_path / "cov.db")
    try:
        records = [_drec("A", "LONG", 5.0, "AAA")]
        srecords = [{"strategy": "A", "vm_receipt": "09:20:00"}]
        meta = {"mode": "LIVE", "n": 1}
        smeta = {"n": 1, "qualified": 1, "rejected": 0, "skipped": 0, "other": 0,
                 "dropped": 0, "traded": 1, "wh_received": 55, "wh_accepted": 32, "wh_dropped": 23}
        sdata = build_strategy_data(store, "2026-06-30", records, srecords, meta)
        slipdata = build_slippage_data(store, "2026-06-30", records, meta)
        _rb, rmeta = build_reconciliation(store, "2026-06-30", records, smeta)
        dash = build_dashboard_data(store, "2026-06-30", records, srecords, meta, smeta,
                                    sdata, slipdata, rmeta)
        sig_cov = next(c for c in dash["coverage"] if c["section"] == "Signals")
        assert "41.8% webhook-dropped" in sig_cov["note"] and "82%" not in sig_cov["note"]
    finally:
        store.close()


def test_fix3_filled_label_disambiguated_entered_vs_qty(tmp_path):
    """FIX 3: a CLOSED_MANUAL record with qty_filled=0 (GICRE-like) is 'entered' in the
    Reconciliation status-partition but NOT 'filled' on the Dashboard (qty-based). The
    word 'filled' now has one meaning report-wide; the two counts legitimately differ."""
    store = StateStore(tmp_path / "filled.db")
    try:
        records = [
            {**_drec("A", "LONG", -21.45, "AGARIND", closure="SYSTEM_CLOSE", qty=1),
             "trade_status": "CLOSED_MANUAL"},
            {**_drec("A", "LONG", None, "GICRE", closure="SYSTEM_CLOSE", qty=0),
             "trade_status": "CLOSED_MANUAL", "broker_order_id": None},
        ]
        srecords = [{"strategy": "A", "vm_receipt": "09:20:00"}]
        meta = {"mode": "LIVE", "n": 2}
        smeta = _full_smeta(2, 2, 2)
        rb, rmeta = build_reconciliation(store, "2026-06-30", records, smeta)
        b_trade = next(b for b in rb if b["name"] == "2 · TRADE")
        assert "entered(open/partial/exiting/closed)=2" in b_trade["detail"]
        assert "filled=" not in b_trade["detail"]         # the ambiguous label is gone from recon
        assert b_trade["identity"] == "placed = entered + cancelled + rejected/failed + pending"
        sdata = build_strategy_data(store, "2026-06-30", records, srecords, meta)
        slipdata = build_slippage_data(store, "2026-06-30", records, meta)
        dash = build_dashboard_data(store, "2026-06-30", records, srecords, meta, smeta,
                                    sdata, slipdata, rmeta)
        assert dash["trading"]["filled"] == 1             # only AGARIND (qty 1); GICRE qty 0 not filled
    finally:
        store.close()


# ── Phase C cron entrypoint: default-date + monitored heartbeat ───────────────────

def test_main_defaults_date_to_today_and_records_heartbeat(tmp_path, monkeypatch):
    """Phase C: `main()` runs with NO --date (defaults to today IST, so the cron line
    needs no date substitution — like the retired daily_review/daily_report) and records
    a monitored cron_heartbeat('daily_trade_review') SUCCESS to the same DB."""
    import sys
    from reports.daily_trade_review import main
    from core.time_authority import now_ist
    db = tmp_path / "trading_system.db"
    StateStore(db).close()                       # create a real (empty) DB with all tables
    outdir = tmp_path / "out"
    monkeypatch.setattr(sys, "argv",
                        ["daily_trade_review.py", "--db", str(db), "--output-dir", str(outdir)])
    rc = main()
    assert rc == 0
    today = now_ist().strftime("%Y-%m-%d")
    assert (outdir / f"daily_trade_review_report_{today}.xlsx").exists()
    s = StateStore(db)
    try:
        row = s.fetch_one("SELECT status FROM cron_heartbeat WHERE job_name='daily_trade_review' "
                          "ORDER BY id DESC LIMIT 1")
        assert row is not None and (row["status"] or "").upper() == "SUCCESS"
    finally:
        s.close()


# ─────────────────────────────────────────────────────────────────────────────
# The headline win% must use the SAME denominator as the per-strategy figures
# ─────────────────────────────────────────────────────────────────────────────

def test_day_summary_win_pct_matches_the_per_strategy_denominator():
    """RED ON OLD. _day_summary divided by len(realized) -- which counts BREAKEVENS
    (net exactly 0) -- while _win_loss_pct divides by decided (wins+losses), and
    _win_loss_pct's docstring claimed the two matched. They did not.

    Shaped like the real book at the time of the fix: 61 wins / 91 losses / 1 breakeven.
    Old headline: 61/153 = 39.87. Per-strategy: 61/152 = 40.13. Same report, two win
    rates, and the headline diluted by a single breakeven -- exactly the dilution FIX 1
    (Phase-B.1) removed everywhere else."""
    records = ([{"_net_raw": 1.0} for _ in range(61)]
               + [{"_net_raw": -1.0} for _ in range(91)]
               + [{"_net_raw": 0.0}])

    headline = _day_summary(records, [])["win_pct"]

    assert headline == _win_loss_pct(61, 91)[0], "headline win% contradicts per-strategy"
    assert headline == 40.13
    assert headline != 39.87, "still using the breakeven-diluted denominator"


def test_day_summary_win_pct_is_none_when_nothing_is_decided():
    """No decided trade -> N/A, never a misleading 0.0% (the _win_loss_pct contract,
    now inherited by the headline for free)."""
    assert _day_summary([], [])["win_pct"] is None
    assert _day_summary([{"_net_raw": None}], [])["win_pct"] is None


def test_day_summary_win_pct_unaffected_when_there_are_no_breakevens():
    """The common case is untouched: with no net==0 trade the two denominators are
    identical, so this fix moves no number on a normal day."""
    records = [{"_net_raw": 1.0}, {"_net_raw": 1.0}, {"_net_raw": -1.0}]
    assert _day_summary(records, [])["win_pct"] == 66.67
