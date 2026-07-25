"""tests/unit/test_scheduled_report_severity.py -- SS-C (26-Jul-2026).

A scheduled report must DECLARE its own severity, because the email transport
INFERS CRITICAL when it is not told.

    alert_watcher._build_email:
        severity = data.get("context", {}).get("severity", "CRITICAL")
        subject  = data.get("subject") or f"[LFL836] {severity} - {title}"

That default is correct for emitters that only ever write a sentinel when something
is genuinely wrong (TelegramNotifier writes one for CRITICAL only). It is a trap for
a report sent on a SCHEDULE regardless of content: drop the context and a routine
status line silently joins the CRITICAL stream.

⭐ MEASURED 26-Jul-2026, and it CORRECTS the 25-Jul audit. All 85 delivered sentinels
on the VM were re-rendered through this exact function. 16 do NOT say CRITICAL, and
they are exactly the always-sent scheduled reports:

    system_manager x6  ->  "[LFL836] INFO - System Manager EOD - clean"
    cron_officer   x6  ->  "[LFL836] Cron Morning Briefing - 24-Jul" / "Cron Daily Report"
    preflight      x4  ->  "[LFL836] Pre-flight READY (1 warnings) - 24-Jul"

So "a CRITICAL whose content is that nothing is wrong" was true of the CHANNEL, not
of the EMAIL. The 18:45 clean EOD report is ALREADY an INFO email. Nothing was
downgraded, because the measurement says there is nothing to downgrade -- these tests
pin that, and pin the trap that would quietly undo it.
"""
from __future__ import annotations

from pathlib import Path

import alerts.critical as critical_mod
import scripts.system_manager as sysm
from scripts.alert_watcher import _build_email


def _capture(monkeypatch) -> dict:
    """Capture the sentinel kwargs system_manager._send would write, without
    touching the filesystem or the network."""
    seen: dict = {}

    def fake_write(**kw):
        seen.update(kw)
        return Path("sentinel.flag")

    monkeypatch.setattr(critical_mod, "write_critical_sentinel", fake_write)
    monkeypatch.setattr(
        "alerts.telegram_notifier.TelegramNotifier.from_env",
        staticmethod(lambda **kw: None),
    )
    return seen


def _subject_for(seen: dict) -> str:
    """Render the sentinel exactly as alert_watcher would."""
    payload = {
        "title": seen["title"],
        "body": seen["body"],
        "source_module": seen["source_module"],
        "context": seen.get("context", {}),
        "ts": "2026-07-24T18:45:03+05:30",
        "id": "sentinel-id",
    }
    return _build_email(payload, "from@example.test", ["to@example.test"])["Subject"]


# ── the always-sent EOD report ───────────────────────────────────────────────

def test_clean_eod_declares_info_and_emails_as_info(monkeypatch):
    """A scheduled report whose content is "nothing is wrong" must not occupy the
    severity reserved for "something is wrong right now"."""
    seen = _capture(monkeypatch)
    sysm._send("INFO", "System Manager EOD - clean", "0 violation(s), 0 warning(s)",
               Path("config"), False)
    assert seen["context"] == {"severity": "INFO"}, (
        "the emitter must PASS severity; _build_email defaults to CRITICAL without it"
    )
    subject = _subject_for(seen)
    assert "CRITICAL" not in subject
    assert "INFO" in subject


def test_a_violation_eod_still_emails_as_critical(monkeypatch):
    """The other half -- proving the INFO above is content-driven, not blanket. A
    report that CAN indicate a problem keeps CRITICAL for the day it does. Without
    this, the test above would pass just as well on a report that had been silenced."""
    seen = _capture(monkeypatch)
    sysm._send("CRITICAL", "System Manager EOD - ACTION REQUIRED", "1 violation(s)",
               Path("config"), False)
    assert seen["context"] == {"severity": "CRITICAL"}
    assert "CRITICAL" in _subject_for(seen)


def test_the_severity_is_chosen_by_content_not_by_schedule():
    """Pins the rule at its source, so the two tests above cannot both be satisfied
    by a hardcoded severity."""
    src = Path(sysm.__file__).read_text(encoding="utf-8")
    assert 'severity = "CRITICAL" if (violations or reasons) else "INFO"' in src
    assert "System Manager EOD" in src


# ── the trap, pinned because it is LATENT rather than live ───────────────────

def test_build_email_infers_critical_when_severity_is_absent():
    """LATENT, deliberately pinned. Currently harmless -- the emitters that omit
    severity only ever write sentinels for real CRITICALs -- but this is the exact
    mechanism by which a future scheduled emitter would silently join the CRITICAL
    stream. If it ever changes, that must be a decision, not a drift."""
    payload = {"title": "Some scheduled roll-up", "body": "all fine",
               "source_module": "future_job", "context": {},
               "ts": "2026-07-26T09:00:00+05:30", "id": "x"}
    assert "CRITICAL" in _build_email(payload, "a@b.test", ["c@d.test"])["Subject"]


def test_dropping_the_context_turns_the_clean_eod_into_a_critical_email(monkeypatch):
    """The failure mode demonstrated rather than asserted in prose: the same clean
    report, minus its context, arrives as a CRITICAL."""
    seen = _capture(monkeypatch)
    sysm._send("INFO", "System Manager EOD - clean", "0 violation(s)",
               Path("config"), False)
    stripped = dict(seen)
    stripped["context"] = {}
    assert "CRITICAL" in _subject_for(stripped)
    assert "CRITICAL" not in _subject_for(seen)


# ── C3: removing a useless CRITICAL must not create a silent success ─────────

def test_the_eod_run_is_provable_without_reading_the_email():
    """C3, pinned at the source. Three independent proofs the 18:45 job ran, none of
    which is the alert -- so downgrading or removing the email could not create a
    silent success. MEASURED on the VM 26-Jul: 34 heartbeat rows (all SUCCESS,
    latest 24-Jul 18:45:03) and 26 saved report files.

    The third is the one that matters: monitored:true means the 18:50 Cron Officer
    EOD flags this job MISSED if it does not run -- an ACTIVE alarm on absence, not
    a passive record that someone has to think to go and read."""
    src = Path(sysm.__file__).read_text(encoding="utf-8")
    assert "record_heartbeat(" in src and '"system_manager_eod"' in src
    assert "_save_report(report," in src
    registry = Path("config/cron_registry.yaml").read_text(encoding="utf-8")
    block = registry.split("system_manager_eod:", 1)[1][:500]
    assert "monitored: true" in block
    assert "enabled: true" in block
