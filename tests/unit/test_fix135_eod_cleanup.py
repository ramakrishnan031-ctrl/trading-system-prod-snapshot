"""Tests for FIX-135 Item 48: EOD cleanup script."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest

from core.state_store import StateStore
from core.time_authority import today_ist
from scripts.eod_cleanup import run_eod_cleanup


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "test.db")


def _insert_signal(store, status="PROCESSING", date_str=None):
    date_str = date_str or today_ist()
    ts = f"{date_str}T10:00:00+05:30"
    signal_id = str(uuid.uuid4())
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, 'TEST', 'test', 'test', ?, ?, ?, ?, ?, ?, 100.0)""",
            (signal_id, ts, ts, ts, status, f"fp-{signal_id}", date_str),
        )
    return signal_id


def _insert_trade_and_order(store, order_status="PENDING", date_str=None):
    date_str = date_str or today_ist()
    ts = f"{date_str}T10:00:00+05:30"
    trade_id = str(uuid.uuid4())
    signal_id = str(uuid.uuid4())
    order_id = str(uuid.uuid4())
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, 'TEST', 'test', 'test', ?, ?, ?, 'TRADED', ?, ?, 100.0)""",
            (signal_id, ts, ts, ts, f"fp-{signal_id}", date_str),
        )
        cur.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, strategy, qty_planned,
                qty_filled, entry_target_price, sl_initial, tgt_initial,
                margin_reserved, risk_amount, created_at, status,
                order_protocol, updated_at)
               VALUES (?, ?, 'TEST', 'LONG', 'test', 10, 10, 100.0, 95.0, 110.0,
                       2000.0, 500.0, ?, 'CLOSED', 'LIMIT_TRIPLE', ?)""",
            (trade_id, signal_id, ts, ts),
        )
        cur.execute(
            """INSERT INTO orders
               (order_id, trade_id, leg, transaction_type, order_type,
                product, variety, qty_requested, status, placed_at, updated_at)
               VALUES (?, ?, 'ENTRY', 'BUY', 'LIMIT', 'MIS', 'regular', 10, ?, ?, ?)""",
            (order_id, trade_id, order_status, ts, ts),
        )
    return trade_id, order_id


