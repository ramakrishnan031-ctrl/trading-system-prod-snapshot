"""
tests/unit/test_signal_processor.py

Validates signals/signal_processor.py against SP1-SP16 + SPW1-SPW10.

All injected dependencies are hand-rolled fakes (no MagicMock) so behaviour
is explicit and portable.  state_store uses a real in-memory SQLite DB so
status-update assertions are meaningful.

Run: python -m pytest tests/unit/test_signal_processor.py -v
Or:  python tests/unit/test_signal_processor.py  (standalone mode)
"""
from __future__ import annotations

import pytest
import queue
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.exceptions import BrokerError
from core.state_store import StateStore
from signals.signal_processor import SignalProcessor


# ---------------------------------------------------------------------------
# Strategy fixture (mock -- avoids importing Pydantic StrategyConfig)
# ---------------------------------------------------------------------------

@dataclass
class _MockStrategy:
    name: str = "gap_go_long_v1"
    direction: str = "LONG"
    intent: str = "INTRADAY"
    entry_method: str = "MARKET"
    entry_offset_pct: float = 0.0
    sl_method: str = "FIXED_PCT"
    sl_pct: float = 0.02
    sl_min_pct: float = 0.003
    sl_max_pct: float = 0.05
    tgt_method: str = "RISK_REWARD"
    tgt_pct: float = 0.04
    tgt_risk_reward: float = 2.0
    lot_size: int = 1
    min_score: int = 0
    min_volume_surge: float = 1.3
    min_adr_pct: float = 0.005
    max_spread_pct: float = 0.005
    pullback_wait_enabled: bool = False


_BUY_STRATEGY = _MockStrategy(name="gap_go_long_v1", direction="LONG")
_SELL_STRATEGY = _MockStrategy(name="gap_go_short_v1", direction="SHORT")
_ATR_STRATEGY = _MockStrategy(name="atr_v1", direction="LONG", sl_method="ATR", sl_pct=0.02)

_STRATEGIES = {
    "gap_go_long_v1": _BUY_STRATEGY,
    "gap_go_short_v1": _SELL_STRATEGY,
    "atr_v1": _ATR_STRATEGY,
}
_SCAN_WEBHOOK_MAP = {
    "gap_go_long":  {"strategy": "gap_go_long_v1"},
    "gap_go_short": {"strategy": "gap_go_short_v1"},
    "atr_scanner":  {"strategy": "atr_v1"},
}


# ---------------------------------------------------------------------------
# Screening result mock
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _MockScreeningResult:
    passed: bool = True
    status: str = "PASSED"
    score: int = 75
    tier: str = "HIGH"
    rejected_step: object = None
    step_results: dict = field(default_factory=dict)
    step_statuses: dict = field(default_factory=dict)
    error_steps: list = field(default_factory=list)
    latencies_ms: dict = field(default_factory=dict)
    market_data_snapshot: dict = field(default_factory=dict)


class _MockScreener:
    """
    Mock SecondaryScreener.  By default returns PASSED (HIGH tier).

    If state_store is provided, mirrors P18 behaviour by writing signal
    status to the store -- so screener-rejected-path tests can verify
    the final DB status.
    """

    def __init__(self, result=None, state_store=None):
        self._result = result if result is not None else _MockScreeningResult()
        self._store = state_store
        self.calls: List[dict] = []

    def screen(
        self,
        signal_id: str,
        symbol: str,
        scanner_name: str,
        trigger_price: float,
        triggered_at,
        *,
        direction: str,
        intent: str,
        strategy,
        market_data=None,
    ):
        self.calls.append({
            "signal_id": signal_id,
            "symbol": symbol,
            "scanner_name": scanner_name,
            "direction": direction,
            "tier": self._result.tier,
        })
        # Mirror P18: screener writes signal status (PASSED / REJECTED_* / SKIPPED_*)
        if self._store is not None:
            self._store.update_signal_status(signal_id, self._result.status)
        return self._result


# ---------------------------------------------------------------------------
# Other test doubles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _SizingResult:
    success: bool
    qty: int = 10
    margin_required: float = 5000.0
    risk_amount: float = 500.0
    bucket: str = "intraday"
    constraint: str = "RISK"
    reason: str = "ok"
    breakdown: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _ApprovalResult:
    approved: bool
    reason: str = "ok"
    failed_check: str = ""
    checks_run: List[str] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _ReservationResult:
    success: bool
    reservation_id: str = "res_abc123"
    margin: float = 5000.0
    bucket: str = "intraday"
    reason_if_failed: str = ""


class _MockPositionSizer:
    def __init__(self, result=None, raise_exc=None):
        self._result = result or _SizingResult(success=True)
        self._raise = raise_exc
        self.calls: List[dict] = []

    def calculate(self, symbol, direction, entry_price, sl_price, intent,
                  score_tier="MEDIUM", lot_size=1, perf_weight=1.0):
        self.calls.append({"symbol": symbol, "score_tier": score_tier,
                           "direction": direction})
        if self._raise:
            raise self._raise
        return self._result


class _MockRiskEngine:
    def __init__(self, result=None, raise_exc=None):
        self._result = result or _ApprovalResult(approved=True)
        self._raise = raise_exc
        self.calls: List[dict] = []

    def approve(self, symbol, direction, intent, sizing_result, signal_id,
                processor_in_flight_count: int = 0):
        self.calls.append({
            "symbol": symbol,
            "signal_id": signal_id,
            "processor_in_flight_count": processor_in_flight_count,
        })
        if self._raise:
            raise self._raise
        return self._result


class _MockFundManager:
    def __init__(self, reserve_result=None, raise_exc=None):
        self._reserve_result = reserve_result or _ReservationResult(success=True)
        self._raise = raise_exc
        self.released: List[str] = []
        # Audit 1.2: signal_processor wraps approve+reserve in
        # `with fm.portfolio_lock:`. Production FundManager exposes its
        # internal RLock via this property; mocks return any object that
        # supports __enter__/__exit__.
        self._lock = threading.RLock()

    @property
    def portfolio_lock(self):
        return self._lock

    def reserve(self, symbol, qty, price, intent, signal_id=None):
        if self._raise:
            raise self._raise
        return self._reserve_result

    def release(self, reservation_id, reason=""):
        self.released.append(reservation_id)
        return True

    def get_snapshot(self):
        class _Snap:
            total = 1_000_000.0
            intraday_avail = 500_000.0
            positional_avail = 500_000.0
            daily_realized_pnl = 0.0
        return _Snap()

    def get_total_unrealized_mtm(self):
        """FIX-035: Return total unrealized MTM."""
        return 0.0


class _MockOrderPlacer:
    def __init__(self, raise_exc=None):
        self._raise = raise_exc
        self.calls: List[dict] = []

    def place(self, *, symbol, side, qty, entry_price, sl_price, intent,
              signal_id, reservation_id, strategy="", tgt_price=None,
              release_ltp=None, signal_trigger_price=None):  # FIX-128
        if self._raise:
            raise self._raise
        self.calls.append({
            "signal_id": signal_id,
            "symbol": symbol,
            "tgt_price": tgt_price,
        })


class _MockKillSwitch:
    def __init__(self, active=False):
        self._active = active
        self.failure_count = 0

    def is_active(self, intent="entry"):
        return self._active

    def record_api_failure(self):
        self.failure_count += 1

    def record_success(self):
        pass


class _MockMarketWindows:
    def __init__(self, entry_allowed=True):
        self._allowed = entry_allowed

    def is_entry_allowed(self, now):
        return self._allowed

    def is_entry_allowed_for_strategy(self, now, strategy):
        # CFG-5 (2026-04-26 audit): mocks delegate per-strategy to global.
        return self._allowed


class _MockBus:
    def publish(self, event):
        pass


class _NullLogger:
    def __init__(self):
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.infos: List[str] = []

    def debug(self, *a, **kw): pass
    def info(self, msg, *a, **kw): self.infos.append(str(msg))
    def warning(self, msg, *a, **kw): self.warnings.append(str(msg))
    def error(self, msg, *a, **kw): self.errors.append(str(msg))
    def critical(self, *a, **kw): pass


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_store() -> tuple:
    td = tempfile.mkdtemp()
    store = StateStore(Path(td) / "test.db")
    return store, td


def _insert_queued_signal(store: StateStore, signal_id: str,
                          symbol: str = "RELIANCE",
                          scanner: str = "gap_go_long") -> None:
    """Insert a minimal signals row with status=QUEUED."""
    today = datetime.now().strftime("%Y-%m-%d")
    fp = f"{scanner}|{symbol}|{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    import hashlib
    fingerprint = hashlib.sha256(fp.encode()).hexdigest()
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT OR IGNORE INTO signals
              (signal_id, symbol, scanner, strategy,
               triggered_at, received_at, expires_at,
               status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, symbol, scanner, scanner,
                datetime.now().isoformat(), datetime.now().isoformat(),
                datetime.now().isoformat(),
                "QUEUED", fingerprint, today,
            ),
        )


def _make_proc(
    sq=None,
    store=None,
    fm=None,
    sizer=None,
    risk=None,
    ks=None,
    placer=None,
    mw=None,
    strategies=None,
    scan_webhook_map=None,
    screener=None,
    quality_scorer=None,
    in_flight_fn=None,
    logger=None,
    worker_count=3,
    drain_poll_sec=0.02,
    signal_expiry_sec=60,
    shadow_tracker=None,
):
    if sq is None:
        sq = queue.Queue(maxsize=100)
    if store is None:
        store, _ = _make_store()
    if fm is None:
        fm = _MockFundManager()
    if sizer is None:
        sizer = _MockPositionSizer()
    if risk is None:
        risk = _MockRiskEngine()
    if ks is None:
        ks = _MockKillSwitch(active=False)
    if mw is None:
        mw = _MockMarketWindows(entry_allowed=True)
    if strategies is None:
        strategies = _STRATEGIES
    if scan_webhook_map is None:
        scan_webhook_map = _SCAN_WEBHOOK_MAP
    if screener is None:
        screener = _MockScreener(state_store=store)
    if quality_scorer is None:
        quality_scorer = object()   # opaque placeholder; not called by processor
    if logger is None:
        logger = _NullLogger()
    bus = _MockBus()

    proc = SignalProcessor(
        signal_queue=sq,
        state_store=store,
        bus=bus,
        fund_manager=fm,
        position_sizer=sizer,
        risk_engine=risk,
        kill_switch=ks,
        market_windows=mw,
        strategies=strategies,
        scan_webhook_map=scan_webhook_map,
        secondary_screener=screener,
        quality_scorer=quality_scorer,
        order_placer=placer,
        logger=logger,
        in_flight_release_fn=in_flight_fn,
        worker_count=worker_count,
        drain_poll_sec=drain_poll_sec,
        signal_expiry_sec=signal_expiry_sec,
        shadow_tracker=shadow_tracker,
        quote_fn=None,  # FIX-067: not needed for these tests
    )
    return proc, sq, store


