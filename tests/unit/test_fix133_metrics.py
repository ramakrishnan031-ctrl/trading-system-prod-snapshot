"""
tests/unit/test_fix133_metrics.py

FIX-133 Item 24: Metrics endpoint on healthcheck server.
  - GET /metrics returns all required JSON fields with correct types
  - GET /metrics/prometheus returns text format
  - DB error does not crash metrics
"""
from __future__ import annotations

import json
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.healthcheck_server import _create_app


def _log():
    return logging.getLogger("test_metrics")


class _MockStore:
    """Mock that returns sensible values for all metrics queries."""
    def __init__(self, raise_on_query=False):
        self._raise = raise_on_query

    def fetch_one(self, sql, params=()):
        if self._raise:
            raise RuntimeError("DB down")

        class _Row:
            def __init__(self, data):
                self._data = data
            def __getitem__(self, key):
                return self._data.get(key)

        if "COUNT(*) AS cnt FROM trades" in sql and "status IN" in sql:
            return _Row({"cnt": 2})
        if "COUNT(*) AS cnt FROM trades" in sql:
            return _Row({"cnt": 5})
        if "COUNT(*) AS cnt FROM signals" in sql and "TRADED" in sql:
            return _Row({"cnt": 3})
        if "COUNT(*) AS cnt FROM signals" in sql:
            return _Row({"cnt": 10})
        if "SUM(net_pnl)" in sql:
            return _Row({"pnl": 1500.0})
        if "capital_snapshot" in sql:
            return _Row({"margin_used": 25000.0, "cash_floor": 100000.0})
        if "kill_switch_state" in sql:
            return _Row({"value": "INACTIVE"})
        if "received_at FROM signals" in sql:
            return _Row({"received_at": "2026-05-31T10:15:30+05:30"})
        return None


class TestMetricsEndpoint:

    def test_metrics_returns_all_fields(self) -> None:
        """GET /metrics returns JSON with all required fields."""
        app = _create_app(_MockStore(), _log())
        client = app.test_client()

        resp = client.get("/metrics")
        assert resp.status_code == 200
        data = json.loads(resp.data)

        required = [
            "trades_today", "signals_received", "signals_traded",
            "open_positions", "daily_pnl", "capital_deployed_pct",
            "kill_switch_state", "uptime_seconds", "last_signal_at",
            "timestamp",
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"

        assert data["trades_today"] == 5
        assert data["signals_received"] == 10
        assert data["signals_traded"] == 3
        assert data["open_positions"] == 2
        assert data["daily_pnl"] == 1500.0
        assert data["capital_deployed_pct"] == 25.0
        assert data["kill_switch_state"] == "INACTIVE"
        assert data["last_signal_at"] == "10:15:30"
        assert isinstance(data["uptime_seconds"], (int, float))
        print(f"  OK: /metrics -> all {len(required)} fields present and correct")

    def test_metrics_db_error_still_200(self) -> None:
        """DB failure does not crash /metrics."""
        app = _create_app(_MockStore(raise_on_query=True), _log())
        client = app.test_client()

        resp = client.get("/metrics")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["trades_today"] == 0
        assert data["daily_pnl"] == 0.0
        print("  OK: DB error -> /metrics still 200, defaults to zeros")

    def test_prometheus_text_format(self) -> None:
        """GET /metrics/prometheus returns text format."""
        app = _create_app(_MockStore(), _log())
        client = app.test_client()

        resp = client.get("/metrics/prometheus")
        assert resp.status_code == 200
        assert "text/plain" in resp.content_type

        text = resp.data.decode()
        assert "trades_today 5" in text
        assert "daily_pnl 1500.00" in text
        assert "open_positions 2" in text
        assert "signals_received 10" in text
        assert "uptime_seconds" in text
        print("  OK: /metrics/prometheus -> text format with all metrics")

    def test_metrics_content_type_json(self) -> None:
        """Response content-type is application/json."""
        app = _create_app(_MockStore(), _log())
        client = app.test_client()

        resp = client.get("/metrics")
        assert "application/json" in resp.content_type
        print("  OK: /metrics content-type is application/json")


if __name__ == "__main__":
    tests = [
        TestMetricsEndpoint().test_metrics_returns_all_fields,
        TestMetricsEndpoint().test_metrics_db_error_still_200,
        TestMetricsEndpoint().test_prometheus_text_format,
        TestMetricsEndpoint().test_metrics_content_type_json,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
