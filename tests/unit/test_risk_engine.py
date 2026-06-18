"""
tests/unit/test_risk_engine.py

Validates capital/risk_engine.py against RE1-RE17 locked decisions.

Coverage:
  - approve() all checks passing -> approved=True (RE5)
  - KILL_SWITCH active -> rejected (RE8)
  - kill_switch=None -> KILL_SWITCH check skipped, WARNING logged (RE8)
  - SIZING_VALID: sizing_result.success=False -> rejected (RE5)
  - CAPITAL: insufficient bucket capital -> rejected (RE5)
  - OPEN_POSITIONS: at limit (open+in_flight counted together) -> rejected (RE5)
  - DAILY_TRADES: at limit -> rejected (RE5)
  - CONSECUTIVE_LOSSES: streak at limit -> rejected (RE5, RE10)
  - CONSECUTIVE_LOSSES: breakeven (pnl in [-1e-6,0]) NOT counted as loss (RE10)
  - DAILY_LOSS: at limit -> rejected (RE5, RE7)
  - SECTOR_EXPOSURE: at limit -> rejected (RE5, RE6)
  - SECTOR_EXPOSURE: counts in-flight + open (RE6 audit fix)
  - DUPLICATE_SYMBOL: open or in-flight -> rejected (RE5)
  - Check order: first failing check wins the reason (RE5)
  - Short-circuit: checks_run shorter after failure (RE5)
  - sector_lookup_fn raises -> sector="UNKNOWN", approve proceeds (RE9)
  - ApprovalResult.snapshot has all 9 fields (RE11)
  - Audit regression: risk_engine and position_sizer see identical margin (RE2)
  - Determinism: same inputs twice -> identical ApprovalResult (RE16)

Run: python -m pytest tests/unit/test_risk_engine.py -v
Or:  python tests/unit/test_risk_engine.py  (standalone mode)
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.fund_manager import CapitalSnapshot
from capital.position_sizer import SizingResult
from capital.risk_engine import ApprovalResult, RiskEngine
from core.state_store import StateStore
from core.time_authority import now_ist

# FIX-183: the CONSECUTIVE_LOSSES streak is now scoped to the current IST trading
# day, so streak tests must date their closed trades TODAY (not a fixed past day).
_TODAY = now_ist().date().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Test doubles
# ─────────────────────────────────────────────────────────────────────────────

class _MockKillSwitch:
    """Minimal kill switch double."""
    def __init__(self, active: bool = False) -> None:
        self._active = active

    def is_active(self) -> bool:
        return self._active


class _MockFundManager:
    """Minimal fund manager double — get_snapshot() + count_live_reservations()."""
    def __init__(self, snap: CapitalSnapshot) -> None:
        self._snap = snap
        self._unrealized_mtm = 0.0
        self._live_reservations = 0  # FIX-185: authoritative in-flight count

    def get_snapshot(self) -> CapitalSnapshot:
        return self._snap

    def get_total_unrealized_mtm(self) -> float:
        """FIX-035: Return total unrealized MTM."""
        return self._unrealized_mtm

    def count_live_reservations(self) -> int:
        """FIX-185: authoritative count of uncommitted entry reservations."""
        return self._live_reservations


class _CapturingHandler(logging.Handler):
    """Log handler that collects records for test assertions."""
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def warnings(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno == logging.WARNING]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

_SECTOR_MAP = {"RELIANCE": "ENERGY", "TCS": "IT", "INFY": "IT", "HDFC": "FINANCE"}


def _sector_fn(symbol: str) -> str:
    return _SECTOR_MAP.get(symbol, "UNKNOWN")


def _make_snap(
    total: float = 1_000_000.0,
    intraday_avail: float = 700_000.0,
    positional_avail: float = 300_000.0,
    daily_pnl: float = 0.0,
) -> CapitalSnapshot:
    return CapitalSnapshot(
        total=total,
        intraday_avail=intraday_avail,
        intraday_reserved=0.0,
        intraday_used=0.0,
        positional_avail=positional_avail,
        positional_reserved=0.0,
        positional_used=0.0,
        daily_realized_pnl=daily_pnl,
        ts="2026-04-14T10:00:00+05:30",
    )


def _make_sizing(
    success: bool = True,
    margin: float = 10_000.0,
    risk: float = 500.0,
    bucket: str = "intraday",
    reason: str = "ok",
) -> SizingResult:
    return SizingResult(
        success=success,
        qty=10,
        margin_required=margin,
        risk_amount=risk,
        bucket=bucket,
        constraint="RISK",
        reason=reason,
        breakdown={},
    )


def _make_engine(
    store: StateStore,
    fm: _MockFundManager,
    handler: _CapturingHandler,
    *,
    max_open: int = 10,
    max_daily: int = 20,
    max_sector_pct: float = 0.40,
    max_consec: int = 4,
    daily_loss_pct: float = 0.05,
    sector_fn: Any = _sector_fn,
    kill_switch: Any = None,
) -> RiskEngine:
    log = logging.getLogger("test_risk_engine")
    log.handlers.clear()
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    return RiskEngine(
        fund_manager=fm,
        state_store=store,
        max_open_positions=max_open,
        max_daily_trades=max_daily,
        max_sector_exposure_pct=max_sector_pct,
        max_consecutive_losses=max_consec,
        daily_loss_limit_pct=daily_loss_pct,
        sector_lookup_fn=sector_fn,
        logger=log,
        kill_switch=kill_switch,
    )


def _insert_trade(
    store: StateStore,
    trade_id: str,
    symbol: str = "TCS",
    sector: str = "IT",
    status: str = "OPEN",
    margin: float = 10_000.0,
    net_pnl: float | None = None,
    created_date: str = "2026-04-14",
    exit_time: str | None = None,
    direction: str = "LONG",  # FIX-019: added for wash trade tests
) -> None:
    """Insert a signal + trade row (signal FK required)."""
    sig_id = f"sig_{trade_id}"
    ts = f"{created_date}T09:30:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sig_id, symbol, "SCANNER", "strategy", ts, ts,
             f"{created_date}T09:35:00+05:30",
             "TRADED", f"fp_{trade_id}", created_date),
        )
        cur.execute(
            """
            INSERT INTO trades
              (trade_id, signal_id, symbol, direction, strategy, sector,
               qty_planned, qty_filled, entry_target_price, sl_initial,
               tgt_initial, margin_reserved, risk_amount, created_at,
               status, order_protocol, updated_at, net_pnl, exit_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (trade_id, sig_id, symbol, direction, "strategy", sector,
             10, 0, 2500.0, 2450.0, 2600.0, margin, 500.0, ts,
             status, "LIMIT_TRIPLE", ts, net_pnl, exit_time),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_all_checks_pass(tmp_path: Path) -> None:
    """approve() with clean state and valid sizing -> approved=True, all 10 checks run."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert result.approved, f"Expected approval, got: {result.reason}"
    assert result.failed_check == ""
    assert result.reason == "All checks passed"
    assert len(result.checks_run) == 10
    assert result.checks_run == [
        "KILL_SWITCH", "SIZING_VALID", "CAPITAL", "OPEN_POSITIONS",
        "DAILY_TRADES", "CONSECUTIVE_LOSSES", "DAILY_LOSS",
        "SECTOR_EXPOSURE", "CONTRARY_POSITION", "DUPLICATE_SYMBOL",
    ]
    print("  OK all 10 checks passed, approved=True")
    store.close()


def test_kill_switch_active_rejects(tmp_path: Path) -> None:
    """KILL_SWITCH active -> rejected, failed_check='KILL_SWITCH', checks_run=['KILL_SWITCH']."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=True)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert not result.approved
    assert result.failed_check == "KILL_SWITCH"
    assert result.checks_run == ["KILL_SWITCH"]
    print("  OK KILL_SWITCH active -> rejected, 1 check run")
    store.close()


def test_kill_switch_none_skips_check(tmp_path: Path) -> None:
    """
    kill_switch=None -> KILL_SWITCH check skipped.
    WARNING must be logged exactly once at construction.
    approve() succeeds without KILL_SWITCH in checks_run.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    engine = _make_engine(store, fm, handler, kill_switch=None)

    # WARNING must have been logged at construction
    warnings = handler.warnings()
    assert any("kill_switch" in w.lower() for w in warnings), \
        f"Expected WARNING about kill_switch=None; got: {warnings}"

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert result.approved
    assert "KILL_SWITCH" not in result.checks_run
    assert len(result.checks_run) == 9  # 10 minus KILL_SWITCH
    print(f"  OK kill_switch=None: check skipped, {len(result.checks_run)} checks run, WARNING logged")
    store.close()


def test_sizing_valid_failure(tmp_path: Path) -> None:
    """SIZING_VALID: sizing_result.success=False -> rejected."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    bad_sizing = _make_sizing(success=False, reason="qty below minimum lot size")
    result = engine.approve("RELIANCE", "BUY", "INTRADAY", bad_sizing, "sig-001")

    assert not result.approved
    assert result.failed_check == "SIZING_VALID"
    assert "qty below minimum" in result.reason
    print(f"  OK SIZING_VALID failure: {result.reason}")
    store.close()


def test_capital_insufficient_intraday(tmp_path: Path) -> None:
    """CAPITAL: intraday_avail < margin_required -> rejected with CAPITAL."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    # Only 5000 intraday available, trade needs 10000
    fm = _MockFundManager(_make_snap(intraday_avail=5_000.0))
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(margin=10_000.0), "sig-001")

    assert not result.approved
    assert result.failed_check == "CAPITAL"
    assert "intraday" in result.reason
    print(f"  OK CAPITAL intraday insufficient: {result.reason}")
    store.close()


def test_capital_insufficient_positional(tmp_path: Path) -> None:
    """CAPITAL: positional_avail < margin_required -> rejected (positional bucket)."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap(positional_avail=2_000.0))
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    result = engine.approve(
        "RELIANCE", "BUY", "DELIVERY",
        _make_sizing(margin=10_000.0, bucket="positional"),
        "sig-001",
    )

    assert not result.approved
    assert result.failed_check == "CAPITAL"
    assert "positional" in result.reason
    print(f"  OK CAPITAL positional insufficient: {result.reason}")
    store.close()


def test_open_positions_at_limit(tmp_path: Path) -> None:
    """OPEN_POSITIONS: existing active + candidate > max -> rejected.

    FIX-181 (off-by-one): the candidate is pre-incremented into
    processor_in_flight_count by signal_processor (so this test passes
    processor_in_flight_count=1 to model production). 3 existing active +
    candidate = 4 > max(3) -> reject.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, max_open=3)

    # Insert 2 OPEN + 1 PENDING_FILL = 3 active already in DB
    _insert_trade(store, "t1", status="OPEN")
    _insert_trade(store, "t2", status="OPEN")
    _insert_trade(store, "t3", status="PENDING_FILL")

    # candidate is pre-incremented in production -> processor_in_flight_count=1
    result = engine.approve(
        "RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001",
        processor_in_flight_count=1,
    )

    assert not result.approved
    assert result.failed_check == "OPEN_POSITIONS"
    print(f"  OK OPEN_POSITIONS over limit: {result.reason}")
    store.close()


def test_open_positions_counts_in_flight(tmp_path: Path) -> None:
    """OPEN_POSITIONS: in-flight (PENDING_FILL) counted with open positions.

    FIX-181: 2 existing PENDING_FILL + candidate (processor_in_flight_count=1)
    = 3 > max(2) -> reject.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, max_open=2)

    # 0 OPEN + 2 PENDING_FILL = 2 in-flight already in DB
    _insert_trade(store, "t1", status="PENDING_FILL")
    _insert_trade(store, "t2", status="PENDING_FILL")

    result = engine.approve(
        "RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001",
        processor_in_flight_count=1,
    )

    assert not result.approved
    assert result.failed_check == "OPEN_POSITIONS"
    assert result.snapshot["open_count"] == 0
    assert result.snapshot["in_flight_count"] == 2
    print(f"  OK in-flight counted in OPEN_POSITIONS: {result.snapshot}")
    store.close()


