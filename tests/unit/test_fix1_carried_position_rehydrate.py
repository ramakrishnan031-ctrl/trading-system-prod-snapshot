"""tests/unit/test_fix1_carried_position_rehydrate.py — FIX 1 regression.

THE DEFECT (measured live, 10-Aug-2026 08:15:25.680, HARD_KILL):
    A carried delivery position is counted TWICE at the moment the FundManager
    re-bases from broker cash.

    Once by its ABSENCE from broker `net` -- the broker already took the money
    when the shares were bought (Rama, 10-Aug: "~Rs907 of stock against a
    Rs1,117 account" => net 209.80 EXCLUDES the 907.02 of holdings).

    And once by its PRESENCE as a replayed reservation deducted from a bucket
    whose base is a fraction of that same, already-reduced cash.

    So `positional_avail = 0.30 * 209.80 - 907.02 = -844.08` and INV6's
    non-negativity guard fired -> CapitalInvariantViolation -> HARD_KILL of
    BOTH books, over a position that already existed and could not be undone.

    The GLOBAL identity was never broken: -697.22 + 0 + 907.02 == 209.80
    == _total, delta 5.68e-14. Only the PARTITION went negative. That is why
    this is an accounting-semantics defect and not ledger corruption.

THE TRIGGER IS PERIODIC, NOT RARE (Rama, 10-Aug): the SEBI quarterly
settlement sweeps free cash to the bank. The Rs8,773.78 that left the account
over the weekend of 08/09-Aug-2026 (Fri net 8,983.58 -> Mon net 209.80, with
Friday's realised P&L only -4.26 and the market shut) WAS that settlement --
not a loss, not a withdrawal, not an error. It is scheduled and recurring, so
it will keep its next appointment, and the first trading day after it ANY
carried CNC position reproduces this. Case A below IS that scenario on the
boot path; case H is the same sweep arriving through the 09:15 sync.

THE RULE, one sentence, applied identically at boot-rehydrate and at the 09:15
sync:
    A bucket's capital base is its share of BROKER CASH plus the margin the
    broker has ALREADY removed from that cash for that bucket's carried
    positions. The carry enters the base and is immediately consumed by
    reserved/used -- so it nets to zero available and stays fully visible as
    exposure.

RED-first, MEASURED against the pre-fix tree (capital/fund_manager.py at
f963438, everything else unchanged) rather than asserted:

    A   RED  CapitalStateInconsistent, NEGATIVE_MARGIN_AVAILABLE -1850.00
    B   RED  total is 10,000 (cash) where the fix makes it 12,000 (account)
    C   RED  same rehydrate violation as A
    D   RED  same rehydrate violation as A
    E   RED  same rehydrate violation as A
    F   GREEN  <- control
    F2  RED  the carry fields do not exist pre-fix (AttributeError)
    G   GREEN  <- control
    H   RED  CapitalInvariantViolation, NEGATIVE_MARGIN_AVAILABLE -1850.00,
             raised out of sync_from_broker -- NOT out of the boot
    H2  RED  at all five sweep depths

F and G are the MUST-NOT-CHANGE guards: F pins that a no-carry session is
byte-identical to today, G pins that a genuine negative still kills. Do not
delete them as "already green" -- being green on BOTH trees is the whole
point of them. Both had to be re-shaped on 10-Aug to earn that: as first
committed neither could reach its own assertion pre-fix (F died on the new
snapshot fields, G's setup raised in _boot_next_morning), so the file claimed
two controls it did not have.

Components -- REAL: StateStore (real schema + fm_ledger), FundManager (real
initialize / reserve / commit_to_used / rehydrate / sync_from_broker /
invariant), OrderManager. SIMULATED: nothing in the capital path. The
"overnight sweep" is modelled the only way it can be -- by initialising the
NEXT session's FundManager from a smaller broker balance, which is exactly
what the 08:15 boot does.

PARITY: LIVE-ONLY behaviour. Paper has no T+1 settlement model and never calls
margins(), so a paper run cannot reach this state at all. No paper claim is
made here.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from capital.fund_manager import FundManager
from core.events import CapitalDriftDetected, EventBus
from core.exceptions import CapitalInvariantViolation, CapitalStateInconsistent
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.order_manager import OrderManager

_SCHEMA = Path(__file__).parent.parent.parent / "core" / "schema.sql"
_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
_INTRADAY_PCT = 0.70
_POSITIONAL_PCT = 0.30
_TOL = 0.01

# Day-0 cash, and the delivery position bought out of it.
_DAY0_CASH = 10_000.0
_QTY = 20
_PRICE = 100.0
_CARRY = _QTY * _PRICE          # 2,000.00 -- DELIVERY leverage is 1.0, so the
                                # reservation is the FULL purchase value.

# Day-1 broker cash, the two operands that matter.
_SWEPT_CASH = 500.0             # after the quarterly settlement sweep
_HIGH_CASH = 10_000.0           # an ordinary morning / after a payin


def _store(tmp: Path) -> StateStore:
    return StateStore(tmp / "t.db", _SCHEMA)


def _fm(store: StateStore, balance: float, bus: EventBus | None = None) -> FundManager:
    fm = FundManager(
        state_store=store, bus=bus or EventBus(), logger=logging.getLogger("t_fix1"),
        intraday_bucket_pct=_INTRADAY_PCT, positional_bucket_pct=_POSITIONAL_PCT,
        daily_loss_limit_pct=0.10, leverage_map=_LEV, slm_margin_buffer_pct=0.0,
    )
    fm.initialize(balance)
    return fm


def _seed_and_commit(store, fm, *, sid, symbol, qty, price, intent, product,
                     direction="LONG") -> str:
    """Open one position and leave it OPEN in the DB (the day-0 session)."""
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,
               received_at,expires_at,status,fingerprint,fingerprint_date)
               VALUES (?,?,'S','vwap_bounce_long','2026-08-07T10:00:00+05:30',
               '2026-08-07T10:00:00+05:30','2026-08-07T10:05:00+05:30','TRADED',?,?)""",
            (sid, symbol, "fp_" + sid, "2026-08-07"),
        )
    res = fm.reserve(symbol=symbol, qty=qty, price=price, intent=intent, signal_id=sid)
    assert res.success, res.reason_if_failed
    fm.commit_to_used(res.reservation_id, price, qty)
    om = OrderManager(store, logging.getLogger("t_om"))
    tid = om.create_trade(
        signal_id=sid, symbol=symbol, direction=direction,
        strategy="vwap_bounce_long", sector=None, qty=qty,
        entry_target_price=price, sl_initial=price * 0.99, tgt_initial=price * 1.02,
        order_protocol="LIMIT_TRIPLE", margin_reserved=res.margin,
        risk_amount=250.0, reservation_id=res.reservation_id,
    )
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "UPDATE trades SET status='OPEN', qty_filled=?, entry_actual_price=? "
            "WHERE trade_id=?", (qty, price, tid),
        )
        cur.execute(
            """INSERT INTO orders
                 (order_id, trade_id, leg, transaction_type, order_type, product,
                  variety, qty_requested, status, trigger_price, placed_at, updated_at)
               VALUES (?, ?, 'ENTRY', ?, 'LIMIT', ?, 'regular', ?, 'COMPLETE',
                       0.0, ?, ?)""",
            (f"ord_{tid}", tid, "BUY" if direction == "LONG" else "SELL",
             product, qty, now, now),
        )
    return tid


