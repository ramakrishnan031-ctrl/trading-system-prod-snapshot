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
        """GET /health returns JSON with status, uptime, trades, timestamp."""
        app = _create_app(_MockStore(trade_count=3), _log())
        client = app.test_client()

        resp = client.get("/health")
        assert resp.status_code == 200
        data = json.loads(resp.data)

        assert data["status"] == "ok"
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0
        assert data["trades_today"] == 3
        assert "timestamp" in data
        print(f"  OK: /health -> 200, trades_today=3, uptime={data['uptime_seconds']}")

    def test_health_zero_trades(self) -> None:
        """GET /health with zero trades returns trades_today=0."""
        app = _create_app(_MockStore(trade_count=0), _log())
        client = app.test_client()

        resp = client.get("/health")
        data = json.loads(resp.data)
        assert data["trades_today"] == 0
        print("  OK: zero trades -> trades_today=0")

    def test_health_db_error_still_returns_200(self) -> None:
        """DB query failure does not crash /health; trades_today defaults to 0."""
        app = _create_app(_MockStore(raise_on_query=True), _log())
        client = app.test_client()

        resp = client.get("/health")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        assert data["trades_today"] == 0
        print("  OK: DB error -> /health still 200, trades=0")

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