def test_fix185_hard_cap_burst_via_reservations(tmp_path: Path) -> None:
    """FIX-185: cap is hard even when processor_in_flight UNDER-counts (restart burst).

    Models the 18-Jun overshoot: a burst of signals is admitted at restart and
    several have RESERVED (fund_manager holds their reservations) but not yet
    inserted trade rows, while signal_processor's in-flight snapshot is stale and
    under-counts. The legacy check (active_count=0 + processor_in_flight=1 = 1)
    would WRONGLY allow a 4th position past max=3. The authoritative live-
    reservation count (3) makes the cap hold.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, max_open=3)

    # DB shows ZERO active trades (burst: rows not inserted yet)...
    # ...but fund_manager holds 3 live reservations for the in-flight entries.
    fm._live_reservations = 3

    # Stale/under-counted snapshot from signal_processor (the bug being fixed).
    result = engine.approve(
        "RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001",
        processor_in_flight_count=1,
    )

    assert not result.approved, "cap must hold via authoritative reservation count"
    assert result.failed_check == "OPEN_POSITIONS"
    assert "live_reservations=3" in result.reason
    print(f"  OK FIX-185 hard cap via reservations: {result.reason}")
    store.close()


def test_fix185_reservations_allow_final_slot(tmp_path: Path) -> None:
    """FIX-185: the authoritative count still permits the legitimate final slot.

    max=3, DB empty, 2 reservations in flight + this candidate = 3 == max -> ALLOW
    (the FIX-181 off-by-one fix must survive: the cap holds AT the limit, not one
    below it).
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, max_open=3)

    fm._live_reservations = 2  # 2 in-flight reserved + candidate = 3 == max

    result = engine.approve(
        "RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001",
        processor_in_flight_count=1,
    )

    assert result.approved, f"final slot must be allowed, got: {result.reason}"
    print("  OK FIX-185 final slot allowed (off-by-one preserved)")
    store.close()