def _now_tup(signal_id="sig_001", scanner="gap_go_long", symbol="RELIANCE",
             price=2500.0, age_sec=0):
    """Return a signal tuple with triggered_at = now - age_sec."""
    triggered_at = datetime.now() - timedelta(seconds=age_sec)
    return (signal_id, scanner, symbol, price, triggered_at)


def _run_one(proc, sig_tuple, store=None, wait_sec=2.0):
    """Start processor, enqueue one signal, stop (drains), return store row."""
    import time
    proc.start()
    proc._queue.put(sig_tuple)
    # FIX-070: Brief wait before stop() to let worker process signal
    # before shutdown event is set (otherwise REJECTED_SHUTDOWN)
    time.sleep(0.1)
    proc.stop()
    if store:
        return store.fetch_one(
            "SELECT status, rejection_reason FROM signals WHERE signal_id = ?",
            (sig_tuple[0],),
        )
    return None


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

def test_start_launches_dispatcher_and_workers():
    """start() transitions is_running() to True; stop() back to False."""
    proc, _, _ = _make_proc()
    assert not proc.is_running()
    proc.start()
    assert proc.is_running()
    proc.stop()
    assert not proc.is_running()
    print("  OK start/stop/is_running lifecycle correct")


def test_stop_drains_within_5s():
    """stop() completes in < 5 seconds even with signals queued."""
    proc, sq, _ = _make_proc()
    proc.start()
    for i in range(20):
        sq.put(_now_tup(f"sig_{i:03d}"))
    t0 = time.monotonic()
    proc.stop()
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"stop() took {elapsed:.2f}s"
    print(f"  OK stop() drained 20 signals in {elapsed:.3f}s")


def test_is_running_before_start():
    """is_running() is False before start()."""
    proc, _, _ = _make_proc()
    assert not proc.is_running()
    print("  OK is_running() False before start()")


# ---------------------------------------------------------------------------
# Full pipeline (status progression)
# ---------------------------------------------------------------------------

def test_full_pipeline_queued_to_processed():
    """Full pipeline with placer: status progresses QUEUED -> PROCESSED."""
    store, _ = _make_store()
    sig_id = "sig_full_001"
    _insert_queued_signal(store, sig_id)

    placer = _MockOrderPlacer()
    screener = _MockScreener(state_store=store)
    proc, sq, _ = _make_proc(store=store, placer=placer, screener=screener)

    row = _run_one(proc, _now_tup(sig_id), store=store)

    assert row is not None, "Signal row not found after processing"
    assert row["status"] == "PROCESSED", f"Expected PROCESSED, got {row['status']}"
    assert len(placer.calls) == 1
    assert placer.calls[0]["signal_id"] == sig_id
    print("  OK full pipeline -> PROCESSED, placer called")


def test_full_pipeline_no_placer_processed_no_placer():
    """order_placer=None -> PROCESSED_NO_PLACER, reservation released."""
    store, _ = _make_store()
    sig_id = "sig_noplac_001"
    _insert_queued_signal(store, sig_id)

    fm = _MockFundManager()
    screener = _MockScreener(state_store=store)
    proc, sq, _ = _make_proc(store=store, fm=fm, placer=None, screener=screener)

    row = _run_one(proc, _now_tup(sig_id), store=store)

    assert row["status"] == "PROCESSED_NO_PLACER", row["status"]
    assert "res_abc123" in fm.released, f"Reservation not released: {fm.released}"
    print("  OK no placer -> PROCESSED_NO_PLACER, reservation released")


# ---------------------------------------------------------------------------
# Pipeline rejection tests
# ---------------------------------------------------------------------------

def _assert_rejected(store, sig_id, expected_status_prefix):
    row = store.fetch_one(
        "SELECT status, rejection_reason FROM signals WHERE signal_id = ?", (sig_id,)
    )
    assert row is not None, f"Signal row missing for {sig_id}"
    status = row["status"]
    assert status.startswith(expected_status_prefix), \
        f"Expected status starting with {expected_status_prefix!r}, got {status!r}"
    return row


def test_kill_switch_active_rejects():
    """Kill switch active -> REJECTED_KILL_SWITCH."""
    store, _ = _make_store()
    sig_id = "sig_ks_001"
    _insert_queued_signal(store, sig_id)

    ks = _MockKillSwitch(active=True)
    proc, _, _ = _make_proc(store=store, ks=ks)

    _run_one(proc, _now_tup(sig_id), store=store)
    _assert_rejected(store, sig_id, "REJECTED_KILL_SWITCH")
    print("  OK kill_switch active -> REJECTED_KILL_SWITCH")


def test_outside_entry_window_rejects():
    """Outside entry window -> REJECTED_OUTSIDE_ENTRY_WINDOW."""
    store, _ = _make_store()
    sig_id = "sig_oew_001"
    _insert_queued_signal(store, sig_id)

    mw = _MockMarketWindows(entry_allowed=False)
    proc, _, _ = _make_proc(store=store, mw=mw)

    _run_one(proc, _now_tup(sig_id), store=store)
    _assert_rejected(store, sig_id, "REJECTED_OUTSIDE_ENTRY_WINDOW")
    print("  OK outside entry window -> REJECTED_OUTSIDE_ENTRY_WINDOW")


def test_expired_signal_rejects():
    """Signal older than expiry_sec -> REJECTED_EXPIRED."""
    store, _ = _make_store()
    sig_id = "sig_exp_001"
    _insert_queued_signal(store, sig_id)

    proc, _, _ = _make_proc(store=store, signal_expiry_sec=30)

    _run_one(proc, _now_tup(sig_id, age_sec=90), store=store)
    _assert_rejected(store, sig_id, "REJECTED_EXPIRED")
    print("  OK expired signal -> REJECTED_EXPIRED")


def test_unknown_strategy_rejects():
    """Scanner not in scan_webhook_map -> REJECTED_UNKNOWN_STRATEGY."""
    store, _ = _make_store()
    sig_id = "sig_unk_001"
    _insert_queued_signal(store, sig_id, scanner="no_such_scanner")

    proc, _, _ = _make_proc(store=store)

    tup = ("sig_unk_001", "no_such_scanner", "RELIANCE", 2500.0, datetime.now())
    _run_one(proc, tup, store=store)
    _assert_rejected(store, sig_id, "REJECTED_UNKNOWN_STRATEGY")
    print("  OK unknown scanner -> REJECTED_UNKNOWN_STRATEGY")


def test_unknown_strategy_name_rejects():
    """strategy name in map not found in strategies dict -> REJECTED_UNKNOWN_STRATEGY."""
    store, _ = _make_store()
    sig_id = "sig_unk_002"
    _insert_queued_signal(store, sig_id, scanner="missing_strat_scanner")

    bad_map = {"missing_strat_scanner": {"strategy": "does_not_exist"}}
    proc, _, _ = _make_proc(store=store, scan_webhook_map=bad_map)

    tup = ("sig_unk_002", "missing_strat_scanner", "RELIANCE", 2500.0, datetime.now())
    _run_one(proc, tup, store=store)
    _assert_rejected(store, sig_id, "REJECTED_UNKNOWN_STRATEGY")
    print("  OK strategy name not in strategies dict -> REJECTED_UNKNOWN_STRATEGY")


def test_sizer_failure_rejects():
    """Sizer returns success=False -> REJECTED_SIZING_<constraint>."""
    store, _ = _make_store()
    sig_id = "sig_sz_001"
    _insert_queued_signal(store, sig_id)

    sizer = _MockPositionSizer(
        result=_SizingResult(success=False, qty=0, constraint="BELOW_MIN",
                             reason="qty below minimum threshold")
    )
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(store=store, sizer=sizer, screener=screener)

    _run_one(proc, _now_tup(sig_id), store=store)
    _assert_rejected(store, sig_id, "REJECTED_SIZING_BELOW_MIN")
    print("  OK sizer failure -> REJECTED_SIZING_BELOW_MIN")


def test_risk_engine_rejection():
    """Risk engine rejects -> REJECTED_<failed_check>."""
    store, _ = _make_store()
    sig_id = "sig_re_001"
    _insert_queued_signal(store, sig_id)

    risk = _MockRiskEngine(result=_ApprovalResult(
        approved=False,
        failed_check="MAX_OPEN_POSITIONS",
        reason="Too many open positions",
    ))
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(store=store, risk=risk, screener=screener)

    _run_one(proc, _now_tup(sig_id), store=store)
    _assert_rejected(store, sig_id, "REJECTED_MAX_OPEN_POSITIONS")
    print("  OK risk engine rejection -> REJECTED_MAX_OPEN_POSITIONS")


def test_reserve_failure_rejects():
    """fund_manager.reserve returns success=False -> REJECTED_RESERVE_FAILED."""
    store, _ = _make_store()
    sig_id = "sig_res_001"
    _insert_queued_signal(store, sig_id)

    fm = _MockFundManager(reserve_result=_ReservationResult(
        success=False, reservation_id="", reason_if_failed="insufficient capital"
    ))
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(store=store, fm=fm, screener=screener)

    _run_one(proc, _now_tup(sig_id), store=store)
    _assert_rejected(store, sig_id, "REJECTED_RESERVE_FAILED")
    print("  OK reserve failure -> REJECTED_RESERVE_FAILED")


def test_order_placer_raises_placement_failed():
    """order_placer.place() raises -> PLACEMENT_FAILED, reservation released."""
    store, _ = _make_store()
    sig_id = "sig_pf_001"
    _insert_queued_signal(store, sig_id)

    fm = _MockFundManager()
    placer = _MockOrderPlacer(raise_exc=RuntimeError("broker refused"))
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(store=store, fm=fm, placer=placer, screener=screener)

    _run_one(proc, _now_tup(sig_id), store=store)

    row = store.fetch_one("SELECT status FROM signals WHERE signal_id = ?", (sig_id,))
    assert row["status"] == "PLACEMENT_FAILED", row["status"]
    assert "res_abc123" in fm.released
    print("  OK placer raises -> PLACEMENT_FAILED, reservation released")


def test_unexpected_exception_placement_failed():
    """Unexpected exception mid-pipeline -> PLACEMENT_FAILED, in_flight cleared."""
    store, _ = _make_store()
    sig_id = "sig_ue_001"
    _insert_queued_signal(store, sig_id)

    released = []

    sizer = _MockPositionSizer(raise_exc=RuntimeError("disk full"))
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(
        store=store, sizer=sizer, screener=screener,
        in_flight_fn=lambda sym: released.append(sym),
    )

    _run_one(proc, _now_tup(sig_id), store=store)

    row = store.fetch_one("SELECT status FROM signals WHERE signal_id = ?", (sig_id,))
    assert row["status"] in ("PLACEMENT_FAILED", "REJECTED_SIZING_BROKER_ERROR"), row["status"]
    assert "RELIANCE" in released, f"in_flight not released: {released}"
    print("  OK unexpected exception -> PLACEMENT_FAILED, in_flight released")


