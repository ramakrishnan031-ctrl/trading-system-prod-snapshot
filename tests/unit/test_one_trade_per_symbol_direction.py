"""tests/unit/test_one_trade_per_symbol_direction.py

27-Jul-2026. One COMPLETED trade per symbol+DIRECTION per trading day. DEFAULT OFF.

The case it exists for, from today's live book:
    SENCO  entry 10:02:17 @418.75 -> TGT_HIT 10:13:31 @425.05  (+5.84)
    SENCO  entry 10:14:51 @425.25 -> SL_HIT  10:25:53 @420.85  (-4.86)
80 seconds between the exit and the re-entry, and the re-entry price is ABOVE the
price its own strategy had just taken profit at. Nothing was broken: the scanner
fired SENCO every ~5-6 minutes all morning and the open-position guard is a QUEUE,
not a filter -- it releases the instant the position closes.

Tested against the REAL StateStore with the REAL schema, using today's actual
timestamps and prices. The gate helper is exercised directly (it is the whole unit;
driving three full pipeline paths would test the pipeline, not the rule).
"""
from __future__ import annotations

import pytest

from core.state_store import StateStore
from signals.signal_processor import SignalProcessor, _PipelineReject

_D = "2026-07-27"


class _Risk:
    def __init__(self, on: bool):
        self._one_trade_per_symbol_direction = on


class _Proc:
    """The helper under test, bound to a real store and a flag holder. Using the
    real function object keeps this honest: if the implementation moves or changes
    signature, this fails rather than silently testing a copy."""
    def __init__(self, store, on: bool):
        self._store = store
        self._risk = _Risk(on)
    _enforce_one_trade_per_symbol_direction = (
        SignalProcessor._enforce_one_trade_per_symbol_direction)


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "t.db")
    yield s
    s.close()


def _trade(store, tid, symbol, direction, status, created=f"{_D}T10:02:12+05:30"):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"sig_{tid}", symbol, "sc", "open_low_breakout_long", created, created,
             created, "PROCESSED", f"fp_{tid}", _D))
        cur.execute(
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,"
            "qty_planned,entry_target_price,sl_initial,tgt_initial,margin_reserved,"
            "risk_amount,created_at,status,order_protocol,updated_at) "
            "VALUES (?,?,?,?,?,1,418.75,414.54,425.01,0,0,?,?,'LIMIT_TRIPLE',?)",
            (tid, f"sig_{tid}", symbol, direction, "open_low_breakout_long",
             created, status, created))


def _at(store, on, symbol="SENCO", side="BUY", now=f"{_D}T10:14:11+05:30"):
    """Run the gate with the clock pinned to today's actual re-entry moment."""
    import signals.signal_processor as sp
    from datetime import datetime
    real = sp.now_ist
    sp.now_ist = lambda: datetime.fromisoformat(now)
    try:
        _Proc(store, on)._enforce_one_trade_per_symbol_direction(symbol, side)
        return None
    except _PipelineReject as exc:
        return exc
    finally:
        sp.now_ist = real


# ── B1: OFF is a TRUE no-op ───────────────────────────────────────────────────

def test_off_is_a_true_no_op_and_never_even_reads_the_store(store):
    """Structural, not behavioural: with the flag off the store is booby-trapped so
    ANY read raises. RED if the early return is ever moved below the query."""
    _trade(store, "t1", "SENCO", "LONG", "CLOSED")

    class _Boom:
        def __getattr__(self, name):
            raise AssertionError(f"store touched while the gate is OFF: {name}")

    p = _Proc(store, False)
    p._store = _Boom()
    p._enforce_one_trade_per_symbol_direction("SENCO", "BUY")   # must not raise


def test_the_SHIPPED_config_has_the_rule_ON():
    """27-Jul EVENING: Rama turned it ON. The shipped value is the operative one.

    RED if it ever drifts back to false -- that would silently restore the 27-Jul
    SENCO behaviour with nothing failing anywhere.
    """
    from core.config_loader import load_all
    from pathlib import Path
    cfg = load_all(Path("config"))
    assert cfg.system.risk.one_trade_per_symbol_direction_per_day is True


def test_the_CODE_default_stays_off_so_an_absent_key_cannot_silently_enable_it():
    """The yaml is the operative source; the Pydantic default is only the fallback
    when the key is missing. It stays False deliberately: a config omission must not
    silently switch on a rule that rejects entries. Same shape as delivery_enabled
    and conditional_allocation_enabled -- code-default-off, explicit yaml."""
    import core.config_loader as cl
    f = cl.RiskConfig.model_fields["one_trade_per_symbol_direction_per_day"]
    assert f.default is False


# ── B6: today's ACTUAL case, both ways ────────────────────────────────────────

def test_todays_senco_reentry_is_blocked_when_ON(store):
    _trade(store, "trd_c9675b4fae4a", "SENCO", "LONG", "CLOSED")   # the 10:02 trade
    exc = _at(store, on=True)
    assert exc is not None, "the 10:14 SENCO re-entry was not blocked"
    assert exc.check == "SYMBOL_DIRECTION_DAILY_LIMIT"
    assert "SENCO" in exc.reason and "LONG" in exc.reason


def test_todays_senco_reentry_is_ALLOWED_when_OFF(store):
    _trade(store, "trd_c9675b4fae4a", "SENCO", "LONG", "CLOSED")
    assert _at(store, on=False) is None, "OFF must reproduce today's behaviour exactly"


# ── B2: a REVERSAL is not a re-entry ──────────────────────────────────────────