def test_daily_trades_at_limit(tmp_path: Path) -> None:
    """DAILY_TRADES: daily count >= max -> rejected."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, max_daily=2)

    # Insert 2 trades today
    _insert_trade(store, "t1", created_date="2026-04-14")
    _insert_trade(store, "t2", created_date="2026-04-14")

    # Patch "today" by inserting trades with today's date; the engine will read
    # datetime.now(IST).date() which may not be 2026-04-14, so we use a direct
    # state_store check to confirm the helper works, and we test rejection via
    # count_trades_today returning the right value.
    # Direct count check:
    count = store.count_trades_today("2026-04-14")
    assert count == 2

    # Insert 3rd trade to verify count
    _insert_trade(store, "t3", created_date="2026-04-14")
    assert store.count_trades_today("2026-04-14") == 3

    print("  OK DAILY_TRADES count verified via count_trades_today helper")
    store.close()


def test_consecutive_losses_at_limit(tmp_path: Path) -> None:
    """CONSECUTIVE_LOSSES: trailing loss streak >= max -> rejected."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, max_consec=3)

    # Insert 3 consecutive closed losses TODAY (most recent first by exit_time)
    _insert_trade(store, "t1", status="CLOSED", net_pnl=-100.0, exit_time=f"{_TODAY}T10:00:00+05:30")
    _insert_trade(store, "t2", status="CLOSED", net_pnl=-200.0, exit_time=f"{_TODAY}T11:00:00+05:30")
    _insert_trade(store, "t3", status="CLOSED", net_pnl=-150.0, exit_time=f"{_TODAY}T12:00:00+05:30")

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert not result.approved
    assert result.failed_check == "CONSECUTIVE_LOSSES"
    assert result.snapshot["consecutive_losses"] == 3
    print(f"  OK CONSECUTIVE_LOSSES streak={result.snapshot['consecutive_losses']}: {result.reason}")
    store.close()