def _insert_smart_tgt_state(store, trade_id):
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO smart_tgt_state
               (trade_id, symbol, instrument_token, direction, entry_price,
                initial_sl, current_sl, qty, trigger_pct, step_pct,
                trail_count, registered_at)
               VALUES (?, 'TEST', 12345, 'LONG', 100.0, 95.0, 95.0, 10,
                       0.005, 0.003, 0, ?)""",
            (trade_id, "2026-01-01T10:00:00"),
        )


# ── Stale signals ────────────────────────────────────────────────────────


class TestStaleSignals:
    def test_in_process_from_yesterday_expired(self, store):
        _insert_signal(store, "PROCESSING", "2026-05-30")
        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31",
            log=logging.getLogger("test"),
        )
        assert results["stale_signals_expired"] == 1
        row = store.fetch_one("SELECT status FROM signals")
        assert row["status"] == "EXPIRED"

    def test_today_in_process_not_expired(self, store):
        _insert_signal(store, "PROCESSING", "2026-05-31")
        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31",
            log=logging.getLogger("test"),
        )
        assert results["stale_signals_expired"] == 0

    def test_traded_signal_not_touched(self, store):
        _insert_signal(store, "TRADED", "2026-05-30")
        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31",
            log=logging.getLogger("test"),
        )
        assert results["stale_signals_expired"] == 0


# ── Q5/P10: old fingerprint prune must catch REJECTED_* + dry-run == actual ──


class TestFingerprintPrune:
    """The pipeline persists REJECTED_<check> (never a bare 'REJECTED'), so the old
    `status IN (...,'REJECTED')` filter pruned no rejects at all -> unbounded growth in a
    table the dedup path reads. And the dry-run counted ALL old rows, not just the prunable
    ones. Both fixed: GLOB 'REJECTED*' + shared predicate for preview == action.
    (15-Jul: the default retention is now 90d from config, so these tests pass an explicit
    signal_retention_days=7 to keep exercising the prune with the seeded 30-day-old dates.)"""

    _OLD = "2026-05-01"          # well before a date_iso - 7d cutoff

    def _seed(self, store):
        _insert_signal(store, "REJECTED_DUPLICATE_SYMBOL", self._OLD)
        _insert_signal(store, "REJECTED_SHADOW_INNING_ACTIVE", self._OLD)
        _insert_signal(store, "EXPIRED", self._OLD)
        _insert_signal(store, "DUPLICATE", self._OLD)
        _insert_signal(store, "TRADED", self._OLD)     # KEEP — trade audit trail
        _insert_signal(store, "PROCESSED", self._OLD)  # KEEP

    def test_rejected_prefix_pruned_trade_trail_kept(self, store):
        self._seed(store)
        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31", log=logging.getLogger("test"),
            signal_retention_days=7,
        )
        # 2 REJECTED_* + EXPIRED + DUPLICATE pruned; TRADED + PROCESSED kept
        assert results["fingerprints_pruned"] == 4
        remaining = {r["status"] for r in store.fetch_all("SELECT status FROM signals")}
        assert remaining == {"TRADED", "PROCESSED"}
        assert store.fetch_one(
            "SELECT COUNT(*) AS n FROM signals WHERE status GLOB 'REJECTED*'")["n"] == 0

    def test_dry_run_count_equals_actual_delete(self, store):
        self._seed(store)
        dry = run_eod_cleanup(
            store=store, date_iso="2026-05-31", log=logging.getLogger("test"), dry_run=True,
            signal_retention_days=7,
        )
        # dry-run deletes nothing
        assert store.fetch_one("SELECT COUNT(*) AS n FROM signals")["n"] == 6
        actual = run_eod_cleanup(
            store=store, date_iso="2026-05-31", log=logging.getLogger("test"),
            signal_retention_days=7,
        )
        # preview == action == the 4 prunable rows (old code over-counted the preview at 6)
        assert dry["fingerprints_pruned"] == 4
        assert actual["fingerprints_pruned"] == 4


class TestFingerprintPruneFkSafe:
    """15-Jul-2026 fix: P10 broadened the prune to GLOB 'REJECTED*', but those signals are
    FK-referenced by screener_results (foreign_keys=ON) -> the whole `DELETE FROM signals`
    rolled back every 15:50 run (~108k stuck, +4.3k/session). The prune is now children-first
    + batched + capital-guarded (NOT EXISTS trades). These tests ERROR on the pre-fix code
    (FK IntegrityError in test (a)) and pass on the fix."""

    _OLD = "2026-05-01"

    @staticmethod
    def _add_screener_child(store, signal_id, date_str="2026-05-01"):
        ts = f"{date_str}T10:00:00+05:30"
        with store.transaction() as cur:
            cur.execute(
                """INSERT INTO screener_results
                   (signal_id, score, tier, status, step_results, latencies,
                    market_data_snapshot, ts)
                   VALUES (?, 57, 'LOW', 'REJECTED_SCORE_57', '{}', '{}', '{}', ?)""",
                (signal_id, ts),
            )

    def test_rejected_with_screener_child_pruned_fk_clean(self, store):
        # (a) old REJECTED_* WITH a screener_results child = the live FK-rollback trigger.
        sid_a = _insert_signal(store, "REJECTED_SCORE_57", self._OLD)
        self._add_screener_child(store, sid_a)
        # (c) old EXPIRED, no children.
        _insert_signal(store, "EXPIRED", self._OLD)

        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31", log=logging.getLogger("test"),
            signal_retention_days=7,
        )
        # both pruned; the screener child dropped children-first with its parent
        assert results["fingerprints_pruned"] == 2
        assert store.fetch_one("SELECT COUNT(*) AS n FROM signals")["n"] == 0
        assert store.fetch_one("SELECT COUNT(*) AS n FROM screener_results")["n"] == 0
        # FK integrity clean afterward (no dangling child)
        assert store.fetch_all("PRAGMA foreign_key_check") == []

    def test_trade_linked_signal_never_pruned(self, store):
        # (b) CAPITAL-SAFETY: a signal WITH a `trades` child is NEVER deleted, even when its
        # status matches the prune filter (adversarial — proves the NOT EXISTS trades guard).
        ts = f"{self._OLD}T10:00:00+05:30"
        sid_b = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        with store.transaction() as cur:
            cur.execute(
                """INSERT INTO signals
                   (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                    expires_at, status, fingerprint, fingerprint_date, trigger_price)
                   VALUES (?, 'TEST', 'test', 'test', ?, ?, ?, 'REJECTED_SCORE_57', ?, ?, 100.0)""",
                (sid_b, ts, ts, ts, f"fp-{sid_b}", self._OLD),
            )
            cur.execute(
                """INSERT INTO trades
                   (trade_id, signal_id, symbol, direction, strategy, qty_planned,
                    qty_filled, entry_target_price, sl_initial, tgt_initial,
                    margin_reserved, risk_amount, created_at, status,
                    order_protocol, updated_at)
                   VALUES (?, ?, 'TEST', 'LONG', 'test', 10, 10, 100.0, 95.0, 110.0,
                           2000.0, 500.0, ?, 'CLOSED', 'LIMIT_TRIPLE', ?)""",
                (tid, sid_b, ts, ts),
            )

        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31", log=logging.getLogger("test"),
            signal_retention_days=7,
        )
        # the trade-linked signal survives; nothing pruned
        assert results["fingerprints_pruned"] == 0
        assert store.fetch_one(
            "SELECT COUNT(*) AS n FROM signals WHERE signal_id = ?", (sid_b,))["n"] == 1
        assert store.fetch_all("PRAGMA foreign_key_check") == []

    def test_batched_prune_clears_backlog_over_one_batch(self, store):
        # a backlog larger than one batch (_PRUNE_BATCH) clears fully across COMMITted chunks.
        from scripts.eod_cleanup import _PRUNE_BATCH
        n = _PRUNE_BATCH + 50
        ts = f"{self._OLD}T10:00:00+05:30"
        with store.transaction() as cur:
            cur.executemany(
                """INSERT INTO signals
                   (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                    expires_at, status, fingerprint, fingerprint_date, trigger_price)
                   VALUES (?, 'TEST', 'test', 'test', ?, ?, ?, 'REJECTED_SCORE_57', ?, ?, 100.0)""",
                [(str(uuid.uuid4()), ts, ts, ts, f"fp-{i}", self._OLD) for i in range(n)],
            )
        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31", log=logging.getLogger("test"),
            signal_retention_days=7,
        )
        assert results["fingerprints_pruned"] == n
        assert store.fetch_one("SELECT COUNT(*) AS n FROM signals")["n"] == 0


# ── Stale orders ─────────────────────────────────────────────────────────


class TestStaleOrders:
    def test_pending_from_yesterday_cancelled(self, store):
        _insert_trade_and_order(store, "PENDING", "2026-05-30")
        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31",
            log=logging.getLogger("test"),
        )
        assert results["stale_orders_cancelled"] == 1

    def test_today_pending_not_cancelled(self, store):
        _insert_trade_and_order(store, "PENDING", "2026-05-31")
        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31",
            log=logging.getLogger("test"),
        )
        assert results["stale_orders_cancelled"] == 0

    def test_complete_order_not_touched(self, store):
        _insert_trade_and_order(store, "COMPLETE", "2026-05-30")
        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31",
            log=logging.getLogger("test"),
        )
        assert results["stale_orders_cancelled"] == 0


# ── Orphaned smart_tgt ───────────────────────────────────────────────────


class TestOrphanedSmartTgt:
    def test_orphaned_deleted(self, store):
        tid, _ = _insert_trade_and_order(store, "COMPLETE")
        _insert_smart_tgt_state(store, tid)
        results = run_eod_cleanup(
            store=store, date_iso=today_ist(),
            log=logging.getLogger("test"),
        )
        assert results["orphaned_smart_tgt_deleted"] == 1

    def test_active_trade_tgt_kept(self, store):
        ts = f"{today_ist()}T10:00:00+05:30"
        trade_id = str(uuid.uuid4())
        signal_id = str(uuid.uuid4())
        with store.transaction() as cur:
            cur.execute(
                """INSERT INTO signals
                   (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                    expires_at, status, fingerprint, fingerprint_date, trigger_price)
                   VALUES (?, 'TEST', 'test', 'test', ?, ?, ?, 'TRADED', ?, ?, 100.0)""",
                (signal_id, ts, ts, ts, f"fp-{signal_id}", today_ist()),
            )
            cur.execute(
                """INSERT INTO trades
                   (trade_id, signal_id, symbol, direction, strategy, qty_planned,
                    qty_filled, entry_target_price, sl_initial, tgt_initial,
                    margin_reserved, risk_amount, created_at, status,
                    order_protocol, updated_at)
                   VALUES (?, ?, 'TEST', 'LONG', 'test', 10, 10, 100.0, 95.0, 110.0,
                           2000.0, 500.0, ?, 'OPEN', 'LIMIT_TRIPLE', ?)""",
                (trade_id, signal_id, ts, ts),
            )
        _insert_smart_tgt_state(store, trade_id)
        results = run_eod_cleanup(
            store=store, date_iso=today_ist(),
            log=logging.getLogger("test"),
        )
        assert results["orphaned_smart_tgt_deleted"] == 0


# ── Dry run ──────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_does_not_modify(self, store):
        _insert_signal(store, "PROCESSING", "2026-05-30")
        _insert_trade_and_order(store, "PENDING", "2026-05-30")
        results = run_eod_cleanup(
            store=store, date_iso="2026-05-31",
            log=logging.getLogger("test"),
            dry_run=True,
        )
        assert results["stale_signals_expired"] == 1
        assert results["stale_orders_cancelled"] == 1
        row = store.fetch_one("SELECT status FROM signals")
        assert row["status"] == "PROCESSING"
