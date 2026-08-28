"""
tests/unit/test_mis_squareoff_notifier.py — F / D-1b.

TEST ASSUMPTIONS RECORDED HERE, so a future change cannot silently invalidate them:

  canonical orchestrator module ..... orders.mis_autosquareoff (named, not scanned)
  contract module ................... core.mis_squareoff_timing
  shipped config .................... cutoff 15:12, offsets 5m/2m, margin 20s
  derived ........................... CHECK_1 15:07, CHECK_2 15:10
  pre-pass lead ..................... 2 min  => PRE_PASS at 15:05
  PASS 2 measured-bound execution ... ~2s against a 120s budget (28-Aug)
    => a blocking send could BY ITSELF cause a DEADLINE_BREACH, so the
       non-blocking bound is MEASURED here, not asserted in prose.

TEST A and TEST B are COMPLEMENTARY, not duplicates:
  A proves F's FIRING path needs no orchestrator.
  B proves that WHEN BOTH EXIST they share no instance.
"""
from __future__ import annotations

import importlib
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

CANON = "orders.mis_autosquareoff"


# ── helpers ──────────────────────────────────────────────────────────────────

def at(h, m, s=0):
    return datetime(2026, 8, 31, h, m, s)


class Rec:
    """Records what each channel was asked to send."""
    def __init__(self, fail=False, exc=None, block=0.0):
        self.calls = []
        self.fail = fail
        self.exc = exc
        self.block = block

    def __call__(self, subject, body):
        self.calls.append((subject, body))
        if self.block:
            time.sleep(self.block)
        if self.exc:
            raise self.exc
        if self.fail:
            raise RuntimeError("provider rejected")


class Log:
    def __init__(self):
        self.info_calls, self.errors = [], []

    def info(self, msg, *a, **k):
        self.info_calls.append((msg, k.get("extra", {})))

    def error(self, msg, *a, **k):
        self.errors.append(msg % a if a else msg)

    def critical(self, msg, *a, **k):
        self.errors.append(msg % a if a else msg)

    def warning(self, msg, *a, **k):
        pass


def shipped_trading_hours():
    from core.config_loader import load_all
    return load_all(Path("config")).system.trading_hours


def build_f(email=None, telegram=None, log=None, now=None, **over):
    from alerts.mis_squareoff_notifier import MisSquareoffNotifier
    holder = {"now": now or at(15, 5)}
    kw = dict(
        trading_hours=shipped_trading_hours(),
        poll_interval_sec=5,
        send_email=email or Rec(),
        send_telegram=telegram or Rec(),
        logger=log or Log(),
        now_fn=lambda: holder["now"],
        boot_id="testboot01",
    )
    kw.update(over)
    f = MisSquareoffNotifier(**kw)
    f._clock = holder
    return f


# ══ TEST A — F FIRES WITH NO ORCHESTRATOR PRESENT ═══════════════════════════
#
# WHY A SUBPROCESS, AND WHY THAT IS NOT IMPORT SURGERY.
# The assertion is "orders.mis_autosquareoff is absent from sys.modules at the
# moment F fires". In THIS interpreter that is unmeasurable: any other test file
# that imports the orchestrator -- test_mis_autosquareoff.py does, legitimately --
# puts it in sys.modules before we run, and the assertion then reports a HARNESS
# artefact rather than a property of F.
#
# The prohibited fix is deleting the entry or reloading modules: that manufactures
# the clean-room condition the assertion exists to prove. The correct fix is to
# give the assertion an interpreter where nothing else has run. Nothing is deleted
# and nothing is reloaded -- the child process simply never imported it.
#
# The child is the REAL path: load config, build F, fire, assert. If F ever gains a
# dependency on the orchestrator, the child fails and these tests go RED.