def test_a_short_entry_after_a_long_exit_is_allowed(store):
    """The reversal case. A long exit followed by a SHORT entry is a different bet,
    consistent with the price having moved. A symbol-only rule would block it."""
    _trade(store, "t1", "SENCO", "LONG", "CLOSED")
    assert _at(store, on=True, side="SELL") is None, "a reversal must not be blocked"


def test_a_second_short_after_a_short_IS_blocked(store):
    _trade(store, "t1", "SENCO", "SHORT", "CLOSED")
    assert _at(store, on=True, side="SELL") is not None


# ── B3: a trade that never existed must not consume the slot ──────────────────

@pytest.mark.parametrize("status", ["FAILED", "REJECTED", "CANCELLED"])
def test_a_broker_rejected_entry_does_not_consume_the_days_slot(store, status):
    """PYRAMID x2 and KECL today were FAILED/REJECTED with ZERO order rows -- they
    never opened exposure. FIX-181's _EXECUTED_TRADE_STATUSES is the ONE definition
    of 'a trade happened' and this gate reuses it rather than restating it."""
    _trade(store, "t1", "PYRAMID", "LONG", status)
    assert _at(store, on=True, symbol="PYRAMID") is None, (
        f"a {status} entry wrongly consumed the day's slot")


def test_the_gate_reuses_fix181s_status_set_rather_than_restating_it():
    """RED if someone writes a second definition of 'a trade happened'."""
    import inspect
    src = inspect.getsource(StateStore.count_executed_trades_today_for_symbol_direction)
    assert "_EXECUTED_TRADE_STATUSES" in src
    assert "'CLOSED'" not in src and '"CLOSED"' not in src, (
        "status literals restated here instead of reusing _EXECUTED_TRADE_STATUSES")


# ── B7: it must not block too much ────────────────────────────────────────────

def test_first_trade_of_the_day_is_never_blocked(store):
    assert _at(store, on=True) is None


def test_a_different_symbol_is_not_blocked(store):
    _trade(store, "t1", "SENCO", "LONG", "CLOSED")
    assert _at(store, on=True, symbol="RKFORGE") is None


def test_yesterdays_trade_does_not_block_today(store):
    """The day boundary is SUBSTR(created_at,1,10) -- the same one count_trades_today
    already uses, not a second definition of 'today'."""
    _trade(store, "t1", "SENCO", "LONG", "CLOSED", created="2026-07-24T10:02:12+05:30")
    assert _at(store, on=True) is None


def test_an_open_position_still_blocks_a_same_direction_entry(store):
    """PENDING_FILL/OPEN are inside _EXECUTED_TRADE_STATUSES, so an in-flight trade
    also holds the slot. That overlaps the existing open-position guard by design --
    belt and braces, never a loosening."""
    _trade(store, "t1", "SENCO", "LONG", "OPEN")
    assert _at(store, on=True) is not None


# ── B4/B5: the ON path, which is now the SHIPPED default ──────────────────────
#
# Every test above proved the OFF path. From 27-Jul evening OFF is no longer what
# ships, so the ON path is the one that matters. These drive the gate with the flag
# on and the REAL store, covering the four cases plus the day boundary.

class TestOnPathIsNowTheShippedDefault:

    def test_the_block_fires(self, store):
        _trade(store, "t1", "SENCO", "LONG", "CLOSED")
        exc = _at(store, on=True)
        assert exc is not None and exc.check == "SYMBOL_DIRECTION_DAILY_LIMIT"

    def test_the_opposite_direction_still_passes(self, store):
        _trade(store, "t1", "SENCO", "LONG", "CLOSED")
        assert _at(store, on=True, side="SELL") is None

    @pytest.mark.parametrize("status", ["FAILED", "REJECTED", "CANCELLED"])
    def test_a_rejected_order_does_not_consume_the_slot(self, store, status):
        _trade(store, "t1", "PYRAMID", "LONG", status)
        assert _at(store, on=True, symbol="PYRAMID") is None

    def test_the_first_trade_of_the_day_is_never_blocked(self, store):
        assert _at(store, on=True) is None

    def test_the_day_boundary_releases_the_symbol_tomorrow(self, store):
        """THE 'per trading day' CLAIM, exercised as a live default for the first
        time. A symbol blocked today must be eligible tomorrow -- otherwise this is
        a permanent ban wearing a daily label."""
        _trade(store, "t1", "SENCO", "LONG", "CLOSED",
               created="2026-07-27T10:02:12+05:30")
        # same day -> blocked
        assert _at(store, on=True, now="2026-07-27T14:00:00+05:30") is not None
        # next trading day -> eligible again
        assert _at(store, on=True, now="2026-07-28T09:30:00+05:30") is None, (
            "the rule must RELEASE at the day boundary, not ban the symbol")

    def test_it_blocks_only_the_traded_direction_across_a_full_day(self, store):
        """Combined shape: SENCO LONG traded -> LONG blocked all day, SHORT free all
        day, and both eligible tomorrow."""
        _trade(store, "t1", "SENCO", "LONG", "CLOSED",
               created="2026-07-27T10:02:12+05:30")
        for t in ("2026-07-27T10:15:00+05:30", "2026-07-27T14:59:00+05:30"):
            assert _at(store, on=True, side="BUY", now=t) is not None
            assert _at(store, on=True, side="SELL", now=t) is None
        assert _at(store, on=True, side="BUY", now="2026-07-28T09:30:00+05:30") is None
