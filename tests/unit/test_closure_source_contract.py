"""tests/unit/test_closure_source_contract.py -- CW-A (26-Jul-2026).

The closure vocabulary must live in ONE location and stay there.

⭐ WHY A TEST AND NOT A CONVENTION. W8 (`trades.closure_source`, P3-r10) exists
because the same question -- "who closed this position?" -- was answered in several
places and the answers diverged: `reports/daily_trade_review.py:34-46` documents the
resulting 4-way collision (daily-EOD / operator-manual / RMS / kill-flatten all
writing exit_reason='MANUAL'). Re-stating the vocabulary in two modules would
recreate exactly that. `core/closure_source.py` is the code single-source (the same
pattern `core/constants.py` already uses for PRODUCT_TO_INTENT, imported by all five
of fund_manager / kill_switch / order_placer / order_reconciler / …), and
`docs/closure_source_contract.md` is the human contract. These tests pin the two to
each other and forbid a third copy.

Run: python -m pytest tests/unit/test_closure_source_contract.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core import closure_source as cs

_REPO = Path(__file__).resolve().parents[2]
_DOC = _REPO / "docs" / "closure_source_contract.md"
_CANONICAL = _REPO / "core" / "closure_source.py"


def _doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


# ── the two axes exist and are distinct ───────────────────────────────────────

def test_closure_source_values_are_exactly_the_five() -> None:
    assert cs.CLOSURE_SOURCES == frozenset({
        "OWN_SL", "OWN_TGT", "OWN_EOD", "OWN_KILL", "EXTERNAL_UNATTRIBUTED",
    })


def test_own_sources_exclude_the_external_bucket() -> None:
    """OWN_CLOSURE_SOURCES is what "not an external close" means. If
    EXTERNAL_UNATTRIBUTED ever leaked into it, every genuine external close would be
    suppressed -- the silent-failure direction."""
    assert cs.EXTERNAL_UNATTRIBUTED not in cs.OWN_CLOSURE_SOURCES
    assert cs.OWN_CLOSURE_SOURCES < cs.CLOSURE_SOURCES
    assert cs.CLOSURE_SOURCES - cs.OWN_CLOSURE_SOURCES == {cs.EXTERNAL_UNATTRIBUTED}


def test_rms_and_operator_manual_are_deliberately_absent() -> None:
    """The 05-Jul audit established they are indistinguishable in-data. Adding them
    would re-create the over-claim CHECK1 is being fixed for. One honest bucket."""
    for forbidden in ("BROKER_RMS", "OPERATOR_MANUAL", "RMS", "MANUAL"):
        assert forbidden not in cs.CLOSURE_SOURCES


def test_mechanism_is_a_separate_axis_from_source() -> None:
    """Q1: a GTT leg IS an SL or a TGT by REASON; broker-managed is its MECHANISM.
    Merging the axes makes every Slice 2.5 CNC exit unattributable by reason."""
    assert cs.MECH_GTT in cs.EXIT_MECHANISMS
    assert cs.MECH_GTT not in cs.CLOSURE_SOURCES
    assert not (cs.EXIT_MECHANISMS & cs.CLOSURE_SOURCES), \
        "the two axes must not share a value"


def test_unknown_mechanism_exists_so_it_is_never_guessed() -> None:
    assert cs.MECH_UNKNOWN in cs.EXIT_MECHANISMS


# ── the precedence ladder ─────────────────────────────────────────────────────

def test_precedence_is_ordered_strongest_identity_first() -> None:
    """Ties are impossible only because the sources are ORDERED, not scored. Broker
    order_id -- the broker naming the order that filled -- must rank first; nothing
    beats direct identity."""
    assert cs.EVIDENCE_PRECEDENCE == (
        cs.EV_BROKER_ORDER_ID,
        cs.EV_LOCAL_AND_BROKER,
        cs.EV_LOCAL_COMPLETE,
        cs.EV_MID_FILL,
    )
    assert cs.EVIDENCE_PRECEDENCE[0] == cs.EV_BROKER_ORDER_ID
    assert len(set(cs.EVIDENCE_PRECEDENCE)) == len(cs.EVIDENCE_PRECEDENCE), \
        "a duplicated rung would make the ladder ambiguous"


# ── ⚠️ the safety property: the contradiction rule must stay stated ────────────

def test_contradiction_rule_is_recorded_in_both_places() -> None:
    """⚠️ If two sources DISAGREE that is NOT a tie and must NOT resolve to the
    higher-precedence source -- it resolves to CRITICAL. Precedence orders sources
    that are SILENT; it never overrules a source that SPOKE. This is the safety
    property, so losing the statement must break a test, not just a review."""
    src = _CANONICAL.read_text(encoding="utf-8")
    for text in (src, _doc_text()):
        low = text.lower()
        assert "contradiction rule" in low
        assert "not a tie" in low
        assert "never overrules a source that" in low


def test_mid_fill_expiry_is_recorded_in_both_places() -> None:
    """⏳ §D. Rung 4 is a claim about NOW: CHECK1 may DEFER on it, and once the bound
    elapses the claim is STALE and falls silent. That is a rule of the ladder, so it
    lives with the ladder -- and losing the statement must break a test, not just a
    review, exactly like the contradiction rule above."""
    src = _CANONICAL.read_text(encoding="utf-8")
    for text in (src, _doc_text()):
        low = text.lower()
        assert "expires" in low, "the ladder must state that rung 4 EXPIRES"
        assert "stale" in low, "an expired mid-fill claim is STALE, not merely old"
        assert "defer" in low, "the deferral is the mechanism the expiry bounds"


def test_first_principle_is_recorded_in_both_places() -> None:
    """Suppress only on POSITIVE evidence, never on absence."""
    src = _CANONICAL.read_text(encoding="utf-8")
    for text in (src, _doc_text()):
        low = text.lower()
        assert "positive evidence" in low
        assert "never on absence" in low


# ── doc <-> code agreement ────────────────────────────────────────────────────

@pytest.mark.parametrize("value", sorted(
    {"OWN_SL", "OWN_TGT", "OWN_EOD", "OWN_KILL", "EXTERNAL_UNATTRIBUTED"}))
def test_every_closure_source_appears_in_the_doc(value: str) -> None:
    assert f"`{value}`" in _doc_text(), \
        f"{value} is in the module but missing from the canonical contract doc"


def test_doc_declares_no_source_the_module_lacks() -> None:
    """The reverse direction: a value documented but never defined would be a
    contract the code cannot honour."""
    # `OWN_CLOSURE_SOURCES` is the CONTAINER's name, not a value — the doc names it
    # when defining "not an external close", so it is excluded from the value scan.
    _CONTAINERS = {"OWN_CLOSURE_SOURCES"}
    documented = set(
        re.findall(r"`(OWN_[A-Z_]+|EXTERNAL_UNATTRIBUTED)`", _doc_text())
    ) - _CONTAINERS
    assert documented <= cs.CLOSURE_SOURCES, \
        f"documented but undefined: {sorted(documented - cs.CLOSURE_SOURCES)}"


# ── ⭐ ONE LOCATION — the guard that actually enforces it ──────────────────────

def test_no_module_restates_the_vocabulary_literals() -> None:
    """⭐ THE POINT OF THE WHOLE EXERCISE. Consumers must IMPORT these names, never
    re-type the strings. A second literal copy is how the 4-way collision started."""
    offenders: list[str] = []
    pattern = re.compile(r"""["'](OWN_SL|OWN_TGT|OWN_EOD|OWN_KILL|EXTERNAL_UNATTRIBUTED)["']""")
    for path in _REPO.rglob("*.py"):
        parts = path.parts
        if any(p in parts for p in ("tests", "venv", ".venv", "node_modules", "build")):
            continue
        if path.resolve() == _CANONICAL.resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_REPO)}:{n}")
    assert not offenders, (
        "closure-source literals restated outside core/closure_source.py -- import "
        f"them instead: {offenders}"
    )