_CHILD = r"""
import sys, json
CANON = "orders.mis_autosquareoff"
out = {{}}
out["clean_at_start"] = CANON not in sys.modules

from alerts.mis_squareoff_notifier import MisSquareoffNotifier
from core.config_loader import load_all
from pathlib import Path
from datetime import datetime

th = load_all(Path("config")).system.trading_hours
calls = {{"email": [], "telegram": []}}
f = MisSquareoffNotifier(
    trading_hours=th, poll_interval_sec=5,
    send_email=lambda s, b: calls["email"].append((s, b)),
    send_telegram=lambda s, b: calls["telegram"].append((s, b)),
    logger=type("L", (), {{"info": lambda *a, **k: None,
                          "error": lambda *a, **k: None}})(),
    now_fn=lambda: datetime(2026, 8, 31, {hh}, {mm}),
    boot_id="childboot",
)
out["present_after_construct"] = CANON in sys.modules
out["pre_pass_at"] = f.pre_pass_at(datetime(2026, 8, 31, 15, 5)).strftime("%H:%M")

rec = f.{fire}

out["present_after_fire"] = CANON in sys.modules
out["fired"] = rec is not None
out["kind"] = rec.kind if rec else None
out["aggregate"] = rec.aggregate if rec else None
out["email_calls"] = len(calls["email"])
out["telegram_calls"] = len(calls["telegram"])
print("RESULT " + json.dumps(out))
"""


def _run_child(fire: str, hh: int, mm: int) -> dict:
    import json
    import subprocess
    src = _CHILD.format(fire=fire, hh=hh, mm=mm)
    proc = subprocess.run(
        [sys.executable, "-c", src],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"child failed rc={proc.returncode}\nSTDOUT:\n{proc.stdout}"
        f"\nSTDERR:\n{proc.stderr}"
    )
    line = [l for l in proc.stdout.splitlines() if l.startswith("RESULT ")]
    assert line, f"child produced no result\nSTDOUT:\n{proc.stdout}"
    return json.loads(line[0][len("RESULT "):])


def test_A_fires_with_the_orchestrator_module_absent_from_sys_modules():
    """The dispositive independence test.

    Not "F can be constructed" -- the full path: config -> construct -> derive
    PRE_PASS -> own trigger -> FIRE -> observable result, with the canonical
    orchestrator module ABSENT at the moment of firing.

    A defective implementation can construct cleanly and defer a forbidden lookup
    to the callback, so the module is checked AFTER the fire as well as before.
    """
    r = _run_child("check_and_fire_pre_pass(datetime(2026, 8, 31, 15, 5))", 15, 5)
    assert r["clean_at_start"] is True
    assert r["present_after_construct"] is False, (
        f"{CANON} was imported by F's CONSTRUCTION path"
    )
    assert r["present_after_fire"] is False, (
        f"{CANON} was imported by F's FIRING path -- F is coupled to the orchestrator"
    )
    # "did not crash" is not "fired": require an observable result.
    assert r["fired"] is True, "the trigger did not fire"
    assert r["kind"] == "PRE_PASS"
    assert r["aggregate"] == "BOTH_ACCEPTED"
    assert r["email_calls"] == 1 and r["telegram_calls"] == 1
    assert r["pre_pass_at"] == "15:05"


def test_A2_boot_self_test_also_fires_with_no_orchestrator():
    r = _run_child("run_boot_self_test(datetime(2026, 8, 31, 8, 15))", 8, 15)
    assert r["present_after_construct"] is False
    assert r["present_after_fire"] is False
    assert r["fired"] is True and r["kind"] == "BOOT"
    assert r["email_calls"] == 1 and r["telegram_calls"] == 1


def test_A3_contract_module_alone_does_not_pull_the_orchestrator():
    """The extraction's own invariant, asserted in a clean interpreter."""
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; import core.mis_squareoff_timing; "
         "print('PRESENT' if 'orders.mis_autosquareoff' in sys.modules else 'ABSENT')"],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ABSENT" in proc.stdout, (
        f"the contract module pulls in the orchestrator\n{proc.stdout}{proc.stderr}"
    )


# ══ TEST B — SAME CLASS, NEVER THE SAME INSTANCE ════════════════════════════

