"""tests/unit/test_security_monitor_realert.py -- SS-B (26-Jul-2026).

The re-alert loop: a persistent condition must stop shouting CRITICAL without
becoming invisible, and a condition that CLEARS and COMES BACK must never be
mistaken for one that simply never left.

The pre-fix _dedup stored ONE number per finding key -- the last time it alerted
-- and compared it to a fixed cooldown. One number cannot express the difference
between "still true" and "true again", so it failed in BOTH directions at once:

  still true  -> a persistent BENIGN condition re-fired at full CRITICAL every
                 realert_cooldown_sec, forever, with no decay and no escalation.
                 One stale SSH baseline produced 37 CRITICAL emails over 9 days
                 and two key generations -- 45% of the entire delivered stream.
  true again  -> a condition that cleared and RECURRED inside the cooldown was
                 silently dropped. The louder half hid the dangerous half.

Every test marked RED-FIRST was run against the pre-fix _dedup and failed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import scripts.security_monitor as sm
from scripts.security_monitor import Finding, SecConfig, _dedup

_IST = timezone(timedelta(hours=5, minutes=30))
_COOL = 21600.0          # the production realert_cooldown_sec (6h)
_T0 = 1_000_000.0
_KEY = "authkeys:unexpected:SHA256:abc"
_BODY = "authorized_keys has key(s) not matching the expected baseline."


def _f(sev="CRITICAL", key=_KEY, title="UNEXPECTED SSH KEY present", body=_BODY):
    return Finding(sev, key, title, body)


def _passes(state, t_from, t_to, present=True, step=60.0, **kw):
    """Drive _dedup the way the watcher actually does -- ONE PASS EVERY ~60s --
    and return everything emitted between the two timestamps (inclusive).

    Tests must not simply jump the clock: presence semantics are defined by the
    passes that ran, so a bare jump is indistinguishable from the watcher having
    been DOWN (see test_a_monitoring_gap_is_treated_as_a_clear)."""
    gap = kw.get("presence_gap_sec") or sm._PRESENCE_GAP_SEC
    assert step < gap, (
        f"a {step}s step exceeds the {gap}s presence gap -- every pass would look "
        f"like a recurrence and the test would be measuring the wrong thing"
    )
    out = []
    t = float(t_from)
    while t <= t_to:
        out.extend(_dedup([_f()] if present else [], state, _COOL, t, **kw))
        t += step
    return out


# ── first report ─────────────────────────────────────────────────────────────

def test_first_report_is_verbatim_and_full_severity():
    """The FIRST time a condition is seen it is untouched -- same severity, same
    title, same body. Nothing here may soften a new incident."""
    state: dict = {}
    out = _dedup([_f()], state, _COOL, _T0)
    assert len(out) == 1
    assert out[0].severity == "CRITICAL"
    assert out[0].title == "UNEXPECTED SSH KEY present"
    assert "STILL PRESENT" not in out[0].title
    assert out[0].body == _BODY


def test_within_the_first_cooldown_nothing_is_emitted():
    state: dict = {}
    _dedup([_f()], state, _COOL, _T0)
    assert _passes(state, _T0 + 60.0, _T0 + _COOL - 60.0) == []


# ── "still true": the 37-email loop ──────────────────────────────────────────

def test_repeat_of_an_unchanged_condition_is_never_critical():
    """RED-FIRST, and the whole point of SS-B: a condition that has already been
    reported and has not changed must not re-fire as CRITICAL."""
    state: dict = {}
    first = _dedup([_f()], state, _COOL, _T0)
    assert first[0].severity == "CRITICAL"

    second = _passes(state, _T0 + 60.0, _T0 + _COOL)
    assert len(second) == 1, "the repeat must still be emitted -- silence is not the fix"
    assert second[0].severity == "WARNING", (
        "pre-fix this was CRITICAL again, and again, every 6h forever"
    )
    assert second[0].key == _KEY, "identity is preserved so dedup keeps working"


def test_repeat_says_it_is_a_repeat_and_where_the_live_status_lives():
    """RED-FIRST. A downgraded repeat that reads like a fresh alert is worse than
    no repeat -- it must be self-evidently a repeat, and must point at the channel
    that carries the condition continuously."""
    state: dict = {}
    _dedup([_f()], state, _COOL, _T0)
    r = _passes(state, _T0 + 60.0, _T0 + _COOL)[0]
    assert "STILL PRESENT" in r.title
    assert "REPEAT #2" in r.body
    assert "last_run.json" in r.body
    assert _BODY in r.body, "the original detail must survive verbatim"


def test_repeat_interval_widens_along_the_ladder():
    """RED-FIRST. 6h -> 24h -> 7d. Pre-fix every interval was 6h, forever."""
    state: dict = {}
    t = _T0
    _dedup([_f()], state, _COOL, t)                                  # report 1 (CRITICAL)

    assert len(_passes(state, t + 60.0, t + _COOL)) == 1              # report 2, after 1x
    t += _COOL

    # report 3 is NOT due at 1x -- the ladder has moved to 4x (24h)
    assert _passes(state, t + 60.0, t + 4 * _COOL - 60.0) == []
    assert len(_passes(state, t + 4 * _COOL - 60.0, t + 4 * _COOL)) == 1   # report 3
    t += 4 * _COOL

    # report 4 is NOT due at 4x -- the ladder has moved to 28x (7d)
    assert _passes(state, t + 60.0, t + 4 * _COOL, step=240.0) == []
    assert len(_passes(state, t + 4 * _COOL, t + 28 * _COOL, step=240.0)) == 1  # report 4
    t += 28 * _COOL

    # and it CAPS at 28x -- it never goes fully silent, and never speeds back up
    assert _passes(state, t + 60.0, t + 27 * _COOL, step=240.0) == []
    assert len(_passes(state, t + 27 * _COOL, t + 28 * _COOL, step=240.0)) == 1


def test_nine_days_of_the_ssh_condition_costs_one_critical_not_thirty_seven():
    """RED-FIRST, and the measured motivation. Replay the real cadence: the watcher
    passes every 60s and the condition is present on every one of them, for the 9
    days the stale baseline actually persisted."""
    state: dict = {}
    crit = warn = 0
    t = _T0
    for _ in range(9 * 24 * 60):          # 9 days of 60-second passes
        for out in _dedup([_f()], state, _COOL, t):
            if out.severity == "CRITICAL":
                crit += 1
            elif out.severity == "WARNING":
                warn += 1
        t += 60.0
    assert crit == 1, f"exactly one CRITICAL for one condition; got {crit}"
    assert 1 <= warn <= 4, f"a handful of decaying repeats, not 36; got {warn}"


# ── "true again": the silent miss ────────────────────────────────────────────

def test_a_condition_that_cleared_and_returned_alerts_immediately_at_full_severity():
    """RED-FIRST, and the more dangerous half. Pre-fix this returned [] -- the
    recurrence was swallowed because the ledger only remembered when it last
    ALERTED, never whether the condition had gone away in between."""
    state: dict = {}
    assert len(_dedup([_f()], state, _COOL, _T0)) == 1        # present
    # ... absent for the next hour (it is simply not in `findings`) ...
    gone_until = _T0 + 3600.0
    for t in range(int(_T0) + 60, int(gone_until), 60):
        assert _dedup([], state, _COOL, float(t)) == []
    # ... and now it is BACK, well inside the 6h cooldown.
    back = _dedup([_f()], state, _COOL, gone_until)
    assert len(back) == 1, "a recurrence inside the cooldown was silently dropped pre-fix"
    assert back[0].severity == "CRITICAL", "'true again' is a NEW event, not a repeat"
    assert "STILL PRESENT" not in back[0].title


def test_a_one_pass_flicker_is_persistence_not_recurrence():
    """A single missed pass must NOT reset the ladder -- otherwise a flapping
    condition re-earns CRITICAL every few minutes and we are back where we started.
    Absence has to exceed the presence gap to count as a clear."""
    state: dict = {}
    _dedup([_f()], state, _COOL, _T0)
    _dedup([], state, _COOL, _T0 + 60.0)          # one pass without it
    assert _dedup([_f()], state, _COOL, _T0 + 120.0) == [], \
        "back within the presence gap -> still the same episode"
    out = _passes(state, _T0 + 180.0, _T0 + _COOL + 120.0)
    assert len(out) == 1 and out[0].severity == "WARNING", \
        "and it stays on the repeat ladder rather than re-earning CRITICAL"


def test_a_monitoring_gap_is_treated_as_a_clear():
    """A deliberate, documented consequence: presence is defined by the passes that
    RAN. If security-watcher is down for longer than the presence gap and the
    condition is still there when it returns, that is reported as a RECURRENCE at
    full severity -- because across a monitoring blind spot we do not KNOW whether
    it cleared, and the safe assumption is "new event". Cost: at most one extra
    CRITICAL after a watcher outage. Pinned so it is a decision, not a surprise."""
    state: dict = {}
    _dedup([_f()], state, _COOL, _T0)
    _passes(state, _T0 + 60.0, _T0 + 600.0)                 # 10 min of normal passes
    out = _dedup([_f()], state, _COOL, _T0 + 600.0 + 3600.0)  # ... then a 1h blind spot
    assert len(out) == 1 and out[0].severity == "CRITICAL"


# ── identity: what protects a genuinely new incident ─────────────────────────

def test_a_changed_condition_gets_a_new_identity_and_full_severity():
    """The safety argument for downgrading repeats at all: every Finding.key
    encodes the IDENTITY of the condition (fingerprints, file sha, audit event id),
    so ANY change to what is wrong is a different key -- a new condition, reported
    at full severity, immediately. Only a byte-identical condition is downgraded."""
    state: dict = {}
    _dedup([_f(key="file:dotenv:aaaaaaaaaaaa")], state, _COOL, _T0)
    changed = _dedup([_f(key="file:dotenv:bbbbbbbbbbbb")], state, _COOL, _T0 + 60.0)
    assert len(changed) == 1
    assert changed[0].severity == "CRITICAL"
    assert "STILL PRESENT" not in changed[0].title


def test_every_live_finding_key_carries_its_identity():
    """Guards the premise above against a future key format that drops the identity
    suffix -- which is the one change that would make the downgrade unsafe."""
    src = Path(sm.__file__).read_text(encoding="utf-8")
    for keyed in ("authkeys:hash:{cur[:12]}",
                  "authkeys:unexpected:{','.join(sorted(unexpected))[:40]}",
                  "file:{label}:{cur[:12]}",
                  "copybypass:{ev['id']}"):
        assert keyed in src, f"finding key lost its identity component: {keyed}"


# ── B3: the condition must stay visible without an email ─────────────────────

def test_persistent_conditions_are_published_in_state():
    """RED-FIRST. Suppressing a repeat is only safe if "what is still wrong" is
    written down somewhere that is read without an alert."""
    state: dict = {}
    _dedup([_f()], state, _COOL, _T0)
    assert state["persistent_conditions"] == [], "a first report is not yet persistent"
    _dedup([_f()], state, _COOL, _T0 + 60.0)
    assert state["persistent_conditions"] == [_KEY]


def test_last_run_status_names_the_persistent_conditions(tmp_path):
    """RED-FIRST. data_store/security/last_run.json is written from the PRE-dedup
    findings on every ~60s pass and is read by the Control Tower security adapter
    (ops/control_tower/aggregator.read_security), which raises a finding whenever
    clean is false. That is the channel that carries "still broken" without email."""
    p = tmp_path / "last_run.json"
    now = datetime(2026, 7, 26, 3, 46, tzinfo=_IST)
    sm._write_last_run_status(p, [_f()], 9, now, persistent=[_KEY])
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["clean"] is False
    assert data["max_severity"] == "CRITICAL"
    assert data["persistent"] == [_KEY]
    assert data["persistent_count"] == 1


def test_last_run_status_still_works_without_the_new_argument(tmp_path):
    """Additive only -- the Phase-1a caller contract keeps working unchanged."""
    p = tmp_path / "last_run.json"
    sm._write_last_run_status(p, [], 9, datetime(2026, 7, 26, tzinfo=_IST))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["clean"] is True and data["persistent"] == []


# ── upgrade path ─────────────────────────────────────────────────────────────

def test_a_legacy_flat_ledger_is_read_without_losing_an_alert():
    """RED-FIRST. The DEPLOYED state file holds {key: float}. On the first pass
    after this ships it must not crash and must not swallow anything: an unknown
    presence history is treated as a RECURRENCE, so a still-present condition alerts
    ONCE at full severity and then settles onto the ladder. One extra alert is the
    safe direction; one fewer is not."""
    state = {"alerted": {_KEY: _T0}}
    out = _dedup([_f()], state, _COOL, _T0 + 60.0)
    assert len(out) == 1 and out[0].severity == "CRITICAL"
    assert isinstance(state["alerted"][_KEY], dict)


def test_a_legacy_entry_that_never_fires_again_is_converted_not_dropped():
    """The live ledger holds rootspike:/newip: floats for conditions that are not
    present now. They must age out on the normal retention clock, not vanish on the
    first pass (which would be a silent, undocumented state change)."""
    state = {"alerted": {"rootspike:2026072513": _T0}}
    _dedup([], state, _COOL, _T0 + 60.0)
    assert isinstance(state["alerted"]["rootspike:2026072513"], dict)


def test_the_ledger_is_bounded():
    """rootspike:/failspike: keys rotate hourly, so a multi-day retention must not
    be able to grow the state file without limit."""
    state: dict = {}
    t = _T0
    for i in range(sm._LEDGER_MAX_ENTRIES + 250):
        _dedup([_f(key=f"rootspike:{i}")], state, _COOL, t)
        t += 3600.0
    assert len(state["alerted"]) <= sm._LEDGER_MAX_ENTRIES


def test_a_long_cleared_condition_is_pruned():
    state: dict = {}
    _dedup([_f()], state, _COOL, _T0)
    _dedup([], state, _COOL, _T0 + sm._LEDGER_RETENTION_SEC + 1.0)
    assert _KEY not in state["alerted"]


# ── config ───────────────────────────────────────────────────────────────────

def test_the_ladder_and_gap_are_config_tunable_without_a_code_change():
    cfg = SecConfig.load(Path("config/security.yaml"))
    assert cfg.realert_cooldown_sec == 21600
    assert list(cfg.realert_backoff_multipliers) == [1, 4, 28]
    assert cfg.realert_presence_gap_sec == 300


def test_a_config_supplied_ladder_actually_reaches_the_behaviour():
    """Not vacuous: prove the yaml value drives the interval, so the knob cannot
    quietly become decorative."""
    state: dict = {}
    _dedup([_f()], state, _COOL, _T0, backoff_ladder=[2.0])
    assert state["alerted"][_KEY]["repeats"] == 1
    assert _passes(state, _T0 + 60.0, _T0 + _COOL, step=240.0, backoff_ladder=[2.0]) == [], \
        "not due at 1x when the configured ladder says 2x"
    assert len(_passes(state, _T0 + _COOL, _T0 + 2 * _COOL,
                       step=240.0, backoff_ladder=[2.0])) == 1


def test_a_config_supplied_presence_gap_actually_reaches_the_behaviour():
    state: dict = {}
    _dedup([_f()], state, _COOL, _T0, presence_gap_sec=30.0)
    _dedup([], state, _COOL, _T0 + 60.0, presence_gap_sec=30.0)
    out = _dedup([_f()], state, _COOL, _T0 + 120.0, presence_gap_sec=30.0)
    assert out and out[0].severity == "CRITICAL", \
        "a 60s absence exceeds a 30s gap -> recurrence"


# ── parity ───────────────────────────────────────────────────────────────────

def test_parity_the_security_watcher_has_no_paper_live_mode():
    """PARITY (Rule 5) argued, not skipped: security_monitor is standalone VM
    infrastructure with no trading mode. It never reads one and never branches on
    one, so PAPER and LIVE are identical by construction. This fails if the module
    ever gains a mode dependence, at which point parity needs real coverage."""
    src = Path(sm.__file__).read_text(encoding="utf-8")
    for token in ("PAPER", "paper_mode", "live_mode"):
        assert token not in src, f"security_monitor gained a mode dependence: {token}"
