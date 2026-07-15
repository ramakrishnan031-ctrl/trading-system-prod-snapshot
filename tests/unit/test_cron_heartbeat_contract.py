"""Prevention tests for the record_heartbeat signature (15-Jul-2026).

Background: Branch-B/F2 inserted a new `functional_status` param into
`record_heartbeat` (before `db_path`). `HeartbeatTimer.__exit__` forwards it on
every exit, but a pre-existing hand-written stub in another test froze the OLD
parameter list → `TypeError` only surfaced under the full combined regression.

Two guards close that class of failure permanently:

  1. SIGNATURE LOCK (test_record_heartbeat_signature_is_locked) — pins the exact
     public signature. ANY future change fails here and forces the dev to update
     BOTH this lock AND every stub (prefer create_autospec so stubs self-track).

  2. DISCOVERY GUARD (test_every_record_heartbeat_stub_is_signature_safe) —
     discovers every place in the test suite that swaps out `record_heartbeat`
     and requires a signature-enforcing stand-in (create_autospec / autospec=True)
     or an explicitly permissive `lambda *a, **k`. A narrow hand-written stub is
     rejected before it can silently drift.

SCOPE LOCK (INTENTIONAL — do not widen this cycle): the discovery guard is deliberately
limited to `record_heartbeat` patch sites and uses a simple line-pattern scan of the test
tree — NOT an AST parse, NOT a generic repository-wide signature scanner. Generalizing it
into a broad "every mock must be autospec" tool is a separate, deliberate decision for a
future cycle, not scope creep here. Keep it focused and cheap.

Test-only; no production code depends on this file.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from utils.cron_heartbeat import record_heartbeat

# ── 1. SIGNATURE LOCK ──────────────────────────────────────────────────────
# (name, kind). Update DELIBERATELY when record_heartbeat's public signature
# changes — and update every stub in the same commit (the discovery guard below
# will flag any that were missed). Order matters: `functional_status` MUST stay
# BEFORE `db_path` (the exact position whose earlier absence caused the F2 break).
_EXPECTED_PARAMS = [
    ("job_name", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ("status", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ("duration_sec", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ("message", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ("functional_status", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ("db_path", inspect.Parameter.POSITIONAL_OR_KEYWORD),
]


def test_record_heartbeat_signature_is_locked():
    params = list(inspect.signature(record_heartbeat).parameters.values())
    got = [(p.name, p.kind) for p in params]
    assert got == _EXPECTED_PARAMS, (
        "record_heartbeat's signature changed. This is a DELIBERATE-CHANGE gate: update "
        "_EXPECTED_PARAMS here AND every hand-written stub/mock (prefer create_autospec so "
        f"stubs self-track) in the SAME commit. Got: {got}")

    by = {p.name: p for p in params}
    # Back-compat invariant: every param except job_name must be OPTIONAL, so the existing
    # ~30 callers that omit them keep working; functional_status in particular defaults None.
    assert by["job_name"].default is inspect.Parameter.empty
    assert by["status"].default == "SUCCESS"
    assert by["duration_sec"].default is None
    assert by["message"].default is None
    assert by["functional_status"].default is None
    assert by["db_path"].default is not inspect.Parameter.empty  # has a real default


# ── 2. DISCOVERY GUARD ─────────────────────────────────────────────────────
_TESTS_ROOT = Path(__file__).resolve().parent.parent          # the tests/ tree
_SELF = {Path(__file__).name}                                  # this mechanism, not a stub
# a line that reassigns record_heartbeat to a stand-in (monkeypatch.setattr / mock.patch)
_PATCH_LINE = re.compile(r"(?:\.setattr|(?:mock\.)?patch)\([^\n]*record_heartbeat")
# an approved, signature-safe stand-in
_APPROVED = re.compile(r"create_autospec\(|autospec\s*=\s*True|lambda \*a, \*\*k\b|lambda \*args, \*\*kwargs\b")


def test_every_record_heartbeat_stub_is_signature_safe():
    offenders: dict[str, list[str]] = {}
    for path in _TESTS_ROOT.rglob("test_*.py"):
        if path.name in _SELF:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "record_heartbeat" not in text:
            continue
        patch_lines = [ln.strip() for ln in text.splitlines() if _PATCH_LINE.search(ln)]
        if not patch_lines:
            continue
        # Signature-safe if the file uses an approved stand-in (covers both the inline form
        # `setattr(..., create_autospec(...))` and the indirection `hb = create_autospec(...);
        # setattr(..., hb)`). A file that patches record_heartbeat with none of these is the
        # exact regression we prevent.
        if not _APPROVED.search(text):
            offenders[path.name] = patch_lines
    assert not offenders, (
        "These test files replace record_heartbeat with a stub that does NOT enforce its "
        "signature — use create_autospec(record_heartbeat) so it tracks the real signature "
        f"(see this file's docstring): {offenders}")
