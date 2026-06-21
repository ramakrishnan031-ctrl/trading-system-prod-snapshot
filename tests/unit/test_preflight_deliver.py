"""
tests/unit/test_preflight_deliver.py -- routing of a PreflightReport to email
(HTML critical-sentinel) + Telegram, ban-aware. No real email/Telegram sent.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts.preflight import deliver
from scripts.preflight.base import Criticality, Status
from scripts.preflight.report import CheckRecord, PreflightReport


def _rep(phase="C", status=Status.PASS, crit=Criticality.CRITICAL):
    rec = CheckRecord(name="x", group="G", criticality=crit, status=status, detail="d", duration_ms=1)
    return PreflightReport(phase=phase, run_date=date(2026, 6, 22), run_id="rid",
                           mode="live", account="LFL836", started_at="s", completed_at="c",
                           records=[rec])


def _flags(d: Path):
    return list(d.glob("*.flag"))


def test_dry_run_sends_nothing(tmp_path):
    parts = deliver.deliver(_rep("C"), tmp_path, dry_run=True, sentinel_dir=tmp_path, ban_active=True)
    assert parts["sent"] is False and not _flags(tmp_path)


def test_phase_c_emails(tmp_path):
    parts = deliver.deliver(_rep("C", Status.PASS), tmp_path, dry_run=False,
                            sentinel_dir=tmp_path, ban_active=True)
    assert parts["sent"] is True
    flags = _flags(tmp_path)
    assert len(flags) == 1
    payload = json.loads(flags[0].read_text(encoding="utf-8"))
    assert payload["content_type"] == "text/html"
    assert payload["subject"].startswith("[LFL836-BAN]")     # ban prefix
    assert payload["plain_fallback"]                          # required for html


def test_critical_emails_any_phase(tmp_path):
    # Phase A + CRITICAL -> loud immediate email even though it's not the final phase
    parts = deliver.deliver(_rep("A", Status.FAIL, Criticality.CRITICAL), tmp_path,
                            dry_run=False, sentinel_dir=tmp_path, ban_active=True)
    assert parts["sent"] is True and len(_flags(tmp_path)) == 1
    payload = json.loads(_flags(tmp_path)[0].read_text(encoding="utf-8"))
    assert "🔴 CRITICAL" in payload["subject"]


def test_clean_phase_a_is_sentinel_only(tmp_path):
    parts = deliver.deliver(_rep("A", Status.PASS), tmp_path, dry_run=False,
                            sentinel_dir=tmp_path, ban_active=True)
    assert parts["sent"] is False and not _flags(tmp_path)


def test_telegram_outside_ban(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(deliver, "_send_telegram", lambda tg, sev, cfg: calls.append((sev,)))
    deliver.deliver(_rep("C", Status.PASS), tmp_path, dry_run=False,
                    sentinel_dir=tmp_path, ban_active=False)
    assert len(calls) == 1                                    # telegram sent outside ban


def test_no_telegram_during_ban(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(deliver, "_send_telegram", lambda tg, sev, cfg: calls.append(1))
    deliver.deliver(_rep("C", Status.PASS), tmp_path, dry_run=False,
                    sentinel_dir=tmp_path, ban_active=True)
    assert calls == []                                        # email-only during ban