# ---------------------------------------------------------------------------
# Screener-specific pipeline tests (SPW3, SPW7, SPW8)
# ---------------------------------------------------------------------------

def test_screener_rejects_signal():
    """Screener passes=False -> pipeline stops, in_flight released, no sizing."""
    store, _ = _make_store()
    sig_id = "sig_scr_rej_001"
    _insert_queued_signal(store, sig_id)

    released = []
    rejected_result = _MockScreeningResult(
        passed=False, status="REJECTED_VOLUME_SURGE", tier="LOW"
    )
    screener = _MockScreener(result=rejected_result, state_store=store)
    sizer = _MockPositionSizer()

    proc, _, _ = _make_proc(
        store=store, screener=screener, sizer=sizer,
        in_flight_fn=lambda sym: released.append(sym),
    )

    _run_one(proc, _now_tup(sig_id), store=store)

    # Screener wrote REJECTED_VOLUME_SURGE to store (P18)
    row = store.fetch_one("SELECT status FROM signals WHERE signal_id = ?", (sig_id,))
    assert row["status"] == "REJECTED_VOLUME_SURGE", row["status"]
    # Sizer must NOT have been called
    assert len(sizer.calls) == 0, "Sizer called after screener rejection"
    # in_flight must be released
    assert "RELIANCE" in released, f"in_flight not released: {released}"
    print("  OK screener rejects -> REJECTED_VOLUME_SURGE, no sizing, in_flight released")


def test_screener_skipped_logs_warning():
    """Screener SKIPPED_* -> WARNING logged, pipeline stops, no sizing, in_flight released."""
    store, _ = _make_store()
    sig_id = "sig_scr_skip_001"
    _insert_queued_signal(store, sig_id)

    released = []
    log = _NullLogger()
    skipped_result = _MockScreeningResult(
        passed=False, status="SKIPPED_QUOTE_UNAVAILABLE", tier="LOW"
    )
    screener = _MockScreener(result=skipped_result, state_store=store)
    sizer = _MockPositionSizer()

    proc, _, _ = _make_proc(
        store=store, screener=screener, sizer=sizer, logger=log,
        in_flight_fn=lambda sym: released.append(sym),
    )

    _run_one(proc, _now_tup(sig_id), store=store)

    # Warning logged
    assert any("SKIPPED" in w for w in log.warnings), f"No SKIPPED warning: {log.warnings}"
    # Sizer not called
    assert len(sizer.calls) == 0, "Sizer called after screener SKIPPED"
    # in_flight released
    assert "RELIANCE" in released, f"in_flight not released: {released}"
    print("  OK screener SKIPPED -> WARNING logged, no sizing, in_flight released")


def test_screener_passed_uses_tier_for_sizing():
    """Screener passes with tier=HIGH -> sizer.calculate called with 'HIGH'."""
    store, _ = _make_store()
    sig_id = "sig_scr_pass_001"
    _insert_queued_signal(store, sig_id)

    high_result = _MockScreeningResult(passed=True, status="PASSED", tier="HIGH")
    screener = _MockScreener(result=high_result, state_store=store)
    sizer = _MockPositionSizer()

    proc, _, _ = _make_proc(store=store, screener=screener, sizer=sizer)

    _run_one(proc, _now_tup(sig_id), store=store)

    assert len(sizer.calls) == 1, f"Sizer not called: {sizer.calls}"
    assert sizer.calls[0]["score_tier"] == "HIGH", f"Wrong tier: {sizer.calls[0]}"
    print("  OK screener PASSED tier=HIGH -> sizer called with HIGH")


def test_screener_passes_direction_from_strategy():
    """Screener.screen() receives direction from strategy_obj.direction (LONG/SHORT)."""
    store, _ = _make_store()
    sig_id = "sig_dir_001"
    _insert_queued_signal(store, sig_id, scanner="gap_go_short")

    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(store=store, screener=screener)

    tup = ("sig_dir_001", "gap_go_short", "RELIANCE", 2500.0, datetime.now())
    _run_one(proc, tup, store=store)

    assert len(screener.calls) >= 1, "Screener not called"
    assert screener.calls[0]["direction"] == "SHORT", \
        f"Expected SHORT, got {screener.calls[0]['direction']}"
    print("  OK screener called with direction=SHORT from strategy_obj")


def test_no_double_write_screener_rejected():
    """P18: processor does NOT write REJECTED status when screener already rejected."""
    store, _ = _make_store()
    sig_id = "sig_nodbl_001"
    _insert_queued_signal(store, sig_id)

    rejected_result = _MockScreeningResult(
        passed=False, status="REJECTED_SCORE_40", tier="LOW"
    )
    screener = _MockScreener(result=rejected_result, state_store=store)

    proc, _, _ = _make_proc(store=store, screener=screener)
    _run_one(proc, _now_tup(sig_id), store=store)

    # The screener wrote REJECTED_SCORE_40; processor must not overwrite
    row = store.fetch_one("SELECT status FROM signals WHERE signal_id = ?", (sig_id,))
    assert row["status"] == "REJECTED_SCORE_40", \
        f"Expected REJECTED_SCORE_40, got {row['status']}"
    print("  OK no double-write: screener status REJECTED_SCORE_40 preserved")


def test_in_flight_released_on_screener_skipped():
    """in_flight release fn called when screener returns SKIPPED_* (SPW8)."""
    released = []
    store, _ = _make_store()
    sig_id = "sig_if_skip_001"
    _insert_queued_signal(store, sig_id)

    skipped = _MockScreeningResult(passed=False, status="SKIPPED_EXECUTOR_ERROR", tier="LOW")
    screener = _MockScreener(result=skipped, state_store=store)

    proc, _, _ = _make_proc(
        store=store, screener=screener,
        in_flight_fn=lambda sym: released.append(sym),
    )

    _run_one(proc, _now_tup(sig_id), store=store)

    assert "RELIANCE" in released, f"in_flight not released on SKIPPED: {released}"
    print("  OK in_flight released on screener SKIPPED path (SPW8)")


def test_in_flight_released_on_screener_rejected():
    """in_flight release fn called when screener rejects (SPW8)."""
    released = []
    store, _ = _make_store()
    sig_id = "sig_if_rej_001"
    _insert_queued_signal(store, sig_id)

    rejected = _MockScreeningResult(passed=False, status="REJECTED_CIRCUIT", tier="LOW")
    screener = _MockScreener(result=rejected, state_store=store)

    proc, _, _ = _make_proc(
        store=store, screener=screener,
        in_flight_fn=lambda sym: released.append(sym),
    )

    _run_one(proc, _now_tup(sig_id), store=store)

    assert "RELIANCE" in released, f"in_flight not released on screener rejected: {released}"
    print("  OK in_flight released on screener REJECTED path (SPW8)")


# ---------------------------------------------------------------------------
# Audit #21 fix: in_flight released ALWAYS via finally
# ---------------------------------------------------------------------------

def test_in_flight_released_on_rejection():
    """in_flight release fn called even when signal is rejected (audit #21)."""
    released = []
    store, _ = _make_store()
    sig_id = "sig_if_001"
    _insert_queued_signal(store, sig_id)

    ks = _MockKillSwitch(active=True)
    proc, _, _ = _make_proc(
        store=store, ks=ks,
        in_flight_fn=lambda sym: released.append(sym),
    )

    _run_one(proc, _now_tup(sig_id), store=store)

    assert "RELIANCE" in released, f"in_flight not released on rejection: {released}"
    print("  OK in_flight released after rejection (audit #21 fix)")


def test_in_flight_released_on_success():
    """in_flight release fn called on successful processing."""
    released = []
    store, _ = _make_store()
    sig_id = "sig_ifs_001"
    _insert_queued_signal(store, sig_id)

    placer = _MockOrderPlacer()
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(
        store=store, placer=placer, screener=screener,
        in_flight_fn=lambda sym: released.append(sym),
    )

    _run_one(proc, _now_tup(sig_id), store=store)

    assert "RELIANCE" in released, f"in_flight not released on success: {released}"
    print("  OK in_flight released after success (audit #21 fix)")


def test_in_flight_released_on_placer_exception():
    """in_flight release fn called when placer raises (audit #21)."""
    released = []
    store, _ = _make_store()
    sig_id = "sig_ifp_001"
    _insert_queued_signal(store, sig_id)

    placer = _MockOrderPlacer(raise_exc=RuntimeError("network error"))
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(
        store=store, placer=placer, screener=screener,
        in_flight_fn=lambda sym: released.append(sym),
    )

    _run_one(proc, _now_tup(sig_id), store=store)

    assert "RELIANCE" in released, f"in_flight not released on placer exception: {released}"
    print("  OK in_flight released when placer raises (audit #21 fix)")


# ---------------------------------------------------------------------------
# Price derivation tests (SPW4)
# ---------------------------------------------------------------------------

def test_derive_prices_market_entry_long():
    """MARKET entry LONG: entry_price == trigger_price."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="LONG", entry_method="MARKET", sl_pct=0.02)
    entry, sl = proc._derive_prices(2500.0, strategy)
    assert abs(entry - 2500.0) < 0.01, f"Expected entry=2500.0, got {entry}"
    assert abs(sl - 2450.0) < 0.01, f"Expected sl=2450.0, got {sl}"
    print(f"  OK MARKET LONG entry={entry}, sl={sl}")


def test_derive_prices_limit_entry_long():
    """LIMIT entry LONG: entry = trigger * (1 - offset_pct)."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="LONG", entry_method="LIMIT",
                             entry_offset_pct=0.005, sl_pct=0.02)
    entry, sl = proc._derive_prices(2000.0, strategy)
    assert abs(entry - 1990.0) < 0.01, f"Expected 1990.0, got {entry}"
    print(f"  OK LIMIT LONG entry={entry} (trigger*(1-0.005))")


def test_derive_prices_limit_entry_short():
    """LIMIT entry SHORT: entry = trigger * (1 + offset_pct)."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="SHORT", entry_method="LIMIT",
                             entry_offset_pct=0.005, sl_pct=0.02)
    entry, sl = proc._derive_prices(2000.0, strategy)
    assert abs(entry - 2010.0) < 0.01, f"Expected 2010.0, got {entry}"
    print(f"  OK LIMIT SHORT entry={entry} (trigger*(1+0.005))")


def test_derive_prices_sl_long():
    """LONG FIXED_PCT: sl = entry * (1 - sl_pct)."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="LONG", entry_method="MARKET", sl_pct=0.02)
    entry, sl = proc._derive_prices(1000.0, strategy)
    assert abs(sl - 980.0) < 0.01, f"Expected 980.0, got {sl}"
    print(f"  OK LONG sl={sl} (entry*(1-0.02))")


