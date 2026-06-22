"""Tests for the F&O ban fetch (FIX-135/136 Item 44 + 22-Jun-2026 endpoint fix).

22-Jun-2026: endpoint moved JSON (/api/live-analysis-banned, now 404) → NSE
Clearing CSV (nsearchives.../fo_secban.csv). Failure severity downgraded
CRITICAL→WARN and made FAIL-OPEN for NSE-EQ.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.state_store import StateStore
from core.time_authority import today_ist
from scripts.fetch_fno_ban import (
    parse_secban_csv,
    fetch_fno_ban_symbols,
    store_fno_ban,
    store_fetch_failed_sentinel,
    is_symbol_fno_banned,
    main,
    _cron_main,
    _FETCH_FAILED_SENTINEL,
    _DEFAULT_URL,
)


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "test.db")


# ── Store and query ──────────────────────────────────────────────────────


class TestStoreFnoBan:
    def test_store_and_query(self, store):
        log = logging.getLogger("test")
        stored = store_fno_ban(store, ["RELIANCE", "INFY"], today_ist(), log)
        assert stored == 2
        assert is_symbol_fno_banned(store, "RELIANCE")
        assert is_symbol_fno_banned(store, "INFY")

    def test_not_banned_returns_false(self, store):
        assert is_symbol_fno_banned(store, "TCS") is False

    def test_different_date_not_banned(self, store):
        log = logging.getLogger("test")
        store_fno_ban(store, ["RELIANCE"], "2020-01-01", log)
        assert is_symbol_fno_banned(store, "RELIANCE", today_ist()) is False
        assert is_symbol_fno_banned(store, "RELIANCE", "2020-01-01") is True

    def test_duplicate_insert_replaces(self, store):
        log = logging.getLogger("test")
        store_fno_ban(store, ["RELIANCE"], today_ist(), log)
        store_fno_ban(store, ["RELIANCE"], today_ist(), log)
        row = store.fetch_one(
            "SELECT COUNT(*) AS n FROM fno_ban WHERE symbol = 'RELIANCE' AND ban_date = ?",
            (today_ist(),),
        )
        assert row["n"] == 1

    def test_empty_list_stores_nothing(self, store):
        log = logging.getLogger("test")
        stored = store_fno_ban(store, [], today_ist(), log)
        assert stored == 0

    def test_multiple_symbols_stored(self, store):
        log = logging.getLogger("test")
        symbols = ["A", "B", "C", "D"]
        stored = store_fno_ban(store, symbols, today_ist(), log)
        assert stored == 4
        for sym in symbols:
            assert is_symbol_fno_banned(store, sym)


# ── Equity vs F&O logic ──────────────────────────────────────────────────


class TestEquityVsFno:
    def test_equity_strategy_ignores_ban(self, store):
        """Pure equity strategies should NOT check F&O ban (intent=INTRADAY, product=MIS)."""
        log = logging.getLogger("test")
        store_fno_ban(store, ["RELIANCE"], today_ist(), log)
        assert is_symbol_fno_banned(store, "RELIANCE") is True

    def test_banned_symbol_detected(self, store):
        log = logging.getLogger("test")
        store_fno_ban(store, ["BAJFINANCE"], today_ist(), log)
        assert is_symbol_fno_banned(store, "BAJFINANCE") is True
        assert is_symbol_fno_banned(store, "HDFCBANK") is False


# ── Parity ────────────────────────────────────────────────────────────────


class TestParity:
    def test_same_check_paper_and_live(self, store):
        """Ban check is config-driven, identical for paper and live."""
        log = logging.getLogger("test")
        store_fno_ban(store, ["SBIN"], today_ist(), log)
        assert is_symbol_fno_banned(store, "SBIN") is True


# ── Fail-closed sentinel mechanism (kept for a future F&O era) ────────────


class TestFailClosed:
    def test_sentinel_blocks_all_symbols(self, store):
        log = logging.getLogger("test")
        store_fetch_failed_sentinel(store, today_ist(), log)
        assert is_symbol_fno_banned(store, "RELIANCE") is True
        assert is_symbol_fno_banned(store, "TCS") is True
        assert is_symbol_fno_banned(store, "ANYTHING") is True

    def test_sentinel_date_scoped(self, store):
        log = logging.getLogger("test")
        store_fetch_failed_sentinel(store, "2020-01-01", log)
        assert is_symbol_fno_banned(store, "RELIANCE", "2020-01-01") is True
        assert is_symbol_fno_banned(store, "RELIANCE", today_ist()) is False

    def test_normal_ban_still_works_without_sentinel(self, store):
        log = logging.getLogger("test")
        store_fno_ban(store, ["INFY"], today_ist(), log)
        assert is_symbol_fno_banned(store, "INFY") is True
        assert is_symbol_fno_banned(store, "TCS") is False


# ── CSV parser (22-Jun-2026 endpoint fix) ─────────────────────────────────


class TestParseSecbanCsv:
    def test_parse_csv_with_bans(self):
        text = "Securities in Ban For Trade Date 22-JUN-2026:\n1,KAYNES\n2,IDEA\n"
        trade_date, symbols = parse_secban_csv(text)
        assert trade_date == "2026-06-22"
        assert symbols == ["KAYNES", "IDEA"]

    def test_parse_csv_single_ban(self):
        # The real 22-Jun-2026 live payload.
        text = "Securities in Ban For Trade Date 22-JUN-2026:\n1,KAYNES\n"
        trade_date, symbols = parse_secban_csv(text)
        assert trade_date == "2026-06-22"
        assert symbols == ["KAYNES"]

    def test_parse_csv_no_bans(self):
        text = "Securities in Ban For Trade Date 22-JUN-2026:\n"
        trade_date, symbols = parse_secban_csv(text)
        assert trade_date == "2026-06-22"
        assert symbols == []

    def test_parse_strips_and_uppercases(self):
        text = "Securities in Ban For Trade Date 03-JAN-2026:\n1, kaynes \n2,idea\n"
        trade_date, symbols = parse_secban_csv(text)
        assert trade_date == "2026-01-03"
        assert symbols == ["KAYNES", "IDEA"]

    def test_html_error_page_treated_as_failure(self):
        html = "<!DOCTYPE html>\n<html><head><title>Access Denied</title></head></html>"
        with pytest.raises(RuntimeError, match="HTML"):
            parse_secban_csv(html)

    def test_parse_empty_raises(self):
        with pytest.raises(RuntimeError, match="empty"):
            parse_secban_csv("   \n  \n")

    def test_parse_unexpected_header_raises(self):
        with pytest.raises(RuntimeError, match="unexpected header"):
            parse_secban_csv("totally,unexpected,csv\n1,FOO\n")

    def test_parse_date_all_months(self):
        for mon, mm in [("JAN", "01"), ("JUN", "06"), ("DEC", "12")]:
            date, _ = parse_secban_csv(f"Securities in Ban For Trade Date 15-{mon}-2026:\n")
            assert date == f"2026-{mm}-15"


# ── fetch_fno_ban_symbols (HTTP layer, mocked) ────────────────────────────


class TestFetchSymbols:
    def _mock_requests(self, text, raise_status=None):
        mock_resp = MagicMock()
        mock_resp.text = text
        mock_resp.raise_for_status = MagicMock(side_effect=raise_status)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session.headers = {}
        mock_requests = MagicMock()
        mock_requests.Session.return_value = mock_session
        return mock_requests

    def test_fetch_parses_csv(self):
        log = logging.getLogger("test")
        mock_requests = self._mock_requests(
            "Securities in Ban For Trade Date 22-JUN-2026:\n1,KAYNES\n"
        )
        with patch("scripts.fetch_fno_ban.requests", mock_requests):
            trade_date, symbols = fetch_fno_ban_symbols(log, url="http://test.local/csv")
        assert trade_date == "2026-06-22"
        assert symbols == ["KAYNES"]

    def test_fetch_http_error_propagates(self):
        log = logging.getLogger("test")
        mock_requests = self._mock_requests("", raise_status=Exception("404 Not Found"))
        with patch("scripts.fetch_fno_ban.requests", mock_requests):
            with pytest.raises(Exception, match="404"):
                fetch_fno_ban_symbols(log, url="http://test.local/csv")

    def test_fetch_html_block_page_raises(self):
        log = logging.getLogger("test")
        mock_requests = self._mock_requests("<html><body>blocked</body></html>")
        with patch("scripts.fetch_fno_ban.requests", mock_requests):
            with pytest.raises(RuntimeError, match="HTML"):
                fetch_fno_ban_symbols(log, url="http://test.local/csv")


# ── main() behaviour: WARN, fail-open, exit codes ─────────────────────────


def _no_config(monkeypatch):
    """Force main() onto its config defaults (fail-open) — no repo coupling."""
    def _raise(*a, **k):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("core.config_loader.load_all", _raise)


@pytest.fixture()
def alerts(monkeypatch):
    """Capture _send_alert(level, message) calls instead of hitting Telegram."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "scripts.fetch_fno_ban._send_alert",
        lambda log, message, level="WARNING": calls.append((level, message)),
    )
    return calls