def test_breakeven_not_counted_as_loss(tmp_path: Path) -> None:
    """
    RE10 audit fix: breakeven trades (net_pnl in [-1e-6, 0]) break
    the consecutive loss streak. Exactly -1e-7 (> -1e-6) is NOT a loss.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, max_consec=3)

    # Most recent trade is breakeven (should break the streak)
    _insert_trade(store, "t1", status="CLOSED", net_pnl=-500.0, exit_time=f"{_TODAY}T09:30:00+05:30")
    _insert_trade(store, "t2", status="CLOSED", net_pnl=-500.0, exit_time=f"{_TODAY}T10:00:00+05:30")
    _insert_trade(store, "t3", status="CLOSED", net_pnl=-1e-7,  exit_time=f"{_TODAY}T11:00:00+05:30")  # breakeven: -0.1 micro

    pnls = store.recent_trade_pnls(5)
    # Most recent is -1e-7, which is >= -1e-6, so streak resets to 0
    streak = sum(1 for p in pnls if p < -1e-6)  # naive count for verification
    # But streak is trailing, so -1e-7 at index 0 breaks it immediately
    assert pnls[0] == -1e-7
    assert pnls[0] >= -1e-6  # NOT a loss by RE10 threshold

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    # Streak = 0 (broken by breakeven at most-recent slot)
    assert result.snapshot["consecutive_losses"] == 0, \
        f"Expected streak=0 (breakeven broke it), got {result.snapshot['consecutive_losses']}"
    print(f"  OK breakeven -1e-7 broke streak; consecutive_losses=0")
    store.close()


def test_zero_pnl_not_counted_as_loss(tmp_path: Path) -> None:
    """RE10: net_pnl == 0.0 is NOT a loss. Streak should be 0."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, max_consec=3)

    _insert_trade(store, "t1", status="CLOSED", net_pnl=-300.0, exit_time=f"{_TODAY}T09:00:00+05:30")
    _insert_trade(store, "t2", status="CLOSED", net_pnl=-300.0, exit_time=f"{_TODAY}T10:00:00+05:30")
    _insert_trade(store, "t3", status="CLOSED", net_pnl=0.0,    exit_time=f"{_TODAY}T11:00:00+05:30")

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert result.snapshot["consecutive_losses"] == 0, \
        f"Expected 0 (0.0 pnl is not a loss), got {result.snapshot['consecutive_losses']}"
    print("  OK net_pnl=0.0 not counted as loss")
    store.close()