def test_derive_prices_sl_short():
    """SHORT FIXED_PCT: sl = entry * (1 + sl_pct)."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="SHORT", entry_method="MARKET", sl_pct=0.02)
    entry, sl = proc._derive_prices(1000.0, strategy)
    assert abs(sl - 1020.0) < 0.01, f"Expected 1020.0, got {sl}"
    print(f"  OK SHORT sl={sl} (entry*(1+0.02))")


def test_derive_prices_atr_fallback_warns():
    """sl_method=ATR without provider -> WARNING logged + FIXED_PCT fallback."""
    log = _NullLogger()
    proc, _, _ = _make_proc(logger=log)
    strategy = _MockStrategy(direction="LONG", sl_method="ATR", sl_pct=0.03)
    entry, sl = proc._derive_prices(1000.0, strategy)
    assert abs(sl - 970.0) < 0.01, f"Expected 970.0, got {sl}"
    assert any("ATR" in w for w in log.warnings), f"No ATR warning: {log.warnings}"
    print(f"  OK ATR fallback -> FIXED_PCT, sl={sl}, warning logged")


def test_e1_atr_fallback_with_zero_sl_pct_rejects():
    """E.1 (2026-04-25): sl_method=ATR + sl_pct=0.0 (e.g.
    positional_momentum_long.yaml) falls back to FIXED_PCT but the
    fallback has no usable sl distance. Pre-fix the bounds enforcement
    silently widened to sl_min_pct, masking the missing ATR input.
    Post-fix: explicit rejection with REJECTED_ZERO_SL so the operator
    notices the YAML is incomplete (set positive sl_pct fallback or
    implement ATR)."""
    log = _NullLogger()
    proc, _, _ = _make_proc(logger=log)
    strategy = _MockStrategy(
        name="positional_momentum_long_v1",
        direction="LONG", sl_method="ATR", sl_pct=0.0,
    )
    from signals.signal_processor import _PipelineReject
    raised = False
    try:
        proc._derive_prices(1000.0, strategy)
    except _PipelineReject as exc:
        raised = True
        assert exc.check == "ZERO_SL", f"Expected ZERO_SL, got {exc.check}"
        assert "FIXED_PCT" in exc.reason
        assert "positional_momentum_long_v1" in exc.reason
    assert raised, "Expected _PipelineReject(ZERO_SL) on ATR + sl_pct=0.0"
    print("  OK E.1: ATR fallback + sl_pct=0.0 -> REJECTED_ZERO_SL")


def test_e1_fixed_pct_with_zero_sl_pct_rejects():
    """E.1: defense in depth -- if a strategy somehow lands here with
    sl_method=FIXED_PCT and sl_pct=0.0 (schema validator should catch this
    at YAML load, but a malformed in-memory _MockStrategy or future bug
    must still trip the rejection in the pricing path)."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(
        name="bad_strategy_v1", direction="LONG",
        sl_method="FIXED_PCT", sl_pct=0.0,
    )
    from signals.signal_processor import _PipelineReject
    raised = False
    try:
        proc._derive_prices(1000.0, strategy)
    except _PipelineReject as exc:
        raised = True
        assert exc.check == "ZERO_SL"
    assert raised
    print("  OK E.1: FIXED_PCT + sl_pct=0.0 (defense in depth) -> ZERO_SL")


def test_e1_negative_sl_pct_also_rejected():
    """E.1: sl_pct < 0 yields negative or above-entry SL (catastrophic).
    Reject with the same gate."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(
        name="negative_sl_v1", direction="LONG",
        sl_method="FIXED_PCT", sl_pct=-0.01,
    )
    from signals.signal_processor import _PipelineReject
    raised = False
    try:
        proc._derive_prices(1000.0, strategy)
    except _PipelineReject as exc:
        raised = True
        assert exc.check == "ZERO_SL"
    assert raised
    print("  OK E.1: negative sl_pct rejected (sl_pct <= 0 guard)")


def test_derive_prices_sl_min_pct_enforced():
    """SL too tight (sl_distance_pct < sl_min_pct) -> adjusted, WARNING logged."""
    log = _NullLogger()
    proc, _, _ = _make_proc(logger=log)
    # sl_pct=0.001 < sl_min_pct=0.003 -> should be adjusted to sl_min_pct
    strategy = _MockStrategy(direction="LONG", sl_pct=0.001,
                             sl_min_pct=0.003, sl_max_pct=0.05)
    entry, sl = proc._derive_prices(1000.0, strategy)
    expected_sl = 1000.0 * (1.0 - 0.003)
    assert abs(sl - expected_sl) < 0.01, f"Expected sl~{expected_sl}, got {sl}"
    assert any("sl_min_pct" in w.lower() or "< sl_min_pct" in w.lower()
               for w in log.warnings), f"No sl_min warning: {log.warnings}"
    print(f"  OK sl_min_pct enforced: sl adjusted to {sl}")


def test_derive_prices_sl_max_pct_enforced():
    """SL too wide (sl_distance_pct > sl_max_pct) -> adjusted, WARNING logged."""
    log = _NullLogger()
    proc, _, _ = _make_proc(logger=log)
    # sl_pct=0.08 > sl_max_pct=0.05 -> should be adjusted to sl_max_pct
    strategy = _MockStrategy(direction="LONG", sl_pct=0.08,
                             sl_min_pct=0.003, sl_max_pct=0.05)
    entry, sl = proc._derive_prices(1000.0, strategy)
    expected_sl = 1000.0 * (1.0 - 0.05)
    assert abs(sl - expected_sl) < 0.01, f"Expected sl~{expected_sl}, got {sl}"
    assert any("sl_max_pct" in w.lower() or "> sl_max_pct" in w.lower()
               for w in log.warnings), f"No sl_max warning: {log.warnings}"
    print(f"  OK sl_max_pct enforced: sl adjusted to {sl}")


# ---------------------------------------------------------------------------
# Target derivation tests (SPW5)
# ---------------------------------------------------------------------------

def test_derive_target_fixed_pct_long():
    """LONG FIXED_PCT: tgt = entry * (1 + tgt_pct)."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="LONG", tgt_method="FIXED_PCT", tgt_pct=0.04)
    tgt = proc._derive_target(1000.0, 980.0, strategy)
    assert abs(tgt - 1040.0) < 0.01, f"Expected 1040.0, got {tgt}"
    print(f"  OK LONG FIXED_PCT tgt={tgt}")


def test_derive_target_fixed_pct_short():
    """SHORT FIXED_PCT: tgt = entry * (1 - tgt_pct)."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="SHORT", tgt_method="FIXED_PCT", tgt_pct=0.04)
    tgt = proc._derive_target(1000.0, 1020.0, strategy)
    assert abs(tgt - 960.0) < 0.01, f"Expected 960.0, got {tgt}"
    print(f"  OK SHORT FIXED_PCT tgt={tgt}")


def test_derive_target_risk_reward_long():
    """LONG RISK_REWARD: tgt = entry + (entry - sl) * ratio."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="LONG", tgt_method="RISK_REWARD", tgt_risk_reward=2.0)
    # entry=1000, sl=980 -> risk=20 -> tgt=1000+20*2=1040
    tgt = proc._derive_target(1000.0, 980.0, strategy)
    assert abs(tgt - 1040.0) < 0.01, f"Expected 1040.0, got {tgt}"
    print(f"  OK LONG RISK_REWARD tgt={tgt} (entry + risk*2)")


def test_derive_target_risk_reward_short():
    """SHORT RISK_REWARD: tgt = entry - (sl - entry) * ratio."""
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="SHORT", tgt_method="RISK_REWARD", tgt_risk_reward=2.0)
    # entry=1000, sl=1020 -> risk=20 -> tgt=1000-20*2=960
    tgt = proc._derive_target(1000.0, 1020.0, strategy)
    assert abs(tgt - 960.0) < 0.01, f"Expected 960.0, got {tgt}"
    print(f"  OK SHORT RISK_REWARD tgt={tgt} (entry - risk*2)")


def test_derive_target_atr_fallback():
    """tgt_method=ATR -> WARNING + FIXED_PCT fallback."""
    log = _NullLogger()
    proc, _, _ = _make_proc(logger=log)
    strategy = _MockStrategy(direction="LONG", tgt_method="ATR", tgt_pct=0.04)
    tgt = proc._derive_target(1000.0, 980.0, strategy)
    # Falls back to FIXED_PCT
    assert abs(tgt - 1040.0) < 0.01, f"Expected 1040.0, got {tgt}"
    assert any("ATR" in w for w in log.warnings), f"No ATR warning: {log.warnings}"
    print(f"  OK tgt_method=ATR fallback -> FIXED_PCT, tgt={tgt}")


def test_atr_fallback_mode_halt_sl_raises_pipeline_reject():
    """MED #12: atr_fallback_mode=HALT + sl_method=ATR -> _PipelineReject raised."""
    from signals.signal_processor import _PipelineReject
    proc, _, _ = _make_proc()
    proc._atr_fallback_mode = "HALT"
    strategy = _MockStrategy(direction="LONG", sl_method="ATR", sl_pct=0.03)
    raised = False
    try:
        proc._derive_prices(1000.0, strategy)
    except _PipelineReject as exc:
        raised = True
        assert exc.check == "REJECTED_NO_ATR_DATA", f"Unexpected check: {exc.check}"
    assert raised, "HALT mode + ATR sl_method must raise _PipelineReject"
    print("  OK atr_fallback_mode=HALT + sl_method=ATR -> REJECTED_NO_ATR_DATA (MED #12)")


def test_atr_fallback_mode_halt_tgt_raises_pipeline_reject():
    """MED #12: atr_fallback_mode=HALT + tgt_method=ATR -> _PipelineReject raised."""
    from signals.signal_processor import _PipelineReject
    proc, _, _ = _make_proc()
    proc._atr_fallback_mode = "HALT"
    strategy = _MockStrategy(direction="LONG", tgt_method="ATR", tgt_pct=0.04)
    raised = False
    try:
        proc._derive_target(1000.0, 970.0, strategy)
    except _PipelineReject as exc:
        raised = True
        assert exc.check == "REJECTED_NO_ATR_DATA", f"Unexpected check: {exc.check}"
    assert raised, "HALT mode + ATR tgt_method must raise _PipelineReject"
    print("  OK atr_fallback_mode=HALT + tgt_method=ATR -> REJECTED_NO_ATR_DATA (MED #12)")


