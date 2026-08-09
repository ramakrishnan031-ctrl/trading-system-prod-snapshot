"""tests/unit/test_n907_forward_shadow_encoding.py — register N9-07.

`scripts/forward_shadow_record.py` read two files with NO explicit encoding, so both
took the platform default. On the VM that is usually UTF-8 and nothing happens — but
THIS IS A CRON JOB, and cron environments routinely carry a minimal locale
(`LANG` unset -> C/POSIX -> ASCII), where a non-ASCII byte raises UnicodeDecodeError.

🔑 REACHABILITY IS MEASURED, NOT ASSUMED: `config/scoring_weights.yaml` contains
**51 non-ASCII bytes** of 5,013. Under a C locale the very first read raises, and the
job produces nothing for that day.

⛔ WHY THAT MATTERS MORE HERE THAN ALMOST ANYWHERE: this is OPEN-1's evidence
instrument, and its output CANNOT BE REGENERATED. A missed day is a permanent hole in
the out-of-sample record — and the standing rule is that a gap is a loss but a
manufactured day is a CORRUPTION, so the hole could never be filled afterwards.

⚠️ THE REGISTER NAMED ONE SITE (`:47`). THE FILE HAS TWO of the same class — `:47`
(scoring weights) and `:102` (the access-token JSON). Both are fixed; the second is
recorded because an absence is only established by a check wide enough to have found
it, and the row's width was one line.

⛔ THE RECORDER IS NEVER RUN BY THESE TESTS. The standing rule is absolute: never run
`forward_shadow_record.py` manually, because its output cannot be regenerated. The
changed code paths are exercised DIRECTLY (`_weights()`, and the token read), which is
strictly narrower than invoking the job and is what "prove it still runs" can honestly
mean here.

⚠️ PARITY: a CRON path, and mode-INDEPENDENT — it reads no paper/live flag. ⛔ No
paper coverage is claimed.
"""
from __future__ import annotations

import io
import json
import locale
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "scripts" / "forward_shadow_record.py"


def test_every_read_in_the_recorder_declares_an_encoding():
    """⭐ The property, not the line number: no bare read may return."""
    text = SRC.read_text(encoding="utf-8")
    offenders = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if ".read_text()" in line or ".read_text( )" in line:
            offenders.append((i, stripped))
        if "open(" in line and "encoding=" not in line and "read_bytes" not in line:
            if "def " not in line and "subprocess" not in line:
                offenders.append((i, stripped))
    assert not offenders, f"unencoded reads remain: {offenders}"


def test_the_two_specific_sites_are_fixed():
    """⚠️ The register named `:47`; the file had TWO. Both pinned by content, not
    by line number — line numbers hold only at their measured SHA."""
    text = SRC.read_text(encoding="utf-8")
    # anchor on the READ EXPRESSION, not the filename: the filename also appears in
    # a comment above it, and matching that would prove nothing about the call.
    i = text.index('"scoring_weights.yaml").read_text(')
    assert 'encoding="utf-8"' in text[i:i + 80], "the scoring-weights read must be encoded"
    j = text.index("tok_path.read_text(")
    assert 'encoding="utf-8"' in text[j:j + 60], "the token read must be encoded"


def test_read_bytes_is_left_alone():
    """⛔ `read_bytes()` in `_provenance` is CORRECT as-is — hashing must see bytes.
    A blanket sweep that 'fixed' it would corrupt the provenance stamp."""
    text = SRC.read_text(encoding="utf-8")
    assert "read_bytes()" in text
    i = text.index("read_bytes()")
    assert "encoding=" not in text[i:i + 40]


def test_the_weights_read_WORKS_and_returns_the_real_weights():
    """⭐ Exercise the changed path directly. ⛔ The recorder itself is never run —
    its output cannot be regenerated."""
    import importlib
    mod = importlib.import_module("scripts.forward_shadow_record")
    w = mod._weights()
    assert isinstance(w, dict) and w, "the weights must still load"
    assert all(isinstance(v, float) for v in w.values())


def test_the_weights_file_really_does_contain_non_ascii():
    """⭐ ANTI-VACUITY: if the YAML were pure ASCII this fix would guard nothing and
    the test above could not distinguish encoded from unencoded."""
    raw = (ROOT / "config" / "scoring_weights.yaml").read_bytes()
    assert any(b > 127 for b in raw), \
        "no non-ASCII byte: the C-locale failure mode would be unreachable"


def test_an_ascii_read_of_that_file_REALLY_DOES_raise__and_the_module_still_loads_it():
    """🔑 THE FAILURE MODE, demonstrated without patching anything global.

    Reading the same file as ASCII -- what a cron job under LANG=C would get from
    an unencoded `read_text()` -- must RAISE; and the module's own `_weights()`,
    which now names its encoding, must still succeed on it. Together those two
    halves are the whole argument, and neither could pass by accident.
    """
    import importlib
    path = ROOT / "config" / "scoring_weights.yaml"
    with pytest.raises(UnicodeDecodeError):
        path.read_text(encoding="ascii")
    mod = importlib.import_module("scripts.forward_shadow_record")
    assert mod._weights(), "the encoded read must still return the weights"


def test_the_output_path_and_method_version_are_untouched():
    """⛔ MUST-NOT-CHANGE GUARD. Bumping `_METHOD_VERSION` rolls the artifact to a
    new file; changing OUT_PATH orphans the existing evidence. An encoding fix must
    do neither."""
    import importlib
    mod = importlib.import_module("scripts.forward_shadow_record")
    assert mod._METHOD_VERSION == "fs-v1"
    assert mod.OUT_PATH.name == "forward_shadow_fs-v1.jsonl"


def test_the_recorder_is_never_invoked_by_this_suite():
    """⛔ The standing rule, pinned so a later edit cannot quietly add a run.

    AST-based, not a substring scan: a text search would match the names written
    into its own assertion and pass or fail for the wrong reason.
    """
    import ast
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    for banned in ("main", "run", "check_call", "check_output", "Popen"):
        assert banned not in called, f"this suite must never invoke the recorder: {banned}"
