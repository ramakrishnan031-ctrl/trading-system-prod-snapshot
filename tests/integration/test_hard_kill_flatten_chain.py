"""
tests/integration/test_hard_kill_flatten_chain.py — Wave 2 Group-A VALIDATION.

End-to-end integration test proving the three Group-A HARD_KILL fixes COMPOSE on one
real flatten (what the isolated unit tests do not). It drives the REAL
`KillSwitch._exit_all_trades_indestructible` — first pass + orphan sweep + retry loop —
and asserts the six required properties together:

  1. (977) the TRACKED trade's resting SL + TGT are cancelled at the broker BEFORE its
     flatten order is placed.
  2. (H-4) after an ambiguous partial first exit (BrokerTimeout after transmission, 100→40),
     the retry fires only the RESIDUAL SELL 40, NOT the stale full SELL 100.
  3. (H-5) the CNC orphan sweep uses the product-mapped intent (DELIVERY), not INTRADAY.
  4. No naked reverse on the tracked trade: the retry qty == residual (no oversell).
  5. No naked MIS short from the CNC sweep: NO INTRADAY order is placed for the CNC orphan.
  6. Delivery-disabled escalation: the CNC (DELIVERY) exit is REFUSED → surfaced (queued to
     failed_trades) → escalated via _alert_exit_failed → not silently swallowed; no MIS substitute.

All broker positions are correctly SIGNED, so the test is NOT gated by H-12 (a paper drill
of the reverse-flatten DIRECTION still waits for H-12, Wave 3). A mocked clock (advanced by
the patched time.sleep) + a shrunk retry deadline make the escalation deterministic.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from broker.zerodha_adapter import CancelResult, PlacedOrder
from capital.kill_switch import KillSwitch
from core.events import EventBus
from core.exceptions import BrokerTimeoutError
from core.state_store import StateStore

_IST = timezone(timedelta(hours=5, minutes=30))
_NOW = "2026-07-05T10:00:00+05:30"


class _GroupAAdapter:
    """Broker-boundary simulator composing all three Group-A conditions on ONE flatten:
    a TRACKED long (RELIANCE) whose first exit is an A-2 timeout after a partial fill
    (100 → residual 40), and an untracked CNC orphan (TCS) whose DELIVERY exit is refused
    (delivery_enabled=false — SLICE2.5-P1 lock). Records place/cancel in ORDER."""

    def __init__(self):
        self.net = {"RELIANCE": 100, "TCS": 10}       # signed net qty
        self.product = {"RELIANCE": "MIS", "TCS": "CNC"}
        self.events = []      # ordered [("cancel", oid) | ("place", rec)]
        self.placed = []
        self.cancelled = []
        self._reliance_first = True

    def get_positions(self):
        return [SimpleNamespace(symbol=s, qty=self.net[s], product=self.product[s])
                for s in ("RELIANCE", "TCS") if self.net[s] != 0]

    def get_quote_raw(self, instruments):
        return {k: {"last_price": 2450.0} for k in instruments}

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        self.events.append(("cancel", oid))
        return CancelResult(broker_order_id=oid, success=True, reason="")

    def place_order(self, symbol, side, qty, order_type, price, intent, tag=None, **kw):
        rec = {"symbol": symbol, "side": side, "qty": qty, "intent": intent, "tag": tag}
        self.placed.append(rec)
        self.events.append(("place", rec))
        if intent == "DELIVERY":
            # SLICE2.5-P1 lock: a CNC order is refused while delivery_enabled=false.
            raise RuntimeError("CNC order refused — delivery_enabled=false")
        if symbol == "RELIANCE" and self._reliance_first:
            # A-2: transmitted, partially filled 60, then the ack timed out → residual 40.
            self._reliance_first = False
            self.net["RELIANCE"] = 40
            raise BrokerTimeoutError("exit ack timed out after transmission")
        if symbol == "RELIANCE":
            self.net["RELIANCE"] = 0   # retry SELL 40 fills → flat
        return PlacedOrder(
            internal_order_id=f"int_{len(self.placed)}", broker_order_id=f"brk_{len(self.placed)}",
            symbol=symbol, side=side, qty=qty, price=price, order_type=order_type,
            product="MIS", status="SUBMITTED", ts=datetime.now(),
        )


def _seed_tracked_long_with_exits(store):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, triggered_at, "
            "received_at, expires_at, status, fingerprint, fingerprint_date) VALUES "
            "('sig_ga', 'RELIANCE', 'gap_go_long', 'gap_go_long', ?, ?, ?, 'PROCESSING', 'fp_ga', ?)",
            (_NOW, _NOW, _NOW, _NOW[:10]),
        )
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, qty_planned, "
            "qty_filled, entry_target_price, entry_actual_price, sl_initial, tgt_initial, "
            "margin_reserved, risk_amount, created_at, entry_time, order_protocol, status, "
            "updated_at) VALUES ('T_GA', 'sig_ga', 'RELIANCE', 'LONG', 'gap_go_long', 100, 100, "
            "2500.0, 2500.0, 2450.0, 2600.0, 50000.0, 5000.0, ?, ?, 'LIMIT_TRIPLE', 'OPEN', ?)",
            (_NOW, _NOW, _NOW),
        )
        for oid, leg, status in (("ENTRY_GA", "ENTRY", "COMPLETE"),
                                 ("SL_R", "SL", "TRIGGER_PENDING"),
                                 ("TGT_R", "TGT", "OPEN")):
            cur.execute(
                "INSERT INTO orders (order_id, trade_id, leg, transaction_type, order_type, "
                "product, variety, qty_requested, status, placed_at, updated_at) VALUES "
                "(?, 'T_GA', ?, 'BUY', 'LIMIT', 'MIS', 'regular', 100, ?, ?, ?)",
                (oid, leg, status, _NOW, _NOW),
            )


def test_hard_kill_flatten_chain_composes(tmp_path, monkeypatch):
    # Deterministic time: now_ist reads a mocked clock; time.sleep advances it; a short
    # retry deadline (0.02h = 72s) so the CNC refusal escalates after a few retries.
    clock = [datetime(2026, 7, 5, 10, 0, 0, tzinfo=_IST)]
    monkeypatch.setattr("capital.kill_switch.now_ist", lambda: clock[0])
    monkeypatch.setattr("capital.kill_switch._HARD_KILL_MAX_RETRY_HOURS", 0.02)
    monkeypatch.setattr(time, "sleep",
                        lambda d: clock.__setitem__(0, clock[0] + timedelta(seconds=d)))

    store = StateStore(str(tmp_path / "ga.db"))
    _seed_tracked_long_with_exits(store)
    adapter = _GroupAAdapter()
    ks = KillSwitch(state_store=store, bus=EventBus(),
                    logger=logging.getLogger("test_ga"), adapter=adapter,
                    enable_auto_trip=False)
    alerts = []
    ks._alert_exit_failed = lambda failed: alerts.append(list(failed))   # spy the escalation

    report = ks._exit_all_trades_indestructible()
    store.close()

    reliance = [p for p in adapter.placed if p["symbol"] == "RELIANCE"]
    tcs = [p for p in adapter.placed if p["symbol"] == "TCS"]
    place_idxs = [i for i, (k, _) in enumerate(adapter.events) if k == "place"]
    cancel_idxs = [i for i, (k, _) in enumerate(adapter.events) if k == "cancel"]

    # (1) 977: resting SL + TGT cancelled at the broker BEFORE the tracked flatten
    assert set(adapter.cancelled) == {"SL_R", "TGT_R"}, "977: resting legs not cancelled"
    assert cancel_idxs and place_idxs and all(ci < place_idxs[0] for ci in cancel_idxs), \
        "977: the SL/TGT cancels must precede the first flatten placement"

    # (2)+(4) H-4: the tracked retry fires the RESIDUAL SELL 40, not the stale SELL 100
    assert [p["side"] for p in reliance] == ["SELL", "SELL"]
    assert [p["qty"] for p in reliance] == [100, 40], (
        "H-4: the retry re-fired the stale full qty (oversell) instead of the residual 40."
    )

    # (3)+(5) H-5: the CNC orphan is swept/retried with DELIVERY intent — never INTRADAY (no naked MIS short)
    assert tcs, "the CNC orphan must be swept"
    assert all(p["intent"] == "DELIVERY" for p in tcs), \
        "H-5: the CNC orphan was swept as INTRADAY → naked MIS short"
    assert not any(p["symbol"] == "TCS" and p["intent"] == "INTRADAY" for p in adapter.placed)

    # (6) delivery-disabled: the CNC exit is refused → surfaced → escalated via _alert_exit_failed
    assert len(alerts) == 1, "the refused CNC orphan must be escalated (not silently swallowed)"
    assert any(t[0] == "sweep" and t[1] == "TCS" for t in alerts[0]), \
        "the escalation must carry the un-flattened CNC orphan"
    assert report.failed == ["sweep"], "the CNC orphan remains reported as un-flattened"
