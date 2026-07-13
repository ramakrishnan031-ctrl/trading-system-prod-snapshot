"""Q4(a) gate-8 SECTOR_EXPOSURE TOCTOU (FIX-185-class), proven with REAL collaborators.

Root cause: RiskEngine.approve()'s SECTOR_EXPOSURE gate summed only StateStore.sector_exposure
(TRADE ROWS: PENDING_FILL/OPEN/PARTIAL). A RESERVED-NOT-PLACED reservation — fund_manager.reserve()
succeeded but its PENDING_FILL trade row is written LATER, outside portfolio_lock — is invisible to
that query, so two concurrent same-sector signals can each read stale exposure and TOGETHER breach
max_sector_exposure_pct.

Fix: approve() now hardens the gate with fund_manager.get_live_reservations(), partitioned to avoid
double-counting a PENDING_FILL that still holds its reservation, and max()-floored by DB truth so it
can ONLY harden (RiskEngine._effective_sector_margin).

Real collaborators: a real schema-backed StateStore, a real FundManager (real reserve() producing a
real _Reservation object under the real portfolio_lock), and the REAL RiskEngine. Because the
reservation comes from the genuine fm.reserve(), this ALSO proves _effective_sector_margin reads the
real _Reservation's .symbol / .margin fields — a hand-rolled mock could get those names wrong and
still pass. Sibling of tests/unit/test_h7_strategy_cap_toctou.py (the strategy-cap TOCTOU).
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from capital.fund_manager import FundManager
from capital.position_sizer import SizingResult
from capital.risk_engine import RiskEngine
from core.events import EventBus
from core.state_store import StateStore
from core.time_authority import today_ist

_LOG = logging.getLogger("test_gate8_sector_toctou")


# ── real-collaborator harness ────────────────────────────────────────────────
def _make_fm(store: StateStore) -> FundManager:
    fm = FundManager(
        state_store=store, bus=EventBus(), logger=_LOG,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30, daily_loss_limit_pct=0.10,
    )
    fm.initialize(1_000_000.0)
    return fm


def _make_engine(store: StateStore, fm: FundManager, *, max_sector_pct: float) -> RiskEngine:
    # Every non-sector threshold is set generous so approve() reaches gate 8 (SECTOR_EXPOSURE)
    # and the sector gate is the ONLY binding constraint under test.
    return RiskEngine(
        fund_manager=fm, state_store=store,
        max_open_positions=50, max_daily_trades=50, max_sector_exposure_pct=max_sector_pct,
        max_consecutive_losses=99, daily_loss_limit_pct=0.99,
        sector_lookup_fn=lambda s: "ENERGY", logger=_LOG, kill_switch=None,
    )


def _insert_open_energy_trade(store: StateStore, symbol: str = "RELIANCE", margin: float = 100_000.0) -> None:
    """A real signal+trade row pair, status OPEN, sector ENERGY (schema-backed)."""
    tid = str(uuid.uuid4())
    sid = f"sig_{tid}"
    ts = f"{today_ist()}T09:30:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,received_at,"
            "expires_at,status,fingerprint,fingerprint_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, symbol, "SC", "strategy", ts, ts, ts, "TRADED", f"fp_{tid}", today_ist()),
        )
        cur.execute(
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,sector,qty_planned,"
            "qty_filled,entry_target_price,sl_initial,tgt_initial,margin_reserved,risk_amount,"
            "created_at,status,order_protocol,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, sid, symbol, "LONG", "strategy", "ENERGY", 10, 10, 2500.0, 2450.0, 2600.0,
             margin, 500.0, ts, "OPEN", "LIMIT_TRIPLE", ts),
        )


def _sizing(margin: float) -> SizingResult:
    return SizingResult(success=True, qty=10, margin_required=margin, risk_amount=500.0,
                        bucket="intraday", constraint="RISK", reason="ok", breakdown={})


# ── Test 1: the fix reads the REAL _Reservation and folds it in ──────────────────
def test_reserved_not_placed_folds_into_effective_via_real_reservation(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "g8.db")
    fm = _make_fm(store)
    _insert_open_energy_trade(store, margin=100_000.0)                       # DB truth = 100_000

    r = fm.reserve("ONGCRES", 100, 500.0, "INTRADAY", "rsig", strategy="s")  # reserved-not-placed
    assert r.success, r
    live = fm.get_live_reservations()
    assert len(live) == 1
    reserved = sum(x.margin for x in live.values())
    assert reserved > 0.0

    engine = _make_engine(store, fm, max_sector_pct=0.40)
    existing = store.sector_exposure("ENERGY")
    assert existing == 100_000.0                       # the reservation is INVISIBLE to DB truth
    effective = engine._effective_sector_margin("ENERGY", existing)
    # max(100_000, open_partial(100_000) + reserved) == 100_000 + reserved  (real .symbol/.margin)
    assert effective == existing + reserved
    assert effective > existing                        # the hardening actually fired


# ── Test 2: full approve() — rejects ONLY because the reservation is now counted ──
def test_full_approve_rejects_when_reservation_pushes_over_cap(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "g8b.db")
    fm = _make_fm(store)
    _insert_open_energy_trade(store, margin=100_000.0)
    r = fm.reserve("ONGCRES", 100, 500.0, "INTRADAY", "rsig", strategy="s")
    assert r.success
    reserved = sum(x.margin for x in fm.get_live_reservations().values())
    assert reserved > 0.0

    total = fm.get_snapshot().total
    new_margin = 10_000.0
    # Limit chosen STRICTLY BETWEEN the two projections:
    #   OLD (DB truth) = 100_000 + new_margin        <= limit  -> old code APPROVES
    #   NEW (effective)= 100_000 + reserved + new_margin > limit -> new code REJECTS
    limit = 100_000.0 + new_margin + reserved / 2.0
    engine = _make_engine(store, fm, max_sector_pct=limit / total)

    assert 100_000.0 + new_margin <= limit             # the old outside-reservation gate would pass
    result = engine.approve("IOC", "BUY", "INTRADAY", _sizing(new_margin), "sig-b")
    assert not result.approved, result.snapshot        # RED against pre-fix code (it approves)
    assert result.failed_check == "SECTOR_EXPOSURE", result.failed_check


# ── Test 3: no reservation -> byte-identical to DB truth (real fm, empty reservations) ──
def test_no_reservation_effective_equals_db_truth(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "g8c.db")
    fm = _make_fm(store)
    _insert_open_energy_trade(store, margin=77_000.0)
    engine = _make_engine(store, fm, max_sector_pct=0.40)
    existing = store.sector_exposure("ENERGY")
    assert existing == 77_000.0
    assert fm.get_live_reservations() == {}            # nothing reserved
    assert engine._effective_sector_margin("ENERGY", existing) == existing   # byte-identical