def test_atr_fallback_mode_warn_still_falls_back():
    """MED #12: atr_fallback_mode=WARN (default) keeps fallback behavior."""
    log = _NullLogger()
    proc, _, _ = _make_proc(logger=log)
    proc._atr_fallback_mode = "WARN"
    strategy = _MockStrategy(direction="LONG", sl_method="ATR", sl_pct=0.03)
    entry, sl = proc._derive_prices(1000.0, strategy)
    assert abs(sl - 970.0) < 0.01, f"Expected fallback sl=970.0, got {sl}"
    assert any("ATR" in w for w in log.warnings), "WARN mode must log warning"
    print(f"  OK atr_fallback_mode=WARN keeps fallback behavior, sl={sl} (MED #12)")


def test_derive_target_rejects_zero_distance():
    """BL-16: degenerate tgt_pct=0 (target == entry) must raise _PipelineReject."""
    from signals.signal_processor import _PipelineReject
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="LONG", tgt_method="FIXED_PCT", tgt_pct=0.0)
    raised = False
    try:
        proc._derive_target(1000.0, 980.0, strategy)
    except _PipelineReject as exc:
        raised = True
        assert exc.check == "TGT_DISTANCE_TOO_SMALL", (
            f"Unexpected check: {exc.check}"
        )
    assert raised, "tgt_pct=0 must raise _PipelineReject(TGT_DISTANCE_TOO_SMALL)"
    print("  OK BL-16: FIXED_PCT tgt_pct=0 -> TGT_DISTANCE_TOO_SMALL")


def test_derive_target_rejects_target_below_min_pct():
    """BL-16: FIXED_PCT with tgt below tgt_min_pct threshold is rejected."""
    from signals.signal_processor import _PipelineReject
    proc, _, _ = _make_proc()
    # tgt_pct=0.001 (0.1%) < default tgt_min_pct=0.003 (0.3%)
    strategy = _MockStrategy(direction="LONG", tgt_method="FIXED_PCT", tgt_pct=0.001)
    raised = False
    try:
        proc._derive_target(1000.0, 980.0, strategy)
    except _PipelineReject as exc:
        raised = True
        assert exc.check == "TGT_DISTANCE_TOO_SMALL"
    assert raised, "tgt_pct=0.001 < tgt_min_pct=0.003 must be rejected"
    print("  OK BL-16: FIXED_PCT tgt_pct<tgt_min_pct -> TGT_DISTANCE_TOO_SMALL")


def test_derive_target_unknown_method_raises_pipeline_reject():
    """F23: unknown tgt_method must raise _PipelineReject, not ValueError."""
    from signals.signal_processor import _PipelineReject
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="LONG", tgt_method="BOGUS", tgt_pct=0.04)
    raised = False
    try:
        proc._derive_target(1000.0, 980.0, strategy)
    except _PipelineReject as exc:
        raised = True
        assert exc.check == "UNKNOWN_TGT_METHOD"
    assert raised, "Unknown tgt_method must raise _PipelineReject(UNKNOWN_TGT_METHOD)"


def test_avg_pipeline_ms_includes_rejections():
    """F24: avg_pipeline_ms denominator must include rejected signals."""
    proc, store, _ = _make_proc()
    with proc._stats_lock:
        proc._stats["total_ms"] = 300.0
        proc._stats["pipeline_total"] = 3
        proc._stats["processed"] = 1
        proc._stats["processed_no_placer"] = 0
    snap = proc.stats()
    assert abs(snap["avg_pipeline_ms"] - 100.0) < 0.01, (
        f"Expected 300/3=100ms, got {snap['avg_pipeline_ms']}"
    )


def test_positional_tgt_not_equal_entry_price():
    """
    BL-16: each positional strategy YAML must produce target != entry when
    run through the real _derive_target pipeline. With RISK_REWARD + ratio 2
    and an sl distance of 2%, target distance = 4% (well above tgt_min_pct).
    """
    from strategies.schema import validate_strategy
    proc, _, _ = _make_proc()
    yaml_dir = Path(__file__).parent.parent.parent / "config" / "strategies"
    entry = 1000.0
    sl_long = entry * (1.0 - 0.02)  # 2% below entry (LONG)
    for name in [
        "positional_momentum_long",
        "positional_sector_rotation",
        "positional_swing_long",
    ]:
        cfg = validate_strategy(yaml_dir / f"{name}.yaml")
        tgt = proc._derive_target(entry, sl_long, cfg)
        distance_pct = abs(tgt - entry) / entry
        assert tgt != entry, f"{name}: target equals entry (guaranteed loss)"
        assert distance_pct >= proc._tgt_min_pct, (
            f"{name}: target distance {distance_pct:.5f} below "
            f"tgt_min_pct {proc._tgt_min_pct:.5f}"
        )
        # RISK_REWARD with ratio 2 and sl_distance 2% -> tgt distance 4%
        assert abs(distance_pct - 0.04) < 1e-9, (
            f"{name}: expected RISK_REWARD distance 4%, got {distance_pct:.5f}"
        )
        print(
            f"  OK BL-16: {name} entry={entry} sl={sl_long} -> tgt={tgt:.2f} "
            f"(dist={distance_pct * 100:.2f}%)"
        )


def test_tgt_price_passed_to_order_placer():
    """tgt_price computed by processor is passed to order_placer.place()."""
    store, _ = _make_store()
    sig_id = "sig_tgt_001"
    _insert_queued_signal(store, sig_id)

    placer = _MockOrderPlacer()
    # RISK_REWARD: entry=2500, sl=2500*(1-0.02)=2450, risk=50, tgt=2500+50*2=2600
    strategy = _MockStrategy(
        direction="LONG", entry_method="MARKET", sl_pct=0.02,
        tgt_method="RISK_REWARD", tgt_risk_reward=2.0, lot_size=1,
    )
    strategies = {"gap_go_long_v1": strategy}
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(
        store=store, placer=placer, screener=screener, strategies=strategies,
    )

    _run_one(proc, _now_tup(sig_id, price=2500.0), store=store)

    assert len(placer.calls) == 1, "Placer not called"
    tgt = placer.calls[0]["tgt_price"]
    assert tgt is not None, "tgt_price not passed to placer"
    assert abs(tgt - 2600.0) < 0.01, f"Expected tgt=2600.0, got {tgt}"
    print(f"  OK tgt_price={tgt} passed to order_placer (RISK_REWARD 2x)")


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------

def test_5_workers_process_5_signals_concurrently():
    """
    5 signals submitted together complete in parallel through the screen
    + sizing stages. Audit 1.2 / Portfolio Lock serialises approve+reserve,
    so the barrier must sit BEFORE approve (we put it in the sizer) to
    prove worker parallelism. If the barrier were inside approve, all 5
    workers would queue on fm.portfolio_lock and the test would deadlock.
    """
    barrier = threading.Barrier(5, timeout=5.0)
    processed_order = []
    lock = threading.Lock()

    class _SlowSizer(_MockPositionSizer):
        def calculate(self, symbol, direction, entry_price, sl_price, intent,
                      score_tier="MEDIUM", lot_size=1, perf_weight=1.0):
            barrier.wait()
            with lock:
                processed_order.append(symbol)
            return _SizingResult(success=True, qty=10, margin_required=10000.0,
                                 risk_amount=500.0)

    proc, sq, store = _make_proc(sizer=_SlowSizer(), worker_count=5)
    proc.start()

    symbols = [f"SYM{i}" for i in range(5)]
    for i, sym in enumerate(symbols):
        sq.put((f"sig_{i:03d}", "gap_go_long", sym, 1000.0, datetime.now()))

    proc.stop()

    assert len(processed_order) == 5, f"Expected 5 processed, got {len(processed_order)}"
    print(f"  OK 5 signals processed concurrently (barrier passed pre-approve): {processed_order}")


def test_portfolio_lock_serialises_approve_and_reserve():
    """
    Audit 1.2 / Portfolio Lock: risk_engine.approve + fund_manager.reserve
    must be a single critical section so two concurrent signals on the
    same sector/bucket cannot both pass approve and then both reserve.
    Asserts that no two workers hold approve concurrently after the lock.
    """
    in_approve = 0
    max_concurrent = 0
    counter_lock = threading.Lock()
    enter_event = threading.Event()
    release_event = threading.Event()

    class _ConcurrencyTrackingRisk(_MockRiskEngine):
        def approve(self, symbol, direction, intent, sizing, signal_id,
                    processor_in_flight_count: int = 0):
            nonlocal in_approve, max_concurrent
            with counter_lock:
                in_approve += 1
                if in_approve > max_concurrent:
                    max_concurrent = in_approve
            enter_event.set()
            # Hold inside approve briefly so a racing worker would overlap
            # if portfolio_lock weren't serialising. release_event fires
            # after we've measured concurrency.
            release_event.wait(timeout=2.0)
            with counter_lock:
                in_approve -= 1
            return _ApprovalResult(approved=True)

    proc, sq, store = _make_proc(
        risk=_ConcurrencyTrackingRisk(), worker_count=5,
    )
    proc.start()
    for i in range(5):
        sq.put((f"sig_{i:03d}", "gap_go_long", f"SYM{i}", 1000.0, datetime.now()))

    # Let one worker enter approve, then unblock everyone.
    enter_event.wait(timeout=2.0)
    release_event.set()
    proc.stop()

    assert max_concurrent == 1, (
        f"portfolio_lock failed: {max_concurrent} workers were inside approve "
        f"concurrently; expected serialisation (1)"
    )
    print(f"  OK portfolio_lock serialised approve (max concurrent={max_concurrent})")


def test_100_signals_complete_in_reasonable_time():
    """100 signals through 5 workers complete in < 10s."""
    proc, sq, _ = _make_proc(worker_count=5, drain_poll_sec=0.01)
    proc.start()

    t0 = time.monotonic()
    for i in range(100):
        sq.put(_now_tup(f"sig_{i:04d}", symbol=f"SYM{i % 20:02d}"))

    proc.stop()
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0, f"100 signals took {elapsed:.2f}s"
    print(f"  OK 100 signals in {elapsed:.3f}s with 5 workers")


# ---------------------------------------------------------------------------
# FIX-018: TOCTOU Fix (processor_in_flight_count)
# ---------------------------------------------------------------------------

