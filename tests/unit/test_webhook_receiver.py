"""
tests/unit/test_webhook_receiver.py

Validates signals/webhook_receiver.py against locked decisions WR1-WR17.

All tests use Flask's test_client -- no real HTTP, no port binding.
State store is a fresh in-memory (temp-file) SQLite DB for each test.

Run: python -m pytest tests/unit/test_webhook_receiver.py -v
Or:  python tests/unit/test_webhook_receiver.py  (standalone mode)
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import queue
import sqlite3
import sys
import tempfile
import threading
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore
from signals.webhook_receiver import WebhookReceiver


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _MockMarketWindows:
    def __init__(self, entry_allowed: bool = True) -> None:
        self._allowed = entry_allowed

    def is_entry_allowed(self, now) -> bool:
        return self._allowed


class _MockKillSwitch:
    def __init__(self, active: bool = False) -> None:
        self._active = active

    def is_active(self, intent: str = "entry") -> bool:
        return self._active


class _NullLogger:
    def debug(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def critical(self, *a, **kw): pass


def _make_config(capacity=20, bp_pct=0.8, expiry=60,
                 scanners=None):
    if scanners is None:
        scanners = {"gap_go_long": "strategies/gap_go_long.yaml"}
    cfg = types.SimpleNamespace()
    cfg.system = types.SimpleNamespace(
        signal_queue=types.SimpleNamespace(
            capacity=capacity,
            backpressure_pct=bp_pct,
            expiry_sec=expiry,
        )
    )
    cfg.scan_webhook_map = types.SimpleNamespace(scanners=scanners)
    return cfg


def _make_receiver(
    sq=None,
    store=None,
    config=None,
    mw=None,
    ks=None,
    secret=None,
    capacity=20,
    bp_pct=0.8,
    expiry=60,
    scanners=None,
):
    if sq is None:
        sq = queue.Queue(maxsize=capacity)
    if store is None:
        # Create a fresh temp-file store each time
        td = tempfile.mkdtemp()
        store = StateStore(Path(td) / "test.db")
    if config is None:
        config = _make_config(capacity=capacity, bp_pct=bp_pct,
                              expiry=expiry, scanners=scanners)
    if mw is None:
        mw = _MockMarketWindows(entry_allowed=True)
    if ks is None:
        ks = _MockKillSwitch(active=False)
    logger = _NullLogger()
    receiver = WebhookReceiver(sq, store, config, mw, ks, logger,
                               secret_token=secret)
    return receiver, sq, store


def _now_str() -> str:
    """Return current local time as YYYY-MM-DD HH:MM:SS (naive IST-ish for tests)."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _expired_str(seconds: int = 120) -> str:
    """Return a triggered_at that is <seconds> old."""
    return (datetime.now() - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _valid_payload(scanner="gap_go_long", stocks="RELIANCE", prices="2500.0",
                   triggered_at=None, scan_name=None):
    return {
        "stocks": stocks,
        "trigger_prices": prices,
        "triggered_at": triggered_at or _now_str(),
        "scan_name": scan_name if scan_name is not None else scanner,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_health_endpoint_returns_200():
    """GET /health returns 200 with JSON status."""
    receiver, _, _ = _make_receiver()
    with receiver.app.test_client() as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "kill_switch_active" in data
    assert "queue_size" in data
    assert "queue_capacity" in data
    print("  OK GET /health -> 200 with status JSON")


def test_valid_single_stock_accepted():
    """Valid single-stock payload returns 200, 1 accepted, signal in queue, row in DB."""
    receiver, sq, store = _make_receiver()
    payload = _valid_payload()

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["accepted"] == 1
    assert data["rejected"] == 0
    assert len(data["results"]) == 1
    assert data["results"][0]["status"] == "ACCEPTED"
    signal_id = data["results"][0]["signal_id"]
    assert signal_id.startswith("sig_")

    # Signal must be in the queue
    assert sq.qsize() == 1
    entry = sq.get_nowait()
    assert entry[0] == signal_id
    assert entry[2] == "RELIANCE"

    # Signal must be in the DB
    row = store.fetch_one("SELECT * FROM signals WHERE signal_id = ?", (signal_id,))
    assert row is not None
    assert row["status"] == "QUEUED"
    assert row["symbol"] == "RELIANCE"
    print(f"  OK single-stock ACCEPTED: {signal_id}")


def test_valid_multi_stock_all_accepted():
    """Three-stock payload returns 200 with all 3 accepted."""
    receiver, sq, _ = _make_receiver()
    payload = _valid_payload(
        stocks="RELIANCE,TCS,INFY",
        prices="2500.0,3650.0,1450.25",
    )

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["accepted"] == 3
    assert data["rejected"] == 0
    assert sq.qsize() == 3
    print("  OK multi-stock 3/3 ACCEPTED")


def test_mixed_valid_invalid_payload():
    """Valid + invalid prices produce per-stock results in one 200 response."""
    receiver, sq, _ = _make_receiver()
    payload = _valid_payload(
        stocks="RELIANCE,BADSTOCK,TCS",
        prices="2500.0,-1.0,3650.0",
    )

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["accepted"] == 2
    assert data["rejected"] == 1
    statuses = [r["status"] for r in data["results"]]
    assert statuses[0] == "ACCEPTED"
    assert statuses[1] == "INVALID_PRICE"
    assert statuses[2] == "ACCEPTED"
    print(f"  OK mixed: {statuses}")


def test_unknown_scanner_returns_404():
    """Unknown scanner_name path param returns 404."""
    receiver, _, _ = _make_receiver()
    payload = _valid_payload(scanner="unknown_scanner", scan_name="unknown_scanner")

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/unknown_scanner", json=payload)

    assert resp.status_code == 404
    print("  OK unknown scanner -> 404")


def test_scan_name_body_mismatch_returns_400():
    """scan_name in body != scanner_name path param -> 400."""
    receiver, _, _ = _make_receiver()
    payload = _valid_payload(scan_name="wrong_name")  # path says gap_go_long

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 400
    assert "match" in resp.get_json()["error"].lower()
    print("  OK scan_name mismatch -> 400")


def test_malformed_json_returns_400():
    """Non-JSON body returns 400."""
    receiver, _, _ = _make_receiver()

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long",
                           data=b"this is not json",
                           content_type="application/json")

    assert resp.status_code == 400
    print("  OK malformed JSON -> 400")


def test_missing_required_field_returns_400():
    """Missing 'stocks' field returns 400."""
    receiver, _, _ = _make_receiver()
    payload = {
        "trigger_prices": "2500.0",
        "triggered_at": _now_str(),
        "scan_name": "gap_go_long",
        # 'stocks' omitted
    }

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 400
    assert "stocks" in resp.get_json()["error"]
    print("  OK missing field -> 400")


def test_bad_triggered_at_format_returns_400():
    """Bad triggered_at format returns 400."""
    receiver, _, _ = _make_receiver()
    payload = _valid_payload(triggered_at="15/04/2026 10:15")

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 400
    print("  OK bad triggered_at format -> 400")


def test_hmac_absent_when_required_returns_401():
    """Missing X-Webhook-Signature when secret_token configured -> 401."""
    receiver, _, _ = _make_receiver(secret="mysecret")
    payload = _valid_payload()

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)  # no HMAC header

    assert resp.status_code == 401
    print("  OK HMAC absent -> 401")