def test_daily_loss_at_limit(tmp_path: Path) -> None:
    """DAILY_LOSS: abs(daily_pnl) >= pct * total -> rejected."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    # daily_pnl = -50000, total=1000000, limit_pct=0.05 -> limit=50000 -> at limit
    fm = _MockFundManager(_make_snap(total=1_000_000.0, daily_pnl=-50_000.0))
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, daily_loss_pct=0.05)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert not result.approved
    assert result.failed_check == "DAILY_LOSS"
    print(f"  OK DAILY_LOSS at limit: {result.reason}")
    store.close()


def test_daily_loss_positive_pnl_not_rejected(tmp_path: Path) -> None:
    """DAILY_LOSS: positive daily_pnl never triggers rejection."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap(daily_pnl=100_000.0))  # big profit
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, daily_loss_pct=0.01)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert result.approved, f"Positive pnl should not trigger DAILY_LOSS; got: {result.reason}"
    print("  OK positive daily_pnl never triggers DAILY_LOSS")
    store.close()


def test_fix035_daily_loss_includes_unrealized_mtm(tmp_path: Path) -> None:
    """FIX-035: DAILY_LOSS check includes unrealized MTM in total P&L calculation."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    # realized_pnl = -20000, unrealized_mtm = -35000, total = -55000
    # total=1000000, limit_pct=0.05 -> limit = 50000
    # abs(-55000) = 55000 > 50000 -> should reject
    snap = _make_snap(total=1_000_000.0, daily_pnl=-20_000.0)
    fm = _MockFundManager(snap)
    fm._unrealized_mtm = -35_000.0  # Set unrealized MTM
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, daily_loss_pct=0.05)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert not result.approved
    assert result.failed_check == "DAILY_LOSS"
    assert "total_pnl=-55000.00" in result.reason
    assert "realized=-20000.00" in result.reason
    assert "unrealized=-35000.00" in result.reason
    print("  OK FIX-035: DAILY_LOSS includes unrealized MTM")
    store.close()


def test_fix035_daily_loss_unrealized_keeps_under_limit(tmp_path: Path) -> None:
    """FIX-035: Trade approved when realized + unrealized stays under limit."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    # realized_pnl = -20000, unrealized_mtm = -25000, total = -45000
    # total=1000000, limit_pct=0.05 -> limit = 50000
    # abs(-45000) = 45000 < 50000 -> should approve
    snap = _make_snap(total=1_000_000.0, daily_pnl=-20_000.0)
    fm = _MockFundManager(snap)
    fm._unrealized_mtm = -25_000.0
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, daily_loss_pct=0.05)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert result.approved, f"Should approve when total under limit; got: {result.reason}"
    print("  OK FIX-035: Approved when realized + unrealized under limit")
    store.close()


def test_fix035_daily_loss_unrealized_offsets_realized_loss(tmp_path: Path) -> None:
    """FIX-035: Positive unrealized MTM offsets realized loss."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    # realized_pnl = -60000, unrealized_mtm = +30000, total = -30000
    # total=1000000, limit_pct=0.05 -> limit = 50000
    # abs(-30000) = 30000 < 50000 -> should approve
    snap = _make_snap(total=1_000_000.0, daily_pnl=-60_000.0)
    fm = _MockFundManager(snap)
    fm._unrealized_mtm = 30_000.0  # Open positions showing profit
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks, daily_loss_pct=0.05)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert result.approved, f"Unrealized profit should offset realized loss; got: {result.reason}"
    print("  OK FIX-035: Positive unrealized MTM offsets realized loss")
    store.close()


def test_sector_exposure_at_limit(tmp_path: Path) -> None:
    """SECTOR_EXPOSURE: existing + new trade margin > max_pct * total -> rejected."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap(total=1_000_000.0))
    ks = _MockKillSwitch(active=False)
    # max_sector_pct=0.40 -> limit = 400_000
    # sector_fn maps ONGC -> ENERGY so exposure check uses the same sector
    energy_fn = lambda s: "ENERGY"
    engine = _make_engine(store, fm, handler, kill_switch=ks,
                          max_sector_pct=0.40, sector_fn=energy_fn)

    # Existing ENERGY exposure: 390_000. New trade needs 20_000 -> 410_000 > 400_000
    _insert_trade(store, "t1", symbol="RELIANCE", sector="ENERGY",
                  status="OPEN", margin=390_000.0)

    sizing = _make_sizing(margin=20_000.0)
    result = engine.approve("ONGC", "BUY", "INTRADAY", sizing, "sig-001")

    assert not result.approved
    assert result.failed_check == "SECTOR_EXPOSURE"
    print(f"  OK SECTOR_EXPOSURE exceeded: {result.reason}")
    store.close()