def test_fix018_toctou_only_one_approved_with_concurrent_signals():
    """
    FIX-018: 5 concurrent signals, max_open=5, db has 4 positions.
    Without TOCTOU fix, all 5 would see "4 < 5" and pass approve().
    With processor_in_flight_count, the in-memory counter prevents
    concurrent signals from all passing the OPEN_POSITIONS check.

    Test approach: verify that risk_engine.approve() receives and uses
    the processor_in_flight_count parameter correctly by checking the
    rejection message includes processor_in_flight count.
    """
    store, _ = _make_store()

    # Insert 4 open positions into the store
    from core.time_authority import now_ist
    today_iso = now_ist().replace(tzinfo=None).isoformat()
    today_date = now_ist().date().isoformat()

    with store.transaction() as cur:
        for i in range(4):
            sig_id = f"sig_exist_{i}"
            # Insert signal first (FK constraint)
            cur.execute(
                """
                INSERT INTO signals
                  (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                   expires_at, status, fingerprint, fingerprint_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sig_id, f"EXIST{i}", "gap_go_long", "gap_go_long_v1",
                 today_iso, today_iso, today_iso, "TRADED", f"fp_exist_{i}", today_date),
            )
            # Insert trade
            cur.execute(
                """
                INSERT INTO trades
                  (trade_id, signal_id, symbol, direction, strategy, sector,
                   qty_planned, qty_filled, entry_target_price, sl_initial,
                   tgt_initial, margin_reserved, risk_amount, created_at,
                   status, order_protocol, updated_at, entry_actual_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"trade_{i}", sig_id, f"EXIST{i}", "LONG", "gap_go_long_v1",
                 "TECH", 10, 10, 1000.0, 950.0, 1050.0, 10000.0, 500.0, today_iso,
                 "OPEN", "CO_PLUS_TGT", today_iso, 1000.0),
            )

    # Verify we have 4 open positions
    assert store.count_open_positions() == 4

    # Real risk engine that enforces max_open_positions=5
    from capital.risk_engine import RiskEngine
    from core.logger import get_logger
    real_risk = RiskEngine(
        fund_manager=_MockFundManager(),
        state_store=store,
        max_open_positions=5,
        max_daily_trades=100,
        max_sector_exposure_pct=0.40,
        max_consecutive_losses=5,
        daily_loss_limit_pct=0.05,
        sector_lookup_fn=lambda s: "TECH",
        logger=get_logger("test_risk"),
        kill_switch=None,
    )

    # Test 1: With processor_in_flight_count=0, should approve (4 + 0 < 5)
    result_no_in_flight = real_risk.approve(
        symbol="NEWSYM",
        side="BUY",
        intent="INTRADAY",
        sizing_result=_SizingResult(success=True, qty=10, margin_required=5000.0),
        signal_id="sig_test_1",
        processor_in_flight_count=0,
    )
    assert result_no_in_flight.approved, (
        f"Should approve with 4 open + 0 in_flight, got: {result_no_in_flight.reason}"
    )

    # Test 2: With processor_in_flight_count=1, should reject (4 + 1 >= 5)
    result_with_in_flight = real_risk.approve(
        symbol="NEWSYM2",
        side="BUY",
        intent="INTRADAY",
        sizing_result=_SizingResult(success=True, qty=10, margin_required=5000.0),
        signal_id="sig_test_2",
        processor_in_flight_count=1,
    )
    assert not result_with_in_flight.approved, (
        "Should reject with 4 open + 1 in_flight (total 5 >= max 5)"
    )
    assert result_with_in_flight.failed_check == "OPEN_POSITIONS"
    assert "processor_in_flight=1" in result_with_in_flight.reason, (
        f"Rejection message should include processor_in_flight count: {result_with_in_flight.reason}"
    )

    # Test 3: With processor_in_flight_count=3, should reject (4 + 3 = 7 >> 5)
    result_high_in_flight = real_risk.approve(
        symbol="NEWSYM3",
        side="BUY",
        intent="INTRADAY",
        sizing_result=_SizingResult(success=True, qty=10, margin_required=5000.0),
        signal_id="sig_test_3",
        processor_in_flight_count=3,
    )
    assert not result_high_in_flight.approved
    assert result_high_in_flight.failed_check == "OPEN_POSITIONS"
    assert "processor_in_flight=3" in result_high_in_flight.reason

    print(f"  OK FIX-018: processor_in_flight_count correctly prevents TOCTOU race")
    print(f"    db=4, max=5: in_flight=0 -> approved, in_flight=1 -> rejected")