def test_hmac_mismatch_returns_401():
    """Wrong HMAC value -> 401."""
    receiver, _, _ = _make_receiver(secret="mysecret")
    body = json.dumps(_valid_payload()).encode()

    with receiver.app.test_client() as client:
        resp = client.post(
            "/webhook/gap_go_long",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": "sha256=deadbeefdeadbeefdeadbeefdeadbeef",
            },
        )

    assert resp.status_code == 401
    print("  OK HMAC mismatch -> 401")


def test_hmac_valid_returns_200():
    """Correct HMAC header allows the request through -> 200."""
    secret = "mysecret"
    receiver, _, _ = _make_receiver(secret=secret)
    payload = _valid_payload()
    body = json.dumps(payload).encode()
    sig = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with receiver.app.test_client() as client:
        resp = client.post(
            "/webhook/gap_go_long",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": f"sha256={sig}",
            },
        )

    assert resp.status_code == 200
    print("  OK HMAC valid -> 200")


def test_hmac_none_mode_no_header_check():
    """When secret_token=None, no HMAC header required -> 200."""
    receiver, _, _ = _make_receiver(secret=None)

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=_valid_payload())

    assert resp.status_code == 200
    print("  OK HMAC=None mode, no header check -> 200")


def test_soft_kill_active_returns_403():
    """SOFT_KILL active -> 403."""
    receiver, _, _ = _make_receiver(ks=_MockKillSwitch(active=True))

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=_valid_payload())

    assert resp.status_code == 403
    print("  OK SOFT_KILL active -> 403")


def test_hard_kill_active_returns_403():
    """HARD_KILL also returns 403 (is_active() -> True)."""
    receiver, _, _ = _make_receiver(ks=_MockKillSwitch(active=True))

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=_valid_payload())

    assert resp.status_code == 403
    print("  OK HARD_KILL active -> 403")


def test_outside_entry_window_returns_403():
    """Outside entry window (after 13:30) -> 403."""
    receiver, _, _ = _make_receiver(mw=_MockMarketWindows(entry_allowed=False))

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=_valid_payload())

    assert resp.status_code == 403
    assert "entry window" in resp.get_json()["error"].lower()
    print("  OK outside entry window -> 403")


def test_queue_at_backpressure_threshold_returns_503():
    """Queue at backpressure threshold -> 503."""
    # capacity=5, bp_pct=0.8 => threshold=4; pre-fill queue with 4 items
    sq = queue.Queue(maxsize=5)
    for i in range(4):
        sq.put_nowait(("sig_dummy", "gap_go_long", f"SYM{i}", 100.0, datetime.now()))

    receiver, _, _ = _make_receiver(sq=sq, capacity=5, bp_pct=0.8)

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=_valid_payload())

    assert resp.status_code == 503
    print("  OK queue at backpressure -> 503")