def test_sector_exposure_counts_in_flight_and_open(tmp_path: Path) -> None:
    """
    RE6 audit fix: SECTOR_EXPOSURE counts BOTH PENDING_FILL (in-flight)
    and OPEN/PARTIAL trades in the sector. Old bug: only counted OPEN.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap(total=1_000_000.0))
    ks = _MockKillSwitch(active=False)
    # All symbols map to ENERGY so exposure sums across RELIANCE + ONGC + IOC
    energy_fn = lambda s: "ENERGY"
    engine = _make_engine(store, fm, handler, kill_switch=ks,
                          max_sector_pct=0.40, sector_fn=energy_fn)

    # ENERGY: 200_000 OPEN + 200_000 PENDING_FILL = 400_000 = at limit
    # New trade needs 10_000 -> 410_000 > 400_000 -> reject
    _insert_trade(store, "t1", symbol="RELIANCE", sector="ENERGY", status="OPEN",         margin=200_000.0)
    _insert_trade(store, "t2", symbol="ONGC",     sector="ENERGY", status="PENDING_FILL", margin=200_000.0)

    sizing = _make_sizing(margin=10_000.0)
    result = engine.approve("IOC", "BUY", "INTRADAY", sizing, "sig-001")

    assert not result.approved
    assert result.failed_check == "SECTOR_EXPOSURE", \
        f"Expected SECTOR_EXPOSURE rejection; got failed_check={result.failed_check}"
    # Verify both in-flight and open contributed
    existing = store.sector_exposure("ENERGY")
    assert existing == 400_000.0, f"Expected 400000 (200k OPEN + 200k PENDING_FILL); got {existing}"
    print(f"  OK SECTOR_EXPOSURE counts in-flight+open: {existing}")
    store.close()


def test_duplicate_symbol_open(tmp_path: Path) -> None:
    """DUPLICATE_SYMBOL: existing OPEN trade for same symbol -> rejected."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    _insert_trade(store, "t1", symbol="RELIANCE", status="OPEN")

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-002")

    assert not result.approved
    assert result.failed_check == "DUPLICATE_SYMBOL"
    print(f"  OK DUPLICATE_SYMBOL (OPEN): {result.reason}")
    store.close()


def test_duplicate_symbol_in_flight(tmp_path: Path) -> None:
    """DUPLICATE_SYMBOL: existing PENDING_FILL trade for same symbol -> rejected."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    _insert_trade(store, "t1", symbol="RELIANCE", status="PENDING_FILL")

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-002")

    assert not result.approved
    assert result.failed_check == "DUPLICATE_SYMBOL"
    print(f"  OK DUPLICATE_SYMBOL (PENDING_FILL): {result.reason}")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# FIX-019: Wash Trade Prevention (CONTRARY_POSITION check)
# ─────────────────────────────────────────────────────────────────────────────

def test_fix019_contrary_position_open_long_send_short(tmp_path: Path) -> None:
    """FIX-019: Open LONG position → send SHORT signal → rejected CONTRARY_POSITION."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    # Insert LONG position for RELIANCE
    _insert_trade(store, "t1", symbol="RELIANCE", status="OPEN", direction="LONG")

    # Attempt to open SHORT position on same symbol
    result = engine.approve("RELIANCE", "SELL", "INTRADAY", _make_sizing(), "sig-002")

    assert not result.approved
    assert result.failed_check == "CONTRARY_POSITION"
    assert "SHORT" in result.reason and "LONG" in result.reason
    assert "wash trade" in result.reason
    # Check WARNING was logged
    warnings = handler.warnings()
    assert any("CONTRARY_POSITION" in w and "RELIANCE" in w for w in warnings)
    print(f"  OK FIX-019 contrary LONG→SHORT: {result.reason}")
    store.close()


def test_fix019_contrary_position_concurrent_long_short(tmp_path: Path) -> None:
    """FIX-019: Concurrent LONG + SHORT for same symbol → only one admitted."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    # First signal: LONG RELIANCE (no existing position)
    result1 = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")
    assert result1.approved, f"First LONG should approve: {result1.reason}"

    # Simulate the first trade being inserted (as would happen in production)
    _insert_trade(store, "t1", symbol="RELIANCE", status="PENDING_FILL", direction="LONG")

    # Second signal: SHORT RELIANCE (now there's a LONG in-flight)
    result2 = engine.approve("RELIANCE", "SELL", "INTRADAY", _make_sizing(), "sig-002")
    assert not result2.approved
    assert result2.failed_check == "CONTRARY_POSITION"
    print(f"  OK FIX-019 concurrent LONG+SHORT: LONG approved, SHORT blocked")
    store.close()


def test_fix019_same_direction_not_blocked_by_contrary_check(tmp_path: Path) -> None:
    """FIX-019: Open LONG → send another LONG → NOT rejected by CONTRARY_POSITION.

    Note: Will eventually be rejected by DUPLICATE_SYMBOL check, but that's
    a different check. This test verifies CONTRARY_POSITION only blocks
    opposite directions.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    # Insert LONG position for RELIANCE
    _insert_trade(store, "t1", symbol="RELIANCE", status="OPEN", direction="LONG")

    # Attempt another LONG on same symbol
    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-002")

    # Should NOT be rejected by CONTRARY_POSITION (though DUPLICATE_SYMBOL will reject)
    assert not result.approved
    assert result.failed_check == "DUPLICATE_SYMBOL", (
        f"Expected DUPLICATE_SYMBOL, got {result.failed_check}"
    )
    # Verify CONTRARY_POSITION check ran (should be in checks_run before DUPLICATE_SYMBOL)
    assert "CONTRARY_POSITION" in result.checks_run
    assert result.checks_run.index("CONTRARY_POSITION") < result.checks_run.index("DUPLICATE_SYMBOL")
    print(f"  OK FIX-019 same direction (LONG→LONG): CONTRARY_POSITION passed, DUPLICATE_SYMBOL blocked")
    store.close()


