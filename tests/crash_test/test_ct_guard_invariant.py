"""
tests/crash_test/test_ct_guard_invariant.py — ANTI-DECAY INVARIANT for the CT guard.

The 18-Jul-2026 fix (tag deploy-18jul-ct-harness-safety) made the crash-test harness
scratch-safe: all writable DB access routes through the ONE fail-closed guard,
``ct_utils.assert_not_live_db``. That fix is only worth as much as its durability — a
crash-test helper added six months from now must not be able to silently re-introduce a
writable live-DB handle just because nobody remembered the rule.

So this file pins the property BY CONSTRUCTION rather than by memory. It statically scans
every module in tests/crash_test/ (via AST, so comments and strings cannot fool it) and
enforces three invariants that are load-bearing *together*:

  A. No module outside ct_utils.py may call ``sqlite3.connect(...)`` directly.
     → every DB open goes through the one guarded helper.
  B. No module outside ct_utils.py may contain a LIVE database path literal
     ("trading_system.db" / "analytics.db").
     → you cannot open what you are not allowed to name.
  C. ``StateStore(db_path=...)`` may not be handed LIVE_DB_PATH / LIVE_ANALYTICS_DB_PATH.
     → closes the one writable opener that is not sqlite3.connect.

Why all three: to obtain a writable live handle a future helper would have to call
sqlite3.connect (blocked by A), name the live DB (blocked by B), or pass the live path
constant to a writable opener (blocked by C, and by the runtime guard for
get_db_connection). Each route is closed, and the closure is checked automatically.

This mirrors the existing anti-decay idiom used elsewhere in the suite (the S1 pin and the
P3 route-map guard): enumerate the real surface, assert the property over all of it.

PERMANENT ENGINEERING RULE (recorded here because this is where it is enforced):
    A destructive / crash-test harness must NEVER hold a writable handle on a live
    database. All writable DB access goes through ONE guard that fails CLOSED — no
    override flag, no environment escape hatch. Resetting a live system is an OPERATOR
    tool in scripts/ with a backup + confirmation gate, never part of a test harness.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

CT_DIR = Path(__file__).resolve().parent

# The single implementation site. ct_utils.py is where sqlite3.connect and the live-path
# constants are ALLOWED to appear, because it is the module that defines the guard.
GUARD_MODULE = "ct_utils.py"

# Modules that legitimately name the live DB while never opening it writable. Kept
# deliberately tiny and justified in-line — an allow-list is a hole, so each entry must
# earn its place.
_ALLOWED_LIVE_LITERAL = {
    GUARD_MODULE,                    # defines LIVE_DB_PATH / LIVE_ANALYTICS_DB_PATH
    "test_ct_harness_safety.py",     # asserts the guard REFUSES those very paths
    "test_ct_guard_invariant.py",    # this file (the literals below are the scan patterns)
}
_ALLOWED_SQLITE_CONNECT = {
    GUARD_MODULE,                    # the one guarded implementation
    "test_ct_harness_safety.py",     # proves a mode=ro handle rejects writes
}

_LIVE_DB_LITERALS = ("trading_system.db", "analytics.db")
_LIVE_PATH_NAMES = {"LIVE_DB_PATH", "LIVE_ANALYTICS_DB_PATH"}


def _modules():
    return sorted(p for p in CT_DIR.glob("*.py") if p.name != "__init__.py")


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:  # a broken harness module is its own failure
        pytest.fail(f"{path.name}: could not parse ({exc})")


def _call_name(node: ast.Call) -> str:
    """Dotted callee name, e.g. 'sqlite3.connect' or 'StateStore'."""
    f = node.func
    if isinstance(f, ast.Attribute):
        parts = [f.attr]
        cur = f.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    if isinstance(f, ast.Name):
        return f.id
    return ""


# ── Invariant A ──────────────────────────────────────────────────────────────

def test_no_raw_sqlite_connect_outside_the_guard():
    """A: every DB open must route through ct_utils — no direct sqlite3.connect."""
    offenders = []
    for path in _modules():
        if path.name in _ALLOWED_SQLITE_CONNECT:
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and _call_name(node).endswith("sqlite3.connect"):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "Raw sqlite3.connect() outside the guard — a DB opened this way bypasses "
        "assert_not_live_db entirely:\n  " + "\n  ".join(offenders) +
        "\nUse ct_utils.get_db_connection(readonly=True) for live inspection, or "
        "make_scratch_db() / get_db_connection(readonly=False) for writable work."
    )


# ── Invariant B ──────────────────────────────────────────────────────────────

def test_no_live_db_path_literal_outside_the_guard():
    """B: you cannot open what you are not allowed to name."""
    offenders = []
    for path in _modules():
        if path.name in _ALLOWED_LIVE_LITERAL:
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for lit in _LIVE_DB_LITERALS:
                    if lit in node.value:
                        offenders.append(f"{path.name}:{node.lineno} -> {node.value!r}")
    assert not offenders, (
        "LIVE database path literal in the crash-test harness — building a live path by "
        "hand sidesteps the guard:\n  " + "\n  ".join(offenders) +
        "\nUse ct_utils.LIVE_DB_PATH (read-only paths only) or the scratch helpers."
    )


# ── Invariant C ──────────────────────────────────────────────────────────────

def test_statestore_is_never_handed_a_live_path():
    """C: StateStore opens its DB writable — it must never receive the live path."""
    offenders = []
    for path in _modules():
        if path.name in _ALLOWED_LIVE_LITERAL:
            continue
        for node in ast.walk(_parse(path)):
            if not (isinstance(node, ast.Call) and _call_name(node).endswith("StateStore")):
                continue
            for kw in node.keywords:
                if kw.arg != "db_path":
                    continue
                for sub in ast.walk(kw.value):
                    if isinstance(sub, ast.Name) and sub.id in _LIVE_PATH_NAMES:
                        offenders.append(f"{path.name}:{node.lineno} -> StateStore(db_path={sub.id})")
    assert not offenders, (
        "StateStore handed a LIVE database path (it opens WRITABLE, and can migrate the "
        "schema on open):\n  " + "\n  ".join(offenders) +
        "\nUse make_scratch_db() instead."
    )


# ── the invariant must be able to FAIL (a green check is evidence only if it could be red) ──

def test_invariant_detects_a_planted_bypass(tmp_path, monkeypatch):
    """Plant a bypass in a scanned module and prove all three invariants catch it.

    Without this, the three tests above could pass vacuously — e.g. if _modules() silently
    returned nothing, or the AST walk quietly matched nothing.
    """
    bypass = tmp_path / "ct_planted_bypass.py"
    bypass.write_text(
        "import sqlite3\n"
        "from core.state_store import StateStore\n"
        "from tests.crash_test.ct_utils import LIVE_DB_PATH\n"
        "conn = sqlite3.connect('data_store/trading_system.db')\n"
        "store = StateStore(db_path=LIVE_DB_PATH)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "CT_DIR", tmp_path)

    for check in (test_no_raw_sqlite_connect_outside_the_guard,
                  test_no_live_db_path_literal_outside_the_guard,
                  test_statestore_is_never_handed_a_live_path):
        with pytest.raises(AssertionError):
            check()

    # ...and with the bypass removed, all three pass again on the same directory.
    bypass.unlink()
    test_no_raw_sqlite_connect_outside_the_guard()
    test_no_live_db_path_literal_outside_the_guard()
    test_statestore_is_never_handed_a_live_path()


def test_scan_actually_covers_the_harness():
    """Guard against a vacuous pass: the scan must see the real modules."""
    names = {p.name for p in _modules()}
    assert len(names) >= 20, f"scan covered only {len(names)} modules — is CT_DIR right?"
    assert GUARD_MODULE in names
    assert "cleanup.py" in names          # the historically dangerous one
