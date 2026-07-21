"""Wave-5 · M-C1 — LIVE warm-restart capital double-count fix (fund_manager seed).

Root cause (audit M-C1, full_system_audit_04july2026.md:113): on a LIVE mid-day
warm restart the broker's net margin (the live seed, now `compute_live_seed()` in
main.py) ALREADY reflects today's
realized PnL, but rehydrate Phase 2 re-applies that same PnL (fm_ledger RELEASE_USED
carryover) -> the live capital seed double-counts it (inflated reservable capital +
a phantom -today_pnl drift on the next sync_from_broker). PAPER was already correct:
its static paper_capital seed excludes today's PnL, so Phase 2 adds it exactly once.

Fix: the LIVE seed subtracts today's realized-PnL carryover -
  compute_live_seed():  _startup_capital = broker.net - today_realized_pnl_carryover(floor)
so seed + Phase 2 == broker.net BY CONSTRUCTION. The carryover Sigma is summed over
the EXACT fm_ledger rows Phase 2 walks (shared helper _today_release_used_pnl_rows),
so the subtraction and the re-addition cancel exactly, incl. sign. PAPER is UNTOUCHED
(this RESTORES parity by fixing the buggy side to match the correct side).

Key identity these tests exploit: broker.net - Sigma == (BASE + today_pnl) - today_pnl
== BASE, i.e. the fixed LIVE seed reduces EXACTLY to paper's static PnL-excluded base,
for any sign/count of closed trades.

Real collaborators: a real StateStore (schema-backed tmp DB) and a real FundManager
(real reserve/commit_to_used/release_used/initialize/rehydrate_from_open_trades/
today_realized_pnl_carryover/sync_from_broker/get_snapshot -> real fm_ledger rows).
Simulated: `broker_net` is computed as BASE + today's realized PnL (the broker margin
main.py would read at a warm restart -- no live broker call); the RED path replays the
PRE-fix main.py seed (broker_net) and the GREEN path replays the POST-fix seed
(broker_net - carryover). No mock of the ledger, rehydrate, or the capital math.

Run: python -m pytest tests/unit/test_mc1_live_seed_rehydrate.py -v
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import Path

import pytest

from capital.fund_manager import FundManager
from core.events import EventBus
from core.state_store import StateStore
from core.time_authority import now_ist

_SCHEMA = Path(__file__).parent.parent.parent / "core" / "schema.sql"
_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
BASE = 100_000.0   # opening equity (PnL-excluded) == paper_capital semantics


# ── real-collaborator harness ────────────────────────────────────────────────
def _store(path: Path) -> StateStore:
    return StateStore(path, _SCHEMA)


def _fm(store: StateStore) -> FundManager:
    return FundManager(
        state_store=store,
        bus=EventBus(),
        logger=logging.getLogger("test_mc1"),
        intraday_bucket_pct=0.70,
        positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.10,
        leverage_map=_LEV,
        slm_margin_buffer_pct=0.0,
    )


def _init_fm(store: StateStore, balance: float) -> FundManager:
    fm = _fm(store)
    fm.initialize(balance)
    return fm


def _seed_open_trade(store: StateStore, *, signal_id: str, trade_id: str,
                     symbol: str, qty: int, price: float) -> None:
    """Minimal signals->trades->orders rows so rehydrate Phase 1 replays this as
    a still-open position (paired with a real reserve+commit on the same signal)."""
    ts = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, ?, 'SCAN', 'strategy', ?, ?, ?, 'TRADED', ?, ?, ?)""",
            (signal_id, symbol, ts, ts, ts, f"fp_{trade_id}",
             now_ist().date().isoformat(), price),
        )
        cur.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, strategy, sector,
                qty_planned, qty_filled, entry_target_price, entry_actual_price,
                sl_initial, tgt_initial, margin_reserved, risk_amount,
                created_at, status, order_protocol, updated_at)
               VALUES (?, ?, ?, 'LONG', 'strategy', 'ENERGY', ?, ?, ?, ?,
                       ?, ?, 5000.0, 500.0, ?, 'OPEN', 'LIMIT_TRIPLE', ?)""",
            (trade_id, signal_id, symbol, qty, qty, price, price,
             price * 0.98, price * 1.02, ts, ts),
        )


def _seed_session(store: StateStore, *, closed: list[tuple[float, str]],
                  with_open: bool = True) -> float:
    """Seed a real session-1 capital state into `store`: for each (pnl, intent)
    run a REAL reserve->commit->release_used closed LONG trade (writes a
    RELEASE_USED fm_ledger row of that pnl in that intent's bucket); optionally
    one still-open reserve+commit trade. Returns the total realized PnL. The
    seeding FundManager instance is discarded; only the durable fm_ledger/trades
    rows survive for the fresh restart FMs to rehydrate."""
    fm = _init_fm(store, BASE)
    total_pnl = 0.0
    for i, (pnl, intent) in enumerate(closed):
        sym, entry, qty = f"CLS{i}", 1000.0, 2
        r = fm.reserve(sym, qty, entry, intent, signal_id=f"sig_c{i}")
        assert r.success, f"reserve failed for {sym}"
        fm.commit_to_used(r.reservation_id, actual_fill_price=entry, actual_qty=qty)
        fm.release_used(symbol=sym, exit_price=entry + pnl / qty, exit_qty=qty,
                        intent=intent, entry_price=entry, direction="LONG")
        total_pnl += pnl
    if with_open:
        r = fm.reserve("OPENSYM", 3, 500.0, "INTRADAY", signal_id="sig_open")
        assert r.success
        fm.commit_to_used(r.reservation_id, actual_fill_price=500.0, actual_qty=3)
        _seed_open_trade(store, signal_id="sig_open", trade_id="tr_open",
                         symbol="OPENSYM", qty=3, price=500.0)
    # Sanity: the correctly-accounted total after session 1 == BASE + realized PnL,
    # which IS the broker equity ('net') a warm restart would read.
    assert abs(fm.get_snapshot().total - (BASE + total_pnl)) < 0.01
    return total_pnl


def _total(fm: FundManager) -> float:
    return fm.get_snapshot().total


# ═════════════════════════════════════════════════════════════════════════════
# T1 — LIVE warm-restart double-count (core, red -> green) + sub-scenarios
# ═════════════════════════════════════════════════════════════════════════════

def test_T1_core_double_count_red_green(tmp_path: Path) -> None:
    """One closed +1,000 intraday trade + a still-open trade, mid-day warm restart.
    RED (pre-fix seed = broker.net): rehydrate double-counts -> BASE + 2*pnl.
    GREEN (fixed seed = broker.net - carryover): total == broker.net == BASE + pnl."""
    store = _store(tmp_path / "t1.db")
    pnl = _seed_session(store, closed=[(1000.0, "INTRADAY")], with_open=True)
    assert pnl == 1000.0
    broker_net = BASE + pnl                       # broker equity at warm restart

    # RED — pre-fix live seed (no subtraction).
    fm_red = _init_fm(store, broker_net)
    fm_red.rehydrate_from_open_trades()
    assert abs(_total(fm_red) - (BASE + 2 * pnl)) < 0.01   # the double-count bug

    # GREEN — fixed live seed.
    fm_green = _fm(store)
    sigma = fm_green.today_realized_pnl_carryover()
    assert abs(sigma - pnl) < 0.01                          # Sigma == exactly today's PnL
    assert abs((broker_net - sigma) - BASE) < 0.01          # fixed seed reduces to opening base
    fm_green.initialize(broker_net - sigma)
    fm_green.rehydrate_from_open_trades()
    assert abs(_total(fm_green) - broker_net) < 0.01         # == broker.net, no double-count
    assert abs(_total(fm_green) - (BASE + pnl)) < 0.01       # base + pnl, once
    store.close()


def test_T1a_loss_day_sign(tmp_path: Path) -> None:
    """Loss day: closed -500 -> Sigma < 0 -> seed = broker.net + |loss| = BASE,
    then Phase 2 adds the negative back -> total == broker.net (== BASE - 500)."""
    store = _store(tmp_path / "t1a.db")
    pnl = _seed_session(store, closed=[(-500.0, "INTRADAY")], with_open=True)
    assert pnl == -500.0
    broker_net = BASE + pnl                        # BASE - 500 (loss shrinks equity)

    fm_red = _init_fm(store, broker_net)
    fm_red.rehydrate_from_open_trades()
    assert abs(_total(fm_red) - (BASE + 2 * pnl)) < 0.01     # BASE - 1000 (double loss) RED

    fm_green = _fm(store)
    sigma = fm_green.today_realized_pnl_carryover()
    assert abs(sigma - pnl) < 0.01                            # -500 (signed)
    assert abs((broker_net - sigma) - BASE) < 0.01            # BASE (seed rises by |loss|)
    fm_green.initialize(broker_net - sigma)
    fm_green.rehydrate_from_open_trades()
    assert abs(_total(fm_green) - broker_net) < 0.01          # BASE - 500, once
    store.close()


def test_T1b_multi_trade(tmp_path: Path) -> None:
    """Several RELEASE_USED rows -> Sigma over all -> correct once."""
    store = _store(tmp_path / "t1b.db")
    pnl = _seed_session(
        store, closed=[(1000.0, "INTRADAY"), (-300.0, "INTRADAY"), (200.0, "INTRADAY")],
        with_open=True)
    assert abs(pnl - 900.0) < 0.01
    broker_net = BASE + pnl

    fm_red = _init_fm(store, broker_net)
    fm_red.rehydrate_from_open_trades()
    assert abs(_total(fm_red) - (BASE + 2 * pnl)) < 0.01      # RED double

    fm_green = _fm(store)
    sigma = fm_green.today_realized_pnl_carryover()
    assert abs(sigma - pnl) < 0.01                            # +900 total
    fm_green.initialize(broker_net - sigma)
    fm_green.rehydrate_from_open_trades()
    assert abs(_total(fm_green) - broker_net) < 0.01          # BASE + 900, once
    store.close()


def test_T1c_multi_bucket_parity_to_paper(tmp_path: Path) -> None:
    """PnL across intraday + positional buckets. The FIXED-live reconstruction
    (seed = broker.net - Sigma) must equal the PAPER reconstruction (static seed)
    at EVERY bucket -> per-bucket attribution intact AND identical to paper."""
    store = _store(tmp_path / "t1c.db")
    pnl = _seed_session(
        store, closed=[(1000.0, "INTRADAY"), (600.0, "DELIVERY")], with_open=True)
    assert abs(pnl - 1600.0) < 0.01
    broker_net = BASE + pnl

    # FIXED-live: seed = broker.net - carryover.
    fm_live = _fm(store)
    sigma = fm_live.today_realized_pnl_carryover()
    assert abs(sigma - pnl) < 0.01
    fm_live.initialize(broker_net - sigma)         # == BASE
    fm_live.rehydrate_from_open_trades()
    live = fm_live.get_snapshot()

    # PAPER reference: static PnL-excluded seed (== paper_capital semantics).
    fm_paper = _init_fm(store, BASE)
    fm_paper.rehydrate_from_open_trades()
    paper = fm_paper.get_snapshot()

    # Full-snapshot parity: fixing live == the already-correct paper reconstruction.
    assert abs(live.total - paper.total) < 0.01
    assert abs(live.intraday_avail - paper.intraday_avail) < 0.01
    assert abs(live.intraday_used - paper.intraday_used) < 0.01
    assert abs(live.positional_avail - paper.positional_avail) < 0.01
    assert abs(live.positional_used - paper.positional_used) < 0.01
    assert abs(live.daily_realized_pnl - paper.daily_realized_pnl) < 0.01
    assert abs(live.total - (BASE + pnl)) < 0.01              # correct magnitude
    store.close()


# ═════════════════════════════════════════════════════════════════════════════
# T2 — PAPER regression guard (paper untouched, both ways)
# ═════════════════════════════════════════════════════════════════════════════

def test_T2_paper_untouched(tmp_path: Path) -> None:
    """Paper warm restart: static seed (paper_capital == BASE) -> rehydrate ->
    total == BASE + today_pnl (unchanged; paper was already correct). Guards that
    the M-C1 carryover is NOT subtracted on paper: subtracting it would WRONGLY
    yield BASE (losing today's PnL)."""
    store = _store(tmp_path / "t2.db")
    pnl = _seed_session(store, closed=[(1000.0, "INTRADAY")], with_open=True)
    broker_net = BASE + pnl

    fm_paper = _init_fm(store, BASE)               # paper seed: static, PnL-excluded
    fm_paper.rehydrate_from_open_trades()
    assert abs(_total(fm_paper) - (BASE + pnl)) < 0.01       # correct, unchanged

    # Negative guard: had paper (wrongly) subtracted the carryover from its seed,
    # it would drop today's PnL. Prove the two seeds differ so the paper path must
    # NOT adopt the live subtraction.
    fm_probe = _fm(store)
    sigma = fm_probe.today_realized_pnl_carryover()
    assert abs((BASE - sigma) - (BASE - pnl)) < 0.01         # paper-minus-Sigma != paper seed
    assert abs(broker_net - BASE) > 0.01                     # live/paper seeds genuinely differ
    store.close()


# ═════════════════════════════════════════════════════════════════════════════
# T3 — cold-boot preservation (0 closed trades -> Sigma 0 -> unchanged)
# ═════════════════════════════════════════════════════════════════════════════

def test_T3_cold_boot_noop(tmp_path: Path) -> None:
    """08:15 cold boot: no closed trades -> carryover Sigma == 0 -> live seed ==
    broker.net (unchanged) -> Phase 2 no-op -> total == broker.net."""
    store = _store(tmp_path / "t3.db")
    _seed_session(store, closed=[], with_open=True)          # only an open trade
    broker_net = BASE                                        # no realized PnL yet

    fm = _fm(store)
    sigma = fm.today_realized_pnl_carryover()
    assert abs(sigma - 0.0) < 0.01                           # nothing to carry
    fm.initialize(broker_net - sigma)                        # == broker.net
    result = fm.rehydrate_from_open_trades()
    assert result["replayed_pnl_rows"] == 0                  # Phase 2 no-op
    assert abs(_total(fm) - broker_net) < 0.01
    store.close()


# ═════════════════════════════════════════════════════════════════════════════
# T4 — sync-drift elimination (no phantom drift after the fixed rehydrate)
# ═════════════════════════════════════════════════════════════════════════════

def test_T4_sync_drift_red_green(tmp_path: Path) -> None:
    """After the LIVE rehydrate, the next sync_from_broker(broker.net) must find
    total == broker.net (ZERO correction) so no spurious drift alert fires.
    RED: buggy total (broker.net + pnl) -> sync corrects by -pnl (phantom drift).
    GREEN: total already == broker.net -> sync is a zero-delta no-op."""
    store = _store(tmp_path / "t4.db")
    pnl = _seed_session(store, closed=[(1000.0, "INTRADAY")], with_open=True)
    broker_net = BASE + pnl

    # RED — the phantom drift the audit describes.
    fm_red = _init_fm(store, broker_net)
    fm_red.rehydrate_from_open_trades()
    red_before = _total(fm_red)
    fm_red.sync_from_broker(broker_net)
    red_correction = _total(fm_red) - red_before
    assert abs(red_before - (broker_net + pnl)) < 0.01       # inflated pre-sync
    assert abs(red_correction - (-pnl)) < 0.01               # phantom -today_pnl drift

    # GREEN — no drift.
    fm_green = _fm(store)
    fm_green.initialize(broker_net - fm_green.today_realized_pnl_carryover())
    fm_green.rehydrate_from_open_trades()
    green_before = _total(fm_green)
    fm_green.sync_from_broker(broker_net)
    green_correction = _total(fm_green) - green_before
    assert abs(green_before - broker_net) < 0.01             # already == broker.net
    assert abs(green_correction - 0.0) < 0.01                # ZERO drift, no alert
    store.close()


# ═════════════════════════════════════════════════════════════════════════════
# T5 — MIDNIGHT DAY-FLOOR HAZARD (DESIGN_midnight_day_floor.md) + the single-floor fix
# ═════════════════════════════════════════════════════════════════════════════

class _StubBroker:
    """Inert broker: get_margins().net returns a chosen number. No client, no
    credentials — 'live' here is the live SEEDING arithmetic, never a connection."""

    def __init__(self, net: float) -> None:
        self._net = net

    def get_margins(self):
        return type("_Margins", (), {"net": self._net})()


def _day_floors() -> tuple[str, str]:
    """(floor_D, floor_Dp1) = midnight-of-today and midnight-of-tomorrow, ISO. The
    seeded RELEASE_USED rows are stamped at now_ist() (day D), so floor_D captures
    them (Sigma = Sigma(D)) and floor_Dp1 captures none (Sigma = 0). These are the
    exact values two independent now_ist() calls would derive across a midnight boot."""
    now = now_ist()
    floor_D = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    floor_Dp1 = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0).isoformat()
    return floor_D, floor_Dp1


def test_T5a_divergent_midnight_floors_break_the_cancellation(tmp_path: Path) -> None:
    """THE HAZARD, shown able to fail (DESIGN_midnight_day_floor B3). On the pre-fix path the
    seed and rehydrate each derive their OWN now_ist() day-floor. A boot straddling midnight
    gives the seed floor D (captures today's Sigma) and rehydrate floor D+1 (captures none), so
    the subtraction and the re-addition span different row-sets and DO NOT cancel -- residue
    -Sigma(D). The divergence is planted by passing the two floors explicitly."""
    store = _store(tmp_path / "t5a.db")
    pnl = _seed_session(
        store, closed=[(-1234.0, "INTRADAY"), (321.0, "INTRADAY")], with_open=True)
    sigma_D = pnl
    assert abs(sigma_D) > 0.01, (
        "ANTI-VACUITY: Sigma(D) must be non-zero, or a zero-P&L day collapses both floor-forms "
        "and the test proves nothing (the E4/W10 no-cost-day trap)."
    )
    broker_net = BASE + pnl
    floor_D, floor_Dp1 = _day_floors()

    fm = _fm(store)
    # seed derives floor D (pre-midnight); rehydrate derives floor D+1 (post-midnight).
    seed = broker_net - fm.today_realized_pnl_carryover(floor_D)
    fm.initialize(seed)
    fm.rehydrate_from_open_trades(floor_Dp1)

    total = _total(fm)
    assert abs(total - (broker_net - sigma_D)) < 0.01, (
        f"expected the BROKEN residue -Sigma(D): _total={total:.2f}, "
        f"broker_net-Sigma(D)={broker_net - sigma_D:.2f}"
    )
    assert abs(total - broker_net) > 0.01, "divergent floors must NOT cancel to broker.net"
    store.close()


def test_T5b_single_shared_floor_survives_midnight(tmp_path: Path) -> None:
    """THE FIX. main() derives ONE day-floor and passes it to BOTH compute_live_seed and
    rehydrate_from_open_trades, so even a boot straddling midnight uses a single floor and the
    cancellation is exact. Drives the REAL extracted compute_live_seed with one floor -- so this
    cannot pass on the pre-extraction HEAD (compute_live_seed does not exist there)."""
    from main import compute_live_seed

    store = _store(tmp_path / "t5b.db")
    pnl = _seed_session(
        store, closed=[(-1234.0, "INTRADAY"), (321.0, "INTRADAY")], with_open=True)
    assert abs(pnl) > 0.01, "ANTI-VACUITY: Sigma(D) must be non-zero"
    broker_net = BASE + pnl
    floor_D, _ = _day_floors()

    fm = _fm(store)
    seed = compute_live_seed(_StubBroker(broker_net), fm, floor_D)   # ONE floor ...
    fm.initialize(seed)
    fm.rehydrate_from_open_trades(floor_D)                           # ... to BOTH sides
    assert abs(_total(fm) - broker_net) < 0.01, (
        f"single shared floor must cancel to broker.net: _total={_total(fm):.2f}, "
        f"broker_net={broker_net:.2f}"
    )
    store.close()


def test_T5c_no_arg_carryover_still_matches_today_floor(tmp_path: Path) -> None:
    """The None-fallback is UNCHANGED and correct: a standalone/test caller that passes no floor
    still derives today's floor and gets the same Sigma as an explicit today-floor. Guards that
    the fix did not alter the fallback that non-boot callers rely on."""
    store = _store(tmp_path / "t5c.db")
    pnl = _seed_session(store, closed=[(-777.0, "INTRADAY")], with_open=False)
    assert abs(pnl) > 0.01
    floor_D, _ = _day_floors()
    fm = _fm(store)
    assert abs(fm.today_realized_pnl_carryover() - fm.today_realized_pnl_carryover(floor_D)) < 0.01
    store.close()


def test_boot_derives_the_day_floor_once_and_passes_it_to_both() -> None:
    """STRUCTURAL PIN -- the single-floor guarantee at boot is BY CONVENTION at main()'s two
    capital-restore call sites (the fund_manager helpers keep a None-fallback for standalone
    callers, so nothing structurally forbids a second derivation). This pins the convention:
    main() must derive ONE now_ist() day-floor and pass the SAME variable to both
    compute_live_seed and rehydrate_from_open_trades. It is RED on any tree where the seed and
    rehydrate call sites derive their own floor (e.g. the pre-fix HEAD), which is how a future
    edit that reintroduces a second derivation gets caught."""
    main_src = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    seed = re.search(
        r"_startup_capital\s*=\s*compute_live_seed\(\s*broker_adapter,\s*fund_manager,"
        r"\s*([A-Za-z_]\w*)\s*\)",
        main_src,
    )
    assert seed, (
        "the LIVE seed is not `compute_live_seed(broker_adapter, fund_manager, <day-floor>)` -- "
        "the extracted helper and the explicit day-floor argument are the fix"
    )
    floor = seed.group(1)
    assert re.search(rf"\.rehydrate_from_open_trades\(\s*{re.escape(floor)}\s*\)", main_src), (
        f"rehydrate_from_open_trades is not passed the SAME day-floor variable ({floor!r}) as "
        f"the seed -- the two boot call sites could derive divergent floors at a midnight boot"
    )
    assert len(re.findall(rf"{re.escape(floor)}\s*=\s*now_ist\(\)", main_src)) == 1, (
        f"the boot day-floor {floor!r} must be derived from now_ist() exactly ONCE"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