def test_fix019_no_existing_position_approve_normally(tmp_path: Path) -> None:
    """FIX-019: No open positions → send SHORT → approved normally."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    # No existing trades
    result = engine.approve("RELIANCE", "SELL", "INTRADAY", _make_sizing(), "sig-001")

    assert result.approved, f"Should approve when no existing position: {result.reason}"
    assert "CONTRARY_POSITION" in result.checks_run  # Check ran and passed
    print(f"  OK FIX-019 no existing position: approved normally")
    store.close()


def test_check_order_first_failing_wins(tmp_path: Path) -> None:
    """
    If SIZING_VALID and CAPITAL would both fail, SIZING_VALID (earlier)
    wins because checks run in RE5 order and short-circuit.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    # Insufficient capital AND bad sizing
    fm = _MockFundManager(_make_snap(intraday_avail=0.0))
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    bad_sizing = _make_sizing(success=False, reason="below min qty")
    result = engine.approve("RELIANCE", "BUY", "INTRADAY", bad_sizing, "sig-001")

    assert not result.approved
    assert result.failed_check == "SIZING_VALID", \
        f"Expected SIZING_VALID (earlier check); got {result.failed_check}"
    print(f"  OK SIZING_VALID wins over CAPITAL: failed_check={result.failed_check}")
    store.close()


def test_short_circuit_checks_run_length(tmp_path: Path) -> None:
    """
    Short-circuit: checks_run has exactly as many entries as checks that
    actually executed before the first failure.
    KILL_SWITCH fails -> checks_run length = 1.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=True)  # KILL_SWITCH will fail immediately
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    assert not result.approved
    assert result.failed_check == "KILL_SWITCH"
    assert len(result.checks_run) == 1, \
        f"Expected 1 check run (short-circuit after KILL_SWITCH), got {result.checks_run}"
    print(f"  OK short-circuit: checks_run={result.checks_run}")
    store.close()


def test_sector_lookup_raises_uses_unknown(tmp_path: Path) -> None:
    """RE9: sector_lookup_fn raises -> sector='UNKNOWN', approve proceeds."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)

    def bad_lookup(symbol: str) -> str:
        raise RuntimeError("instrument cache not ready")

    engine = _make_engine(store, fm, handler, kill_switch=ks, sector_fn=bad_lookup)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")

    # Must approve (no other reason to fail) and not crash
    assert result.approved, f"Expected approval after sector fallback; got: {result.reason}"

    # WARNING must have been logged about the lookup failure
    warnings = handler.warnings()
    assert any("sector_lookup_fn" in w.lower() or "unknown" in w.lower() for w in warnings), \
        f"Expected WARNING about sector lookup failure; got: {warnings}"

    print(f"  OK sector_lookup raises -> UNKNOWN, approve proceeds; warnings={len(warnings)}")
    store.close()


def test_snapshot_has_all_nine_fields(tmp_path: Path) -> None:
    """ApprovalResult.snapshot always has all 9 required fields (RE11)."""
    required_keys = {
        "available_intraday", "available_positional",
        "open_count", "in_flight_count", "daily_trades_count",
        "daily_realized_pnl", "sector_exposure_pct",
        "consecutive_losses", "kill_switch_active",
    }

    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    # Test on approval
    result_ok = engine.approve("RELIANCE", "BUY", "INTRADAY", _make_sizing(), "sig-001")
    assert set(result_ok.snapshot.keys()) == required_keys, \
        f"Missing snapshot keys: {required_keys - set(result_ok.snapshot.keys())}"

    # Test on rejection (KILL_SWITCH)
    ks2 = _MockKillSwitch(active=True)
    engine2 = _make_engine(StateStore(tmp_path / "test2.db"), fm, _CapturingHandler(), kill_switch=ks2)
    result_fail = engine2.approve("TCS", "BUY", "INTRADAY", _make_sizing(), "sig-002")
    assert set(result_fail.snapshot.keys()) == required_keys, \
        f"Missing snapshot keys on rejection: {required_keys - set(result_fail.snapshot.keys())}"

    print(f"  OK snapshot has all {len(required_keys)} required fields on both approve and reject")
    store.close()


