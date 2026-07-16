"""
tests/unit/test_mc5_commit_adopted_cas.py — M-C5 (16-Jul-2026).

M-C5: `FundManager.commit_adopted_entry` evaluates its exactly-one-commit guard
(`_commit_exists` — no COMMIT row in fm_ledger yet) INSIDE self._lock, then runs the
commit OUTSIDE it. It must run outside: commit_to_used takes the lock itself and
defers its BL-4 hard_kill to after release (C.1), so calling it under the lock would
risk deadlock.

That leaves a window. Two callers that did NOT gate on the caller-side atomic
trade-state transition would BOTH pass the guard (neither has written a COMMIT row
yet) and BOTH reach commit_to_used. The loser does not corrupt capital — _apply_commit
pops the reservation, so the second commit_to_used finds none, raises ValueError, and
BL-4 fires **hard_kill**. A spurious emergency halt caused by nothing but a race.

Not reachable in production TODAY: both callers gate on an atomic transition
(order_reconciler:3603 `adopt_recovery_trade_to_open`, :3660
`mark_recovery_trade_exiting`). This suite locks in BOTH halves of the fix:
  - the caller gate still works (nothing about it changed), and
  - the METHOD is now safe ON ITS OWN, via a CAS claim, so a future non-gating caller
    cannot double-commit.

Run: python -m pytest tests/unit/test_mc5_commit_adopted_cas.py -v
"""
from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

from capital.fund_manager import FundManager, required_margin
from core.events import EventBus
from core.state_store import StateStore
from orders.order_manager import OrderManager

_SCHEMA = Path(__file__).parent.parent.parent / "core" / "schema.sql"
_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
_BAL = 100_000.0
_QTY, _PRICE = 10, 2500.0
_MARGIN = required_margin(_QTY, _PRICE, "INTRADAY", _LEV)  # 5000.0


# ─────────────────────────────────────────────────────────────────────────────
# Builders (mirroring test_a1e1_recovery_matrix's real-FM/real-store approach)
# ─────────────────────────────────────────────────────────────────────────────

def _make_fm(store: StateStore, kill_switch=None) -> FundManager:
    fm = FundManager(
        state_store=store, bus=EventBus(), logger=logging.getLogger("t_mc5"),
        kill_switch=kill_switch,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.10, leverage_map=_LEV,
        slm_margin_buffer_pct=0.0,
    )
    fm.initialize(_BAL)
    return fm


def _reserve_and_create(store, fm, *, status="UNKNOWN_IN_FLIGHT") -> tuple[str, str]:
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,
               received_at,expires_at,status,fingerprint,fingerprint_date)
               VALUES ('sig_mc5','RELIANCE','S','vwap_bounce_long',
               '2026-07-16T09:30:00+05:30','2026-07-16T09:30:00+05:30',
               '2026-07-16T09:35:00+05:30','TRADED','fp_mc5','2026-07-16')"""
        )
    res = fm.reserve(symbol="RELIANCE", qty=_QTY, price=_PRICE, intent="INTRADAY",
                     signal_id="sig_mc5")
    assert res.success, res.reason_if_failed
    om = OrderManager(store, logging.getLogger("t_om"))
    tid = om.create_trade(
        signal_id="sig_mc5", symbol="RELIANCE", direction="LONG",
        strategy="vwap_bounce_long", sector=None, qty=_QTY,
        entry_target_price=_PRICE, sl_initial=2475.0, tgt_initial=2550.0,
        order_protocol="LIMIT_TRIPLE", margin_reserved=res.margin,
        risk_amount=250.0, reservation_id=res.reservation_id,
    )
    with store.transaction() as cur:
        cur.execute("UPDATE trades SET status=? WHERE trade_id=?", (status, tid))
    return tid, res.reservation_id


def _trade_row(store, tid):
    return store.fetch_one("SELECT * FROM trades WHERE trade_id=?", (tid,))


def _commit_count(store, rid: str) -> int:
    return len(store.fetch_all(
        "SELECT 1 FROM fm_ledger WHERE reservation_id=? AND entry_type='COMMIT'",
        (rid,),
    ) or [])


# ─────────────────────────────────────────────────────────────────────────────
# The fix: the METHOD is safe on its own, without a caller gate
# ─────────────────────────────────────────────────────────────────────────────

def test_two_concurrent_nongating_callers_cannot_double_commit():
    """RED ON OLD. Two callers race with NO caller-side gate — the future-caller
    scenario M-C5 is about.

    The race is made deterministic by holding the FIRST caller inside commit_to_used
    (only the first — blocking both would just deadlock the old code instead of
    failing it). While caller 1 is parked there, caller 2 runs the whole guard path:

      OLD: caller 2 passes _commit_exists (no COMMIT row yet — caller 1 hasn't
           written one), reaches commit_to_used, finds the reservation still present,
           and COMMITS. Two commits. Caller 1 then resumes, finds its reservation
           popped, raises ValueError -> BL-4 hard_kill. A spurious emergency halt.
      NEW: caller 2 hits the CAS claim and returns None without ever touching
           commit_to_used.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = StateStore(Path(tmp) / "t.db", _SCHEMA)
        ks = MagicMock()
        fm = _make_fm(store, kill_switch=ks)
        tid, rid = _reserve_and_create(store, fm)
        row = _trade_row(store, tid)

        entered = threading.Event()
        release = threading.Event()
        real_commit = fm.commit_to_used
        commit_entries: list[str] = []

        def _slow_first_commit(reservation_id, price, qty):
            commit_entries.append(reservation_id)
            if len(commit_entries) == 1:
                # Park ONLY caller 1, holding the window open for caller 2.
                entered.set()
                release.wait(5)
            return real_commit(reservation_id, price, qty)

        fm.commit_to_used = _slow_first_commit

        winner: list = []
        t1 = threading.Thread(
            target=lambda: winner.append(fm.commit_adopted_entry(row, _PRICE, _QTY)),
            name="caller-1",
        )
        t1.start()
        try:
            assert entered.wait(3.0), "caller 1 never reached commit_to_used"

            # Caller 2 — no gate, same reservation, while caller 1 is mid-commit.
            loser = fm.commit_adopted_entry(row, _PRICE, _QTY)

            assert loser is None, (
                "the second concurrent caller COMMITTED — double commit; on the old "
                "code caller 1 then raises ValueError and BL-4 fires a spurious "
                "hard_kill"
            )
            assert commit_entries == [rid], (
                f"commit_to_used was entered {len(commit_entries)}x — the CAS did not "
                f"stop the second caller"
            )
        finally:
            release.set()
            t1.join(5)

        # Caller 1 completed normally; exactly one commit; capital is correct.
        assert winner and winner[0] is not None, "the winner's commit did not complete"
        assert _commit_count(store, rid) == 1
        snap = fm.get_snapshot()
        assert abs(snap.intraday_used - _MARGIN) < 1e-6
        assert abs(snap.intraday_reserved) < 1e-6

        # The whole point: no emergency halt was triggered by a mere race.
        ks.hard_kill.assert_not_called()
        store.close()