def test_fix018_in_flight_counter_decrements_on_exception():
    """
    FIX-018: If an exception occurs mid-pipeline, the finally block must
    decrement the in_flight_count. Without this, the counter would leak
    and eventually block all new signals.

    Test approach: Directly monitor the counter during exception processing.
    """
    store, _ = _make_store()

    # Track counter values
    counter_values = []
    lock = threading.Lock()

    class _TrackingRiskEngine(_MockRiskEngine):
        """Records counter value when approve() is called."""
        def approve(self, symbol, direction, intent, sizing_result, signal_id,
                    processor_in_flight_count: int = 0):
            with lock:
                counter_values.append({
                    "signal_id": signal_id,
                    "processor_in_flight_count": processor_in_flight_count,
                })
            # First call: let it proceed (will fail later in placer)
            # Second call: let it proceed normally
            return super().approve(
                symbol, direction, intent, sizing_result, signal_id,
                processor_in_flight_count,
            )

    # Placer that fails on first call, succeeds on second
    call_count = [0]

    class _ExceptionThenOkPlacer:
        def place(self, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated placement failure")
            # Second call succeeds (no-op)

    # First signal: will raise exception in placer
    sig_id_fail = "sig_exc_001"
    _insert_queued_signal(store, sig_id_fail)

    # Second signal: should succeed
    sig_id_ok = "sig_exc_002"
    _insert_queued_signal(store, sig_id_ok, symbol="RELOK")

    screener = _MockScreener(state_store=store)
    proc, sq, _ = _make_proc(
        store=store,
        risk=_TrackingRiskEngine(),
        placer=_ExceptionThenOkPlacer(),
        screener=screener,
        worker_count=1,  # Sequential processing
    )

    # Check initial counter state
    assert proc._in_flight_count == 0, "Counter should start at 0"

    proc.start()
    sq.put((sig_id_fail, "gap_go_long", "RELFAIL", 1000.0, datetime.now()))
    time.sleep(0.15)  # Let first signal process
    sq.put((sig_id_ok, "gap_go_long", "RELOK", 1000.0, datetime.now()))
    proc.stop()

    # Verify counter is back to 0 after all processing
    assert proc._in_flight_count == 0, (
        f"Counter should be 0 after stop(), got {proc._in_flight_count}. "
        f"Counter values during execution: {counter_values}"
    )

    # Verify both signals reached approve() (counter was incremented twice)
    assert len(counter_values) == 2, (
        f"Expected 2 approve() calls, got {len(counter_values)}: {counter_values}"
    )

    # First signal sees in_flight=1 (itself)
    assert counter_values[0]["processor_in_flight_count"] == 1, (
        f"First signal should see in_flight=1, got {counter_values[0]}"
    )

    # Second signal sees in_flight=1 (itself, after first was decremented)
    assert counter_values[1]["processor_in_flight_count"] == 1, (
        f"Second signal should see in_flight=1, got {counter_values[1]}"
    )

    print(f"  OK FIX-018: exception mid-pipeline -> counter properly decremented")
    print(f"    Counter values: {[v['processor_in_flight_count'] for v in counter_values]}")


# ---------------------------------------------------------------------------
# Metrics (SP13, SPW9)
# ---------------------------------------------------------------------------

def test_stats_returns_valid_dict():
    """stats() returns a dict with all required keys including screener metrics."""
    proc, _, _ = _make_proc()
    s = proc.stats()
    required = {
        "signals_processed", "signals_rejected", "signals_placed",
        "avg_pipeline_ms", "workers_active", "queue_depth",
        "signals_screened_passed", "signals_screened_rejected",
        "signals_screened_skipped", "avg_screening_ms",
    }
    missing = required - set(s.keys())
    assert not missing, f"Missing stats keys: {missing}"
    assert isinstance(s["signals_rejected"], dict)
    assert isinstance(s["signals_screened_rejected"], dict)
    assert isinstance(s["signals_screened_skipped"], dict)
    print(f"  OK stats() keys all present: {sorted(s.keys())}")


def test_stats_correct_after_run(monkeypatch):
    """stats() counts reflect actual processing results."""
    # Fix flaky timing by mocking time.monotonic() to ensure measurable elapsed time
    _mono_counter = [1000.0]  # Start at 1000 seconds
    def _mock_monotonic():
        val = _mono_counter[0]
        _mono_counter[0] += 0.010  # Each call advances by 10ms
        return val
    monkeypatch.setattr(time, "monotonic", _mock_monotonic)

    store, _ = _make_store()
    placer = _MockOrderPlacer()
    screener = _MockScreener(state_store=store)
    proc, sq, _ = _make_proc(store=store, placer=placer, screener=screener, worker_count=2)
    proc.start()

    for i in range(2):
        sig_id = f"sig_stat_{i:03d}"
        _insert_queued_signal(store, sig_id, symbol=f"SYM{i}")
        sq.put((sig_id, "gap_go_long", f"SYM{i}", 1000.0, datetime.now()))

    # Wait for signals to be processed before stopping
    # Poll until both signals reach terminal status
    max_wait = 2.0
    poll_interval = 0.05
    elapsed = 0.0
    while elapsed < max_wait:
        s = proc.stats()
        if s["signals_processed"] >= 2:
            break
        time.sleep(poll_interval)
        elapsed += poll_interval

    proc.stop()

    s = proc.stats()
    assert s["signals_placed"] == 2, f"Expected 2 placed, got {s['signals_placed']}"
    assert s["signals_processed"] == 2, f"Expected 2 processed, got {s['signals_processed']}"
    assert s["avg_pipeline_ms"] > 0, f"avg_pipeline_ms should be > 0, got {s['avg_pipeline_ms']}"
    assert s["signals_screened_passed"] == 2, f"Expected 2 screened_passed, got {s['signals_screened_passed']}"
    print(f"  OK stats after 2 signals: {s}")


def test_stats_screener_rejected_counted():
    """stats() screener_rejected dict incremented on screener rejection."""
    store, _ = _make_store()
    sig_id = "sig_scr_stat_001"
    _insert_queued_signal(store, sig_id)

    rejected = _MockScreeningResult(passed=False, status="REJECTED_SCORE_30", tier="LOW")
    screener = _MockScreener(result=rejected, state_store=store)

    proc, _, _ = _make_proc(store=store, screener=screener)
    _run_one(proc, _now_tup(sig_id), store=store)

    s = proc.stats()
    assert "REJECTED_SCORE_30" in s["signals_screened_rejected"], \
        f"Missing in screener_rejected: {s['signals_screened_rejected']}"
    assert s["signals_screened_rejected"]["REJECTED_SCORE_30"] == 1
    print(f"  OK screener_rejected counted: {s['signals_screened_rejected']}")


# ---------------------------------------------------------------------------
# Signal status step-by-step (SP9)
# ---------------------------------------------------------------------------

def test_signal_status_updated_at_each_step():
    """Status written before pipeline (PROCESSING) and by screener (PASSED) before sizer."""
    store, _ = _make_store()
    sig_id = "sig_step_001"
    _insert_queued_signal(store, sig_id)

    statuses_seen = []

    class _ObservingSizer(_MockPositionSizer):
        def calculate(self, *a, **kw):
            row = store.fetch_one(
                "SELECT status FROM signals WHERE signal_id = ?", (sig_id,)
            )
            if row:
                statuses_seen.append(row["status"])
            return _SizingResult(success=True)

    placer = _MockOrderPlacer()
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(store=store, sizer=_ObservingSizer(), placer=placer,
                             screener=screener)
    _run_one(proc, _now_tup(sig_id), store=store)

    # At sizer time: screener has already written PASSED (P18 compliance)
    assert "PASSED" in statuses_seen, \
        f"Expected PASSED at sizer-time (screener wrote it); got: {statuses_seen}"
    row_final = store.fetch_one("SELECT status FROM signals WHERE signal_id = ?", (sig_id,))
    assert row_final["status"] == "PROCESSED", row_final["status"]
    print(f"  OK status at sizer-time: {statuses_seen} -> final PROCESSED")


# ---------------------------------------------------------------------------
# BrokerError -> record_api_failure (SP12)
# ---------------------------------------------------------------------------

def test_broker_error_in_sizer_calls_record_failure():
    """BrokerError from sizer -> kill_switch.record_api_failure() called."""
    store, _ = _make_store()
    sig_id = "sig_be_001"
    _insert_queued_signal(store, sig_id)

    ks = _MockKillSwitch()
    sizer = _MockPositionSizer(raise_exc=BrokerError("connection refused"))
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(store=store, ks=ks, sizer=sizer, screener=screener)

    _run_one(proc, _now_tup(sig_id), store=store)

    assert ks.failure_count >= 1, f"record_api_failure not called: {ks.failure_count}"
    print(f"  OK BrokerError in sizer -> record_api_failure called ({ks.failure_count}x)")


def test_broker_error_in_risk_calls_record_failure():
    """BrokerError from risk_engine -> kill_switch.record_api_failure() called."""
    store, _ = _make_store()
    sig_id = "sig_be_002"
    _insert_queued_signal(store, sig_id)

    ks = _MockKillSwitch()
    risk = _MockRiskEngine(raise_exc=BrokerError("timeout"))
    screener = _MockScreener(state_store=store)
    proc, _, _ = _make_proc(store=store, ks=ks, risk=risk, screener=screener)

    _run_one(proc, _now_tup(sig_id), store=store)

    assert ks.failure_count >= 1, f"record_api_failure not called: {ks.failure_count}"
    print(f"  OK BrokerError in risk -> record_api_failure called ({ks.failure_count}x)")


# ---------------------------------------------------------------------------
# Concurrent shutdown
# ---------------------------------------------------------------------------

def test_concurrent_shutdown_cleans_up():
    """Stop during active processing completes or times out cleanly."""
    slow_done = threading.Event()

    class _SlowSizer(_MockPositionSizer):
        def calculate(self, *a, **kw):
            time.sleep(0.1)
            slow_done.set()
            return _SizingResult(success=True)

    proc, sq, store = _make_proc(sizer=_SlowSizer(), worker_count=3)
    proc.start()

    for i in range(3):
        sq.put(_now_tup(f"sig_shut_{i:03d}", symbol=f"SYM{i}"))

    time.sleep(0.02)
    t0 = time.monotonic()
    proc.stop()
    elapsed = time.monotonic() - t0

    assert not proc.is_running()
    assert elapsed < 8.0, f"stop() during active processing took {elapsed:.2f}s"
    print(f"  OK concurrent shutdown clean in {elapsed:.3f}s")


# ---------------------------------------------------------------------------
# Regression tests: audit blocker fixes
# ---------------------------------------------------------------------------

def test_derive_prices_negative_entry_raises():
    """
    BLOCKER #15 regression: entry_price <= 0 short-circuits the pipeline.
    A strategy with entry_offset_pct >= 1.0 on a LONG would produce entry <= 0.
    E.4 M-4: upgraded from ValueError to _PipelineReject(INVALID_DERIVED_PRICE)
    so the signal is categorized as REJECTED, not PLACEMENT_FAILED.
    """
    from signals.signal_processor import _PipelineReject
    proc, _, _ = _make_proc()
    strategy = _MockStrategy(direction="LONG", entry_method="LIMIT",
                              entry_offset_pct=1.0, sl_pct=0.02)
    with pytest.raises(_PipelineReject) as excinfo:
        proc._derive_prices(trigger_price=100.0, strategy=strategy)
    assert excinfo.value.check == "INVALID_DERIVED_PRICE"
    assert "entry_price=" in excinfo.value.reason


def test_continue_from_gate_uses_side_not_direction():
    """
    BLOCKER #4 regression: continue_from_gate converts LONG->BUY / SHORT->SELL
    before calling sizer.calculate() and risk.approve().
    Previously passed "LONG"/"SHORT" directly, causing ValueError in sizer.
    """
    from screening.entry_gate import WatchEntry
    from datetime import datetime

    sizer = _MockPositionSizer()
    risk = _MockRiskEngine()

    class _CapturePlacer:
        calls = []
        def place(self, **kwargs):
            self.calls.append(kwargs)

    placer = _CapturePlacer()
    proc, _, store = _make_proc(sizer=sizer, risk=risk, placer=placer)

    entry = WatchEntry(
        signal_id="sig_gate_001",
        symbol="RELIANCE",
        direction="LONG",  # "LONG"/"SHORT" — NOT "BUY"/"SELL"
        trigger_price=2500.0,
        entry_price=2495.0,
        sl_price=2445.0,
        tgt_price=2595.0,
        tolerance_pct=0.005,
        timeout_sec=300,
        strategy_name="gap_go_long_v1",
        tier="HIGH",
        scanner_name="gap_go_long",
        intent="INTRADAY",
        added_at=datetime.now(),
    )

    # Insert a matching signal row so update_signal_status doesn't raise FK error
    with store.transaction() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO signals "
            "(signal_id, symbol, scanner, strategy, triggered_at, "
            "received_at, expires_at, status, fingerprint, fingerprint_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sig_gate_001", "RELIANCE", "gap_go_long", "gap_go_long_v1",
             "2026-04-15 10:00:00", "2026-04-15 10:00:00", "2026-04-15 10:01:00",
             "PROCESSING", "fp_gate_001", "2026-04-15"),
        )

    proc.continue_from_gate(entry)

    # sizer and risk should have been called with "BUY", not "LONG"
    assert sizer.calls, "sizer.calculate() was not called"
    assert sizer.calls[0]["direction"] == "BUY", (
        f"Expected sizer called with 'BUY' but got {sizer.calls[0]['direction']!r}"
    )
    assert placer.calls, "placer.place() was not called"
    assert placer.calls[0]["side"] == "BUY", (
        f"Expected placer called with side='BUY' but got {placer.calls[0]['side']!r}"
    )


def test_continue_from_gate_short_converts_to_sell():
    """
    BLOCKER #4 regression (SHORT path): direction="SHORT" -> side="SELL".
    """
    from screening.entry_gate import WatchEntry
    from datetime import datetime

    sizer = _MockPositionSizer()

    class _CapturePlacer:
        calls = []
        def place(self, **kwargs):
            self.calls.append(kwargs)

    placer = _CapturePlacer()
    proc, _, store = _make_proc(
        sizer=sizer, placer=placer,
        strategies={"gap_go_short_v1": _SELL_STRATEGY},
        scan_webhook_map={"gap_go_short": {"strategy": "gap_go_short_v1"}},
    )

    entry = WatchEntry(
        signal_id="sig_gate_002",
        symbol="HDFCBANK",
        direction="SHORT",
        trigger_price=1500.0,
        entry_price=1502.0,
        sl_price=1530.0,
        tgt_price=1440.0,
        tolerance_pct=0.005,
        timeout_sec=300,
        strategy_name="gap_go_short_v1",
        tier="HIGH",
        scanner_name="gap_go_short",
        intent="INTRADAY",
        added_at=datetime.now(),
    )

    with store.transaction() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO signals "
            "(signal_id, symbol, scanner, strategy, triggered_at, "
            "received_at, expires_at, status, fingerprint, fingerprint_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sig_gate_002", "HDFCBANK", "gap_go_short", "gap_go_short_v1",
             "2026-04-15 10:00:00", "2026-04-15 10:00:00", "2026-04-15 10:01:00",
             "PROCESSING", "fp_gate_002", "2026-04-15"),
        )

    proc.continue_from_gate(entry)

    assert sizer.calls, "sizer.calculate() was not called"
    assert sizer.calls[0]["direction"] == "SELL", (
        f"Expected 'SELL' but got {sizer.calls[0]['direction']!r}"
    )
    assert placer.calls, "placer.place() was not called"
    assert placer.calls[0]["side"] == "SELL", (
        f"Expected side='SELL' but got {placer.calls[0]['side']!r}"
    )


# ---------------------------------------------------------------------------
# B.5 / Audit 5.1 — shadow-tracker re-entry guard
# ---------------------------------------------------------------------------

class _StubShadowTracker:
    """Minimal shadow-tracker stub: is_tracking() returns True for symbols
    in the seeded set; raises if `raise_on` is set."""

    def __init__(self, tracking=None, raise_on=None):
        self._tracking = set(tracking or ())
        self._raise_on = raise_on

    def is_tracking(self, symbol: str) -> bool:
        if self._raise_on and symbol == self._raise_on:
            raise RuntimeError("simulated shadow_tracker failure")
        return symbol in self._tracking


def test_b5_shadow_inning_active_rejects_signal() -> None:
    """Audit 5.1: signal for symbol with active shadow inning is rejected."""
    store, _ = _make_store()
    sig_id = "sig_b5_act_001"
    _insert_queued_signal(store, sig_id)

    sh = _StubShadowTracker(tracking={"RELIANCE"})
    proc, _, _ = _make_proc(store=store, shadow_tracker=sh)
    _run_one(proc, _now_tup(sig_id, symbol="RELIANCE"), store=store)
    _assert_rejected(store, sig_id, "REJECTED_SHADOW_INNING_ACTIVE")
    print("  OK B.5 shadow inning active -> REJECTED_SHADOW_INNING_ACTIVE")