def _carry_one_delivery_overnight(store) -> None:
    """Day 0: buy one CNC position out of _DAY0_CASH and leave it OPEN."""
    fm0 = _fm(store, _DAY0_CASH)
    _seed_and_commit(
        store, fm0, sid="sig_carry", symbol="DIFFNKG", qty=_QTY, price=_PRICE,
        intent="DELIVERY", product="CNC",
    )
    # Sanity on the day-0 side: the reservation IS the full purchase value.
    s0 = fm0.get_snapshot()
    assert s0.positional_used == pytest.approx(_CARRY, abs=_TOL)


def _boot_next_morning(store, broker_cash: float,
                       bus: EventBus | None = None) -> FundManager:
    """Day 1: the 08:15 boot -- fresh FundManager, fresh broker balance."""
    fm = _fm(store, broker_cash, bus=bus)
    fm.rehydrate_from_open_trades()
    return fm


def _assert_global_identity(fm) -> None:
    """The invariant that was NEVER broken and must stay unbroken:
    sum of every partition == _total."""
    s = fm.get_snapshot()
    lhs = (s.intraday_avail + s.positional_avail
           + s.intraday_reserved + s.positional_reserved
           + s.intraday_used + s.positional_used)
    assert lhs == pytest.approx(s.total, abs=_TOL), (
        f"global identity broken: lhs={lhs} total={s.total}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# A — carried CNC + SWEPT cash => NO false boot kill   (the 10-Aug scenario)
# ─────────────────────────────────────────────────────────────────────────────
def test_a_carried_delivery_with_swept_cash_does_not_kill_the_boot():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)

        # Pre-fix this raises CapitalStateInconsistent (INV6: positional_avail
        # = 0.30*500 - 2000 = -1850). That was the live HARD_KILL.
        fm = _boot_next_morning(store, _SWEPT_CASH)

        s = fm.get_snapshot()
        assert s.positional_avail >= -_TOL, (
            f"false negative-capital: positional_avail={s.positional_avail}"
        )
        assert s.intraday_avail >= -_TOL
        # The bucket gets exactly its share of the cash that actually exists.
        assert s.positional_avail == pytest.approx(
            _POSITIONAL_PCT * _SWEPT_CASH, abs=_TOL)
        _assert_global_identity(fm)


