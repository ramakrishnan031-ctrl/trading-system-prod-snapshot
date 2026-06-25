"""
tests/unit/test_cnc_gtt_monitor.py — SLICE2.5-P2 STEP 3+3.5 reconcile + GTT_EXIT.

Y7 injected-state tests (paper, off-hours — no full GTT-trigger simulator): a real
StateStore (v36) + a real paper ZerodhaAdapter (paper GTT/holdings stores) + a real
store-wired CncGttPlacer; only fund_manager/kill_switch/notifier/bus are recorded.
A GTT "fires" by flipping the paper record's status to 'triggered'; a holding is
injected via seed_paper_holding.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from orders.cnc_gtt import CncGttPlacer
from orders.cnc_gtt_monitor import CncGttMonitor
from tests.unit.test_order_reconciler import _make_store

_LOG = logging.getLogger("test_cnc_gtt_monitor")
_NOW = "2026-06-25T10:00:00+05:30"


def _seed_trade(store, trade_id, *, symbol="RAMCOIND", status="OPEN", entry=337.0, qty=1):
    sig = f"sig_{trade_id}"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sig, symbol, "SC", "strat", _NOW, _NOW, _NOW, "TRADED", f"fp_{trade_id}", "2026-06-25"),
        )
        cur.execute(
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,qty_planned,"
            "qty_filled,entry_target_price,entry_actual_price,sl_initial,tgt_initial,"
            "margin_reserved,risk_amount,created_at,status,order_protocol,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, sig, symbol, "LONG", "strat", qty, qty, entry, entry, 334.0, 343.0,
             100.0, 5.0, _NOW, status, "LIMIT_TRIPLE", _NOW),
        )


class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, *, severity, title, body, source_module):
        self.sent.append((severity, title, source_module))

    def sev(self, s):
        return [x for x in self.sent if x[0] == s]


class _KS:
    def __init__(self):
        self.kills = []

    def soft_kill(self, *, reason, triggered_by):
        self.kills.append(reason)


class _FM:
    def __init__(self):
        self.releases = []

    def release_used(self, **kw):
        self.releases.append(kw)
        return SimpleNamespace(pnl_delta=12.5)


class _Bus:
    def __init__(self):
        self.published = []

    def publish(self, ev):
        self.published.append(ev)


def _adapter():
    from tests.unit.test_zerodha_adapter import _make_adapter
    a, *_ = _make_adapter(
        paper=True,
        quote_provider=lambda s: {x: SimpleNamespace(last_price=337.0) for x in s},
    )
    a._delivery_enabled = True
    return a


def _setup(tmp_path, *, in_hours=True):
    store = _make_store(tmp_path)
    adapter = _adapter()
    placer = CncGttPlacer(adapter, gtt_sl_limit_offset_pct=0.03,
                          gtt_tgt_limit_offset_pct=0.002, delivery_enabled=True,
                          logger=_LOG, quote_fn=adapter.get_quote,
                          tick_fn=lambda _s: 0.05, store=store)
    fm, ks, notifier, bus = _FM(), _KS(), _Notifier(), _Bus()
    mon = CncGttMonitor(store=store, adapter=adapter, placer=placer, fund_manager=fm,
                        kill_switch=ks, notifier=notifier, bus=bus, logger=_LOG,
                        mode="PAPER", market_hours_fn=lambda: in_hours)
    return SimpleNamespace(store=store, adapter=adapter, placer=placer, fm=fm,
                           ks=ks, notifier=notifier, bus=bus, mon=mon)


def _place(env, trade_id="t1", symbol="RAMCOIND", qty=1):
    return env.placer.place_for_fill(symbol=symbol, exit_side="SELL", qty=qty,
                                     sl_price=334.0, tgt_price=343.0, trade_id=trade_id)


def _trigger(env, gid):
    env.adapter._paper_gtts[str(gid)]["status"] = "triggered"


def test_healthy_touches_verified_no_action(tmp_path: Path):
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    env.adapter.seed_paper_holding("RAMCOIND", 1)
    before = env.store.get_active_gtt_for_trade("t1")["last_verified_at"]
    out = env.mon.reconcile()
    assert out == ["healthy:RAMCOIND"]
    row = env.store.get_active_gtt_for_trade("t1")
    assert row["status"] == "ACTIVE" and row["gtt_id"] == int(res.gtt_id)
    assert row["last_verified_at"] >= before
    assert not env.fm.releases and not env.ks.kills


def test_gtt_exit_finalises_trade_releases_capital_system_owned(tmp_path: Path):
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _trigger(env, res.gtt_id)            # GTT fired
    # no holding seeded -> held == 0 (flat)
    out = env.mon.reconcile()
    assert out == ["gtt_exit:RAMCOIND"]
    tr = env.store.fetch_one("SELECT * FROM trades WHERE trade_id='t1'")
    assert tr["status"] == "CLOSED" and tr["exit_reason"] == "GTT_EXIT"
    assert tr["net_pnl"] == 12.5 and tr["exit_price"] > 0
    assert env.store.get_active_gtt_for_trade("t1") is None       # row CLEANED
    assert len(env.fm.releases) == 1                              # capital released once
    assert env.fm.releases[0]["intent"] == "DELIVERY"
    # SYSTEM-OWNED close (not human/external)
    assert env.bus.published and env.bus.published[0].source_module == "cnc_gtt_monitor"


def test_gtt_exit_idempotent_under_double_observe(tmp_path: Path):
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _trigger(env, res.gtt_id)
    env.mon.reconcile()
    assert len(env.fm.releases) == 1
    # simulate a duplicate observer: row flipped back to ACTIVE while trade is CLOSED
    env.store.set_gtt_state_status(int(res.gtt_id), "ACTIVE", _NOW)
    _trigger(env, res.gtt_id)
    out = env.mon.reconcile()
    assert out == ["gtt_exit_dup:RAMCOIND"]
    assert len(env.fm.releases) == 1            # NO double release (3.5e)
    assert env.store.get_active_gtt_for_trade("t1") is None  # re-cleaned


def test_f6_triggered_but_holding_remains_reprotects_not_closed(tmp_path: Path):
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _trigger(env, res.gtt_id)
    env.adapter.seed_paper_holding("RAMCOIND", 1)   # still holding -> F6
    out = env.mon.reconcile()
    assert out == ["recreated:RAMCOIND"]
    tr = env.store.fetch_one("SELECT status FROM trades WHERE trade_id='t1'")
    assert tr["status"] == "OPEN"                   # NOT closed
    assert env.store.get_active_gtt_for_trade("t1") is not None  # re-protected (new ACTIVE)
    assert env.notifier.sev("CRITICAL")             # F6 is CRITICAL
    assert not env.fm.releases


def test_missing_gtt_in_hours_auto_recreates(tmp_path: Path):
    env = _setup(tmp_path, in_hours=True)
    _seed_trade(env.store, "t1")
    res = _place(env)
    env.adapter.delete_gtt(res.gtt_id)              # GTT vanished at broker
    env.adapter.seed_paper_holding("RAMCOIND", 1)   # holding intact, qty match
    out = env.mon.reconcile()
    assert out == ["recreated:RAMCOIND"]
    rows = env.store.fetch_all("SELECT status FROM gtt_state WHERE trade_id='t1'")
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["ACTIVE", "EXPIRED"]        # old EXPIRED, new ACTIVE
    assert len(env.adapter.get_gtts()) == 1         # a fresh broker GTT exists


def test_missing_gtt_preopen_queued_then_drained_in_hours(tmp_path: Path):
    env = _setup(tmp_path, in_hours=False)
    _seed_trade(env.store, "t1")
    res = _place(env)
    env.adapter.delete_gtt(res.gtt_id)
    env.adapter.seed_paper_holding("RAMCOIND", 1)
    out = env.mon.reconcile(in_hours=False)
    assert out == ["queued_preopen:RAMCOIND"]
    assert len(env.adapter.get_gtts()) == 0          # NOT recreated yet (pre-open)
    assert env.notifier.sev("WARNING")
    # first in-hours cycle drains the queue immediately
    out2 = env.mon.reconcile(in_hours=True)
    assert "recreated:RAMCOIND" in out2
    assert len(env.adapter.get_gtts()) == 1


def test_qty_mismatch_critical_once_no_recreate_no_softkill(tmp_path: Path):
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    env.adapter.seed_paper_holding("RAMCOIND", 3)    # held 3 != protected 1
    out = env.mon.reconcile()
    assert out == ["qty_mismatch:RAMCOIND"]
    row = env.store.fetch_one("SELECT * FROM gtt_state WHERE gtt_id=?", (int(res.gtt_id),))
    assert row["needs_review"] == 1
    assert len(env.adapter.get_gtts()) == 0          # wrong-qty GTT cancelled
    assert not env.ks.kills                          # NO soft-kill
    assert len(env.notifier.sev("CRITICAL")) == 1
    # Y2: a second cycle does NOT re-alert (needs_review latched)
    out2 = env.mon.reconcile()
    assert out2 == ["needs_review:RAMCOIND"]
    assert len(env.notifier.sev("CRITICAL")) == 1


def test_more_than_one_active_gtt_soft_kills(tmp_path: Path):
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)                                # one ACTIVE row
    # inject a SECOND ACTIVE row for the same trade
    env.store.insert_gtt_state(gtt_id=555, trade_id="t1", symbol="RAMCOIND",
                               exit_side="SELL", qty=1, sl_trigger=334.0, sl_limit=324.0,
                               tgt_trigger=343.0, tgt_limit=342.0, created_at=_NOW)
    out = env.mon.reconcile()
    assert any(a.startswith("soft_kill") for a in out)
    assert env.ks.kills and "ownership ambiguity" in env.ks.kills[0]


def test_orphan_leaked_system_gtt_forensic_then_deleted(tmp_path: Path):
    # System GTT we believe is finished (row CLEANED) but still ACTIVE at the broker.
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    env.store.set_gtt_state_status(int(res.gtt_id), "CLEANED", _NOW)   # we think it's done
    assert len(env.adapter.get_gtts()) == 1                            # ... but broker still has it
    out = env.mon.reconcile()
    assert any(a.startswith("orphan_deleted") for a in out)
    assert len(env.adapter.get_gtts()) == 0                            # leaked GTT deleted
    assert env.notifier.sev("WARNING")


def test_unknown_human_gtt_left_alone(tmp_path: Path):
    env = _setup(tmp_path)
    # a GTT with no gtt_state row at all -> human/external -> NEVER deleted
    env.adapter._paper_gtts["88888"] = env.adapter._paper_gtt_record(
        "88888", "INFY", [100.0, 110.0], 105.0, [], status="active")
    out = env.mon.reconcile()
    assert "unknown_gtt:88888" in out
    assert "88888" in env.adapter._paper_gtts                          # NOT deleted


def test_50_cap_warning(tmp_path: Path):
    env = _setup(tmp_path)
    for i in range(45):
        gid = str(10_000 + i)
        env.adapter._paper_gtts[gid] = env.adapter._paper_gtt_record(
            gid, "SYM", [1.0, 2.0], 1.5, [], status="active")
    out = env.mon.reconcile()
    assert any(a.startswith("gtt_cap:") for a in out)
    assert env.notifier.sev("WARNING")


def test_alerts_carry_cnc_gtt_monitor_source_and_soft_kill_is_critical(tmp_path: Path):
    # STEP 6: every alert is source_module="cnc_gtt_monitor"; soft-kill is CRITICAL
    # (which writes a sentinel -> email fallback via the notifier).
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    _place(env)
    env.store.insert_gtt_state(gtt_id=556, trade_id="t1", symbol="RAMCOIND",
                               exit_side="SELL", qty=1, sl_trigger=334.0, sl_limit=324.0,
                               tgt_trigger=343.0, tgt_limit=342.0, created_at=_NOW)
    env.mon.reconcile()                              # >1 ACTIVE -> soft-kill
    assert env.ks.kills and env.notifier.sev("CRITICAL")
    assert env.notifier.sent and all(s[2] == "cnc_gtt_monitor" for s in env.notifier.sent)


def test_broker_unavailable_defers_no_crash(tmp_path: Path):
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    _place(env)

    def _boom():
        raise RuntimeError("kite down")

    env.adapter.get_gtts = _boom                     # Y4
    out = env.mon.reconcile()
    assert out == ["deferred:broker_unavailable"]
    assert env.notifier.sev("WARNING")
    # nothing acted on: trade still OPEN, GTT row still ACTIVE
    assert env.store.fetch_one("SELECT status FROM trades WHERE trade_id='t1'")["status"] == "OPEN"
    assert env.store.get_active_gtt_for_trade("t1") is not None