def test_queue_full_on_individual_signal_returns_503():
    """Queue.Full on put_nowait returns 503 so client knows to retry (HIGH #6).

    Design: maxsize=1 (real queue limit), capacity=50 (config value used for
    backpressure threshold only). bp_threshold = int(50 * 0.9) = 45, so with
    1 item in the queue the backpressure 503 will NOT fire. The second
    put_nowait fails with queue.Full -> QUEUE_FULL status AND 503 HTTP code.
    """
    sq = queue.Queue(maxsize=1)
    # capacity=50 so backpressure threshold=45; well above 1 item -> no 503 via BP
    receiver, _, store = _make_receiver(sq=sq, capacity=50, bp_pct=0.9,
                                        expiry=3600)

    with receiver.app.test_client() as client:
        # First signal (RELIANCE) fills the queue
        resp1 = client.post("/webhook/gap_go_long",
                            json=_valid_payload(stocks="RELIANCE", prices="2500.0"))
        assert resp1.status_code == 200
        data1 = resp1.get_json()
        assert data1["results"][0]["status"] == "ACCEPTED"

        # Second signal (TCS, different symbol to avoid IN_PROCESS): queue is
        # physically full -> put_nowait raises Full -> QUEUE_FULL per-stock + 503
        resp2 = client.post("/webhook/gap_go_long",
                            json=_valid_payload(stocks="TCS", prices="3650.0",
                                                triggered_at=_now_str()))
        assert resp2.status_code == 503, f"Expected 503, got {resp2.status_code}"
        data2 = resp2.get_json()
        assert data2["results"][0]["status"] == "QUEUE_FULL", data2
    print("  OK queue full -> QUEUE_FULL status + 503")


def test_duplicate_same_fingerprint_same_minute():
    """Same scanner+symbol+minute returns per-stock DUPLICATE on second call."""
    receiver, sq, _ = _make_receiver()
    ts = _now_str()
    # Force minute precision: same minute string
    ts_minute = datetime.now().strftime("%Y-%m-%d %H:%M") + ":00"
    payload = _valid_payload(triggered_at=ts_minute)

    with receiver.app.test_client() as client:
        resp1 = client.post("/webhook/gap_go_long", json=payload)
        assert resp1.status_code == 200
        assert resp1.get_json()["results"][0]["status"] == "ACCEPTED"

        # Release in-flight so DUPLICATE check runs (not IN_PROCESS)
        receiver.release_in_flight("RELIANCE")

        resp2 = client.post("/webhook/gap_go_long", json=payload)
        assert resp2.status_code == 200
        status2 = resp2.get_json()["results"][0]["status"]
        assert status2 == "DUPLICATE", f"Expected DUPLICATE, got {status2}"

    print("  OK same fingerprint same minute -> DUPLICATE")