# ─────────────────────────────────────────────────────────────────────────────
# B — carried CNC + HIGH cash => initialises correctly (pre-fix: also green,
#     but by luck -- the double deduction simply did not reach zero)
# ─────────────────────────────────────────────────────────────────────────────
def test_b_carried_delivery_with_high_cash_initialises_correctly():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)
        fm = _boot_next_morning(store, _HIGH_CASH)

        s = fm.get_snapshot()
        assert s.positional_avail == pytest.approx(
            _POSITIONAL_PCT * _HIGH_CASH, abs=_TOL)
        assert s.positional_used == pytest.approx(_CARRY, abs=_TOL)
        assert s.total == pytest.approx(_HIGH_CASH + _CARRY, abs=_TOL)
        _assert_global_identity(fm)


# ─────────────────────────────────────────────────────────────────────────────
# C — the carry stays VISIBLE as exposure
#     (the fix must NOT make INV6 green by forgetting the position)
# ─────────────────────────────────────────────────────────────────────────────
def test_c_the_carry_stays_visible_as_exposure():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)
        fm = _boot_next_morning(store, _SWEPT_CASH)

        s = fm.get_snapshot()
        # Still occupying the bucket, at full purchase value.
        assert s.positional_used == pytest.approx(_CARRY, abs=_TOL)
        # And named explicitly, so "how much of this is carried?" is answerable
        # without re-deriving it from the DB.
        assert s.positional_carry == pytest.approx(_CARRY, abs=_TOL)
        assert s.intraday_carry == pytest.approx(0.0, abs=_TOL)
        # _total is the account, not the cash: 500 cash + 2,000 of stock.
        assert s.total == pytest.approx(_SWEPT_CASH + _CARRY, abs=_TOL)


# ─────────────────────────────────────────────────────────────────────────────
# D — NO artificial new capacity: the delivery carry must not leak into the
#     intraday bucket, and must not enlarge its own bucket beyond the split
# ─────────────────────────────────────────────────────────────────────────────
def test_d_rehydration_creates_no_artificial_capacity():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)
        fm = _boot_next_morning(store, _SWEPT_CASH)

        s = fm.get_snapshot()
        # The intraday bucket sees ONLY cash. A pro-rata split of (cash+carry)
        # would hand it 0.70*2,500 = 1,750 of money locked in someone else's
        # stock -- that is the leak this pins shut.
        assert s.intraday_avail == pytest.approx(
            _INTRADAY_PCT * _SWEPT_CASH, abs=_TOL)
        assert s.intraday_avail < _INTRADAY_PCT * (_SWEPT_CASH + _CARRY)
        # And the carrying bucket gets the split on cash -- no more.
        assert s.positional_avail == pytest.approx(
            _POSITIONAL_PCT * _SWEPT_CASH, abs=_TOL)
        # Free capital across both buckets can never exceed the cash that exists.
        assert (s.intraday_avail + s.positional_avail) == pytest.approx(
            _SWEPT_CASH, abs=_TOL)


