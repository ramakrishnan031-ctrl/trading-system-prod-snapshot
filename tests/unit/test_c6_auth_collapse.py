"""tests/unit/test_c6_auth_collapse.py -- C6 (2026-07-25): the receiver's auth
decision is written ONCE and called by both routes.

WHAT C6 ACTUALLY WAS. The auth *state* was already shared -- `_secret`,
`_require_hmac` and `_ip_limiter` are instance attributes both routes read. What
was duplicated is the *check*: the same three-way decision (HMAC -> require_hmac
-> token) spelled twice, in two shapes. /webhook returned early from each branch;
/health accumulated an `ok` boolean and issued a single 401. The two were patched
separately (G.1 on /webhook 2026-04-25; the /health parity fix later), which is
the maintenance hazard C6 removes.

SCOPE OF THESE TESTS. Two halves, and the second is the one that matters:

  A. `TestC6BranchMatrix` -- the collapsed call accepts and rejects exactly as
     the two idioms did, branch by branch, on BOTH routes.

  B. `TestC6DivergencesPreserved` -- the four things a naive flattening would
     destroy. A refactor that "unified" the routes would pass half of A and fail
     these. Each test names the divergence and what breaks in production if it
     goes green for the wrong reason:
       D1 HMAC payload differs (raw body vs b"") -- structural to GET/POST
       D2 /health's uniform 401 message -- deliberate anti-oracle
       D3 the EOD route returns BEFORE the kill and entry-window gates -- this
          is what makes the 17:00 PB-01 capture possible
       D4 /webhook audits every outcome incl. 401; /health audits nothing

RED-FIRST NOTE (this is a behaviour-preserving refactor, so "red on HEAD" is not
available -- by construction the collapse changes no outcome). The honest
demonstration is that each divergence test goes RED when the corresponding
flattening is PLANTED. Recorded in docs/audit/c6_auth_collapse_25jul2026.md with
the four planted diffs and their failures.

PARITY: pure request-path logic, no paper/live branch anywhere in _authenticate
or either caller -- identical in both modes. Asserted in test_c6_parity_no_mode_branch.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import inspect
import json
import queue
import tempfile
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import create_autospec

from core.state_store import StateStore
from signals.webhook_receiver import WebhookReceiver

_SECRET = "c6-test-secret"


class _MockMarketWindows:
    def __init__(self, entry_allowed: bool = True):
        self._allowed = entry_allowed

    def is_entry_allowed(self, now) -> bool:
        return self._allowed


class _MockKillSwitch:
    def __init__(self, active: bool = False):
        self._active = active

    def is_active(self, intent="entry") -> bool:
        return self._active


class _NullLogger:
    def debug(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def critical(self, *a, **kw): pass


class _CapturingEodWorker:
    """Stands in for the PB-01 watchlist capture worker."""

    def __init__(self):
        self.submitted = []

    def submit(self, scanner_name, symbol, triggered_at) -> bool:
        self.submitted.append((scanner_name, symbol))
        return True


def _make_config(require_hmac=None, with_eod_scanner=False):
    cfg = types.SimpleNamespace()
    cfg.system = types.SimpleNamespace(
        signal_queue=types.SimpleNamespace(
            capacity=100, backpressure_pct=0.80, expiry_sec=600, warning_pct=0.60,
        )
    )
    scanners = {"gap_go_long": "strategies/gap_go_long.yaml"}
    if with_eod_scanner:
        # The PB-01 route: /webhook/<scanner_name> with scanner_type == "eod".
        scanners["pb01_breakout_retest"] = types.SimpleNamespace(scanner_type="eod")
    cfg.scan_webhook_map = types.SimpleNamespace(scanners=scanners)
    if require_hmac is not None:
        cfg.system.webhook = types.SimpleNamespace(
            bind_host="127.0.0.1", bind_port=5000, require_hmac=require_hmac,
        )
    return cfg


def _make_receiver(secret=_SECRET, require_hmac=None, with_eod_scanner=False,
                   kill_active=False, entry_allowed=True):
    sq = queue.Queue(maxsize=100)
    store = StateStore(Path(tempfile.mkdtemp()) / "test.db")
    receiver = WebhookReceiver(
        sq, store, _make_config(require_hmac, with_eod_scanner),
        _MockMarketWindows(entry_allowed=entry_allowed),
        _MockKillSwitch(active=kill_active),
        _NullLogger(),
        secret_token=secret,
    )
    return receiver, sq, store


def _sig(payload: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _intraday_body(scanner="gap_go_long") -> bytes:
    return json.dumps({
        "stocks": "RELIANCE",
        "trigger_prices": "2500.0",
        "triggered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scan_name": scanner,
    }).encode()


def _eod_body() -> bytes:
    return json.dumps({
        "stocks": "RELIANCE,TCS",
        "trigger_prices": "2500.0,3900.0",
        "triggered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }).encode()


def _post(client, body: bytes, scanner="gap_go_long", headers=None, qs=""):
    return client.post(
        f"/webhook/{scanner}{qs}",
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
    )


# ═══════════════════════════════════════════════════════════════════════════
# A. The collapsed decision, branch by branch, on BOTH routes
# ═══════════════════════════════════════════════════════════════════════════

class TestC6BranchMatrix:
    """One decision, two routes: every branch must land the same way on each.

    `authenticated` below means "auth did not refuse" -- i.e. NOT 401. The routes
    legitimately answer differently after auth passes (200 status body vs the
    signal path), so the assertion is on the 401/not-401 boundary, which is the
    only thing _authenticate decides.
    """

    # ── the HMAC branch ──────────────────────────────────────────────────
    def test_valid_hmac_authenticates_both_routes(self):
        receiver, _, _ = _make_receiver()
        body = _intraday_body()
        with receiver.app.test_client() as c:
            w = _post(c, body, headers={"X-Webhook-Signature": _sig(body)})
            h = c.get("/health", headers={"X-Webhook-Signature": _sig(b"")})
        assert w.status_code != 401, f"/webhook refused a valid HMAC: {w.data!r}"
        assert h.status_code == 200, f"/health refused a valid HMAC: {h.data!r}"

    def test_bad_hmac_refuses_both_routes(self):
        receiver, _, _ = _make_receiver()
        with receiver.app.test_client() as c:
            w = _post(c, _intraday_body(),
                      headers={"X-Webhook-Signature": "sha256=deadbeef"})
            h = c.get("/health", headers={"X-Webhook-Signature": "sha256=deadbeef"})
        assert w.status_code == 401
        assert h.status_code == 401

    def test_bad_hmac_is_not_rescued_by_a_valid_token_on_either_route(self):
        """The sha256= branch is terminal: a present-but-wrong signature must not
        fall through to the token fallback. Both routes, one rule."""
        receiver, _, _ = _make_receiver()
        with receiver.app.test_client() as c:
            w = _post(c, _intraday_body(), qs=f"?token={_SECRET}",
                      headers={"X-Webhook-Signature": "sha256=deadbeef"})
            h = c.get(f"/health?token={_SECRET}",
                      headers={"X-Webhook-Signature": "sha256=deadbeef"})
        assert w.status_code == 401
        assert h.status_code == 401

    # ── the require_hmac branch (G.1) ────────────────────────────────────
    def test_require_hmac_refuses_token_only_on_both_routes(self):
        receiver, _, _ = _make_receiver(require_hmac=True)
        assert receiver._require_hmac is True, (
            "guard: if require_hmac did not resolve True this class is vacuous"
        )
        with receiver.app.test_client() as c:
            w = _post(c, _intraday_body(), qs=f"?token={_SECRET}")
            h = c.get(f"/health?token={_SECRET}")
        assert w.status_code == 401
        assert h.status_code == 401

    def test_require_hmac_still_accepts_a_valid_signature_on_both_routes(self):
        receiver, _, _ = _make_receiver(require_hmac=True)
        body = _intraday_body()
        with receiver.app.test_client() as c:
            w = _post(c, body, headers={"X-Webhook-Signature": _sig(body)})
            h = c.get("/health", headers={"X-Webhook-Signature": _sig(b"")})
        assert w.status_code != 401
        assert h.status_code == 200

    # ── the token branch ─────────────────────────────────────────────────
    def test_valid_token_authenticates_both_routes_when_require_hmac_false(self):
        receiver, _, _ = _make_receiver(require_hmac=False)
        with receiver.app.test_client() as c:
            w = _post(c, _intraday_body(), qs=f"?token={_SECRET}")
            h = c.get(f"/health?token={_SECRET}")
        assert w.status_code != 401
        assert h.status_code == 200

    def test_wrong_token_refuses_both_routes(self):
        receiver, _, _ = _make_receiver()
        with receiver.app.test_client() as c:
            w = _post(c, _intraday_body(), qs="?token=wrong")
            h = c.get("/health?token=wrong")
        assert w.status_code == 401
        assert h.status_code == 401

    # ── the no-credential and no-secret branches ─────────────────────────
    def test_no_credential_refuses_both_routes(self):
        receiver, _, _ = _make_receiver()
        with receiver.app.test_client() as c:
            w = _post(c, _intraday_body())
            h = c.get("/health")
        assert w.status_code == 401
        assert h.status_code == 401

    def test_no_secret_configured_leaves_both_routes_open(self):
        """The historical secret-less deployment. Unreachable in production
        (WEBHOOK_SECRET is a required startup secret) but it is the contract the
        tests rely on, and the collapse must not change it."""
        receiver, _, _ = _make_receiver(secret=None)
        with receiver.app.test_client() as c:
            w = _post(c, _intraday_body())
            h = c.get("/health")
        assert w.status_code != 401
        assert h.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# B. The four divergences the collapse must NOT flatten
# ═══════════════════════════════════════════════════════════════════════════

class TestC6DivergencesPreserved:

    # ── D1: the HMAC payload is per-route, and structurally must be ──────
    def test_d1_health_shaped_signature_does_not_authenticate_webhook(self):
        """/health signs b""; /webhook signs the body. A shared helper that
        assumed one payload would let an empty-body signature -- which is a
        CONSTANT for a given secret, and therefore replayable forever -- pass on
        the signal-entry route."""
        receiver, _, _ = _make_receiver()
        with receiver.app.test_client() as c:
            resp = _post(c, _intraday_body(),
                         headers={"X-Webhook-Signature": _sig(b"")})
        assert resp.status_code == 401, (
            "an empty-body signature must NOT authenticate POST /webhook"
        )

    def test_d1_body_shaped_signature_does_not_authenticate_health(self):
        receiver, _, _ = _make_receiver()
        body = _intraday_body()
        with receiver.app.test_client() as c:
            resp = c.get("/health", headers={"X-Webhook-Signature": _sig(body)})
        assert resp.status_code == 401

    # ── D2: /health's uniform message vs /webhook's granular one ─────────
    def test_d2_webhook_keeps_a_DISTINCT_message_per_failure_branch(self):
        receiver, _, _ = _make_receiver(require_hmac=False)
        rh, _, _ = _make_receiver(require_hmac=True)
        body = _intraday_body()
        msgs = {}
        with receiver.app.test_client() as c:
            msgs["mismatch"] = _post(
                c, body, headers={"X-Webhook-Signature": "sha256=deadbeef"}
            ).get_json()["error"]
            msgs["bad_token"] = _post(c, body, qs="?token=wrong").get_json()["error"]
            msgs["missing"] = _post(c, body).get_json()["error"]
        with rh.app.test_client() as c:
            msgs["require_hmac"] = _post(
                c, body, qs=f"?token={_SECRET}"
            ).get_json()["error"]

        assert msgs["mismatch"] == "HMAC signature mismatch"
        assert msgs["bad_token"] == "Invalid token"
        assert msgs["missing"].startswith("Missing auth:")
        assert "HMAC signature required" in msgs["require_hmac"]
        assert len(set(msgs.values())) == 4, (
            f"/webhook's four failure reasons must stay distinguishable: {msgs}"
        )

    def test_d2_health_message_is_UNIFORM_across_every_failure_branch(self):
        """*** THE ANTI-ORACLE PIN. *** /health is on 0.0.0.0:5000. If the
        collapse let the granular reason through, an anonymous caller could read
        'HMAC signature required (require_hmac=True)' off a 401 and learn the
        deploy's security posture. Every branch must answer identically."""
        seen = set()
        for require_hmac in (False, True):
            receiver, _, _ = _make_receiver(require_hmac=require_hmac)
            with receiver.app.test_client() as c:
                for path, headers in (
                    ("/health", None),                                   # no credential
                    ("/health?token=wrong", None),                       # bad token
                    (f"/health?token={_SECRET}", None),                  # token (refused iff require_hmac)
                    ("/health", {"X-Webhook-Signature": "sha256=deadbeef"}),   # bad sig
                ):
                    resp = c.get(path, headers=headers)
                    if resp.status_code == 401:
                        seen.add(json.dumps(resp.get_json(), sort_keys=True))
        assert seen == {'{"error": "authentication required"}'}, (
            f"/health leaked a distinguishable 401 body: {seen}"
        )

    def test_d2_health_401_still_leaks_no_system_state(self):
        receiver, _, _ = _make_receiver()
        with receiver.app.test_client() as c:
            data = c.get("/health").get_json() or {}
        for leaky in ("kill_switch_active", "queue_size", "queue_capacity", "queue_depth"):
            assert leaky not in data, f"/health 401 leaked {leaky}"

    # ── D3: THE EOD EARLY RETURN. The 17:00 PB-01 capture depends on it ──
    def test_d3_eod_route_is_reachable_AFTER_hours_and_under_a_kill(self):
        """*** DO NOT FLATTEN THIS. *** /webhook/<eod scanner> returns BEFORE the
        kill-switch gate and BEFORE the intraday entry-window gate, because a
        post-close alert is legitimate -- that early return is exactly what makes
        the 17:00 PB-01 watchlist capture possible. Here both gates are set to
        REFUSE; the EOD alert must still be captured."""
        worker = _CapturingEodWorker()
        receiver, sq, _ = _make_receiver(
            with_eod_scanner=True, kill_active=True, entry_allowed=False,
        )
        receiver.set_eod_capture(worker)
        body = _eod_body()
        with receiver.app.test_client() as c:
            resp = _post(c, body, scanner="pb01_breakout_retest",
                         headers={"X-Webhook-Signature": _sig(body)})

        assert resp.status_code == 200, (
            f"EOD capture must survive kill_active + outside-entry-window, got "
            f"{resp.status_code}: {resp.data!r}"
        )
        assert resp.get_json()["captured"] == 2
        assert [s for _, s in worker.submitted] == ["RELIANCE", "TCS"]
        assert sq.qsize() == 0, "an EOD alert must NEVER reach the intraday queue"

    def test_d3_eod_route_still_runs_AFTER_auth(self):
        """The early return is early w.r.t. the kill/window gates only. Auth
        still comes first, so an unauthenticated caller cannot reach the capture
        worker (nor enumerate scanner names via the 404 below it)."""
        worker = _CapturingEodWorker()
        receiver, _, _ = _make_receiver(with_eod_scanner=True)
        receiver.set_eod_capture(worker)
        with receiver.app.test_client() as c:
            resp = _post(c, _eod_body(), scanner="pb01_breakout_retest")
        assert resp.status_code == 401
        assert worker.submitted == [], "EOD capture ran for an unauthenticated caller"

    def test_d3_unknown_scanner_404_stays_behind_auth(self):
        """Divergence #4 of the scoping: the 404 must not become an
        unauthenticated scanner-name oracle."""
        receiver, _, _ = _make_receiver()
        with receiver.app.test_client() as c:
            resp = _post(c, _intraday_body(), scanner="does_not_exist")
        assert resp.status_code == 401, "unknown-scanner 404 leaked before auth"

    # ── D4: the audit trail differs, and that is the record's contract ───
    def test_d4_webhook_audits_a_401_but_health_does_not(self):
        receiver, _, store = _make_receiver()
        with receiver.app.test_client() as c:
            assert _post(c, _intraday_body()).status_code == 401
            assert c.get("/health").status_code == 401
        rows = store.fetch_all("SELECT scanner_name, response_code FROM webhook_audit")
        codes = [(r["scanner_name"], r["response_code"]) for r in rows]
        assert ("gap_go_long", 401) in codes, (
            "webhook_audit is the authoritative POST record -- a 401 must appear"
        )
        assert len(codes) == 1, (
            f"/health must write no webhook_audit row (it never did): {codes}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# C. Structural pins: it really is ONE call site, and it is mode-independent
# ═══════════════════════════════════════════════════════════════════════════

class TestC6Structure:

    def test_c6_auth_is_decided_in_exactly_one_place(self):
        """The point of C6. If a future change re-inlines an auth decision into a
        route, this fails: compare_digest must appear only inside _authenticate."""
        import signals.webhook_receiver as wr

        src = inspect.getsource(wr)
        total = src.count("compare_digest")
        in_helper = inspect.getsource(wr.WebhookReceiver._authenticate).count("compare_digest")
        assert total == in_helper, (
            f"{total - in_helper} compare_digest call(s) live outside _authenticate "
            f"-- the auth decision has been duplicated again"
        )
        assert in_helper == 2, "expected the HMAC and token comparisons in the helper"

    def test_c6_both_routes_call_the_shared_helper(self):
        import signals.webhook_receiver as wr

        health_src = inspect.getsource(wr.WebhookReceiver._register_routes)
        process_src = inspect.getsource(wr.WebhookReceiver._process_request)
        assert "_authenticate(b\"\")" in health_src, "/health no longer calls the shared helper"
        assert "_authenticate(raw_body)" in process_src, "/webhook no longer calls the shared helper"

    def test_c6_parity_no_mode_branch(self):
        """PARITY (permanent rule): the auth path must behave identically in
        paper and live. Assert there is no mode/paper/live branch in the helper
        or either caller -- parity by construction, not by a second test run."""
        import signals.webhook_receiver as wr

        for fn in (wr.WebhookReceiver._authenticate,
                   wr.WebhookReceiver._register_routes,
                   wr.WebhookReceiver._process_request):
            src = inspect.getsource(fn).lower()
            for token in ("paper_mode", "is_paper", "live_mode", "trading_mode"):
                assert token not in src, (
                    f"{fn.__name__} branches on {token!r} -- auth is not mode-independent"
                )


# ═══════════════════════════════════════════════════════════════════════════
# C3 (25-Jul-2026) — the PB-01 capture heartbeat.
#
# Before this, the 17:00 EOD capture emitted nothing but INFO log lines. An
# empty pb01_watchlist next morning was identical under all of: no breakouts /
# Chartink never fired / boot wiring failed (_eod_capture is None) / every
# symbol skipped / the service died at 16:20. Only the third was detectable,
# and only by grepping the MORNING's boot log.
#
# One cron_heartbeat row per alert collapses that. It records QUEUED, not
# captured -- the worker is asynchronous -- which is why the message says
# `queued=`; the watchlist row count stays the proof the worker finished.
# ═══════════════════════════════════════════════════════════════════════════

class _HeartbeatSpy:
    """Captures record_heartbeat calls without touching a DB.

    Installed BEHIND create_autospec (see _patch_heartbeat) — never as the stub
    itself. A hand-written stub freezes today's parameter list and drifts silently
    the next time record_heartbeat gains a parameter; that is the exact 15-Jul-2026
    F2 break, and tests/unit/test_cron_heartbeat_contract.py's discovery guard
    rejects any file that patches record_heartbeat without a signature-safe
    stand-in.
    """

    def __init__(self, explode=False):
        self.calls = []
        self._explode = explode

    def __call__(self, job_name, status="SUCCESS", duration_sec=None,
                 message=None, functional_status=None, db_path=None):
        if self._explode:
            raise RuntimeError("heartbeat DB unavailable")
        self.calls.append({"job_name": job_name, "status": status,
                           "message": message, "functional_status": functional_status})
        return True


def _patch_heartbeat(monkeypatch, spy):
    """Swap in a stand-in that ENFORCES record_heartbeat's real signature.

    create_autospec tracks the live signature, so a future parameter change fails
    here loudly instead of a narrow stub swallowing it. The spy rides as
    side_effect: autospec binds the call (raising TypeError on a bad signature),
    then the spy records it. Patching the MODULE attribute is what makes this
    work — production imports record_heartbeat inside the function, so the name
    is resolved at call time.
    """
    import utils.cron_heartbeat as hb
    monkeypatch.setattr(hb, "record_heartbeat",
                        create_autospec(hb.record_heartbeat, side_effect=spy))


def _post_eod(receiver, body=None):
    body = body if body is not None else _eod_body()
    with receiver.app.test_client() as c:
        return _post(c, body, scanner="pb01_breakout_retest",
                     headers={"X-Webhook-Signature": _sig(body)})


class TestC3CaptureHeartbeat:

    def test_success_records_counts_and_no_functional_status(self, monkeypatch):
        spy = _HeartbeatSpy()
        _patch_heartbeat(monkeypatch, spy)
        receiver, _, _ = _make_receiver(with_eod_scanner=True)
        receiver.set_eod_capture(_CapturingEodWorker())

        resp = _post_eod(receiver)
        assert resp.status_code == 200 and resp.get_json()["captured"] == 2
        assert len(spy.calls) == 1
        call = spy.calls[0]
        assert call["job_name"] == "pb01_capture"
        assert call["status"] == "SUCCESS"
        assert call["functional_status"] is None
        assert "symbols=2" in call["message"] and "queued=2" in call["message"]
        assert "pb01_breakout_retest" in call["message"]

    def test_message_says_QUEUED_not_captured(self, monkeypatch):
        """A3: the worker is async, so this proves arrival/acceptance, NOT that a
        pb01_watchlist row exists. The wording must not overstate it."""
        spy = _HeartbeatSpy()
        _patch_heartbeat(monkeypatch, spy)
        receiver, _, _ = _make_receiver(with_eod_scanner=True)
        receiver.set_eod_capture(_CapturingEodWorker())

        _post_eod(receiver)
        msg = spy.calls[0]["message"]
        assert "queued=" in msg
        assert "captured=" not in msg, f"heartbeat overstates the worker's outcome: {msg}"

    def test_zero_queued_is_flagged_EMPTY_NO_DATA(self, monkeypatch):
        spy = _HeartbeatSpy()
        _patch_heartbeat(monkeypatch, spy)

        class _RefusingWorker:
            def submit(self, **kw):
                return False

        receiver, _, _ = _make_receiver(with_eod_scanner=True)
        receiver.set_eod_capture(_RefusingWorker())

        _post_eod(receiver)
        assert spy.calls[0]["status"] == "SUCCESS"
        assert spy.calls[0]["functional_status"] == "EMPTY_NO_DATA"
        assert "queued=0" in spy.calls[0]["message"]

    def test_no_capture_worker_records_FAILED_and_DISABLED(self, monkeypatch):
        """*** THE ONE THAT MATTERS. *** A boot-wiring failure leaves _eod_capture
        None; the alert still answers 200, so without this row it is invisible until
        someone notices an empty watchlist and cannot tell why."""
        spy = _HeartbeatSpy()
        _patch_heartbeat(monkeypatch, spy)
        receiver, _, _ = _make_receiver(with_eod_scanner=True)   # set_eod_capture NOT called

        resp = _post_eod(receiver)
        assert resp.status_code == 200                      # unchanged, fail-safe
        assert resp.get_json()["detail"] == "watchlist disabled"
        assert spy.calls[0]["status"] == "FAILED"
        assert spy.calls[0]["functional_status"] == "DISABLED"
        assert "symbols=2" in spy.calls[0]["message"] and "queued=0" in spy.calls[0]["message"]

    # ── A4: the ingress must be unbreakable ──────────────────────────────
    def test_a_heartbeat_failure_cannot_change_the_response(self, monkeypatch):
        """⛔ SIGNAL INGRESS. If the heartbeat raises, the alert must still be
        accepted with the same 200 and the same body. Observability may never
        break the thing it observes."""
        _patch_heartbeat(monkeypatch, _HeartbeatSpy(explode=True))
        worker = _CapturingEodWorker()
        receiver, sq, _ = _make_receiver(with_eod_scanner=True)
        receiver.set_eod_capture(worker)

        resp = _post_eod(receiver)
        assert resp.status_code == 200, f"a heartbeat failure changed the response: {resp.data!r}"
        assert resp.get_json() == {"accepted": 2, "captured": 2}
        assert [s for _, s in worker.submitted] == ["RELIANCE", "TCS"]
        assert sq.qsize() == 0

    def test_a_heartbeat_failure_cannot_break_the_disabled_branch_either(self, monkeypatch):
        _patch_heartbeat(monkeypatch, _HeartbeatSpy(explode=True))
        receiver, _, _ = _make_receiver(with_eod_scanner=True)
        resp = _post_eod(receiver)
        assert resp.status_code == 200
        assert resp.get_json()["detail"] == "watchlist disabled"

    # ── A5: the protected divergence is untouched ────────────────────────
    def test_the_eod_early_return_still_precedes_the_kill_and_window_gates(self, monkeypatch):
        """A5: adding the heartbeat must not disturb the early return that makes a
        17:00 capture possible. Both gates set to REFUSE; capture must still work."""
        spy = _HeartbeatSpy()
        _patch_heartbeat(monkeypatch, spy)
        worker = _CapturingEodWorker()
        receiver, sq, _ = _make_receiver(with_eod_scanner=True,
                                         kill_active=True, entry_allowed=False)
        receiver.set_eod_capture(worker)

        resp = _post_eod(receiver)
        assert resp.status_code == 200 and resp.get_json()["captured"] == 2
        assert spy.calls[0]["status"] == "SUCCESS"
        assert sq.qsize() == 0

    def test_an_unauthenticated_alert_records_no_heartbeat(self, monkeypatch):
        """Auth still runs first, so a rejected caller cannot write rows."""
        spy = _HeartbeatSpy()
        _patch_heartbeat(monkeypatch, spy)
        receiver, _, _ = _make_receiver(with_eod_scanner=True)
        receiver.set_eod_capture(_CapturingEodWorker())

        with receiver.app.test_client() as c:
            resp = _post(c, _eod_body(), scanner="pb01_breakout_retest")   # no credential
        assert resp.status_code == 401
        assert spy.calls == []