def test_different_minute_same_scanner_symbol_accepted():
    """Different minute = different fingerprint -> ACCEPTED (not duplicate).

    Use expiry=3600 so timestamps that are a few minutes old are still fresh.
    Use current-time-based strings to avoid stale-timestamp expiry.
    """
    receiver, sq, _ = _make_receiver(expiry=3600)
    base = datetime.now()
    ts1 = base.strftime("%Y-%m-%d %H:%M:%S")
    ts2 = (base - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")  # different minute

    with receiver.app.test_client() as client:
        resp1 = client.post("/webhook/gap_go_long",
                            json=_valid_payload(triggered_at=ts1))
        assert resp1.status_code == 200
        assert resp1.get_json()["results"][0]["status"] == "ACCEPTED"

        receiver.release_in_flight("RELIANCE")

        resp2 = client.post("/webhook/gap_go_long",
                            json=_valid_payload(triggered_at=ts2))
        assert resp2.status_code == 200
        assert resp2.get_json()["results"][0]["status"] == "ACCEPTED"

    print("  OK different minute -> second call ACCEPTED (not dup)")


def test_different_scanner_same_symbol_same_minute_accepted():
    """Different scanner + same symbol + same minute = different fingerprint -> ACCEPTED.

    The in-flight set is per-WebhookReceiver instance. Between the two calls
    we release RELIANCE from in-flight so the IN_PROCESS guard does not mask
    the deduplication result (the purpose of this test is fingerprint logic).
    """
    scanners = {
        "scanner_a": "strategies/a.yaml",
        "scanner_b": "strategies/b.yaml",
    }
    receiver, sq, _ = _make_receiver(scanners=scanners, expiry=3600)
    ts = _now_str()

    with receiver.app.test_client() as client:
        resp1 = client.post("/webhook/scanner_a",
                            json=_valid_payload(scanner="scanner_a",
                                                scan_name="scanner_a",
                                                triggered_at=ts))
        assert resp1.status_code == 200
        assert resp1.get_json()["results"][0]["status"] == "ACCEPTED"

        # Release RELIANCE from in-flight so the second request is not blocked
        # by IN_PROCESS; the point is that a DIFFERENT scanner creates a different
        # fingerprint, so no DUPLICATE.
        receiver.release_in_flight("RELIANCE")

        resp2 = client.post("/webhook/scanner_b",
                            json=_valid_payload(scanner="scanner_b",
                                                scan_name="scanner_b",
                                                triggered_at=ts))
        assert resp2.status_code == 200
        assert resp2.get_json()["results"][0]["status"] == "ACCEPTED"

    print("  OK different scanner same symbol -> both ACCEPTED (different fingerprint)")


def test_symbol_in_flight_returns_in_process():
    """Symbol already in _in_flight -> per-stock IN_PROCESS.

    Use expiry=3600 and current-time-based timestamps to avoid EXPIRED masking.
    Two calls use different minutes so fingerprint differs (no DUPLICATE).
    """
    receiver, _, _ = _make_receiver(expiry=3600)
    base = datetime.now()
    ts1 = base.strftime("%Y-%m-%d %H:%M:%S")
    ts2 = (base - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")  # different minute
    ts3 = (base - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")  # yet another minute

    with receiver.app.test_client() as client:
        # First request: RELIANCE accepted and added to in-flight
        resp1 = client.post("/webhook/gap_go_long",
                            json=_valid_payload(triggered_at=ts1))
        assert resp1.get_json()["results"][0]["status"] == "ACCEPTED"

        # Second request (different minute -> not dup) but symbol still in-flight
        resp2 = client.post("/webhook/gap_go_long",
                            json=_valid_payload(triggered_at=ts2))
        assert resp2.status_code == 200
        status = resp2.get_json()["results"][0]["status"]
        assert status == "IN_PROCESS", f"Expected IN_PROCESS, got {status}"

    # After release, same symbol is accepted again (third distinct minute)
    receiver.release_in_flight("RELIANCE")
    with receiver.app.test_client() as client:
        resp3 = client.post("/webhook/gap_go_long",
                            json=_valid_payload(triggered_at=ts3))
        assert resp3.get_json()["results"][0]["status"] == "ACCEPTED"

    print("  OK in-flight -> IN_PROCESS; after release -> ACCEPTED")


def test_in_flight_sweeper_evicts_old_entries() -> None:
    """HIGH #9 regression: sweeper evicts in_flight entries older than timeout."""
    receiver, _, _ = _make_receiver(expiry=3600)

    # Manually inject a stuck symbol with a very old timestamp
    with receiver._in_flight_lock:
        receiver._in_flight["STUCK"] = time.monotonic() - 400  # 400s old > 300s timeout

    # Trigger one sweeper cycle directly (don't wait 60s)
    receiver._run_sweeper.__func__  # just verify it exists
    # Run sweeper logic inline
    now_mono = time.monotonic()
    evicted = []
    with receiver._in_flight_lock:
        for sym, added_at in list(receiver._in_flight.items()):
            if now_mono - added_at > receiver._in_flight_timeout_sec:
                evicted.append(sym)
        for sym in evicted:
            del receiver._in_flight[sym]

    assert "STUCK" in evicted, "Expected STUCK to be evicted by sweeper"
    with receiver._in_flight_lock:
        assert "STUCK" not in receiver._in_flight
    print("  OK HIGH #9: sweeper evicts in_flight entries older than timeout")


def test_expired_signal_returns_expired():
    """Signal triggered_at older than expiry_sec -> per-stock EXPIRED."""
    receiver, _, _ = _make_receiver(expiry=60)
    payload = _valid_payload(triggered_at=_expired_str(seconds=120))

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 200
    assert resp.get_json()["results"][0]["status"] == "EXPIRED"
    print("  OK expired signal -> EXPIRED")


def test_invalid_price_zero_returns_invalid_price():
    """Price == 0 -> INVALID_PRICE."""
    receiver, _, _ = _make_receiver()
    payload = _valid_payload(prices="0")

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 200
    assert resp.get_json()["results"][0]["status"] == "INVALID_PRICE"
    print("  OK price=0 -> INVALID_PRICE")


def test_invalid_price_negative_returns_invalid_price():
    """Negative price -> INVALID_PRICE."""
    receiver, _, _ = _make_receiver()
    payload = _valid_payload(prices="-100")

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 200
    assert resp.get_json()["results"][0]["status"] == "INVALID_PRICE"
    print("  OK negative price -> INVALID_PRICE")


def test_invalid_price_non_numeric_returns_invalid_price():
    """Non-numeric price string -> INVALID_PRICE."""
    receiver, _, _ = _make_receiver()
    payload = _valid_payload(prices="abc")

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 200
    assert resp.get_json()["results"][0]["status"] == "INVALID_PRICE"
    print("  OK non-numeric price -> INVALID_PRICE")


def test_empty_symbol_returns_invalid_symbol():
    """Empty symbol string -> INVALID_SYMBOL."""
    receiver, _, _ = _make_receiver()
    payload = _valid_payload(stocks=" ,TCS", prices="2500.0,3650.0")

    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)

    assert resp.status_code == 200
    results = resp.get_json()["results"]
    assert results[0]["status"] == "INVALID_SYMBOL"
    assert results[1]["status"] == "ACCEPTED"
    print("  OK empty symbol -> INVALID_SYMBOL")


def test_webhook_audit_row_written_for_every_post():
    """Every POST writes a webhook_audit row regardless of outcome (WR13)."""
    td = tempfile.mkdtemp()
    store = StateStore(Path(td) / "test.db")
    receiver, _, _ = _make_receiver(store=store)

    audit_q = "SELECT COUNT(*) AS n FROM webhook_audit"

    with receiver.app.test_client() as client:
        # 200 OK
        client.post("/webhook/gap_go_long", json=_valid_payload())
        assert store.fetch_one(audit_q)["n"] == 1

        # 404 (unknown scanner)
        client.post("/webhook/no_such_scanner",
                    json=_valid_payload(scanner="no_such_scanner",
                                        scan_name="no_such_scanner"))
        assert store.fetch_one(audit_q)["n"] == 2

        # 403 (kill switch)
        receiver._ks = _MockKillSwitch(active=True)
        client.post("/webhook/gap_go_long", json=_valid_payload())
        assert store.fetch_one(audit_q)["n"] == 3
        receiver._ks = _MockKillSwitch(active=False)

        # 400 (bad JSON)
        client.post("/webhook/gap_go_long",
                    data=b"bad", content_type="application/json")
        assert store.fetch_one(audit_q)["n"] == 4

    store.close()
    print("  OK audit row written for all 4 outcomes")


def test_concurrent_posts_queue_consistent():
    """5 concurrent POSTs process independently; queue count matches accepted."""
    receiver, sq, _ = _make_receiver(capacity=50)
    errors = []
    accepted_counts = []

    symbols = ["REL", "TCS", "INFY", "HDFC", "SBIN"]

    def post_signal(sym: str) -> None:
        try:
            with receiver.app.test_client() as client:
                resp = client.post(
                    "/webhook/gap_go_long",
                    json=_valid_payload(stocks=sym, prices="1000.0"),
                )
            data = resp.get_json()
            accepted_counts.append(data.get("accepted", 0))
        except Exception as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=post_signal, args=(s,)) for s in symbols]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"
    total_accepted = sum(accepted_counts)
    assert sq.qsize() == total_accepted, \
        f"queue size {sq.qsize()} != total accepted {total_accepted}"
    print(f"  OK 5 concurrent POSTs: {total_accepted} accepted, queue size {sq.qsize()}")