def test_B_f_and_orchestrator_never_share_a_timing_instance():
    """Complementary to A: when BOTH exist, they must not share an instance.

    IDENTITY (`is not`), never equality: two independently built objects SHOULD be
    value-equal, and a future "cleanup" replacing `is not` with `!=` would silently
    void this guard.
    """
    from orders.mis_autosquareoff import MisAutoSquareoff          # both exist now
    from core.mis_squareoff_timing import MisSquareoffTiming

    th = shipped_trading_hours()
    orch_timing = MisSquareoffTiming.build(
        cutoff=th.mis_squareoff_cutoff, first_offset=th.mis_squareoff_first_offset,
        second_offset=th.mis_squareoff_second_offset,
        margin_sec=th.mis_squareoff_margin_sec, poll_interval_sec=5,
        entry_end=th.entry_end, eod_squareoff_time=th.eod_squareoff_time)
    f = build_f()

    assert f.timing is not orch_timing, "F must not share the orchestrator's instance"
    assert f.timing == orch_timing, "independently built objects should be value-equal"
    assert MisAutoSquareoff is not None

    # The identity assertion above is necessary but, on its own, NOT RED-CAPABLE:
    # F builds its own timing from config, so `is not` is trivially true and no
    # mutation of F's body can falsify it. What must actually be prevented is the
    # plausible future refactor -- "both build the same object, so build it once and
    # pass it in" -- which reads as good hygiene and silently reintroduces the
    # lifecycle coupling. So assert the coupling is impossible BY CONSTRUCTION:
    # there must be no parameter through which a prebuilt timing can be injected.
    import inspect
    from alerts.mis_squareoff_notifier import MisSquareoffNotifier
    params = set(inspect.signature(MisSquareoffNotifier.__init__).parameters)
    forbidden = {"timing", "mis_timing", "squareoff_timing", "orchestrator",
                 "mis_autosq", "check_1", "check_1_ts"}
    leaked = params & forbidden
    assert not leaked, (
        f"F accepts injectable timing/orchestrator state {sorted(leaked)} -- the "
        "instance-sharing refactor is now possible"
    )
    # and it must genuinely derive from config, not from a passed-in object
    assert "trading_hours" in params


def test_B2_f_builds_its_own_timing_from_config():
    f = build_f()
    assert (f.timing.check_1.hour, f.timing.check_1.minute) == (15, 7)
    assert (f.timing.check_2.hour, f.timing.check_2.minute) == (15, 10)


def test_B3_contract_module_owns_no_singleton():
    """A module-level cached instance would MOVE the shared-instance problem here
    rather than solve it -- and both components would then share it."""
    import core.mis_squareoff_timing as m
    from core.mis_squareoff_timing import MisSquareoffTiming
    leaked = [n for n, v in vars(m).items()
              if isinstance(v, MisSquareoffTiming)]
    assert leaked == [], f"contract module owns a timing instance: {leaked}"


# ══ TEST E — THE FOUR CRITICAL BRANCHES, INDEPENDENTLY ══════════════════════

@pytest.mark.parametrize("condition", [
    "DEADLINE_BREACH", "MIS_REMAINS", "CANCEL_FAILED", "BROKER_STATE_UNAVAILABLE",
])
def test_E_each_critical_branch_delivers_on_both_channels(condition):
    """A green self-test proves NOTHING about whether each branch invokes F.
    Class A (path health) and Class B (incident delivery) are separate."""
    email, telegram = Rec(), Rec()
    f = build_f(email=email, telegram=telegram)
    rec = f.notify_critical(condition, "detail here", correlation_id="corr-1")
    assert rec.kind == condition
    assert rec.correlation_id == "corr-1"
    assert rec.aggregate == "BOTH_ACCEPTED"
    assert condition in email.calls[0][0] and condition in telegram.calls[0][0]


def test_E2_correlation_id_ties_the_whole_chain_together():
    """Log adjacency is not identity."""
    log = Log()
    f = build_f(log=log)
    rec = f.notify_critical("MIS_REMAINS", "x", correlation_id="abc123")
    payloads = [e for _, e in log.info_calls if e.get("correlation_id") == "abc123"]
    assert payloads, "no log record carried the correlation id"
    p = payloads[0]
    assert p["kind"] == "MIS_REMAINS"
    assert p["email_state"] and p["telegram_state"]
    assert rec.correlation_id == "abc123"


# ══ TRANSPORT SEMANTICS — PARTIAL IS NOT SUCCESS ════════════════════════════

