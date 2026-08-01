"""
tests/unit/test_effect_telemetry.py — ledger #1 Phase B.

Tests the TELEMETRY MODULE's own mechanics (handles, B2 assertion modes,
census determinism + mismatch classes). ⛔ Deliberately does NOT construct
production managers and assert their counters — that would reproduce the
unit-construct blindness the audit indicts (IA-XTEST-01). Composition truth
is validated by running the real system (the Phase-B validation checklist).
"""

import textwrap

import pytest

from core import effect_telemetry as et


@pytest.fixture(autouse=True)
def _clean_state():
    et.reset_for_tests()
    yield
    et.reset_for_tests()


@pytest.fixture()
def registry(tmp_path):
    """A miniature registry exercising every state class."""
    p = tmp_path / "expected_managers.yaml"
    p.write_text(textwrap.dedent("""\
        version: 1
        managers:
          - {name: alpha_active,   state: expected-active}
          - {name: beta_dormant,   state: expected-dormant, reason: "IA-TEST-1 — never fed"}
          - {name: gamma_event,    state: expected-event-driven}
          - {name: infra_thing,    state: infra}
          - {name: covered_thing,  state: covered-existing}
          - {name: absent_thing,   state: expected-absent, reason: "never built"}
          - {name: live_only_thing, state: infra, modes: "live-only ctor BY DESIGN"}
    """), encoding="utf-8")
    return p


class _Logger:
    def __init__(self):
        self.lines = []
        self.criticals = []

    def info(self, msg, *a):
        self.lines.append(msg % a if a else msg)

    def critical(self, msg, *a):
        self.criticals.append(msg % a if a else msg)

    def error(self, msg, *a, **k):
        self.lines.append(("ERROR: " + msg) % a if a else "ERROR: " + msg)


class _Notifier:
    def __init__(self, raise_on_send=False):
        self.sent = []
        self._raise = raise_on_send

    def send_critical(self, msg):
        if self._raise:
            raise RuntimeError("telegram down")
        self.sent.append(msg)


def _register_all_expected(*, skip=()):
    for n in ("alpha_active", "beta_dormant", "gamma_event",
              "infra_thing", "covered_thing"):
        if n in skip:
            continue
        if n in ("infra_thing", "covered_thing"):
            et.register_constructed(n)
        else:
            et.handle(n)


# ── handles ──────────────────────────────────────────────────────────────────

def test_handle_is_idempotent_same_object_and_counts():
    h1 = et.handle("x")
    h2 = et.handle("x")
    assert h1 is h2
    h1.inc()
    h2.inc()
    h1.add(3)
    assert h1.n == 5


# ── B2 assertion ─────────────────────────────────────────────────────────────

def test_assert_ok_when_everything_expected_registers(registry):
    et.set_mode(True)  # paper: live_only exempt
    _register_all_expected()
    report = et.assert_composition(logger=_Logger(), registry_path=registry)
    assert report["ok"] is True


def test_assert_paper_fails_fast_on_missing_registration(registry):
    et.set_mode(True)
    _register_all_expected(skip=("beta_dormant",))
    with pytest.raises(et.EffectCompositionError) as exc:
        et.assert_composition(logger=_Logger(), registry_path=registry)
    assert "beta_dormant" in str(exc.value)


def test_assert_live_alerts_and_continues_never_raises(registry):
    et.set_mode(False)  # live
    # live-only entry must now be REQUIRED (not exempt)
    _register_all_expected(skip=("alpha_active",))
    notifier = _Notifier()
    log = _Logger()
    report = et.assert_composition(notifier=notifier, logger=log,
                                   registry_path=registry)
    assert report["ok"] is False
    assert "alpha_active" in report["missing"]
    assert "live_only_thing" in report["missing"]  # required in live
    assert notifier.sent and "EFFECT-TELEMETRY" in notifier.sent[0]
    assert log.criticals  # CRITICAL logged, no raise


