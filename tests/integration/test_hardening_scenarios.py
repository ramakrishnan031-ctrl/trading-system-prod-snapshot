"""
tests/integration/test_hardening_scenarios.py -- Trading System v2

Q7: the Q4/Q5 capital-safety + pipeline hardenings, exercised against the WIRED
paper-mode system (real subsystems, SQLite in tmp_path) rather than in isolation.
Deterministic ONLY — no threads, no timing races, no wall-clock dependence.

Why these three here (and not the other two):
  * gate-8 (SECTOR_EXPOSURE reserved-not-placed fold-in): the unit test
    (test_gate8_sector_toctou.py) uses a FAKE fund_manager/store. This exercises the
    fix against the REAL wired capital stack — real FundManager.reserve(),
    real get_live_reservations(), real StateStore.sector_exposure() — so a wiring
    regression between those and RiskEngine.approve() is caught. LIVE in prod.
  * M-S5 (shadow-inning guard hoisted to continue_from_gate): the unit test drives a
    STUB SignalProcessor; this drives the REAL wired SignalProcessor with a real
    ShadowTracker. This is the hardening whose earlier regression individual-suite
    runs MISSED (only the cross-suite run caught it), so the wired path matters. LIVE
    in prod (shadow_tracker.enabled=true; EntryGate pullback path active).
  * M-S2 (QUEUE_FULL no longer poisons dedup): driven through the real WebhookReceiver
    Flask endpoint + real StateStore with a controlled small queue. LIVE in prod.
  * S4 boot fix (an authenticated /health must still let the system boot): added
    17-Jul-2026 after S4 halted production. Both sides' unit tests were green and
    correct — /health 401s anonymous callers, and 2xx means reachable — yet the system
    could not boot, because nothing owned the SEAM between them. Only a wired test can
    own it, and only if the receiver is built the way prod is CONFIGURED: with a secret.
    Every other wired case here passes secret_token=None, which is precisely why the
    401 that took prod down cannot occur anywhere else in this suite.

Deliberately NOT duplicated here (their deterministic coverage already lives at
real-component level, and a wired copy would add maintenance surface without new
coverage):
  * M-S2's own unit test (test_webhook_receiver.py) ALREADY uses a real receiver +
    real StateStore + Flask test_client — it is integration-grade. The wired test
    below is added anyway because M-S2 is a LIVE backpressure path worth having in the
    integration suite; it is intentionally the one small overlap.
  * M-S6 (RetestDiverter no double-order): the divert path is DORMANT in prod
    (wait_for_retest_enabled=false → RetestDiverter is never constructed), so a wired
    e2e would first have to ENABLE a path prod does not run. Its deterministic coverage
    is test_sr_v2_divert.py, including a 24-thread concurrency proof — stronger than any
    single-threaded wired copy. See docs/audit/deploy_behaviour_delta_prediction_14jul2026.md.
  * Migration guard (non-boot opener + pending migration -> REFUSES + CRITICAL sentinel):
    does not fire on THIS deploy at all (live DB v44 == EXPECTED 44, no pending migration), and
    the wired fixture builds a fresh v44 DB (nothing to refuse). Its deterministic scenario is
    test_migration_guard.py::test_blocked_migration_fires_critical_sentinel — a REAL StateStore
    that stamps an older version, opens non-boot, asserts MigrationNotPermitted AND verifies the
    CRITICAL sentinel file. That IS "a non-boot opener with a pending migration refuses + alerts".

W3 coverage: gate-8 / M-S5 / M-S2 are wired here (the LIVE hardenings); M-S6 and the migration
guard — neither fires under this deploy's config — are covered by the deterministic targeted unit
tests named above (per the work order's "targeted test instead / never ship flaky").
"""
from __future__ import annotations

import queue
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from capital.position_sizer import SizingResult
from core.market_windows import MarketWindows
from orders.shadow_tracker import Inning, ShadowTracker
from screening.entry_gate import WatchEntry
from signals.webhook_receiver import WebhookReceiver
from utils.startup_checks import check_webhook_endpoint

from tests.integration.conftest import (
    MOCK_TRIGGERED_AT,
    SCANNER_NAME,
    SystemContext,
    _logger,
    _make_webhook_config,
)
from tests.integration.test_full_signal_flow import _make_payload


# ═══════════════════════════════════════════════════════════════════════════════
# gate-8 — SECTOR_EXPOSURE folds reserved-not-placed reservations (FIX-185 TOCTOU)
#   Fixture fact: sector_lookup_fn -> "UNKNOWN" for every symbol, so all symbols share
#   one sector; max_sector_exposure_pct=0.40. A FundManager.reserve() with no trade row
#   is the exact reserved-not-placed state the old gate could not see.
# ═══════════════════════════════════════════════════════════════════════════════

