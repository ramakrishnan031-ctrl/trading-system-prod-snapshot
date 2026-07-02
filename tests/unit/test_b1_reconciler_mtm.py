"""B-1 (02-Jul): order_reconciler._refresh_unrealized_mtm() — populate open positions'
unrealized MTM from get_quote, SET-BASED prune of closed trades, and quote-outage
fallback. Parity: the compute is mode-agnostic (one get_quote path, paper + live).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from orders.order_reconciler import OrderReconciler
from tests.unit.test_fund_manager import _make_store, _initialized_fm


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def get_all_open_trades(self):
        return self._rows


class _Quote:
    def __init__(self, last_price):
        self.last_price = last_price


def _quote_fn(prices, *, raise_exc=None):
    def qf(symbols):
        if raise_exc is not None:
            raise raise_exc
        return {s: _Quote(prices[s]) for s in symbols if s in prices}
    return qf


def _row(trade_id, symbol, direction, avg, qty):
    return {
        "trade_id": trade_id, "symbol": symbol, "direction": direction,
        "entry_actual_price": avg, "qty_filled": qty,
    }


def _recon(store, fm, quote_fn):
    r = OrderReconciler.__new__(OrderReconciler)
    r._store = store
    r._fm = fm
    r._quote_fn = quote_fn
    r._log = logging.getLogger("test_b1_recon")
    r._mtm_refresh_success = 0
    r._mtm_refresh_failure = 0
    return r


class _Fm:
    """Context manager: an initialized FundManager whose backing StateStore is closed
    on exit (Windows locks the temp DB otherwise)."""
    def __init__(self, tmp, balance=1_000_000.0):
        self._store = _make_store(Path(tmp))
        self.fm = _initialized_fm(self._store, balance=balance)

    def __enter__(self):
        return self.fm

    def __exit__(self, *exc):
        self._store.close()


def test_b1_refresh_populates_unrealized_from_ltp():
    """LONG RELIANCE avg=100 qty=10 @ltp=110 → +100; SHORT INFY avg=200 qty=5 @ltp=190
    → (190−200)×5×−1 = +50. Total = +150, fresh."""
    with tempfile.TemporaryDirectory() as tmp, _Fm(tmp) as fm:
        rows = [_row("t_long", "RELIANCE", "LONG", 100.0, 10),
                _row("t_short", "INFY", "SHORT", 200.0, 5)]
        r = _recon(_FakeStore(rows), fm, _quote_fn({"RELIANCE": 110.0, "INFY": 190.0}))
        r._refresh_unrealized_mtm()
        total, fresh = fm.get_unrealized_mtm_status()
        assert abs(total - 150.0) < 1e-6, total
        assert fresh is True
        assert r._mtm_refresh_success == 1 and r._mtm_refresh_failure == 0
    print("  OK B-1: refresh populates unrealized from LTP (LONG + SHORT)")


def test_b1_refresh_prunes_closed_trade_any_path():
    """A trade no longer in the open set is pruned on the next refresh — removal for a
    close by ANY path, no per-close hook, no stale inflation."""
    with tempfile.TemporaryDirectory() as tmp, _Fm(tmp) as fm:
        fm.update_unrealized_mtm("t_closed", -9_999.0)   # stale from a prior cycle
        rows = [_row("t_open", "RELIANCE", "LONG", 100.0, 10)]
        r = _recon(_FakeStore(rows), fm, _quote_fn({"RELIANCE": 105.0}))
        r._refresh_unrealized_mtm()
        total, _ = fm.get_unrealized_mtm_status()
        assert abs(total - 50.0) < 1e-6, total   # only t_open (+50); t_closed pruned
    print("  OK B-1: set-based prune removes closed trade (no stale MTM)")


def test_b1_refresh_quote_outage_marks_unavailable():
    """get_quote outage → MTM marked UNAVAILABLE (gate degrades to realized-only);
    last-known values retained, failure counted."""
    with tempfile.TemporaryDirectory() as tmp, _Fm(tmp) as fm:
        fm.update_unrealized_mtm("t_open", -200.0)         # last-known
        fm.mark_unrealized_mtm_refreshed(available=True)
        rows = [_row("t_open", "RELIANCE", "LONG", 100.0, 10)]
        r = _recon(_FakeStore(rows), fm, _quote_fn({}, raise_exc=RuntimeError("feed down")))
        r._refresh_unrealized_mtm()
        total, fresh = fm.get_unrealized_mtm_status()
        assert fresh is False                     # unavailable → gate falls back
        assert abs(total - (-200.0)) < 1e-6       # last-known retained, not cleared
        assert r._mtm_refresh_failure == 1
    print("  OK B-1: quote outage → unavailable + last-known retained")


def test_b1_refresh_no_usable_quote_marks_unavailable():
    """Positions open but no quote returned for any → unavailable (don't trust an empty
    'fresh' map)."""
    with tempfile.TemporaryDirectory() as tmp, _Fm(tmp) as fm:
        rows = [_row("t_open", "RELIANCE", "LONG", 100.0, 10)]
        r = _recon(_FakeStore(rows), fm, _quote_fn({}))   # empty quote dict
        r._refresh_unrealized_mtm()
        _, fresh = fm.get_unrealized_mtm_status()
        assert fresh is False
        assert r._mtm_refresh_failure == 1
    print("  OK B-1: no usable quote → unavailable")


def test_b1_refresh_empty_open_set_is_fresh_zero():
    """No open positions → MTM is trivially 0 and fresh."""
    with tempfile.TemporaryDirectory() as tmp, _Fm(tmp) as fm:
        r = _recon(_FakeStore([]), fm, _quote_fn({}))
        r._refresh_unrealized_mtm()
        total, fresh = fm.get_unrealized_mtm_status()
        assert total == 0.0 and fresh is True
        assert r._mtm_refresh_success == 1
    print("  OK B-1: empty open set → 0 + fresh")


def test_b1_parity_same_compute_paper_and_live():
    """Parity: the SAME _refresh path yields the SAME MTM whether get_quote stands in for
    the paper provider or the live adapter (both return .last_price). One code path."""
    rows = [_row("t1", "RELIANCE", "LONG", 100.0, 10)]
    results = []
    for _mode in ("PAPER", "LIVE"):
        with tempfile.TemporaryDirectory() as tmp, _Fm(tmp) as fm:
            r = _recon(_FakeStore(rows), fm, _quote_fn({"RELIANCE": 112.5}))
            r._refresh_unrealized_mtm()
            total, _ = fm.get_unrealized_mtm_status()
            results.append(total)
    assert abs(results[0] - results[1]) < 1e-9 and abs(results[0] - 125.0) < 1e-6
    print("  OK B-1: identical MTM compute in paper + live (one path)")