def test_b5_no_shadow_inning_proceeds() -> None:
    """B.5: a symbol NOT in shadow_tracker proceeds normally through pipeline."""
    store, _ = _make_store()
    sig_id = "sig_b5_unr_001"
    _insert_queued_signal(store, sig_id)

    sh = _StubShadowTracker(tracking={"INFY"})  # different symbol
    proc, _, _ = _make_proc(
        store=store, shadow_tracker=sh, placer=_MockOrderPlacer(),
    )
    _run_one(proc, _now_tup(sig_id, symbol="RELIANCE"), store=store)
    row = store.fetch_one(
        "SELECT status FROM signals WHERE signal_id = ?", (sig_id,),
    )
    assert row is not None and row["status"] == "PROCESSED", (
        f"Expected PROCESSED; got {row and row['status']!r}"
    )
    print("  OK B.5 unrelated shadow inning -> signal proceeds")


def test_b5_no_shadow_tracker_wired_proceeds() -> None:
    """B.5 fail-open: no shadow_tracker injected -> guard is skipped."""
    store, _ = _make_store()
    sig_id = "sig_b5_none_001"
    _insert_queued_signal(store, sig_id)

    proc, _, _ = _make_proc(
        store=store, shadow_tracker=None, placer=_MockOrderPlacer(),
    )
    _run_one(proc, _now_tup(sig_id, symbol="RELIANCE"), store=store)
    row = store.fetch_one(
        "SELECT status FROM signals WHERE signal_id = ?", (sig_id,),
    )
    assert row is not None and row["status"] == "PROCESSED"
    print("  OK B.5 no shadow_tracker wired -> proceeds (fail-open)")


def test_b5_is_tracking_raises_fails_closed() -> None:
    """B.5 fail-closed: is_tracking() raising must REJECT the signal, not pass it."""
    store, _ = _make_store()
    sig_id = "sig_b5_err_001"
    _insert_queued_signal(store, sig_id)

    sh = _StubShadowTracker(raise_on="RELIANCE")
    proc, _, _ = _make_proc(store=store, shadow_tracker=sh)
    _run_one(proc, _now_tup(sig_id, symbol="RELIANCE"), store=store)
    _assert_rejected(store, sig_id, "REJECTED_SHADOW_TRACKER_ERROR")
    print("  OK B.5 is_tracking exception -> REJECTED_SHADOW_TRACKER_ERROR")


# ---------------------------------------------------------------------------
# FIX-007: rate_limiter pre-check
# ---------------------------------------------------------------------------

class _MockRateLimiter:
    def __init__(self, permit: bool = True) -> None:
        self.permit = permit
        self.calls: list = []

    def try_acquire(self, bucket: str) -> bool:
        self.calls.append(bucket)
        return self.permit


def test_fix007_rate_limiter_exhausted_requeues_signal() -> None:
    """FIX-007: when rate_limiter.try_acquire returns False, signal is re-queued,
    pipeline is skipped, and the queue still contains the signal."""
    store, _ = _make_store()
    sig_id = "sig_rl_001"
    _insert_queued_signal(store, sig_id)

    sq = queue.Queue(maxsize=100)
    rl = _MockRateLimiter(permit=False)

    proc, _, _ = _make_proc(store=store, sq=sq)
    proc._rate_limiter = rl

    signal_tup = _now_tup(sig_id)
    proc._process_one_safe(signal_tup)

    # Signal status must NOT have been advanced (no PROCESSING/PLACEMENT_FAILED)
    row = store.fetch_one("SELECT status FROM signals WHERE signal_id=?", (sig_id,))
    assert row["status"] == "QUEUED", f"Expected QUEUED, got {row['status']}"

    # Signal must have been re-enqueued
    assert not sq.empty(), "Signal must be re-queued when rate_limiter exhausted"
    requeued = sq.get_nowait()
    assert requeued[0] == sig_id

    # try_acquire must have been called with 'order'
    assert rl.calls == ["order"], f"Unexpected try_acquire calls: {rl.calls}"

    # active_workers counter must NOT have been incremented (returned before)
    assert proc._active_workers == 0
    print("  OK FIX-007: rate_limiter exhausted -> signal re-queued, pipeline skipped")


def test_fix007_rate_limiter_permitted_proceeds_normally() -> None:
    """FIX-007: when rate_limiter.try_acquire returns True, pipeline proceeds normally."""
    store, _ = _make_store()
    sig_id = "sig_rl_002"
    _insert_queued_signal(store, sig_id)

    rl = _MockRateLimiter(permit=True)
    proc, _, _ = _make_proc(store=store)
    proc._rate_limiter = rl

    _run_one(proc, _now_tup(sig_id), store=store)

    # try_acquire was called
    assert rl.calls == ["order"]
    # Signal advanced beyond QUEUED
    row = store.fetch_one("SELECT status FROM signals WHERE signal_id=?", (sig_id,))
    assert row["status"] != "QUEUED", "Signal should advance when rate_limiter permits"
    print("  OK FIX-007: rate_limiter=permit -> pipeline proceeds (FIX-007)")


def test_fix007_no_rate_limiter_proceeds_normally() -> None:
    """FIX-007: rate_limiter=None (default) has no effect; pipeline runs as before."""
    store, _ = _make_store()
    sig_id = "sig_rl_003"
    _insert_queued_signal(store, sig_id)

    proc, _, _ = _make_proc(store=store)
    assert proc._rate_limiter is None

    _run_one(proc, _now_tup(sig_id), store=store)

    row = store.fetch_one("SELECT status FROM signals WHERE signal_id=?", (sig_id,))
    assert row["status"] != "QUEUED", "Signal should advance with no rate_limiter"
    print("  OK FIX-007: rate_limiter=None -> pipeline unaffected (FIX-007)")


def test_fix048_queue_full_abandons_signal() -> None:
    """FIX-048: when queue is full on requeue attempt, signal is abandoned and warning logged."""
    store, _ = _make_store()
    sig_id = "sig_rl_full_001"
    _insert_queued_signal(store, sig_id)

    # Create a queue at capacity (maxsize=1, already full)
    sq = queue.Queue(maxsize=1)
    sq.put("blocking_item")  # Fill the queue

    rl = _MockRateLimiter(permit=False)  # Rate limiter exhausted -> triggers requeue
    logger = _NullLogger()

    proc, _, _ = _make_proc(store=store, sq=sq, logger=logger)
    proc._rate_limiter = rl

    signal_tup = _now_tup(sig_id)
    proc._process_one_safe(signal_tup)

    # FIX-165f: Signal status updated to REJECTED (was QUEUED before fix)
    row = store.fetch_one("SELECT status FROM signals WHERE signal_id=?", (sig_id,))
    assert row["status"] == "REJECTED", f"Expected REJECTED, got {row['status']}"

    # Queue should still have only the original blocking item (signal abandoned)
    assert sq.qsize() == 1, f"Expected queue size 1, got {sq.qsize()}"

    # Warning must be logged
    assert any("REJECTED_QUEUE_FULL" in w for w in logger.warnings), \
        f"Expected REJECTED_QUEUE_FULL warning, got: {logger.warnings}"
    assert any(sig_id in w for w in logger.warnings), \
        f"Expected signal_id {sig_id} in warning, got: {logger.warnings}"

    # active_workers counter must NOT have been incremented (returned before)
    assert proc._active_workers == 0
    print("  OK FIX-048: queue full on requeue -> signal abandoned, warning logged")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def run_all_tests() -> int:
    tests = [
        test_start_launches_dispatcher_and_workers,
        test_stop_drains_within_5s,
        test_is_running_before_start,
        test_full_pipeline_queued_to_processed,
        test_full_pipeline_no_placer_processed_no_placer,
        test_kill_switch_active_rejects,
        test_outside_entry_window_rejects,
        test_expired_signal_rejects,
        test_unknown_strategy_rejects,
        test_unknown_strategy_name_rejects,
        test_sizer_failure_rejects,
        test_risk_engine_rejection,
        test_reserve_failure_rejects,
        test_order_placer_raises_placement_failed,
        test_unexpected_exception_placement_failed,
        test_screener_rejects_signal,
        test_screener_skipped_logs_warning,
        test_screener_passed_uses_tier_for_sizing,
        test_screener_passes_direction_from_strategy,
        test_no_double_write_screener_rejected,
        test_in_flight_released_on_screener_skipped,
        test_in_flight_released_on_screener_rejected,
        test_in_flight_released_on_rejection,
        test_in_flight_released_on_success,
        test_in_flight_released_on_placer_exception,
        test_derive_prices_market_entry_long,
        test_derive_prices_limit_entry_long,
        test_derive_prices_limit_entry_short,
        test_derive_prices_sl_long,
        test_derive_prices_sl_short,
        test_derive_prices_atr_fallback_warns,
        test_e1_atr_fallback_with_zero_sl_pct_rejects,
        test_e1_fixed_pct_with_zero_sl_pct_rejects,
        test_e1_negative_sl_pct_also_rejected,
        test_derive_prices_sl_min_pct_enforced,
        test_derive_prices_sl_max_pct_enforced,
        test_derive_target_fixed_pct_long,
        test_derive_target_fixed_pct_short,
        test_derive_target_risk_reward_long,
        test_derive_target_risk_reward_short,
        test_derive_target_atr_fallback,
        test_atr_fallback_mode_halt_sl_raises_pipeline_reject,
        test_atr_fallback_mode_halt_tgt_raises_pipeline_reject,
        test_atr_fallback_mode_warn_still_falls_back,
        test_derive_target_rejects_zero_distance,
        test_derive_target_rejects_target_below_min_pct,
        test_derive_target_unknown_method_raises_pipeline_reject,
        test_avg_pipeline_ms_includes_rejections,
        test_positional_tgt_not_equal_entry_price,
        test_tgt_price_passed_to_order_placer,
        test_5_workers_process_5_signals_concurrently,
        test_100_signals_complete_in_reasonable_time,
        # FIX-018 TOCTOU fix
        test_fix018_toctou_only_one_approved_with_concurrent_signals,
        test_fix018_in_flight_counter_decrements_on_exception,
        test_stats_returns_valid_dict,
        test_stats_correct_after_run,
        test_stats_screener_rejected_counted,
        test_signal_status_updated_at_each_step,
        test_broker_error_in_sizer_calls_record_failure,
        test_broker_error_in_risk_calls_record_failure,
        test_concurrent_shutdown_cleans_up,
        # B.5 / Audit 5.1 — shadow-tracker re-entry guard
        test_b5_shadow_inning_active_rejects_signal,
        test_b5_no_shadow_inning_proceeds,
        test_b5_no_shadow_tracker_wired_proceeds,
        test_b5_is_tracking_raises_fails_closed,
        # FIX-007 rate_limiter pre-check
        test_fix007_rate_limiter_exhausted_requeues_signal,
        test_fix007_rate_limiter_permitted_proceeds_normally,
        test_fix007_no_rate_limiter_proceeds_normally,
        # FIX-048 queue full on requeue
        test_fix048_queue_full_abandons_signal,
    ]

    print("=" * 70)
    print("signal_processor.py -- Test Suite (SPW1-SPW10)")
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
            traceback.print_exc()

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