# ─────────────────────────────────────────────────────────────────────────────
# E — boot-rehydrate and the 09:15 sync share ONE semantic
#     (two meanings for these fields IS the defect)
# ─────────────────────────────────────────────────────────────────────────────
def test_e_boot_and_0915_sync_agree_exactly():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)
        fm = _boot_next_morning(store, _SWEPT_CASH)
        after_boot = fm.get_snapshot()

        # 09:15: the broker reports the SAME cash. Nothing has changed, so
        # nothing may move.
        fm.sync_from_broker(_SWEPT_CASH)
        after_sync = fm.get_snapshot()

        for field in ("total", "intraday_avail", "positional_avail",
                      "intraday_reserved", "positional_reserved",
                      "intraday_used", "positional_used",
                      "intraday_carry", "positional_carry"):
            assert getattr(after_sync, field) == pytest.approx(
                getattr(after_boot, field), abs=_TOL), (
                f"boot and sync disagree on {field}: "
                f"boot={getattr(after_boot, field)} sync={getattr(after_sync, field)}"
            )

        # And a real cash change is still tracked, on the same rule.
        fm.sync_from_broker(_HIGH_CASH)
        s = fm.get_snapshot()
        assert s.positional_avail == pytest.approx(
            _POSITIONAL_PCT * _HIGH_CASH, abs=_TOL)
        assert s.total == pytest.approx(_HIGH_CASH + _CARRY, abs=_TOL)
        _assert_global_identity(fm)


# ─────────────────────────────────────────────────────────────────────────────
# F — intraday behaviour BYTE-UNCHANGED when nothing is carried
#     TRUE MUST-NOT-CHANGE GUARD: green pre-fix AND post-fix, VERIFIED by
#     running it with capital/fund_manager.py reverted to f963438.
#
#     It asserts nothing that did not exist before the fix. The
#     intraday_carry/positional_carry fields are NEW, so asserting them here
#     would make this file's only two-tree control die pre-fix with
#     AttributeError -- i.e. it would stop being a control at all. Those
#     assertions live in F2 below, which is post-fix-only by construction.
# ─────────────────────────────────────────────────────────────────────────────
def test_f_no_carry_is_byte_identical_to_today():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store, _DAY0_CASH)
        fm.rehydrate_from_open_trades()          # nothing open: carry is 0

        s = fm.get_snapshot()
        assert s.total == pytest.approx(_DAY0_CASH, abs=_TOL)
        assert s.intraday_avail == pytest.approx(
            _INTRADAY_PCT * _DAY0_CASH, abs=_TOL)
        assert s.positional_avail == pytest.approx(
            _POSITIONAL_PCT * _DAY0_CASH, abs=_TOL)

        # The 09:15 sync with no carry is likewise unchanged.
        fm.sync_from_broker(_DAY0_CASH)
        s2 = fm.get_snapshot()
        assert s2.total == pytest.approx(_DAY0_CASH, abs=_TOL)
        assert s2.intraday_avail == pytest.approx(
            _INTRADAY_PCT * _DAY0_CASH, abs=_TOL)
        assert s2.positional_avail == pytest.approx(
            _POSITIONAL_PCT * _DAY0_CASH, abs=_TOL)
        _assert_global_identity(fm)


# ─────────────────────────────────────────────────────────────────────────────
# F2 — with nothing carried, both carry fields read zero.
#      Post-fix-only by construction: pre-fix the fields do not exist.
# ─────────────────────────────────────────────────────────────────────────────
def test_f2_no_carry_reports_zero_on_both_carry_fields():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        fm = _fm(store, _DAY0_CASH)
        fm.rehydrate_from_open_trades()

        s = fm.get_snapshot()
        assert s.intraday_carry == pytest.approx(0.0, abs=_TOL)
        assert s.positional_carry == pytest.approx(0.0, abs=_TOL)

        fm.sync_from_broker(_DAY0_CASH)
        s2 = fm.get_snapshot()
        assert s2.intraday_carry == pytest.approx(0.0, abs=_TOL)
        assert s2.positional_carry == pytest.approx(0.0, abs=_TOL)