def test_partial_is_not_success_and_neither_channel_erases_the_other():
    email, telegram = Rec(fail=True), Rec()
    f = build_f(email=email, telegram=telegram)
    rec = f.notify_critical("CANCEL_FAILED", "d")
    assert rec.aggregate == "PARTIAL"
    assert rec.aggregate != "BOTH_ACCEPTED"
    assert rec.email.state == "EXCEPTION"
    assert rec.telegram.state == "ACCEPTED_BY_PROVIDER"
    # the failing channel did not stop the other being attempted
    assert len(telegram.calls) == 1


def test_both_failed_is_recorded_and_never_reads_as_sent():
    log = Log()
    f = build_f(email=Rec(fail=True), telegram=Rec(fail=True), log=log)
    rec = f.notify_critical("DEADLINE_BREACH", "d")
    assert rec.aggregate == "BOTH_FAILED"
    assert any("F_NOTIFY_DEGRADED" in e for e in log.errors), (
        "a transport failure must itself be recorded"
    )


def test_provider_acceptance_is_never_called_human_delivery():
    from alerts.mis_squareoff_notifier import TransportState
    assert not hasattr(TransportState, "DELIVERED_TO_HUMAN")
    assert TransportState.ACCEPTED_BY_PROVIDER == "ACCEPTED_BY_PROVIDER"


# ══ TEST F — NON-BLOCKING, MEASURED ═════════════════════════════════════════

def test_F_a_blocking_channel_cannot_hold_the_caller():
    """MEASURED, not asserted. PASS 2 has ~2s of work against a 120s budget; a
    blocking send could by itself push it past the 15:12 cutoff."""
    slow = Rec(block=30.0)                 # would hang for 30s if awaited
    f = build_f(email=slow, telegram=Rec(), send_timeout_sec=0.5)
    t0 = time.monotonic()
    rec = f.notify_critical("BROKER_STATE_UNAVAILABLE", "d")
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"F held the caller for {elapsed:.1f}s"
    assert rec.email.state == "TIMEOUT"
    assert rec.telegram.state == "ACCEPTED_BY_PROVIDER"
    assert rec.aggregate == "PARTIAL"


def test_F2_timeout_is_recorded_not_silently_dropped():
    f = build_f(email=Rec(block=5.0), telegram=Rec(block=5.0), send_timeout_sec=0.3)
    rec = f.notify_critical("MIS_REMAINS", "d")
    assert rec.email.state == "TIMEOUT" and rec.telegram.state == "TIMEOUT"
    assert rec.aggregate == "BOTH_FAILED"


# ══ SELF-TEST SEMANTICS ═════════════════════════════════════════════════════

def test_self_test_is_a_timestamped_event_not_a_health_latch():
    f = build_f(now=at(8, 15))
    f.run_boot_self_test(at(8, 15))
    rec = f.last_self_test()
    assert rec.at == at(8, 15)
    assert not hasattr(f, "notification_healthy")
    assert rec.boot_id == "testboot01"


def test_boot_self_test_fires_once_per_boot():
    email = Rec()
    f = build_f(email=email, now=at(8, 15))
    assert f.run_boot_self_test(at(8, 15)) is not None
    assert f.run_boot_self_test(at(8, 16)) is None
    assert len(email.calls) == 1


def test_pre_pass_does_not_fire_before_its_time_and_only_once():
    email = Rec()
    f = build_f(email=email)
    assert f.check_and_fire_pre_pass(at(15, 4, 59)) is None
    assert f.check_and_fire_pre_pass(at(15, 5)) is not None
    assert f.check_and_fire_pre_pass(at(15, 6)) is None
    assert len(email.calls) == 1


def test_self_test_is_info_and_distinct_from_the_four_critical_conditions():
    from alerts.mis_squareoff_notifier import CRITICAL_CONDITIONS
    email = Rec()
    f = build_f(email=email, now=at(8, 15))
    f.run_boot_self_test(at(8, 15))
    subject = email.calls[0][0]
    assert "NOTIFICATION_SELF_TEST" in subject
    for c in CRITICAL_CONDITIONS:
        assert c not in subject, "a self-test must not be mistakable for a CRITICAL"