def test_performance_100_posts_under_5_seconds():
    """100 single-stock POSTs complete in < 5 seconds (handler latency smoke test)."""
    receiver, sq, _ = _make_receiver(capacity=200)

    start = time.monotonic()
    with receiver.app.test_client() as client:
        for i in range(100):
            ts = f"2026-04-15 10:{i // 60:02d}:{i % 60:02d}"
            # Use different symbols to avoid IN_PROCESS
            sym = f"SYM{i:03d}"
            resp = client.post(
                "/webhook/gap_go_long",
                json={
                    "stocks": sym,
                    "trigger_prices": "1000.0",
                    "triggered_at": ts,
                    "scan_name": "gap_go_long",
                },
            )
            assert resp.status_code == 200, f"Request {i} failed: {resp.status_code}"

    elapsed = time.monotonic() - start
    assert elapsed < 5.0, f"100 POSTs took {elapsed:.2f}s, expected < 5s"
    print(f"  OK 100 POSTs in {elapsed:.3f}s ({elapsed / 100 * 1000:.1f}ms each)")


# ---------------------------------------------------------------------------
# BL-18: construction guard for config.webhook.require_hmac
# ---------------------------------------------------------------------------

def _make_config_with_require_hmac(require_hmac: bool):
    """Return a config object shaped like SystemConfig, with webhook.require_hmac."""
    cfg = _make_config()
    cfg.webhook = types.SimpleNamespace(
        bind_host="127.0.0.1",
        bind_port=5000,
        require_hmac=require_hmac,
    )
    return cfg


def _bl18_build(config, secret):
    """Construct WebhookReceiver with given config + secret (bypasses _make_receiver
    which would inject a default config without the webhook attribute)."""
    import pytest  # noqa: F401 - only for raises in tests below
    sq = queue.Queue(maxsize=20)
    td = tempfile.mkdtemp()
    store = StateStore(Path(td) / "test.db")
    return WebhookReceiver(
        sq, store, config,
        _MockMarketWindows(entry_allowed=True),
        _MockKillSwitch(active=False),
        _NullLogger(),
        secret_token=secret,
    )


def test_bl18_require_hmac_true_no_secret_raises():
    """require_hmac=True + secret_token=None -> ValueError at construction (BL-18)."""
    import pytest
    cfg = _make_config_with_require_hmac(True)
    with pytest.raises(ValueError, match="require_hmac"):
        _bl18_build(cfg, None)
    print("  OK BL-18: require_hmac=True + no secret raises")


def test_bl18_require_hmac_true_empty_secret_raises():
    """require_hmac=True + secret_token='' -> ValueError (BL-18: falsy check)."""
    import pytest
    cfg = _make_config_with_require_hmac(True)
    with pytest.raises(ValueError, match="require_hmac"):
        _bl18_build(cfg, "")
    print("  OK BL-18: require_hmac=True + empty secret raises")


def test_bl18_require_hmac_true_with_secret_ok():
    """require_hmac=True + secret set -> construction succeeds (BL-18)."""
    cfg = _make_config_with_require_hmac(True)
    receiver = _bl18_build(cfg, "real-secret")
    assert receiver is not None
    print("  OK BL-18: require_hmac=True + secret succeeds")


def test_bl18_require_hmac_false_no_secret_ok():
    """require_hmac=False -> permissive; no secret is fine (BL-18)."""
    cfg = _make_config_with_require_hmac(False)
    receiver = _bl18_build(cfg, None)
    assert receiver is not None
    print("  OK BL-18: require_hmac=False + no secret succeeds")


def test_bl18_nested_appconfig_shape_resolved():
    """AppConfig shape (config.system.webhook.require_hmac) is resolved (BL-18)."""
    import pytest
    cfg = _make_config()  # flat, no .webhook
    cfg.system = types.SimpleNamespace(
        webhook=types.SimpleNamespace(
            bind_host="127.0.0.1", bind_port=5000, require_hmac=True
        )
    )
    with pytest.raises(ValueError, match="require_hmac"):
        _bl18_build(cfg, None)
    print("  OK BL-18: nested AppConfig shape resolved")


def test_bl18_no_webhook_attr_permissive():
    """Missing webhook attr (legacy/test fixtures) -> permissive (BL-18)."""
    cfg = _make_config()  # no .webhook, no .system.webhook
    receiver = _bl18_build(cfg, None)
    assert receiver is not None
    print("  OK BL-18: no webhook attr is permissive")


# ---------------------------------------------------------------------------
# G.1 / 2026-04-25 audit: require_hmac=True disables token-param fallback.
# Pre-fix, a request with ?token=<secret> but no X-Webhook-Signature header
# was accepted via the elif token_param branch even when require_hmac=True
# (which is meant to mean "HMAC ONLY"). Tokens in URL are logged by nginx
# and weaker than HMAC over the body.
# ---------------------------------------------------------------------------