# ─────────────────────────────────────────────────────────────────────────────
# G — a GENUINELY invalid negative-capital case STILL hard-kills
#     TRUE MUST-NOT-CHANGE GUARD: green pre-fix AND post-fix, VERIFIED by
#     running it with capital/fund_manager.py reverted to f963438.
#
#     It boots on _HIGH_CASH and drives the violation RELATIVE to whatever
#     the snapshot reports, so the setup itself survives on both trees. An
#     earlier form booted on _SWEPT_CASH and used an absolute overdraw
#     figure: pre-fix that setup raised in _boot_next_morning, so the
#     assertion under test was never reached and the guard could not have
#     gone red for its own reason.
# ─────────────────────────────────────────────────────────────────────────────
def test_g_genuine_negative_capital_still_raises():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)
        fm = _boot_next_morning(store, _HIGH_CASH)

        # A wrong-bucket release drives positional_used negative while the
        # global books still balance (the M-C3 "borrow" shape). No carry
        # arithmetic can explain this away -- it must still raise.
        fm._positional_used -= _CARRY + 50.0
        fm._positional_avail += _CARRY + 50.0
        with pytest.raises(CapitalInvariantViolation):
            fm._check_invariant("test_borrow", "T-G")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)
        fm = _boot_next_morning(store, _HIGH_CASH)

        # And a bucket genuinely overdrawn still raises: move whatever the
        # bucket has available into `used`, plus 100 more. Expressed against
        # the reported available so it overdraws on ANY base -- pre-fix
        # (1,000) and post-fix (3,000) alike.
        over = fm.get_snapshot().positional_avail + 100.0
        fm._positional_avail -= over
        fm._positional_used += over
        with pytest.raises(CapitalInvariantViolation):
            fm._check_invariant("test_overdrawn", "T-G2")


