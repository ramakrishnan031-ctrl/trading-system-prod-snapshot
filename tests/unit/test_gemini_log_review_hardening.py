"""
tests/unit/test_gemini_log_review_hardening.py — 23-Jun gemini review hardening:
  1. restart-aware review window (_process_start_ts + _extract_warning_plus skip)
  2. full-failure sentinel (run_review's all-models-exhausted branch is LOUD;
     a degraded-but-completed review stays quiet).
"""
from __future__ import annotations

import logging
from unittest.mock import create_autospec

import scripts.gemini_log_review as glr

_MARKER = ('{"ts":"%s","level":"INFO","logger":"main",'
           '"msg":"Trading System v2.0.0 starting (mode=live)"}')
_ERR = '{"ts":"%s","level":"ERROR","logger":"%s","msg":"%s"}'


def _w(p, lines):
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── restart-aware windowing ──────────────────────────────────────────────────
def test_extract_skips_pre_restart_lines(tmp_path):
    log = tmp_path / "system_2026-06-23.log"
    _w(log, [
        _ERR % ("2026-06-23T08:15:00.000+05:30", "tgt_retry_manager",
                "is_within_market_hours() missing 2 required positional arguments"),
        _MARKER % "2026-06-23T12:28:19.000+05:30",
        _ERR % ("2026-06-23T14:00:00.000+05:30", "order_placer", "afternoon real error"),
    ])
    out = glr._extract_warning_plus(log)
    assert "afternoon real error" in out
    assert "is_within_market_hours" not in out          # stale pre-restart EXCLUDED


def test_extract_no_marker_reviews_whole_file(tmp_path):
    log = tmp_path / "system_2026-06-23.log"
    _w(log, [
        _ERR % ("2026-06-23T08:15:00.000+05:30", "x", "morning err"),
        _ERR % ("2026-06-23T14:00:00.000+05:30", "y", "afternoon err"),
    ])
    out = glr._extract_warning_plus(log)
    assert "morning err" in out and "afternoon err" in out   # whole-file fallback


def test_process_start_ts_latest_marker_wins(tmp_path):
    log = tmp_path / "system_2026-06-23.log"
    _w(log, [
        _MARKER % "2026-06-23T08:15:04.000+05:30",
        _MARKER % "2026-06-23T12:28:19.000+05:30",
    ])
    assert glr._process_start_ts(log) == "2026-06-23T12:28:19.000+05:30"


def test_extract_includes_unparseable_ts_lines(tmp_path):
    log = tmp_path / "system_2026-06-23.log"
    _w(log, [
        _MARKER % "2026-06-23T12:28:19.000+05:30",
        "plain ERROR line with no json ts",             # no ts -> conservatively kept
    ])
    out = glr._extract_warning_plus(log)
    assert "plain ERROR line" in out


# ── full-failure sentinel ────────────────────────────────────────────────────
def _logdir_with_error(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    _w(d / "system_2026-06-23.log", [
        _MARKER % "2026-06-23T12:28:19.000+05:30",
        _ERR % ("2026-06-23T14:00:00.000+05:30", "z", "some error"),
    ])
    return d


def test_full_failure_writes_sentinel_and_stub(tmp_path, monkeypatch):
    import alerts.critical as ac
    import alerts.telegram_notifier as tn
    calls = []
    monkeypatch.setattr(ac, "write_critical_sentinel", lambda **kw: calls.append(kw))
    monkeypatch.setattr(tn.TelegramNotifier, "from_env", staticmethod(lambda **k: None))
    monkeypatch.setattr(glr, "_call_gemini_cli", lambda *a, **k: None)   # all models fail
    out_dir = tmp_path / "out"
    rc = glr.run_review("2026-06-23", _logdir_with_error(tmp_path), out_dir,
                        tmp_path / "wm", logging.getLogger("t"), dry_run=False)
    assert rc == 1
    assert len(calls) == 1
    assert "FAILED" in calls[0]["title"]
    assert calls[0]["context"]["severity"] == "CRITICAL"
    assert "REVIEW FAILED" in (out_dir / "eod_review_2026-06-23.md").read_text()


def test_degraded_but_completed_no_sentinel(tmp_path, monkeypatch):
    import alerts.critical as ac
    import utils.cron_heartbeat as ch
    calls = []
    monkeypatch.setattr(ac, "write_critical_sentinel", lambda **kw: calls.append(kw))
    monkeypatch.setattr(glr, "_call_gemini_cli", lambda *a, **k: "real review text")
    monkeypatch.setattr(glr, "_send_telegram_summary", lambda *a, **k: None)
    # AUTOSPEC (15-Jul-2026): enforce the real record_heartbeat signature at the mock boundary
    # (was a permissive `lambda *a, **k`). See test_cron_heartbeat_contract.py.
    monkeypatch.setattr(ch, "record_heartbeat", create_autospec(ch.record_heartbeat, return_value=None))
    out_dir = tmp_path / "out"
    rc = glr.run_review("2026-06-23", _logdir_with_error(tmp_path), out_dir,
                        tmp_path / "wm", logging.getLogger("t"), dry_run=False)
    assert rc == 0
    assert calls == []                                  # NO sentinel on success
    assert (out_dir / "eod_review_2026-06-23.md").exists()