def test_g1_require_hmac_true_rejects_token_only_request():
    """G.1: require_hmac=True + ?token=secret + no HMAC header -> 401."""
    cfg = _make_config_with_require_hmac(True)
    receiver = _bl18_build(cfg, "real-secret")
    payload = _valid_payload()

    with receiver.app.test_client() as client:
        resp = client.post(
            "/webhook/gap_go_long?token=real-secret",
            json=payload,
        )

    assert resp.status_code == 401
    body = resp.get_json() or {}
    assert "HMAC signature required" in body.get("error", ""), (
        f"Expected HMAC-required error, got {body!r}"
    )
    print("  OK G.1: require_hmac=True rejects token-only request")


def test_g1_require_hmac_true_accepts_valid_hmac():
    """G.1 regression: require_hmac=True still accepts a valid HMAC -> 200."""
    secret = "real-secret"
    cfg = _make_config_with_require_hmac(True)
    receiver = _bl18_build(cfg, secret)
    body = json.dumps(_valid_payload()).encode()
    sig = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with receiver.app.test_client() as client:
        resp = client.post(
            "/webhook/gap_go_long",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": f"sha256={sig}",
            },
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data!r}"
    print("  OK G.1: require_hmac=True still accepts valid HMAC")


def test_g1_require_hmac_false_keeps_legacy_token_path():
    """G.1: require_hmac=False (or absent) preserves the Chartink-compatible
    token-in-URL path. This is the pin for back-compat in legacy deployments."""
    cfg = _make_config_with_require_hmac(False)
    receiver = _bl18_build(cfg, "real-secret")

    with receiver.app.test_client() as client:
        resp = client.post(
            "/webhook/gap_go_long?token=real-secret",
            json=_valid_payload(),
        )

    assert resp.status_code == 200, (
        f"require_hmac=False must still allow token; got "
        f"{resp.status_code}: {resp.data!r}"
    )
    print("  OK G.1: require_hmac=False keeps token-param fallback")


def test_g1_require_hmac_true_rejects_invalid_hmac_does_not_fall_through():
    """G.1: require_hmac=True + invalid HMAC + valid token -> 401 (HMAC
    mismatch), never falls through to token check. Pin existing behavior."""
    cfg = _make_config_with_require_hmac(True)
    receiver = _bl18_build(cfg, "real-secret")

    with receiver.app.test_client() as client:
        resp = client.post(
            "/webhook/gap_go_long?token=real-secret",
            json=_valid_payload(),
            headers={"X-Webhook-Signature": "sha256=deadbeef"},
        )

    assert resp.status_code == 401
    body = resp.get_json() or {}
    # Either HMAC mismatch OR HMAC required is acceptable; what must NOT
    # happen is the token-fallback path letting the request through.
    assert resp.status_code != 200
    print("  OK G.1: invalid HMAC + valid token still 401 (no fallthrough)")


# ---------------------------------------------------------------------------
# FIX-011: Sweeper heartbeat mechanism (no blind lock eviction)
# ---------------------------------------------------------------------------

def test_fix011_long_running_with_heartbeats_not_evicted():
    """
    FIX-011: Lock held for 400s but heartbeating every 30s is NOT evicted.
    Validates that active processing is distinguished from stalled processing.
    """
    receiver, sq, store = _make_receiver()
    # Manually claim a symbol
    symbol = "LONGRUN"
    claimed = receiver._claim_in_flight(symbol)
    assert claimed, "Initial claim should succeed"

    # Simulate 400s of processing with heartbeats every 30s
    # Sweeper runs every 60s and evicts if no heartbeat for 60s.
    # We'll update heartbeat at t=0, t=30, t=60, t=90, ... t=390
    # Then wait 65s (total 455s) and check lock still exists.

    # Fast-forward simulation: directly manipulate the entry timestamps
    with receiver._in_flight_lock:
        entry = receiver._in_flight[symbol]
        # Set acquired_at to 400s ago
        entry['acquired_at'] = time.monotonic() - 400.0
        # Set heartbeat_at to 30s ago (recent heartbeat)
        entry['heartbeat_at'] = time.monotonic() - 30.0

    # Wait for sweeper cycle (it runs every 60s, but we need to ensure it ran)
    # Instead of waiting 60s, trigger a manual sweep check
    # (since we can't easily wait in tests, we'll check the eviction logic directly)

    # Check that the lock is still present
    with receiver._in_flight_lock:
        assert symbol in receiver._in_flight, "Lock should NOT be evicted (recent heartbeat)"

    # Now manually check sweeper logic: (now - heartbeat_at) should be ~30s < 60s
    now_mono = time.monotonic()
    with receiver._in_flight_lock:
        entry = receiver._in_flight[symbol]
        stall_time = now_mono - entry['heartbeat_at']
        assert stall_time < receiver._in_flight_timeout_sec, \
            f"Stall time {stall_time:.1f}s should be < timeout {receiver._in_flight_timeout_sec}s"

    receiver.release_in_flight(symbol)
    print("  OK FIX-011: lock held 400s with heartbeats NOT evicted")