# ─────────────────────────────────────────────────────────────────────────────
# H — CASE 8, THE SWEEP: cash leaves the account AFTER the carry is established,
#     and the 09:15 sync is the one that meets it.
#
# WHAT THE SWEEP IS (Rama, 10-Aug-2026): the Rs8,773.78 that left the account
# over the weekend of 08/09-Aug was the SEBI QUARTERLY SETTLEMENT -- the broker
# returning unused client funds to the bank. NOT a loss, NOT a withdrawal, NOT
# an error. It is SCHEDULED and RECURRING, so it will keep its next appointment,
# and every carried CNC position meets it again when it does.
#
# THE BOOT HALF OF THIS CASE IS ALREADY CASE A ABOVE -- A establishes the carry
# at _DAY0_CASH (10,000) and boots the next morning on _SWEPT_CASH (500), which
# IS "cash leaves after the carry is established", arriving through
# rehydrate_from_open_trades. What did NOT exist is the other door: case E
# exercises sync_from_broker only at UNCHANGED cash and then at RISING cash.
# A FALLING sync was never tested, and sync_from_broker calls _check_invariant
# too -- so it is an independent firing site for the same mismatch, one that
# aims at a RUNNING session with the market open rather than at a dead boot.
#
# RED-first: fails pre-fix. The pre-fix boot at 10,000 leaves positional_avail
# at 3,000 - 2,000 = 1,000; the pre-fix sync at 500 recomputes it as
# 0.30*500 - 2,000 = -1,850 -> INV6 -> CapitalInvariantViolation -> hard_kill of
# a session that was healthy a moment earlier.
# ─────────────────────────────────────────────────────────────────────────────
def test_h_case8_settlement_sweep_lands_on_the_0915_sync():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)      # carry bought out of 10,000

        bus = EventBus()
        drifts: list[CapitalDriftDetected] = []
        bus.subscribe(CapitalDriftDetected, drifts.append)

        # 08:15 -- an ORDINARY morning. Normal cash, carry rehydrated, nothing
        # wrong yet. This is the state the sweep arrives into.
        #
        # Deliberately a HEALTH precondition, not a post-fix value: it holds on
        # BOTH trees (pre-fix the boot at high cash survives -- 3,000 - 2,000 =
        # 1,000), so this test can only go red at the SYNC. Asserting the
        # post-fix number here would make it fail before reaching the thing it
        # exists to test.
        fm = _boot_next_morning(store, _HIGH_CASH, bus=bus)
        before = fm.get_snapshot()
        assert before.positional_avail >= -_TOL
        assert before.intraday_avail >= -_TOL
        drifts.clear()   # isolate what the SYNC publishes

        # 09:15 -- the settlement sweep has taken the free cash. The carry is
        # untouched: the shares are still held, so the only operand that moved
        # is cash. Pre-fix this raises and hard-kills.
        fm.sync_from_broker(_SWEPT_CASH)

        s = fm.get_snapshot()
        assert s.positional_avail >= -_TOL, (
            f"false negative-capital on the sync path: "
            f"positional_avail={s.positional_avail}"
        )
        assert s.intraday_avail >= -_TOL
        # The partition stays coherent: each bucket gets its share of the cash
        # that actually exists, and nothing else.
        assert s.positional_avail == pytest.approx(
            _POSITIONAL_PCT * _SWEPT_CASH, abs=_TOL)
        assert s.intraday_avail == pytest.approx(
            _INTRADAY_PCT * _SWEPT_CASH, abs=_TOL)
        # Surviving the sweep must not mean forgetting the position.
        assert s.positional_used == pytest.approx(_CARRY, abs=_TOL)
        assert s.positional_carry == pytest.approx(_CARRY, abs=_TOL)
        assert s.total == pytest.approx(_SWEPT_CASH + _CARRY, abs=_TOL)
        _assert_global_identity(fm)

        # The sweep IS real money leaving the account, so it must STILL be
        # reported as drift. Surviving it is not the same as not noticing it,
        # and a fix that silenced this would be the worse defect.
        fm_drifts = [d for d in drifts if d.source_module == "fund_manager"]
        assert fm_drifts, "the sweep must still publish CapitalDriftDetected"
        assert fm_drifts[-1].delta == pytest.approx(
            _SWEPT_CASH - _HIGH_CASH, abs=_TOL)
        # But it is NOT a bucket overflow. That escalating source is the false
        # alarm the fix removes.
        assert not [d for d in drifts
                    if d.source_module == "fund_manager_bucket_overflow"], (
            "bucket overflow published on an ordinary settlement sweep"
        )


# ─────────────────────────────────────────────────────────────────────────────
# H2 — the sweep's survival is STRUCTURAL, not a property of these numbers.
#      Sweep the cash to nearly nothing, on both paths, and the partition is
#      still coherent -- because the carry enters its own bucket's base and is
#      immediately consumed by `used`, so positional_avail reduces to
#      `pct * cash` for ANY cash >= 0. If this ever goes red, the fix is
#      numeric rather than structural and the report must say so.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("swept_cash", [0.0, 0.01, 1.0, 137.53, _SWEPT_CASH])
def test_h2_case8_survives_any_depth_of_sweep_on_both_paths(swept_cash):
    # the boot path
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)
        fm = _boot_next_morning(store, swept_cash)
        s = fm.get_snapshot()
        assert s.positional_avail == pytest.approx(
            _POSITIONAL_PCT * swept_cash, abs=_TOL)
        assert s.intraday_avail == pytest.approx(
            _INTRADAY_PCT * swept_cash, abs=_TOL)
        _assert_global_identity(fm)

    # the 09:15 sync path, arriving into a healthy session
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _store(Path(tmp))
        _carry_one_delivery_overnight(store)
        fm = _boot_next_morning(store, _HIGH_CASH)
        fm.sync_from_broker(swept_cash)
        s = fm.get_snapshot()
        assert s.positional_avail == pytest.approx(
            _POSITIONAL_PCT * swept_cash, abs=_TOL)
        assert s.intraday_avail == pytest.approx(
            _INTRADAY_PCT * swept_cash, abs=_TOL)
        assert s.positional_carry == pytest.approx(_CARRY, abs=_TOL)
        _assert_global_identity(fm)
