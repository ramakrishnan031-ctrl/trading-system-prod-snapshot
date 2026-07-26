"""
tests/unit/test_holiday_calendar_reminder.py — the reminder that has to reach a HUMAN.

THE PROBLEM THIS PROVES SOLVED. `config/nse_holidays_<year>.yaml` is a HARD boot
requirement: `load_all()` raises ConfigMissingError when it is absent and main
returns 5, so the first 08:15 boot of a year whose calendar was never committed
does not start (MEASURED 26-Jul-2026 by patching date.today before the import).
Only NSE can supply the dates and only Rama can commit them, so the system cannot
fix this itself — it can only ask, early enough, and to somebody who is listening.

A dated tripwire for the same condition already exists and is KEPT — it is free
and it catches the case where the alert path itself is broken. ⚠️ Note where it
lives: `test_config_loader.py` on branch `hold-check1-w8-26jul` (`ffea817` +
`b53b5ac`), NOT on main, so on main this file is currently the only cover. It
fires from 1-Dec, deliberately earlier than the 15-Dec email — developer first,
then the operator. But a test only fires if somebody runs it, and in December
that is a hope with a date on it. This file covers the production reminder that
fires with NOBODY DOING ANYTHING:
`security_monitor.check_nse_holiday_calendar` -> CRITICAL sentinel ->
alert-watcher.service -> email.

What is proven here, in both directions (a reminder nobody has watched fire is
not a reminder):
  * it is SILENT today, so nothing changes until December;
  * it FIRES from 15-Dec, and not on 14-Dec;
  * it GOES SILENT the moment the file exists — presence IS the resolution;
  * it generalises: December 2027 behaves exactly like December 2026;
  * it is WIRED into run_pass (the whole point — a check nobody calls is the
    class of defect this batch is chasing);
  * it rides the existing presence-ledger backoff, so seventeen days of a missing
    file produce ONE CRITICAL, not seventeen;
  * the sentinel it writes is real, well-formed, and picked up by the watcher's
    own reader.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.security_monitor as sm

_IST = timezone(timedelta(hours=5, minutes=30))
_REAL_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def _cfg(config_dir, **kw) -> sm.SecConfig:
    c = sm.SecConfig()
    c.config_dir = str(config_dir)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _at(y, m, d, hh=9, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_IST)


# ── the window, as a pure function of the date ───────────────────────────────

_BOOT_DEAD = sm._HOLIDAY_PHASE_BOOT_DEAD
_NOTICE = sm._HOLIDAY_PHASE_NOTICE
_FINAL = sm._HOLIDAY_PHASE_FINAL


def _due(today, lead=None, final=None):
    c = sm.SecConfig()
    return sm._holiday_files_due(
        today,
        c.holiday_calendar_lead_days if lead is None else lead,
        c.holiday_calendar_final_days if final is None else final)


def test_the_current_year_is_always_due_and_next_year_only_in_the_window():
    """PURE in `today`, so the reminder can be driven to any date without patching
    a clock. The current year is due unconditionally: if THAT file is missing the
    boot is already dead, and no calendar window applies to a thing that already
    happened."""
    assert _due(date(2026, 7, 26)) == [(2026, _BOOT_DEAD)]
    assert _due(date(2026, 12, 14)) == [(2026, _BOOT_DEAD)]                      # not yet
    assert _due(date(2026, 12, 15)) == [(2026, _BOOT_DEAD), (2027, _NOTICE)]
    assert _due(date(2026, 12, 27)) == [(2026, _BOOT_DEAD), (2027, _NOTICE)]
    assert _due(date(2026, 12, 28)) == [(2026, _BOOT_DEAD), (2027, _FINAL)]      # escalates
    assert _due(date(2026, 12, 31)) == [(2026, _BOOT_DEAD), (2027, _FINAL)]


def test_the_window_opens_on_15_dec_in_every_december_not_just_2026():
    """A5: generalised to {next_year}. If this were pinned to 2027 the exact same
    conversation would happen in December 2027 and the fix would be a
    one-character-different version of the bug. The ESCALATION generalises too."""
    for year in (2026, 2027, 2028, 2030, 2099):
        assert _due(date(year, 12, 14)) == [(year, _BOOT_DEAD)]
        assert _due(date(year, 12, 15)) == [(year, _BOOT_DEAD), (year + 1, _NOTICE)]
        assert _due(date(year, 12, 28)) == [(year, _BOOT_DEAD), (year + 1, _FINAL)]


def test_the_phase_vocabulary_is_CLOSED_and_carries_no_date():
    """⭐ THE SAFETY ARGUMENT FOR RE-ALERTING AT ALL, as a test rather than a
    comment. The ledger fires at full severity whenever a key changes, so what
    bounds the CRITICALs is the number of values the key can take. A phase has
    three, ever ⇒ at most three CRITICALs per file per year, however long the
    condition lasts. A DATE in the key would make every day a new condition —
    MEASURED at 17 CRITICALs across the December window, the exact flood the
    presence ledger exists to prevent.

    So: every phase a full year of dates can produce must come from the declared
    set, and no key may contain a date."""
    import re
    seen = set()
    for offset in range(400):                       # more than a year, every day
        today = date(2026, 1, 1) + timedelta(days=offset)
        for _year, phase in _due(today):
            seen.add(phase)
    assert seen <= set(sm._HOLIDAY_PHASES), f"undeclared phase(s): {seen - set(sm._HOLIDAY_PHASES)}"
    assert seen == set(sm._HOLIDAY_PHASES), "every declared phase must be reachable"

    cfg = sm.SecConfig()
    cfg.config_dir = "/nonexistent-for-this-test"
    for when in (_at(2026, 7, 26), _at(2026, 12, 20), _at(2026, 12, 30)):
        for f in sm.check_nse_holiday_calendar(cfg, when):
            assert not re.search(r"\d{4}-\d{2}-\d{2}", f.key), (
                f"a date in the dedup key makes every day a new condition: {f.key}")


def test_the_lead_days_config_moves_the_window_and_is_not_a_hidden_constant():
    """The dates are derived from the two knobs, so retuning them in security.yaml
    actually moves the reminder — config, not numbers baked into the code."""
    assert _due(date(2026, 12, 2), lead=16) == [(2026, _BOOT_DEAD)]
    assert _due(date(2026, 12, 2), lead=30) == [(2026, _BOOT_DEAD), (2027, _NOTICE)]
    assert _due(date(2026, 12, 20), final=11) == [(2026, _BOOT_DEAD), (2027, _FINAL)]


def test_a_misconfigured_final_days_degrades_to_one_escalation_not_to_silence():
    """`final` is tested before `notice`, so final_days >= lead_days costs you the
    early notice — never the alert itself. The direction of the degradation is the
    point: a config mistake must not be able to silence a boot-blocker."""
    got = _due(date(2026, 12, 15), lead=16, final=99)
    assert got == [(2026, _BOOT_DEAD), (2027, _FINAL)]


def test_the_committed_security_yaml_actually_reaches_the_behaviour(tmp_path):
    """A knob is only config if the file is read. Same argument as the re-alert
    ladder: prove the YAML -> SecConfig -> check path end to end, so the window can
    be retuned on the VM without a deploy."""
    import yaml
    real = yaml.safe_load((_REAL_CONFIG_DIR / "security.yaml").read_text(encoding="utf-8"))
    loaded = sm.SecConfig.load(_REAL_CONFIG_DIR / "security.yaml")
    assert real["security"]["holiday_calendar_alert"] is True
    assert loaded.holiday_calendar_lead_days == real["security"]["holiday_calendar_lead_days"]
    assert loaded.holiday_calendar_final_days == real["security"]["holiday_calendar_final_days"]
    # THE property that buys two escalations rather than one — asserted on the
    # COMMITTED config, not on the dataclass defaults.
    assert loaded.holiday_calendar_final_days < loaded.holiday_calendar_lead_days

    stub = tmp_path / "security.yaml"
    stub.write_text("security:\n  holiday_calendar_lead_days: 45\n", encoding="utf-8")
    cfg = sm.SecConfig.load(stub)
    cfg.config_dir = str(tmp_path)
    assert cfg.holiday_calendar_lead_days == 45
    # 45 days out is 17-Nov: silent under the committed 16, loud under this one.
    assert _due(date(2026, 11, 17), lead=16) == [(2026, _BOOT_DEAD)]
    assert [f.key for f in sm.check_nse_holiday_calendar(cfg, _at(2026, 11, 17))] == [
        f"holidaycal:missing:nse_holidays_2026.yaml:{_BOOT_DEAD}",
        f"holidaycal:missing:nse_holidays_2027.yaml:{_NOTICE}",
    ]


# ── fires / goes silent, against a real directory ────────────────────────────

def test_it_is_silent_today_against_the_real_config_directory(tmp_path):
    """A6, and the answer to "is this a behaviour change Tuesday would see?": no.
    config/nse_holidays_2026.yaml exists and July is not December, so the check
    produces nothing at all. It is inert until 15-Dec-2026."""
    assert (_REAL_CONFIG_DIR / "nse_holidays_2026.yaml").exists(), (
        "premise: the current year's calendar is committed")
    assert sm.check_nse_holiday_calendar(_cfg(_REAL_CONFIG_DIR), _at(2026, 7, 26)) == []


def test_it_fires_on_15_dec_naming_the_file_that_does_not_exist(tmp_path):
    (tmp_path / "nse_holidays_2026.yaml").write_text("holidays: []", encoding="utf-8")
    findings = sm.check_nse_holiday_calendar(_cfg(tmp_path), _at(2026, 12, 15))
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "CRITICAL"
    assert f.key == f"holidaycal:missing:nse_holidays_2027.yaml:{_NOTICE}"
    assert "nse_holidays_2027.yaml" in f.body
    assert "2027" in f.title
    assert "FINAL NOTICE" not in f.title, "the 15-Dec notice is not the escalation"


def test_it_is_still_silent_on_14_dec(tmp_path):
    """The boundary is not off by one — one day earlier and nothing is sent."""
    (tmp_path / "nse_holidays_2026.yaml").write_text("holidays: []", encoding="utf-8")
    assert sm.check_nse_holiday_calendar(_cfg(tmp_path), _at(2026, 12, 14)) == []


def test_it_goes_silent_the_moment_the_file_lands(tmp_path):
    """A4: presence of the file IS the resolution. No acknowledgement, no flag to
    clear, nothing for Rama to remember to switch off."""
    (tmp_path / "nse_holidays_2026.yaml").write_text("holidays: []", encoding="utf-8")
    when = _at(2026, 12, 20)
    assert len(sm.check_nse_holiday_calendar(_cfg(tmp_path), when)) == 1   # could be red
    (tmp_path / "nse_holidays_2027.yaml").write_text("holidays: []", encoding="utf-8")
    assert sm.check_nse_holiday_calendar(_cfg(tmp_path), when) == []


def test_a_missing_CURRENT_year_file_fires_immediately_with_no_window(tmp_path):
    """§C1 wired where it CAN run. utils.startup_checks.check_holiday_calendar has
    this exact branch and it is UNREACHABLE on the boot path: load_all() at
    main.py:1816 has already killed the boot 253 lines before
    run_all_startup_checks() calls it. The security watcher is a separate,
    always-on process, so it is the one place the branch can actually be reached —
    and on that morning it is the only thing on the box that can say WHY the
    service will not start."""
    findings = sm.check_nse_holiday_calendar(_cfg(tmp_path), _at(2026, 7, 26))
    assert [f.key for f in findings] == [
        f"holidaycal:missing:nse_holidays_2026.yaml:{_BOOT_DEAD}"]
    assert "CANNOT START" in findings[0].body


def test_both_horizons_can_be_missing_at_once(tmp_path):
    findings = sm.check_nse_holiday_calendar(_cfg(tmp_path), _at(2026, 12, 20))
    assert [f.key for f in findings] == [
        f"holidaycal:missing:nse_holidays_2026.yaml:{_BOOT_DEAD}",
        f"holidaycal:missing:nse_holidays_2027.yaml:{_NOTICE}",
    ]


def test_the_final_escalation_says_it_is_the_last_one(tmp_path):
    """B2. Two phases, and the second has to READ like a last chance — otherwise
    it is just another copy of a message that was already ignored once."""
    (tmp_path / "nse_holidays_2026.yaml").write_text("holidays: []", encoding="utf-8")
    f = sm.check_nse_holiday_calendar(_cfg(tmp_path), _at(2026, 12, 28))[0]
    assert f.key == f"holidaycal:missing:nse_holidays_2027.yaml:{_FINAL}"
    assert f.severity == "CRITICAL"
    assert "FINAL NOTICE" in f.title
    assert "LAST escalation" in f.body
    assert "3 day(s) LEFT" in f.body


def test_the_master_switch_turns_it_off(tmp_path):
    cfg = _cfg(tmp_path, holiday_calendar_alert=False)
    assert sm.check_nse_holiday_calendar(cfg, _at(2026, 12, 20)) == []


def test_the_body_refuses_to_let_anyone_invent_the_dates(tmp_path):
    """⛔ A guessed calendar is far worse than a missing one: the system would trade
    on a market holiday, or skip a real trading day, and believe it was right. The
    alert that asks for the file has to say so, because it is the only thing the
    reader will have in front of them."""
    body = sm.check_nse_holiday_calendar(_cfg(tmp_path), _at(2026, 12, 20))[1].body
    assert "DO NOT invent" in body
    assert "PUBLISHED" in body
    assert "nse_holidays_2027.yaml" in body


# ── wired, not merely built ──────────────────────────────────────────────────

def test_the_check_is_actually_WIRED_into_run_pass(tmp_path):
    """The class this whole batch is chasing: a check that exists, is correct, and
    is never called. Driving it through run_pass — not through the function — is
    what makes that impossible here."""
    authlog = tmp_path / "auth.log"
    authlog.write_text("", encoding="utf-8")
    cfgdir = tmp_path / "config"
    cfgdir.mkdir()
    (cfgdir / "nse_holidays_2026.yaml").write_text("holidays: []", encoding="utf-8")
    cfg = _cfg(cfgdir, watched_files=[], authlog_path=str(authlog),
               authorized_keys_path=str(tmp_path / "nope"))
    findings = sm.run_pass(cfg, {}, authlog, _at(2026, 12, 20), baseline=False)
    assert f"holidaycal:missing:nse_holidays_2027.yaml:{_NOTICE}" in [f.key for f in findings]


def test_run_pass_does_not_raise_it_when_the_file_is_there(tmp_path):
    """The same call, one file different — so the assertion above could have been
    red for the right reason."""
    authlog = tmp_path / "auth.log"
    authlog.write_text("", encoding="utf-8")
    cfgdir = tmp_path / "config"
    cfgdir.mkdir()
    for y in (2026, 2027):
        (cfgdir / f"nse_holidays_{y}.yaml").write_text("holidays: []", encoding="utf-8")
    cfg = _cfg(cfgdir, watched_files=[], authlog_path=str(authlog),
               authorized_keys_path=str(tmp_path / "nope"))
    findings = sm.run_pass(cfg, {}, authlog, _at(2026, 12, 20), baseline=False)
    assert not [f for f in findings if f.key.startswith("holidaycal:")]


# ── A3: it must not become the thing this week spent days undoing ────────────

def _december_alert_stream(tmp_path, *, file_lands_on=None):
    """Replay the watcher's real ~60s cadence from 15-Dec to 31-Dec through the
    REAL dedup ledger, and return every alert it would actually have sent."""
    cfgdir = tmp_path / "config"
    cfgdir.mkdir(exist_ok=True)
    (cfgdir / "nse_holidays_2026.yaml").write_text("holidays: []", encoding="utf-8")
    cfg = _cfg(cfgdir)
    state: dict = {}
    sent = []
    start = _at(2026, 12, 15, 0, 0)
    passes = 17 * 24 * 60          # 17 days at one pass a minute
    for i in range(passes):
        now = start + timedelta(minutes=i)
        if file_lands_on is not None and now.date() == file_lands_on:
            (cfgdir / "nse_holidays_2027.yaml").write_text("holidays: []", encoding="utf-8")
            file_lands_on = None
        findings = sm.check_nse_holiday_calendar(cfg, now)
        fresh = sm._dedup(findings, state, cfg.realert_cooldown_sec, now.timestamp(),
                          backoff_ladder=cfg.realert_backoff_multipliers,
                          presence_gap_sec=cfg.realert_presence_gap_sec)
        for f in fresh:
            sent.append((now, f))
    return sent, state


def test_the_whole_december_window_is_TWO_criticals_not_seventeen(tmp_path):
    """⭐ THE DESIGN'S HONESTY CHECK, and the number is MEASURED rather than
    asserted from the design.

    Two forces pull against each other here. A3 forbids a daily CRITICAL — 17 of
    them would train Rama to ignore exactly the message that matters. But ONE
    CRITICAL is thin cover for an outage with a known date: everything after the
    first notice is downgraded to WARNING, and a WARNING Telegram dies silently on
    a delivery failure, so missing one message on 15-Dec would mean the next thing
    he hears is a service that will not start.

    Two phases is the answer, and the ledger delivers it with no new mechanism:
    the escalation is a different KEY, so it is a different condition, so it fires
    at full severity — which is the rule working, not a way around it."""
    sent, _ = _december_alert_stream(tmp_path)
    criticals = [(t, f) for t, f in sent if f.severity == "CRITICAL"]
    assert len(criticals) == 2, (
        f"expected exactly the 15-Dec notice and the 28-Dec final; got "
        f"{[(t.strftime('%d-%b %H:%M'), f.key) for t, f in criticals]}")
    assert criticals[0][0].date() == date(2026, 12, 15)
    assert criticals[1][0].date() == date(2026, 12, 28)
    assert criticals[0][1].key.endswith(_NOTICE)
    assert criticals[1][1].key.endswith(_FINAL)
    assert "FINAL NOTICE" in criticals[1][1].title

    assert len(sent) < 17, (
        f"{len(sent)} alerts over 17 days is at least one a day — the backoff is "
        f"not being applied")
    for _t, f in sent:
        if f.severity != "CRITICAL":
            assert "STILL PRESENT" in f.title


def test_the_gaps_within_a_phase_widen_and_the_escalation_restarts_the_ladder(tmp_path):
    """The shape, not a remembered count: inside one phase each interval is at
    least as long as the one before it; the escalation deliberately breaks that,
    because a new condition starts a new episode at the top of the ladder."""
    sent, _ = _december_alert_stream(tmp_path)
    cool = sm.SecConfig().realert_cooldown_sec
    for phase in (_NOTICE, _FINAL):
        times = [t for t, f in sent if f.key.endswith(phase)]
        assert len(times) >= 2, f"phase {phase} should have a first report and repeats"
        gaps = [(times[i] - times[i - 1]).total_seconds() for i in range(1, len(times))]
        assert gaps == sorted(gaps), f"{phase} intervals must not shrink: {gaps}"
        assert gaps[0] == pytest.approx(cool, abs=120)             # each phase starts at 6h


def test_the_stream_stops_dead_when_the_file_is_committed(tmp_path):
    """A4/B4 end to end: nothing after the file lands, and the ledger stops naming
    it as a still-present condition — so the Control Tower stops reporting it too."""
    sent, state = _december_alert_stream(tmp_path, file_lands_on=date(2026, 12, 20))
    assert sent, "premise: it was alerting before the file landed"
    assert max(t for t, _f in sent).date() <= date(2026, 12, 20)
    assert not [k for k in state["persistent_conditions"] if k.startswith("holidaycal:")]


def test_it_also_goes_silent_if_the_file_lands_DURING_the_final_phase(tmp_path):
    """B4: at ANY phase. The escalation must not become a thing that keeps firing
    once the work is actually done — presence of the file is still the whole clear
    condition, and the second phase is not an exception to that."""
    sent, state = _december_alert_stream(tmp_path, file_lands_on=date(2026, 12, 29))
    assert [f.severity for _t, f in sent].count("CRITICAL") == 2, (
        "premise: it had escalated before the file landed")
    assert max(t for t, _f in sent).date() <= date(2026, 12, 29)
    assert not [k for k in state["persistent_conditions"] if k.startswith("holidaycal:")]


# ── the last hop we own: a real sentinel on disk ─────────────────────────────

def test_the_alert_becomes_a_real_sentinel_the_watcher_can_read(tmp_path, monkeypatch):
    """A1/A6. The sentinel -> alert-watcher -> email path was verified end to end in
    production on 25-Jul (21:15:28 sentinel -> Delivered 21:15:58), so what is left
    to prove here is our end of it: that this finding produces a sentinel the
    watcher's own reader accepts, carrying the text a human needs.

    from_env is stubbed to None on purpose — it both forces the sentinel branch and
    guarantees this test can never send Rama a real Telegram message."""
    from alerts import critical as crit
    from alerts import telegram_notifier as tn
    monkeypatch.setattr(tn.TelegramNotifier, "from_env", staticmethod(lambda **kw: None))

    finding = sm.check_nse_holiday_calendar(_cfg(tmp_path), _at(2026, 12, 20))[1]
    sm._send(finding, _cfg(tmp_path, sentinel_dir=str(tmp_path)))

    pending = crit.list_pending_sentinels(tmp_path)
    assert len(pending) == 1
    payload = crit.read_sentinel(pending[0])
    assert payload["source_module"] == "security_monitor"
    assert "nse_holidays_2027.yaml" in payload["body"]
    assert "DO NOT invent" in payload["body"]
    assert json.loads(json.dumps(payload))          # round-trips as the watcher parses it