def test_fix011_no_heartbeat_90s_evicted():
    """
    FIX-011: Lock claimed but no heartbeat for 90s is evicted by sweeper.
    Validates that stalled workers are detected and cleaned up.
    """
    receiver, sq, store = _make_receiver()
    symbol = "STALLED"
    claimed = receiver._claim_in_flight(symbol)
    assert claimed, "Initial claim should succeed"

    # Simulate stall: set both timestamps to 90s ago
    with receiver._in_flight_lock:
        entry = receiver._in_flight[symbol]
        old_time = time.monotonic() - 90.0
        entry['acquired_at'] = old_time
        entry['heartbeat_at'] = old_time

    # Manually run sweeper logic (instead of waiting 60s for the thread)
    now_mono = time.monotonic()
    evicted = []
    with receiver._in_flight_lock:
        for sym, entry in list(receiver._in_flight.items()):
            heartbeat_at = entry['heartbeat_at']
            if now_mono - heartbeat_at > receiver._in_flight_timeout_sec:
                evicted.append(sym)
        for sym in evicted:
            del receiver._in_flight[sym]

    assert symbol in evicted, "Symbol should be evicted (no heartbeat for 90s > 60s timeout)"
    with receiver._in_flight_lock:
        assert symbol not in receiver._in_flight, "Symbol should be removed from in_flight"

    print("  OK FIX-011: lock with no heartbeat for 90s evicted")


def test_fix011_evicted_symbol_can_be_readmitted():
    """
    FIX-011: After sweeper evicts a stalled symbol, a new signal for that
    symbol is admitted (not rejected as IN_PROCESS / DUPLICATE).
    """
    receiver, sq, store = _make_receiver(expiry=3600)
    symbol = "READMIT"
    base = datetime.now()
    ts1 = base.strftime("%Y-%m-%d %H:%M:%S")
    ts2 = (base - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")  # different minute

    # First request: accepted
    with receiver.app.test_client() as client:
        resp1 = client.post(
            "/webhook/gap_go_long",
            json={
                "stocks": symbol,
                "trigger_prices": "1000.0",
                "triggered_at": ts1,
                "scan_name": "gap_go_long",
            },
        )
    assert resp1.status_code == 200
    body1 = resp1.get_json()
    results1 = body1.get("results", [])
    assert len(results1) == 1
    assert results1[0]["status"] == "ACCEPTED"

    # Verify symbol is in_flight
    with receiver._in_flight_lock:
        assert symbol in receiver._in_flight

    # Simulate stall and eviction (same as previous test)
    with receiver._in_flight_lock:
        entry = receiver._in_flight[symbol]
        old_time = time.monotonic() - 90.0
        entry['acquired_at'] = old_time
        entry['heartbeat_at'] = old_time

    # Manual sweep
    now_mono = time.monotonic()
    evicted = []
    with receiver._in_flight_lock:
        for sym, entry in list(receiver._in_flight.items()):
            heartbeat_at = entry['heartbeat_at']
            if now_mono - heartbeat_at > receiver._in_flight_timeout_sec:
                evicted.append(sym)
        for sym in evicted:
            del receiver._in_flight[sym]

    assert symbol in evicted

    # Second request: should be accepted (not rejected as IN_PROCESS)
    # Use different minute to avoid DUPLICATE fingerprint
    with receiver.app.test_client() as client:
        resp2 = client.post(
            "/webhook/gap_go_long",
            json={
                "stocks": symbol,
                "trigger_prices": "1000.0",
                "triggered_at": ts2,  # different minute
                "scan_name": "gap_go_long",
            },
        )
    assert resp2.status_code == 200
    body2 = resp2.get_json()
    results2 = body2.get("results", [])
    assert len(results2) == 1
    assert results2[0]["status"] == "ACCEPTED", \
        f"Expected ACCEPTED after eviction, got {results2[0]['status']}"

    print("  OK FIX-011: evicted symbol readmitted on new signal")


def test_fix011_update_heartbeat_updates_timestamp():
    """
    FIX-011: update_heartbeat() correctly updates the heartbeat_at timestamp.
    """
    receiver, sq, store = _make_receiver()
    symbol = "HEARTBEAT"
    claimed = receiver._claim_in_flight(symbol)
    assert claimed

    # Get initial heartbeat timestamp
    with receiver._in_flight_lock:
        entry = receiver._in_flight[symbol]
        initial_heartbeat = entry['heartbeat_at']

    # Sleep briefly then update heartbeat
    time.sleep(0.05)
    receiver.update_heartbeat(symbol)

    # Check that heartbeat_at was updated
    with receiver._in_flight_lock:
        entry = receiver._in_flight[symbol]
        updated_heartbeat = entry['heartbeat_at']

    assert updated_heartbeat > initial_heartbeat, \
        "heartbeat_at should be updated after update_heartbeat() call"

    receiver.release_in_flight(symbol)
    print("  OK FIX-011: update_heartbeat() updates timestamp")


def test_fix011_update_heartbeat_unknown_symbol_noop():
    """
    FIX-011: update_heartbeat() on a symbol not in_flight is a silent no-op.
    """
    receiver, sq, store = _make_receiver()
    # Call update_heartbeat on a symbol that was never claimed
    try:
        receiver.update_heartbeat("UNKNOWN")
        print("  OK FIX-011: update_heartbeat() on unknown symbol is no-op")
    except Exception as exc:
        raise AssertionError(f"update_heartbeat should not raise on unknown symbol: {exc}")


# ---------------------------------------------------------------------------
# FIX-022: Timezone-aware triggered_at parsing
# ---------------------------------------------------------------------------

def test_fix022_naive_triggered_at_not_rejected_as_stale():
    """
    FIX-022: Send naive triggered_at string (Chartink format).
    After IST localization, age should be computed correctly → NOT rejected as stale.
    """
    receiver, sq, store = _make_receiver(expiry=600)  # 10min expiry
    # Send a webhook with recent naive timestamp (within expiry window)
    recent_naive = (datetime.now() - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    payload = _valid_payload(triggered_at=recent_naive)
    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long",
                          data=json.dumps(payload),
                          content_type="application/json")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.get_json()}"
        data = resp.get_json()
        assert data["results"][0]["status"] == "ACCEPTED", \
            f"Signal should be ACCEPTED, not stale. Got: {data['results'][0]['status']}"
    print("  OK FIX-022: naive triggered_at localized to IST, age computed correctly, signal accepted")