def test_assert_live_survives_notifier_failure(registry):
    et.set_mode(False)
    notifier = _Notifier(raise_on_send=True)
    report = et.assert_composition(notifier=notifier, logger=_Logger(),
                                   registry_path=registry)
    assert report["ok"] is False  # and no exception escaped


def test_assert_flags_ghost_and_unknown(registry):
    et.set_mode(True)
    _register_all_expected()
    et.handle("absent_thing")       # ghost: expected-absent but constructed
    et.handle("not_in_registry")    # unknown: C2 same-diff rule violation
    with pytest.raises(et.EffectCompositionError) as exc:
        et.assert_composition(logger=_Logger(), registry_path=registry)
    msg = str(exc.value)
    assert "absent_thing" in msg and "not_in_registry" in msg


# ── census ───────────────────────────────────────────────────────────────────

def test_census_lines_states_and_clean_mismatch(registry):
    et.set_mode(True)
    _register_all_expected()
    et.handle("alpha_active").inc()
    lines = et.emit_census(logger=_Logger(), day="2026-08-01",
                           registry_path=registry)
    joined = "\n".join(lines)
    assert "alpha_active: acted 1 | active" in joined
    assert "beta_dormant: acted 0 | dormant" in joined
    assert "gamma_event: acted 0 | event-driven" in joined
    assert "absent_thing: NEVER-CONSTRUCTED [expected]" in joined
    assert "infra_thing" not in joined          # infra: no census line
    assert "MISMATCH: NONE" in joined


def test_census_mismatch_classes_i_ii_iii_iv(registry):
    et.set_mode(True)
    _register_all_expected()
    # (i) active & 0 — do not inc alpha. (ii) dormant & >0:
    et.handle("beta_dormant").inc()
    # (iii) absent but constructed; (iv) constructed, not in registry:
    et.handle("absent_thing")
    et.handle("stray_unit").inc()
    lines = et.emit_census(logger=_Logger(), day="2026-08-01",
                           registry_path=registry)
    joined = "\n".join(lines)
    assert "MISMATCH(i) alpha_active" in joined
    assert "MISMATCH(ii) beta_dormant" in joined and "acted 1" in joined
    assert "MISMATCH(iii) absent_thing" in joined
    assert "MISMATCH(iv) stray_unit" in joined
    # event-driven acting is NOT a mismatch:
    assert "MISMATCH(ii) gamma_event" not in joined


def test_census_deterministic_across_two_emits(registry):
    et.set_mode(True)
    _register_all_expected()
    et.handle("alpha_active").inc()
    log = _Logger()
    first = et.emit_census(logger=log, day="2026-08-01", registry_path=registry)
    second = et.emit_census(logger=log, day="2026-08-01", registry_path=registry)
    assert first == second  # stable order, stable content (C5)


def test_census_covered_existing_derive_and_failure(registry):
    et.set_mode(True)
    _register_all_expected()
    lines = et.emit_census(
        logger=_Logger(), day="2026-08-01", registry_path=registry,
        webhook_day_count=None,  # no derive fn wired for covered_thing anyway
    )
    assert any("covered_thing: acted ?" in ln for ln in lines)
    # covered-existing never enters the mismatch block:
    assert not any("MISMATCH" in ln and "covered_thing" in ln for ln in lines)


def test_census_never_raises_even_on_broken_logger(registry):
    class _Boom:
        def info(self, *a):
            raise RuntimeError("logger broke")

        def error(self, *a, **k):
            raise RuntimeError("logger broke twice")

    # Must not raise — the census can never break shutdown.
    assert et.emit_census(logger=_Boom(), day="2026-08-01",
                          registry_path=registry) is None


# ── the real frozen registry parses and covers the composition ───────────────

def test_frozen_registry_parses_with_expected_states():
    entries = et._load_registry()  # config/expected_managers.yaml
    states = {str(e.get("state")) for e in entries}
    assert states <= {
        "expected-active", "expected-dormant", "expected-event-driven",
        "expected-absent", "infra", "covered-existing",
    }
    names = [str(e.get("name")) for e in entries]
    assert len(names) == len(set(names)), "duplicate registry names"