def test_the_claim_is_released_so_a_later_legitimate_call_is_still_guarded(tmp_path):
    """The CAS must not become a permanent lock-out (a leaked claim would silently
    disable commits for that reservation) NOR a bypass: after the winner completes,
    the DURABLE ledger guard takes over and a later call is still a clean no-op."""
    store = StateStore(tmp_path / "t.db", _SCHEMA)
    fm = _make_fm(store)
    tid, rid = _reserve_and_create(store, fm)
    row = _trade_row(store, tid)

    first = fm.commit_adopted_entry(row, _PRICE, _QTY)
    assert first is not None
    assert rid not in fm._commit_claims, "the claim leaked after a successful commit"

    again = fm.commit_adopted_entry(_trade_row(store, tid), _PRICE, _QTY)
    assert again is None, "the durable ledger guard must still no-op a later call"
    assert _commit_count(store, rid) == 1
    store.close()


def test_the_claim_is_released_when_the_commit_fails(tmp_path):
    """A failed commit must not strand the claim — that would block every legitimate
    retry for the reservation, forever."""
    store = StateStore(tmp_path / "t.db", _SCHEMA)
    ks = MagicMock()
    fm = _make_fm(store, kill_switch=ks)
    tid, rid = _reserve_and_create(store, fm)
    row = _trade_row(store, tid)

    def _boom(reservation_id, price, qty):
        raise RuntimeError("ledger write exploded")

    fm.commit_to_used = _boom
    try:
        fm.commit_adopted_entry(row, _PRICE, _QTY)
    except RuntimeError:
        pass  # commit_to_used's own BL-4 handling is out of scope here

    assert rid not in fm._commit_claims, (
        "the claim survived a failed commit — every retry for this reservation would "
        "now be silently skipped"
    )
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# The caller gate is untouched (kept explicitly — it is still the primary defence)
# ─────────────────────────────────────────────────────────────────────────────

def test_a_gating_caller_still_commits_normally(tmp_path):
    """The CAS must not break the real production path: the winner of the caller's
    atomic trade-state gate still commits exactly once, reserved -> used."""
    store = StateStore(tmp_path / "t.db", _SCHEMA)
    fm = _make_fm(store)
    tid, rid = _reserve_and_create(store, fm)

    # This is what the reconciler's gate does before calling us (order_reconciler:3603).
    assert store.adopt_recovery_trade_to_open(
        tid, avg_fill_price=_PRICE, qty_filled=_QTY,
        filled_at="2026-07-16T09:31:00+05:30",
    ) is True
    cr = fm.commit_adopted_entry(_trade_row(store, tid), _PRICE, _QTY)

    assert cr is not None
    assert _commit_count(store, rid) == 1
    snap = fm.get_snapshot()
    assert abs(snap.intraday_used - _MARGIN) < 1e-6
    assert abs(snap.intraday_reserved) < 1e-6
    store.close()


def test_the_caller_gate_still_admits_exactly_one_winner(tmp_path):
    """Defence 1 unchanged: the atomic transition lets exactly one caller through, so
    a second cycle never reaches commit_adopted_entry at all."""
    store = StateStore(tmp_path / "t.db", _SCHEMA)
    fm = _make_fm(store)
    tid, _rid = _reserve_and_create(store, fm)

    first = store.adopt_recovery_trade_to_open(
        tid, avg_fill_price=_PRICE, qty_filled=_QTY,
        filled_at="2026-07-16T09:31:00+05:30",
    )
    second = store.adopt_recovery_trade_to_open(
        tid, avg_fill_price=_PRICE, qty_filled=_QTY,
        filled_at="2026-07-16T09:31:00+05:30",
    )
    assert first is True and second is False
    store.close()