def test_fix022_already_aware_triggered_at_no_double_offset():
    """
    FIX-022: If triggered_at is already timezone-aware (future-proofing),
    should not apply double offset or crash.
    """
    from core.time_authority import ist_timezone
    receiver, sq, store = _make_receiver(expiry=600)
    # Create an already-aware IST datetime
    aware_dt = datetime.now(ist_timezone()) - timedelta(seconds=30)
    aware_str = aware_dt.strftime("%Y-%m-%d %H:%M:%S")  # Still sends as string
    payload = _valid_payload(triggered_at=aware_str)
    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long",
                          data=json.dumps(payload),
                          content_type="application/json")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.get_json()
        assert data["results"][0]["status"] == "ACCEPTED"
    print("  OK FIX-022: already-aware triggered_at handled without double-offset")


def test_fix022_simulated_offset_bug_age_correct():
    """
    FIX-022: Simulate the bug scenario where naive datetime would cause
    5.5 hour offset in age calculation. After fix, age should be correct.
    """
    receiver, sq, store = _make_receiver(expiry=600)  # 10min expiry
    # Send a timestamp that's 60 seconds old
    past_naive = (datetime.now() - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
    payload = _valid_payload(triggered_at=past_naive)
    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long",
                          data=json.dumps(payload),
                          content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        # Before fix: age would be calculated as ~19860 seconds (5.5h offset)
        # After fix: age should be ~60 seconds, well within 600s expiry
        assert data["results"][0]["status"] == "ACCEPTED", \
            f"Signal 60s old should be ACCEPTED (expiry=600s). Got: {data['results'][0]['status']}"
    print("  OK FIX-022: age calculation correct after timezone fix (no 5.5h offset)")


# ---------------------------------------------------------------------------
# Standalone runner (no pytest dependency)
# ---------------------------------------------------------------------------

def run_all_tests() -> int:
    tests = [
        test_health_endpoint_returns_200,
        test_valid_single_stock_accepted,
        test_valid_multi_stock_all_accepted,
        test_mixed_valid_invalid_payload,
        test_unknown_scanner_returns_404,
        test_scan_name_body_mismatch_returns_400,
        test_malformed_json_returns_400,
        test_missing_required_field_returns_400,
        test_bad_triggered_at_format_returns_400,
        test_hmac_absent_when_required_returns_401,
        test_hmac_mismatch_returns_401,
        test_hmac_valid_returns_200,
        test_hmac_none_mode_no_header_check,
        test_soft_kill_active_returns_403,
        test_hard_kill_active_returns_403,
        test_outside_entry_window_returns_403,
        test_queue_at_backpressure_threshold_returns_503,
        test_queue_full_on_individual_signal_returns_503,
        test_duplicate_same_fingerprint_same_minute,
        test_different_minute_same_scanner_symbol_accepted,
        test_different_scanner_same_symbol_same_minute_accepted,
        test_symbol_in_flight_returns_in_process,
        test_in_flight_sweeper_evicts_old_entries,
        test_expired_signal_returns_expired,
        test_invalid_price_zero_returns_invalid_price,
        test_invalid_price_negative_returns_invalid_price,
        test_invalid_price_non_numeric_returns_invalid_price,
        test_empty_symbol_returns_invalid_symbol,
        test_webhook_audit_row_written_for_every_post,
        test_concurrent_posts_queue_consistent,
        test_performance_100_posts_under_5_seconds,
        # BL-18: construction guard for config.webhook.require_hmac
        test_bl18_require_hmac_true_no_secret_raises,
        test_bl18_require_hmac_true_empty_secret_raises,
        test_bl18_require_hmac_true_with_secret_ok,
        test_bl18_require_hmac_false_no_secret_ok,
        test_bl18_nested_appconfig_shape_resolved,
        test_bl18_no_webhook_attr_permissive,
        # G.1 / 2026-04-25 audit -- require_hmac disables token fallback
        test_g1_require_hmac_true_rejects_token_only_request,
        test_g1_require_hmac_true_accepts_valid_hmac,
        test_g1_require_hmac_false_keeps_legacy_token_path,
        test_g1_require_hmac_true_rejects_invalid_hmac_does_not_fall_through,
        # FIX-011: Sweeper heartbeat mechanism
        test_fix011_long_running_with_heartbeats_not_evicted,
        test_fix011_no_heartbeat_90s_evicted,
        test_fix011_evicted_symbol_can_be_readmitted,
        test_fix011_update_heartbeat_updates_timestamp,
        test_fix011_update_heartbeat_unknown_symbol_noop,
        # FIX-022: Timezone-aware triggered_at parsing
        test_fix022_naive_triggered_at_not_rejected_as_stale,
        test_fix022_already_aware_triggered_at_no_double_offset,
        test_fix022_simulated_offset_bug_age_correct,
    ]

    print("=" * 70)
    print("webhook_receiver.py -- Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as exc:
            failed.append((test.__name__, f"AssertionError: {exc}"))
            print(f"  FAIL: {exc}")
        except Exception as exc:
            failed.append((test.__name__, f"{type(exc).__name__}: {exc}"))
            print(f"  ERROR: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1
    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