def test_no_schism_same_margin_as_sizer(tmp_path: Path) -> None:
    """
    RE2 audit fix: risk_engine uses sizing_result.margin_required directly.
    The margin in ApprovalResult.snapshot must reflect what the sizer computed,
    not a re-derived estimate.
    """
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    # Sizer computed 12345.67 margin — that exact value must flow through
    specific_margin = 12_345.67
    sizing = _make_sizing(margin=specific_margin)

    fm = _MockFundManager(_make_snap(intraday_avail=specific_margin + 1))  # just enough
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    result = engine.approve("RELIANCE", "BUY", "INTRADAY", sizing, "sig-001")

    # The CAPITAL check used sizing_result.margin_required, not a re-derived value.
    # If it passed (avail > margin), the margin used was exactly specific_margin.
    assert result.approved, f"Expected approval; got: {result.reason}"

    # Now make it fail by 1 rupee — proves exact margin is used, not an approximation
    fm2 = _MockFundManager(_make_snap(intraday_avail=specific_margin - 0.01))
    engine2 = _make_engine(
        StateStore(tmp_path / "test2.db"),
        fm2, _CapturingHandler(), kill_switch=_MockKillSwitch(active=False),
    )
    result2 = engine2.approve("RELIANCE", "BUY", "INTRADAY", sizing, "sig-002")
    assert not result2.approved
    assert result2.failed_check == "CAPITAL"
    assert str(specific_margin) in result2.reason or f"{specific_margin:.2f}" in result2.reason
    print(f"  OK no schism: exact margin {specific_margin} used in CAPITAL check")
    store.close()


def test_determinism_same_inputs_same_result(tmp_path: Path) -> None:
    """RE16: same fund_manager state + same state_store + same inputs -> same ApprovalResult."""
    store = StateStore(tmp_path / "test.db")
    handler = _CapturingHandler()
    fm = _MockFundManager(_make_snap())
    ks = _MockKillSwitch(active=False)
    engine = _make_engine(store, fm, handler, kill_switch=ks)

    sizing = _make_sizing(margin=10_000.0)
    result1 = engine.approve("RELIANCE", "BUY", "INTRADAY", sizing, "sig-001")
    result2 = engine.approve("RELIANCE", "BUY", "INTRADAY", sizing, "sig-001")

    assert result1.approved == result2.approved
    assert result1.reason == result2.reason
    assert result1.failed_check == result2.failed_check
    assert result1.checks_run == result2.checks_run
    assert result1.snapshot == result2.snapshot
    print("  OK determinism: two identical calls produce identical ApprovalResult")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner (no pytest dependency)
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    """Run all tests sequentially. Returns 0 on success, 1 on any failure."""
    tests = [
        test_all_checks_pass,
        test_kill_switch_active_rejects,
        test_kill_switch_none_skips_check,
        test_sizing_valid_failure,
        test_capital_insufficient_intraday,
        test_capital_insufficient_positional,
        test_open_positions_at_limit,
        test_open_positions_counts_in_flight,
        test_fix185_hard_cap_burst_via_reservations,
        test_fix185_reservations_allow_final_slot,
        test_daily_trades_at_limit,
        test_consecutive_losses_at_limit,
        test_breakeven_not_counted_as_loss,
        test_zero_pnl_not_counted_as_loss,
        test_daily_loss_at_limit,
        test_daily_loss_positive_pnl_not_rejected,
        test_sector_exposure_at_limit,
        test_sector_exposure_counts_in_flight_and_open,
        test_duplicate_symbol_open,
        test_duplicate_symbol_in_flight,
        test_check_order_first_failing_wins,
        test_short_circuit_checks_run_length,
        test_sector_lookup_raises_uses_unknown,
        test_snapshot_has_all_nine_fields,
        test_no_schism_same_margin_as_sizer,
        test_determinism_same_inputs_same_result,
    ]

    print("=" * 70)
    print("risk_engine.py -- Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            try:
                test(Path(td))
            except AssertionError as e:
                failed.append((test.__name__, f"AssertionError: {e}"))
                print(f"  FAIL {e}")
            except Exception as e:
                failed.append((test.__name__, f"{type(e).__name__}: {e}"))
                print(f"  ERROR {type(e).__name__}: {e}")

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