class TestGate8SectorToctouWired:

    def test_effective_sector_margin_folds_live_reservation(self, wired_system):
        """_effective_sector_margin (the fold-in helper the gate consumes) must add a
        live reservation that has NO trade row yet, using the REAL fund_manager +
        store. DB truth alone (sector_exposure over trade rows) is 0 here; the effective
        value must be the reservation margin — proving the reserved-not-placed window is
        closed with real wiring, and that it can only HARDEN (max-floored)."""
        ctx = wired_system

        db_truth = ctx.store.sector_exposure("UNKNOWN")
        assert db_truth == pytest.approx(0.0), "no trade rows yet -> DB-truth sector margin is 0"

        res = ctx.fund_manager.reserve(
            symbol="SECTORHOG", qty=1000, price=100.0,
            intent="INTRADAY", signal_id="sig_hog_fold",
        )
        assert res.success, f"reserve failed: {res}"
        assert res.margin > 0

        effective = ctx.risk_engine._effective_sector_margin("UNKNOWN", db_truth)

        # The reservation is folded in (real get_live_reservations wiring) ...
        assert effective == pytest.approx(db_truth + res.margin)
        # ... and the fold-in can only harden, never loosen.
        assert effective > db_truth

    def test_approve_rejects_only_because_of_the_reserved_not_placed_fold_in(self, wired_system):
        """The full wired RiskEngine.approve() must REJECT a same-sector signal on
        SECTOR_EXPOSURE once a big reserved-not-placed reservation exists — and the SAME
        signal must PASS the gate when that reservation is absent. Running both halves in
        one test isolates the reserved-not-placed fold-in as the sole cause of the reject
        (this is the deploy behaviour-delta the prediction doc predicts is ~0 in prod)."""
        ctx = wired_system
        # F1 (16-Jul): exercise the ENFORCE gate (prod default is observe/log-only). This test
        # asserts the SECTOR_EXPOSURE gate REJECTS on the reserved-not-placed fold-in.
        ctx.risk_engine._sector_cap_mode = "enforce"
        total = ctx.fund_manager.get_snapshot().total
        price = 100.0

        # A small sizing result that alone is far under the 40% sector cap.
        m_new = round(0.05 * total, 2)
        sizing = SizingResult(
            success=True, qty=10, margin_required=m_new, risk_amount=round(m_new * 0.1, 2),
            bucket="intraday", constraint="test", reason="ok", breakdown={},
        )

        # (a) No reservation -> gate-8 is NOT the failing check (clean state -> approved).
        before = ctx.risk_engine.approve(
            symbol="NEWSIGA", side="BUY", intent="INTRADAY",
            sizing_result=sizing, signal_id="sig_gate8_before",
        )
        assert before.failed_check != "SECTOR_EXPOSURE", (
            f"unexpected sector reject with no reservation present: {before.reason}"
        )
        assert before.approved is True, f"clean-state approve should pass all gates: {before.reason}"

        # A reserved-not-placed same-sector position ~45% of total (reservable: intraday
        # bucket is 70% of total). No trade row is written -> the OLD gate could not see it.
        res_margin_target = 0.45 * total
        qty_hog = int(res_margin_target / (price * 0.2)) + 1
        res = ctx.fund_manager.reserve(
            symbol="SECTORHOG", qty=qty_hog, price=price,
            intent="INTRADAY", signal_id="sig_gate8_hog",
        )
        assert res.success, f"reserve failed: {res}"

        # (b) Same signal, reservation present -> effective (folded) margin breaches 40%.
        after = ctx.risk_engine.approve(
            symbol="NEWSIGB", side="BUY", intent="INTRADAY",
            sizing_result=sizing, signal_id="sig_gate8_after",
        )
        assert after.approved is False
        assert after.failed_check == "SECTOR_EXPOSURE", (
            f"expected SECTOR_EXPOSURE reject via the fold-in, got {after.failed_check}: {after.reason}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# M-S5 — shadow-inning re-entry guard enforced on the EntryGate resume path
#   The guard used to live only in _process_one; hoisted to continue_from_gate /
#   continue_from_retest. Here: the REAL wired SignalProcessor + a REAL ShadowTracker
#   with an active simulated inning (is_real=0) on RELIANCE.
# ═══════════════════════════════════════════════════════════════════════════════

class TestMs5ShadowInningWired:

    def _seed_processing_signal(self, ctx: SystemContext, signal_id: str, symbol: str) -> None:
        with ctx.store.transaction() as cur:
            cur.execute(
                """INSERT OR IGNORE INTO signals
                   (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                    expires_at, status, fingerprint, fingerprint_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (signal_id, symbol, SCANNER_NAME, SCANNER_NAME,
                 "2026-04-15 10:00:00", "2026-04-15 10:00:00", "2026-04-15 10:29:00",
                 "PROCESSING", f"fp_{signal_id}", "2026-04-15"),
            )

    def test_gate_resume_blocked_by_active_shadow_inning(self, wired_system):
        """A pullback entry released by EntryGate (continue_from_gate) on a symbol with an
        active shadow inning must be REJECTED (SHADOW_INNING_ACTIVE), not placed — else the
        real entry overlaps the simulated position. Proves the hoisted guard fires in the
        REAL wired SignalProcessor. RED on pre-fix code (guard was only in _process_one)."""
        ctx = wired_system
        symbol = "RELIANCE"
        signal_id = "sig_ms5_gate_wired"

        # Real ShadowTracker with one active SIMULATED inning (inning>=2, is_real=0) on the
        # symbol. is_tracking() only reads _active_innings + _enabled, so the feed/clock deps
        # are never touched -> MagicMock is safe and the test stays deterministic (no ticks).
        tracker = ShadowTracker(
            state_store=ctx.store, bus=ctx.bus, live_feed=MagicMock(),
            market_windows=MagicMock(), time_authority=MagicMock(),
            logger=_logger("shadow"), enabled=True,
        )
        tracker._active_innings["t_ms5_shadow"] = Inning(
            inning_number=2, trade_id="t_ms5_shadow", symbol=symbol, direction="LONG",
            entry_price=100.0, entry_ts=datetime(2026, 4, 15, 10, 0, 0), sl_price=98.0,
            tgt_price=104.0, exit_price=None, exit_ts=None, exit_reason=None,
            duration_sec=None, pnl_pct=None, pnl_per_share=None, is_real=False,
        )
        assert tracker.is_tracking(symbol) is True
        ctx.signal_processor._shadow_tracker = tracker

        self._seed_processing_signal(ctx, signal_id, symbol)

        entry = WatchEntry(
            signal_id=signal_id, symbol=symbol, direction="LONG",
            trigger_price=100.5, entry_price=100.0, sl_price=98.0, tgt_price=104.0,
            tolerance_pct=0.005, timeout_sec=300, strategy_name=SCANNER_NAME,
            tier="HIGH", scanner_name=SCANNER_NAME, intent="INTRADAY",
            added_at=datetime(2026, 4, 15, 10, 0, 0),
        )
        ctx.signal_processor.continue_from_gate(entry)

        # Rejected on the shadow-inning guard (which runs BEFORE strategy lookup / placement)
        row = ctx.store.fetch_one("SELECT status FROM signals WHERE signal_id = ?", (signal_id,))
        assert row["status"] == "REJECTED_SHADOW_INNING_ACTIVE", (
            f"gate resume was not blocked by the active shadow inning: {row['status']}"
        )
        # And nothing was placed for the symbol.
        trades = ctx.store.fetch_all("SELECT trade_id FROM trades WHERE symbol = ?", (symbol,))
        assert trades == [], "an order was placed despite the active shadow inning (overlap)"


# ═══════════════════════════════════════════════════════════════════════════════
# M-S2 — QUEUE_FULL is backpressure, not a duplicate: the sender's retry recovers
#   Real WebhookReceiver Flask endpoint + real StateStore + a controlled maxsize-1
#   queue that nothing drains (the wired SP drains a DIFFERENT queue).
# ═══════════════════════════════════════════════════════════════════════════════

class TestMs2QueueFullRecoveryWired:

    def test_queue_full_then_drain_then_retry_recovers(self, wired_system):
        """Fill the queue -> a signal returns QUEUE_FULL (503) and is marked QUEUE_FULL in
        the DB, with its fast-path dedup claim ROLLED BACK. Drain the queue, then the
        sender's retry of the SAME signal is ACCEPTED (the QUEUE_FULL row is recognised and
        RE-QUEUED, reusing its signal_id) — not bounced as DUPLICATE. RED on pre-fix code:
        the retry was a permanent DUPLICATE and backpressure recovery was impossible."""
        ctx = wired_system  # provides the MOCK_NOW now_ist patch + a real StateStore

        tiny_q: queue.Queue = queue.Queue(maxsize=1)
        receiver = WebhookReceiver(
            tiny_q, ctx.store, _make_webhook_config([SCANNER_NAME]),
            MarketWindows(holidays=set()), ctx.kill_switch, _logger("wh_ms2"),
            secret_token=None,
        )
        # Occupy the single slot so the next signal hits queue.Full.
        tiny_q.put_nowait(("filler_sig", SCANNER_NAME, "FILLER", 1.0, MOCK_TRIGGERED_AT))

        payload = _make_payload(symbol="TCS", price="3600.0")

        # 1) First delivery -> QUEUE_FULL, DB row QUEUE_FULL.
        with receiver.app.test_client() as client:
            r1 = client.post(f"/webhook/{SCANNER_NAME}", data=payload,
                             content_type="application/json")
        d1 = r1.get_json()
        assert d1["results"][0]["status"] == "QUEUE_FULL", d1
        row = ctx.store.fetch_one("SELECT signal_id, status FROM signals WHERE symbol = 'TCS'")
        assert row is not None and row["status"] == "QUEUE_FULL"

        # 2) Drain the queue (make room). The QUEUE_FULL signal was never queued.
        assert tiny_q.get_nowait()[2] == "FILLER"

        # 3) Retry the SAME signal -> ACCEPTED (re-queued), exactly one row, now QUEUED,
        #    signal_id reused (no orphan). Not DUPLICATE.
        with receiver.app.test_client() as client:
            r2 = client.post(f"/webhook/{SCANNER_NAME}", data=payload,
                             content_type="application/json")
        d2 = r2.get_json()
        assert d2["results"][0]["status"] == "ACCEPTED", d2

        rows = ctx.store.fetch_all("SELECT signal_id, status FROM signals WHERE symbol = 'TCS'")
        assert len(rows) == 1, f"expected exactly one TCS row (reused), got {len(rows)}"
        assert rows[0]["status"] == "QUEUED"
        assert rows[0]["signal_id"] == row["signal_id"], "retry must reuse the QUEUE_FULL row's signal_id"
        # The recovered signal is now actually in the queue.
        assert tiny_q.qsize() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# S4 boot fix — the REAL authenticated /health wired to the REAL boot self-check.
#   This is the seam that halted the system on 17-Jul-2026: S4's own unit tests
#   (/health 401s anonymous callers) and the boot check's unit tests (2xx ->
#   reachable) were BOTH green and BOTH correct, and the system still could not
#   boot. Nothing owned the join between them, so nothing tested it.
#
#   It stayed invisible because the DEFAULT wired fixture (wired_system) builds the
#   receiver with secret_token=None -- so S4's `if receiver._secret:` branch never runs
#   on that path, and the 401 that broke production cannot occur there. P2 (17-Jul) added
#   `wired_system_authenticated` (conftest.py) — the receiver built the way PROD is
#   configured — so this test drives the real wired receiver instead of hand-building one.
# ═══════════════════════════════════════════════════════════════════════════════

class TestS4AuthenticatedHealthStillBootsWired:

    def test_boot_self_check_passes_against_authenticated_health(self, wired_system_authenticated):
        """A secret IS configured (as in prod), so the unauthenticated boot self-check
        gets 401 -- and that must still count as "Flask is listening", because that is the
        only thing the check exists to prove. RED before the fix: reachable=False ->
        main.py:3242 fires _shutdown_event -> the system halts at boot and trades nothing
        (17-Jul-2026: 0 trades on a live trading day, first boot after S4 shipped)."""
        ctx = wired_system_authenticated
        receiver = ctx.receiver   # P2: built WITH a prod-like secret by the fixture
        assert ctx.webhook_secret, "fixture must configure a secret (auth ON)"

        # Drive the REAL /health exactly as main.py:3238 does: no token, no signature.
        with receiver.app.test_client() as client:
            resp = client.get("/health")

        # S4 itself is working as designed -- an anonymous caller is still denied.
        assert resp.status_code == 401, "S4 must keep denying anonymous /health callers"

        # ...and the REAL boot self-check must nonetheless call that reachable.
        result = check_webhook_endpoint(
            "http://127.0.0.1:5000/health",
            lambda url, timeout: (resp.status_code, resp.get_data(as_text=True)),
            _logger("s4_boot"),
        )
        assert result.reachable is True, (
            "main.py:3240-3242 fires _shutdown_event when this is False -- the system "
            "halts at boot and trades nothing"
        )
        assert result.status_code == 401
