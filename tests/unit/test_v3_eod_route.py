"""
tests/unit/test_v3_eod_route.py — V3 Step 10b · T0a structural EOD routing.

Proves an "eod" scanner is captured to the watchlist ONLY: it bypasses the intraday
entry-window gate (a post-close alert is legitimate) and NEVER enters the intraday
signal_queue — structurally incapable of the order path. The intraday path is unchanged
(an intraday scanner is still entry-window-gated). Fail-safe when the watchlist is off.
"""
from __future__ import annotations

import queue
import tempfile
from pathlib import Path
from types import SimpleNamespace

from core.state_store import StateStore
from signals.webhook_receiver import WebhookReceiver


class _MockMW:
    def __init__(self, entry_allowed): self._a = entry_allowed
    def is_entry_allowed(self, now): return self._a


class _NullLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class _FakeCapture:
    def __init__(self): self.calls = []
    def submit(self, *, scanner_name, symbol, triggered_at):
        self.calls.append((scanner_name, symbol)); return True


def _receiver(*, entry_allowed=False, capture=True):
    scanners = {
        "pb01_breakout_retest": SimpleNamespace(
            strategy="pb01_breakout_retest", chartink_url="https://x", scanner_type="eod"),
        "gap_go_long": SimpleNamespace(
            strategy="gap_go_long", chartink_url="https://x", scanner_type="intraday"),
    }
    cfg = SimpleNamespace()
    cfg.system = SimpleNamespace(
        signal_queue=SimpleNamespace(capacity=20, backpressure_pct=0.8, expiry_sec=60),
        excluded_symbols=[])
    cfg.scan_webhook_map = SimpleNamespace(scanners=scanners)
    sq = queue.Queue(maxsize=20)
    store = StateStore(Path(tempfile.mkdtemp()) / "eod.db")
    cap = _FakeCapture() if capture else None
    rcv = WebhookReceiver(sq, store, cfg, _MockMW(entry_allowed), None, _NullLogger(),
                          secret_token=None, eod_capture=cap)
    return rcv, sq, cap


def test_eod_route_captures_and_bypasses_entry_window():
    rcv, sq, cap = _receiver(entry_allowed=False)   # post-close (entry NOT allowed)
    client = rcv.app.test_client()
    resp = client.post("/webhook/pb01_breakout_retest",
                       json={"stocks": "ABB,TCS", "trigger_prices": "100,200",
                             "triggered_at": "2026-07-10 15:40:00"})
    assert resp.status_code == 200                  # NOT 403 — EOD bypasses the entry-window gate
    data = resp.get_json()
    assert data["accepted"] == 2 and data["captured"] == 2
    assert {s for _, s in cap.calls} == {"ABB", "TCS"}   # routed to the capture worker
    assert sq.qsize() == 0                          # G-NO-ORDER: never enqueued to the intraday queue


def test_intraday_scanner_still_entry_window_gated():
    rcv, sq, cap = _receiver(entry_allowed=False)   # post-close
    client = rcv.app.test_client()
    resp = client.post("/webhook/gap_go_long",
                       json={"stocks": "RELIANCE", "trigger_prices": "2500",
                             "triggered_at": "2026-07-10 15:40:00", "scan_name": "gap_go_long"})
    assert resp.status_code == 403                  # intraday path unchanged (byte-identical)
    assert len(cap.calls) == 0                      # intraday NEVER touches the capture worker


def test_eod_route_failsafe_when_watchlist_disabled():
    rcv, sq, cap = _receiver(entry_allowed=False, capture=False)   # no capture worker
    client = rcv.app.test_client()
    resp = client.post("/webhook/pb01_breakout_retest",
                       json={"stocks": "ABB", "trigger_prices": "100",
                             "triggered_at": "2026-07-10 15:40:00"})
    assert resp.status_code == 200
    assert resp.get_json()["detail"] == "watchlist disabled"   # fail-safe miss, logged
    assert sq.qsize() == 0


def test_set_eod_capture_late_binds_the_worker():
    # main.py builds the receiver BEFORE the fetch closure, then late-binds the capture
    # worker via set_eod_capture (same pattern as signal_processor.set_v3_chain). Prove
    # the wiring seam: None → fail-safe miss; after set → routed to the worker.
    rcv, sq, _ = _receiver(entry_allowed=False, capture=False)   # starts with no worker
    client = rcv.app.test_client()
    r1 = client.post("/webhook/pb01_breakout_retest",
                     json={"stocks": "ABB", "trigger_prices": "100",
                           "triggered_at": "2026-07-10 15:40:00"})
    assert r1.get_json()["detail"] == "watchlist disabled"       # miss before wiring
    cap = _FakeCapture()
    rcv.set_eod_capture(cap)                                     # ← the wiring seam
    r2 = client.post("/webhook/pb01_breakout_retest",
                     json={"stocks": "ABB", "trigger_prices": "100",
                           "triggered_at": "2026-07-10 15:40:00"})
    assert r2.get_json()["captured"] == 1 and cap.calls == [("pb01_breakout_retest", "ABB")]
    assert sq.qsize() == 0                                       # still never the intraday queue
    rcv._store.close()


def test_eod_route_rejects_malformed_payload():
    rcv, sq, cap = _receiver(entry_allowed=True)
    client = rcv.app.test_client()
    resp = client.post("/webhook/pb01_breakout_retest",
                       json={"stocks": "ABB"})   # missing trigger_prices / triggered_at
    assert resp.status_code == 400
    assert len(cap.calls) == 0


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    for name in sorted(dir(mod)):
        if name.startswith("test_"):
            getattr(mod, name)()
            print(f"  OK {name}")
    print("\nT0a EOD routing: all checks passed.")