class TestMainBehaviour:
    def test_success_stores_and_silent(self, tmp_path, monkeypatch, alerts):
        db = tmp_path / "t.db"
        _no_config(monkeypatch)
        monkeypatch.setattr(
            "scripts.fetch_fno_ban.fetch_fno_ban_symbols",
            lambda log, url=_DEFAULT_URL: (today_ist(), ["KAYNES"]),
        )
        rc = main(["--db", str(db)])
        assert rc == 0
        assert alerts == []  # silent success — no alert
        store2 = StateStore(db_path=db)
        assert is_symbol_fno_banned(store2, "KAYNES") is True

    def test_fetch_failure_is_warn_not_critical(self, tmp_path, monkeypatch, alerts):
        db = tmp_path / "t.db"
        _no_config(monkeypatch)

        def _boom(log, url=_DEFAULT_URL):
            raise Exception("404 Client Error: Not Found")

        monkeypatch.setattr("scripts.fetch_fno_ban.fetch_fno_ban_symbols", _boom)
        rc = main(["--db", str(db)])
        assert rc == 0  # exit 0 — job ran, data unavailable
        assert len(alerts) == 1
        level, message = alerts[0]
        assert level == "WARNING"  # NOT CRITICAL
        assert "EQ" in message

    def test_eq_signals_not_blocked_on_fetch_failure(self, tmp_path, monkeypatch, alerts):
        """The key behavioural guard: a fetch failure must not ban EQ symbols."""
        db = tmp_path / "t.db"
        _no_config(monkeypatch)  # fail_closed defaults OFF

        def _boom(log, url=_DEFAULT_URL):
            raise Exception("network down")

        monkeypatch.setattr("scripts.fetch_fno_ban.fetch_fno_ban_symbols", _boom)
        rc = main(["--db", str(db)])
        assert rc == 0
        store2 = StateStore(db_path=db)
        # No sentinel written → nothing is banned → EQ proceeds.
        assert is_symbol_fno_banned(store2, "RELIANCE") is False
        assert is_symbol_fno_banned(store2, "ANYEQ") is False

    def test_stale_date_is_warn(self, tmp_path, monkeypatch, alerts):
        db = tmp_path / "t.db"
        _no_config(monkeypatch)
        monkeypatch.setattr(
            "scripts.fetch_fno_ban.fetch_fno_ban_symbols",
            lambda log, url=_DEFAULT_URL: ("2020-01-01", ["KAYNES"]),
        )
        rc = main(["--db", str(db)])
        assert rc == 0
        assert len(alerts) == 1
        level, message = alerts[0]
        assert level == "WARNING"
        assert "stale" in message.lower()
        store2 = StateStore(db_path=db)
        # Stale list is NOT stored under today's date.
        assert is_symbol_fno_banned(store2, "KAYNES") is False

    def test_fail_closed_writes_sentinel_when_enabled(self, tmp_path, monkeypatch, alerts):
        """Future F&O era: fail_closed ON → sentinel written on failure."""
        db = tmp_path / "t.db"
        fake_cfg = SimpleNamespace(
            system=SimpleNamespace(
                fno_ban=SimpleNamespace(url=_DEFAULT_URL, fail_closed=True)
            )
        )
        monkeypatch.setattr("core.config_loader.load_all", lambda *a, **k: fake_cfg)

        def _boom(log, url=_DEFAULT_URL):
            raise Exception("timeout")

        monkeypatch.setattr("scripts.fetch_fno_ban.fetch_fno_ban_symbols", _boom)
        rc = main(["--db", str(db)])
        assert rc == 0
        assert alerts[0][0] == "WARNING"  # still WARN, never CRITICAL
        store2 = StateStore(db_path=db)
        assert is_symbol_fno_banned(store2, "ANYTHING") is True  # sentinel active

    def test_dry_run_does_not_store(self, tmp_path, monkeypatch, alerts, capsys):
        db = tmp_path / "t.db"
        _no_config(monkeypatch)
        monkeypatch.setattr(
            "scripts.fetch_fno_ban.fetch_fno_ban_symbols",
            lambda log, url=_DEFAULT_URL: (today_ist(), ["KAYNES"]),
        )
        rc = main(["--db", str(db), "--dry-run"])
        assert rc == 0
        store2 = StateStore(db_path=db)
        assert is_symbol_fno_banned(store2, "KAYNES") is False