def test_pre_pass_trigger_moves_with_the_config_not_a_hardcoded_1505():
    """Derived from the same authoritative schedule, so a holiday or special
    session that moves the cutoff moves F with it."""
    th = shipped_trading_hours()

    class Shifted:
        mis_squareoff_cutoff = "14:12"
        mis_squareoff_first_offset = th.mis_squareoff_first_offset
        mis_squareoff_second_offset = th.mis_squareoff_second_offset
        mis_squareoff_margin_sec = th.mis_squareoff_margin_sec
        entry_end = "13:00"
        eod_squareoff_time = "14:17"

    f = build_f(trading_hours=Shifted())
    assert f.pre_pass_at(at(14, 0)).strftime("%H:%M") == "14:05"


def test_f_never_gates_alerting_on_recipient_confirmation():
    """`if not confirmed: suppress` would be a circular dependency and is
    forbidden -- F_RECIPIENT_CONFIRMED is an evidence field, nothing else."""
    from alerts import mis_squareoff_notifier as m
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "RECIPIENT_CONFIRMED" not in src, (
        "recipient confirmation must not appear in F's runtime logic"
    )


def test_mutation_map_documents_what_must_break_this_file():
    """
    Each names an observed unsafe EVENT, not "goes RED":

      F imports orders.mis_autosquareoff       : F coupled to the orchestrator
                                                 -> test_A (sys.modules assertion)
      F accepts the orchestrator's timing obj  : lifecycle coupling reintroduced
                                                 -> test_B (`is not`)
      `is not` -> `!=` in test_B               : the guard silently voids
                                                 -> test_B fails to discriminate
      PARTIAL treated as success               : a half-delivered incident reads OK
                                                 -> test_partial_is_not_success
      one channel's failure skips the other    : evidence erased
                                                 -> test_partial_is_not_success
      send awaited instead of bounded          : F causes a DEADLINE_BREACH
                                                 -> test_F
      timeout dropped silently                 : a dead transport reads green
                                                 -> test_F2 / test_both_failed
      self-test becomes a latch                : stale 08:15 PASS reads as 15:07 cover
                                                 -> test_self_test_is_a_timestamped_event
      pre-pass hardcoded to 15:05              : F drifts when the cutoff moves
                                                 -> test_pre_pass_trigger_moves_with_config
      a CRITICAL branch not wired to F         : that incident is invisible
                                                 -> test_E (four params)
    """
    from alerts.mis_squareoff_notifier import (
        CANONICAL_ORCHESTRATOR_MODULE, CRITICAL_CONDITIONS,
    )
    assert CANONICAL_ORCHESTRATOR_MODULE == "orders.mis_autosquareoff"
    assert len(CRITICAL_CONDITIONS) == 4


def test_pre_pass_is_a_window_not_a_threshold():
    """A threshold (`now >= pre_pass_at`) would announce "MIS pass due in 2
    minutes" at ANY later time -- so a service restarting at 16:00 would send a
    message that is simply false. A line Rama learns to ignore is worse than none.
    """
    email = Rec()
    f = build_f(email=email)
    assert f.check_and_fire_pre_pass(at(15, 4, 59)) is None, "too early"
    assert f.check_and_fire_pre_pass(at(16, 0)) is None, (
        "fired after CHECK_1 had passed -- the announcement would be false"
    )
    assert f.check_and_fire_pre_pass(at(20, 0)) is None
    assert email.calls == []
    # and it still fires inside the window
    assert f.check_and_fire_pre_pass(at(15, 6)) is not None
    assert len(email.calls) == 1


def test_pre_pass_does_not_fire_at_or_after_check_1():
    f = build_f()
    assert f.check_and_fire_pre_pass(at(15, 7)) is None, "CHECK_1 itself is too late"


def test_boot_self_test_does_not_block_the_boot_path():
    """A slow SMTP at 08:15 must not delay the service's own startup. Same rule as
    PASS 2: notification never blocks the path it observes."""
    slow = Rec(block=30.0)
    f = build_f(email=slow, telegram=Rec(), now=at(8, 15), send_timeout_sec=5.0)
    t0 = time.monotonic()
    f.run_boot_self_test(at(8, 15), background=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"boot path held for {elapsed:.1f}s"
