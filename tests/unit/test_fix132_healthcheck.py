"""
tests/unit/test_fix132_healthcheck.py

FIX-132 Item 15: External VM health monitor.
  - GET /health returns correct JSON fields
  - trades_today queries DB correctly
  - uptime_seconds is positive
  - DB error does not crash health endpoint
"""
from __future__ import annotations

import json
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.healthcheck_server import _create_app


def _log():
    return logging.getLogger("test_healthcheck")


class _MockStore:
    def __init__(self, trade_count=5, raise_on_query=False):
        self._count = trade_count
        self._raise = raise_on_query

    def fetch_one(self, sql, params):
        if self._raise:
            raise RuntimeError("DB connection lost")

        class _Row:
            def __init__(self, cnt):
                self._cnt = cnt
            def __getitem__(self, key):
                if key == "cnt":
                    return self._cnt
                return None
        return _Row(self._count)


class TestHealthEndpoint:

    def test_health_returns_200_with_correct_fields(self) -> None:
        """GET /health returns JSON with status, uptime, trades, timestamp.

        FIX-188: /health is 200/"healthy" only when db+token+kill_switch are all
        OK; patch token+kill to healthy so this test exercises the DB/trades
        fields with a fully-healthy result."""
        from unittest.mock import patch
        with patch("scripts.healthcheck_server._check_token",
                   return_value={"ok": True, "account_id": "LFL836"}), \
             patch("scripts.healthcheck_server._check_kill_switch",
                   return_value={"ok": True, "state": "INACTIVE"}):
            app = _create_app(_MockStore(trade_count=3), _log())
            client = app.test_client()
            resp = client.get("/health")

        assert resp.status_code == 200
        data = json.loads(resp.data)

        assert data["status"] == "healthy"
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0
        assert data["trades_today"] == 3
        assert "timestamp" in data
        print(f"  OK: /health -> 200, trades_today=3, uptime={data['uptime_seconds']}")

    def test_metrics_merges_runtime_provider(self) -> None:
        """FIX-190 (Bug B): /metrics merges the signal-processor runtime counters
        (entries placed/throttled/rejected) on top of the DB-derived fields."""
        def provider():
            return {"entries_placed": 4, "entries_throttled": 2, "entries_rejected": 1,
                    "signals_processed": 7}
        app = _create_app(_MockStore(trade_count=3), _log(), metrics_provider=provider)
        client = app.test_client()
        resp = client.get("/metrics")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["entries_placed"] == 4
        assert data["entries_throttled"] == 2
        assert data["entries_rejected"] == 1
        assert data["signals_processed"] == 7
        print("  OK: /metrics merges runtime counters (Bug B)")

    def test_health_zero_trades(self) -> None:
        """GET /health with zero trades returns trades_today=0."""
        app = _create_app(_MockStore(trade_count=0), _log())
        client = app.test_client()

        resp = client.get("/health")
        data = json.loads(resp.data)
        assert data["trades_today"] == 0
        print("  OK: zero trades -> trades_today=0")

    def test_health_db_error_does_not_crash(self) -> None:
        """DB query failure does not crash /health; it returns a structured
        response. FIX-188: a failed db check makes /health degraded -> 503 (the
        endpoint stays listening so uptime monitors can detect the degradation)."""
        app = _create_app(_MockStore(raise_on_query=True), _log())
        client = app.test_client()

        resp = client.get("/health")
        assert resp.status_code == 503             # degraded, but did not crash
        data = json.loads(resp.data)
        assert data["status"] == "degraded"
        assert data["checks"]["db"]["ok"] is False
        assert data["trades_today"] == 0
        print("  OK: DB error -> /health 503 degraded (no crash), trades=0")

    def test_health_content_type_json(self) -> None:
        """Response content-type is application/json."""
        app = _create_app(_MockStore(), _log())
        client = app.test_client()

        resp = client.get("/health")
        assert "application/json" in resp.content_type
        print("  OK: content-type is application/json")

    def test_health_404_on_other_routes(self) -> None:
        """Non-/health routes return 404."""
        app = _create_app(_MockStore(), _log())
        client = app.test_client()

        resp = client.get("/status")
        assert resp.status_code == 404
        print("  OK: /status -> 404")


if __name__ == "__main__":
    tests = [
        TestHealthEndpoint().test_health_returns_200_with_correct_fields,
        TestHealthEndpoint().test_health_zero_trades,
        TestHealthEndpoint().test_health_db_error_still_returns_200,
        TestHealthEndpoint().test_health_content_type_json,
        TestHealthEndpoint().test_health_404_on_other_routes,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")


# ═════════════════════════════════════════════════════════════════════════════
# 25-Jul-2026 — /metrics capital block reads live sources, not capital_snapshot.
#
# Before: `SELECT margin_used, cash_floor FROM capital_snapshot WHERE id = 1`.
# That table has 0 rows in production, so fetch_one returned None and
# capital_deployed_pct was NEVER emitted at all -- the metric looked implemented
# and shipped nothing for months.
#
# The definition changed with the source, deliberately and dated:
#   was  margin_used / cash_floor    (a ratio against REMAINING cash, unbounded)
#   now  margin_used / total_capital (an actual deployment percentage)
# Safe precisely because the old one never emitted -- no series, no consumer.
# ═════════════════════════════════════════════════════════════════════════════

class _CapitalStore:
    """Store double that answers the two queries the capital block makes."""

    def __init__(self, margin_used=0.0, opening=None, raise_on_opening=False):
        self._margin = margin_used
        self._opening = opening
        self._raise = raise_on_opening

    def fetch_one(self, sql, params=()):
        class _Row:
            def __init__(self, m):
                self._m = m
            def __getitem__(self, key):
                return self._m if key == "m" else (0 if key == "cnt" else None)
        return _Row(self._margin)

    def get_day_opening_capital(self, date_iso):
        if self._raise:
            raise RuntimeError("ledger unreadable")
        return self._opening


def _metrics(store):
    app = _create_app(store, _log())
    return json.loads(app.test_client().get("/metrics").data)


class TestMetricsCapitalRedirect:

    def test_deployed_pct_is_real_and_nonzero_against_open_positions(self):
        """B8: a flat book proves nothing. 2,500 margin against a 10,000 opening
        is 25% -- and it is margin/TOTAL, not margin/remaining (which would be
        33.33% here)."""
        d = _metrics(_CapitalStore(margin_used=2500.0, opening=10000.0))
        assert d["capital_deployed_pct"] == 25.0
        assert d["margin_used"] == 2500.0
        assert d["total_capital"] == 10000.0
        assert "capital_deployed_pct_unavailable" not in d

    def test_no_init_row_states_a_reason_instead_of_a_silent_zero(self):
        """A silent 0.0 would read as 'nothing deployed' when the truth is
        'we could not tell'."""
        d = _metrics(_CapitalStore(margin_used=2500.0, opening=None))
        assert d["capital_deployed_pct"] is None
        assert "no INIT ledger row" in d["capital_deployed_pct_unavailable"]
        assert d["margin_used"] == 2500.0          # still reported

    def test_a_failing_opening_capital_read_does_not_cost_the_other_metrics(self):
        """The capital block sits mid-way through /metrics. If it raised, every
        metric after it would be lost to the outer handler."""
        d = _metrics(_CapitalStore(margin_used=2500.0, raise_on_opening=True))
        assert d["capital_deployed_pct"] is None
        assert "kill_switch_state" in d            # a metric that comes AFTER it