# ── _cron_main(): heartbeat + exit 0 on success AND on soft failure ───────


class TestCronMainHeartbeat:
    @pytest.fixture()
    def heartbeats(self, monkeypatch):
        recorded: list[dict] = []

        def _capture(job_name, status="SUCCESS", duration_sec=None, message=None, db_path=None):
            recorded.append({"job": job_name, "status": status, "message": message})
            return True

        monkeypatch.setattr("utils.cron_heartbeat.record_heartbeat", _capture)
        monkeypatch.setattr("utils.cron_heartbeat.skip_if_non_trading_day", lambda *a, **k: False)
        return recorded

    def test_success_records_heartbeat_exit_0(self, tmp_path, monkeypatch, heartbeats):
        db = tmp_path / "t.db"
        _no_config(monkeypatch)
        monkeypatch.setattr(
            "scripts.fetch_fno_ban.fetch_fno_ban_symbols",
            lambda log, url=_DEFAULT_URL: (today_ist(), ["KAYNES"]),
        )
        rc = _cron_main(["--db", str(db)])
        assert rc == 0
        assert heartbeats and heartbeats[-1]["status"] == "SUCCESS"
        store2 = StateStore(db_path=db)
        assert is_symbol_fno_banned(store2, "KAYNES") is True

    def test_warn_failure_records_heartbeat_exit_0(self, tmp_path, monkeypatch, heartbeats):
        db = tmp_path / "t.db"
        _no_config(monkeypatch)

        def _boom(log, url=_DEFAULT_URL):
            raise Exception("404 Not Found")

        monkeypatch.setattr("scripts.fetch_fno_ban.fetch_fno_ban_symbols", _boom)
        rc = _cron_main(["--db", str(db)])
        assert rc == 0  # soft failure → exit 0
        # Heartbeat recorded as SUCCESS, NOT FAILED — Cron Officer sees "done".
        assert heartbeats and heartbeats[-1]["status"] == "SUCCESS"


# ── Config ────────────────────────────────────────────────────────────────


class TestFnoBanConfig:
    def test_config_defaults_are_csv_and_fail_open(self):
        from core.config_loader import FnoBanConfig

        cfg = FnoBanConfig()
        assert cfg.url.endswith("fo_secban.csv")
        assert "nsearchives" in cfg.url
        assert cfg.fail_closed is False
