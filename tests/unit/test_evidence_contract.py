"""Batch 1 — the forward evidence contract.

Two properties are under test and they must stay separate:

  * the CORPUS is complete and honest — every capture point emits, no reject path
    bypasses capture, and a record never claims to be healthier than it is; and
  * the OBSERVER cannot hurt trading — it never raises, never blocks, and its own
    failure is loud exactly once a day and counted thereafter.

⛔ NO DECISION OUTCOME MAY CHANGE. `test_recorder_off_is_byte_identical` and
`test_capture_failure_does_not_change_the_outcome` are the two that own that.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.evidence_contract import (  # noqa: E402
    COMPLETE,
    CONTRACT_VERSION,
    EVIDENCE_EPOCH,
    FAILED,
    OPTIONAL_FIELDS,
    P1_ACCEPT,
    P2_REJECT,
    P3_SCREEN,
    PARTIAL,
    REQUIRED_FIELDS,
    EvidenceRecorder,
    compute_code_fingerprint,
    evidence_path,
)

REPO = Path(__file__).resolve().parents[2]
FIXED_NOW = datetime(2026, 9, 11, 10, 30, 0)


def rec_at(tmp_path, seed=True, **over):
    """A recorder rooted at tmp_path, so writes land there and the fingerprint is
    computed over that same tree.

    `seed=True` plants one file so the fingerprint RESOLVES — an empty tree
    legitimately yields no fingerprint, which is FAILED by design and is proven
    separately by test_absent_fingerprint_makes_the_record_FAILED.
    """
    if seed:
        pkg = tmp_path / "core"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "seed.py").write_text("SEED = 1" + chr(10), encoding="utf-8")
    kw = dict(
        root=tmp_path,
        config_hashes={"system_config.yaml": "cafe"},
        arm_fn=lambda: "TESTARM",
        now_fn=lambda: FIXED_NOW,
    )
    kw.update(over)
    root = kw.pop("root")
    return EvidenceRecorder(root, **kw)


def read_records(tmp_path, arm="TESTARM", on=None):
    p = evidence_path(tmp_path, arm, on or FIXED_NOW.date())
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ═════════════════════════════════════════════════════════════════════════════
# The three capture points emit
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("point", [P1_ACCEPT, P2_REJECT, P3_SCREEN])
def test_each_capture_point_emits_a_record(tmp_path, point):
    r = rec_at(tmp_path)
    r.capture(point, {"signal_id": "sig_1", "symbol": "ACME"})
    rows = read_records(tmp_path)
    assert len(rows) == 1
    assert rows[0]["capture_point"] == point
    assert rows[0]["signal_id"] == "sig_1"


def test_records_are_append_only(tmp_path):
    r = rec_at(tmp_path)
    for i in range(5):
        r.capture(P3_SCREEN, {"signal_id": f"sig_{i}"})
    rows = read_records(tmp_path)
    assert [x["signal_id"] for x in rows] == [f"sig_{i}" for i in range(5)]


# ═════════════════════════════════════════════════════════════════════════════
# §6.2 — record_status, and the narrowness of PARTIAL
# ═════════════════════════════════════════════════════════════════════════════

def _full_payload():
    return {f: (f + "_v") for f in OPTIONAL_FIELDS}


def test_full_payload_is_COMPLETE(tmp_path):
    r = rec_at(tmp_path)
    p = _full_payload(); p["signal_id"] = "sig_1"
    r.capture(P1_ACCEPT, p)
    assert read_records(tmp_path)[0]["record_status"] == COMPLETE


def test_missing_optional_context_is_PARTIAL_not_FAILED(tmp_path):
    """Every IDENTITY field present (P1 requires symbol + strategy as well as the
    base set); only optional CONTEXT absent."""
    r = rec_at(tmp_path)
    r.capture(P1_ACCEPT, {"signal_id": "sig_1", "symbol": "ACME", "strategy": "gap_fade"})
    row = read_records(tmp_path)[0]
    assert row["record_status"] == PARTIAL
    assert "tgt_price" in row["missing_optional"]
    assert "symbol" not in row["missing_optional"], \
        "a per-point identity field must never be double-counted as optional"


def test_missing_identity_is_FAILED_and_never_hidden_behind_PARTIAL(tmp_path):
    """⚠️ The whole point of §6.2: a missing IDENTITY field must not be able to
    present as PARTIAL, which would make the corpus look healthier than it is."""
    r = rec_at(tmp_path)
    p = _full_payload()          # every optional field present...
    p.pop("signal_id", None)     # ...but the identity is gone
    r.capture(P1_ACCEPT, p)
    row = read_records(tmp_path)[0]
    assert row["record_status"] == FAILED
    assert row["record_status"] != PARTIAL
    assert "signal_id" in row["missing_required"]


def test_unresolvable_arm_is_FAILED_and_no_default_is_invented(tmp_path):
    """§6.6 — if the arm cannot resolve, ⛔ do not invent one."""
    def boom():
        raise RuntimeError("accounts.csv unreadable")
    r = rec_at(tmp_path, arm_fn=boom)
    r.capture(P1_ACCEPT, {"signal_id": "sig_1"})
    rows = read_records(tmp_path, arm="UNKNOWN_ARM")
    assert rows and rows[0]["record_status"] == FAILED
    assert "arm" in rows[0]["missing_required"]
    assert rows[0]["arm"] is None, "an invented arm would silently merge two corpora"


def test_missing_config_hashes_is_FAILED(tmp_path):
    r = rec_at(tmp_path, config_hashes=None)
    r.capture(P1_ACCEPT, {"signal_id": "sig_1"})
    row = read_records(tmp_path)[0]
    assert row["record_status"] == FAILED
    assert "config_hashes" in row["missing_required"]


def test_a_FAILED_record_is_still_written(tmp_path):
    """The corpus must show its own holes; a hole that writes nothing is invisible."""
    r = rec_at(tmp_path)
    r.capture(P1_ACCEPT, {})
    assert len(read_records(tmp_path)) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Identity, epoch, config hashes
# ═════════════════════════════════════════════════════════════════════════════

def test_contract_version_and_evidence_epoch_are_present(tmp_path):
    r = rec_at(tmp_path)
    r.capture(P1_ACCEPT, {"signal_id": "sig_1"})
    row = read_records(tmp_path)[0]
    assert row["contract_version"] == CONTRACT_VERSION
    assert row["evidence_epoch"] == EVIDENCE_EPOCH


def test_evidence_epoch_is_not_a_timestamp_alias(tmp_path):
    """§6.2 — an explicit generation marker, ⛔ not a date and ⛔ not 'today'."""
    r = rec_at(tmp_path)
    r.capture(P1_ACCEPT, {"signal_id": "sig_1"})
    row = read_records(tmp_path)[0]
    ep = row["evidence_epoch"]
    assert ep not in (row["ts"], row["captured_at"])
    assert FIXED_NOW.date().isoformat() not in str(ep)
    assert str(ep).isdigit() is False       # not a bare timestamp/serial


def test_config_hashes_are_the_ones_supplied(tmp_path):
    """§6.5 — reuse AppConfig.file_hashes; ⛔ no second scheme."""
    r = rec_at(tmp_path, config_hashes={"a.yaml": "h1", "b.yaml": "h2"})
    r.capture(P1_ACCEPT, {"signal_id": "sig_1"})
    assert read_records(tmp_path)[0]["config_hashes"] == {"a.yaml": "h1", "b.yaml": "h2"}


# ═════════════════════════════════════════════════════════════════════════════
# §6.4 — the fingerprint must not be able to lie
# ═════════════════════════════════════════════════════════════════════════════

def test_fingerprint_is_computed_from_deployed_bytes_not_a_git_ref():
    fp, n = compute_code_fingerprint(REPO)
    assert fp and len(fp) == 64 and n > 50
    src = (REPO / "core" / "evidence_contract.py").read_text(encoding="utf-8")
    for banned in ("rev-parse", "git_dir", "--git-dir", "subprocess"):
        assert banned not in src, f"the fingerprint must never consult git ({banned})"


def test_fingerprint_changes_when_a_deployed_file_changes(tmp_path):
    pkg = tmp_path / "core"; pkg.mkdir()
    f = pkg / "x.py"; f.write_text("A = 1\n", encoding="utf-8")
    before, n1 = compute_code_fingerprint(tmp_path)
    f.write_text("A = 2\n", encoding="utf-8")
    after, n2 = compute_code_fingerprint(tmp_path)
    assert before and after and before != after and n1 == n2 == 1


def test_fingerprint_is_stable_for_unchanged_bytes(tmp_path):
    pkg = tmp_path / "core"; pkg.mkdir()
    (pkg / "x.py").write_text("A = 1\n", encoding="utf-8")
    assert compute_code_fingerprint(tmp_path) == compute_code_fingerprint(tmp_path)


def test_fingerprint_ignores_pyc_and_non_python(tmp_path):
    pkg = tmp_path / "core"; pkg.mkdir()
    (pkg / "x.py").write_text("A = 1\n", encoding="utf-8")
    base, n = compute_code_fingerprint(tmp_path)
    cache = pkg / "__pycache__"; cache.mkdir()
    (cache / "x.cpython-311.pyc").write_bytes(b"\x00\x01volatile")
    (pkg / "notes.txt").write_text("irrelevant", encoding="utf-8")
    after, n2 = compute_code_fingerprint(tmp_path)
    assert (base, n) == (after, n2), "a .pyc or a .txt must not move the fingerprint"


def test_fingerprint_distinguishes_a_rename_from_an_edit(tmp_path):
    """path and length are hashed alongside content, so a rename cannot collide."""
    pkg = tmp_path / "core"; pkg.mkdir()
    (pkg / "a.py").write_text("X = 1\n", encoding="utf-8")
    one, _ = compute_code_fingerprint(tmp_path)
    (pkg / "a.py").unlink()
    (pkg / "b.py").write_text("X = 1\n", encoding="utf-8")
    two, _ = compute_code_fingerprint(tmp_path)
    assert one != two


def test_absent_fingerprint_makes_the_record_FAILED(tmp_path):
    """An empty tree yields no fingerprint -> FAILED, ⛔ never an approximation."""
    r = rec_at(tmp_path, seed=False)      # a genuinely empty tree
    assert r.code_fingerprint is None
    r.capture(P1_ACCEPT, {"signal_id": "sig_1"})
    row = read_records(tmp_path)[0]
    assert row["record_status"] == FAILED
    assert "code_fingerprint" in row["missing_required"]


# ═════════════════════════════════════════════════════════════════════════════
# §6.9 — the arm is in the FILENAME, not only the field
# ═════════════════════════════════════════════════════════════════════════════

def test_filename_carries_the_arm_and_the_date():
    p = evidence_path(Path("/r"), "LFL836", date(2026, 9, 11))
    assert p.name == "signal_evidence_LFL836_2026-09-11.jsonl"
    assert p.parent.as_posix().endswith("data_store/evidence")


def test_two_arms_never_share_a_file(tmp_path):
    """The FIELD protects the analysis; the FILENAME protects the file. This
    project has already copied one machine's disk onto another."""
    rec_at(tmp_path, arm_fn=lambda: "LFL836").capture(P1_ACCEPT, {"signal_id": "a"})
    rec_at(tmp_path, arm_fn=lambda: "VBB097").capture(P1_ACCEPT, {"signal_id": "b"})
    d = tmp_path / "data_store" / "evidence"
    names = sorted(p.name for p in d.glob("signal_evidence_*.jsonl"))
    assert names == ["signal_evidence_LFL836_2026-09-11.jsonl",
                     "signal_evidence_VBB097_2026-09-11.jsonl"]
    # Both captures are FAILED (P1 requires symbol + strategy), so each arm also
    # has a §6.7 failure ledger -- and those must be arm-separated too.
    ledgers = sorted(p.name for p in d.glob("evidence_failures_*.jsonl"))
    assert ledgers == ["evidence_failures_LFL836_2026-09-11.jsonl",
                       "evidence_failures_VBB097_2026-09-11.jsonl"]


# ═════════════════════════════════════════════════════════════════════════════
# §6.7 — the observer never hurts trading; loud once, then counted
# ═════════════════════════════════════════════════════════════════════════════

class BoomRecorder(EvidenceRecorder):
    def _capture_inner(self, capture_point, payload):
        raise RuntimeError("disk on fire")


def test_capture_failure_never_raises(tmp_path):
    r = BoomRecorder(tmp_path, config_hashes={"a": "b"},
                     arm_fn=lambda: "A", now_fn=lambda: FIXED_NOW)
    r.capture(P1_ACCEPT, {"signal_id": "s"})     # must not raise
    assert r.failure_summary()["total_failures"] == 1


def test_first_failure_alerts_once_then_counted(tmp_path):
    alerts = []
    r = BoomRecorder(tmp_path, config_hashes={"a": "b"}, arm_fn=lambda: "A",
                     now_fn=lambda: FIXED_NOW,
                     critical_sink=lambda s, d: alerts.append((s, d)))
    for _ in range(50):
        r.capture(P2_REJECT, {"signal_id": "s"})
    assert len(alerts) == 1, "50 failures must not become 50 alerts"
    assert alerts[0][0] == "EVIDENCE_CAPTURE_FAILED"
    s = r.failure_summary()
    assert s["total_failures"] == 50
    assert s["capture_points"] == {P2_REJECT: 50}
    assert s["failure_classes"] == {"RuntimeError": 50}
    assert s["first_ts"] and s["last_ts"]


def test_failure_state_resets_at_the_day_boundary(tmp_path):
    alerts = []
    now = {"t": datetime(2026, 9, 11, 10, 0, 0)}
    r = BoomRecorder(tmp_path, config_hashes={"a": "b"}, arm_fn=lambda: "A",
                     now_fn=lambda: now["t"],
                     critical_sink=lambda s, d: alerts.append(s))
    r.capture(P1_ACCEPT, {"signal_id": "s"})
    r.capture(P1_ACCEPT, {"signal_id": "s"})
    assert len(alerts) == 1
    now["t"] = datetime(2026, 9, 12, 10, 0, 0)      # next day
    r.capture(P1_ACCEPT, {"signal_id": "s"})
    assert len(alerts) == 2, "a new day must be loud again"
    assert r.failure_summary()["total_failures"] == 1


def test_a_broken_critical_sink_still_does_not_raise(tmp_path):
    def bad_sink(s, d):
        raise RuntimeError("telegram down")
    r = BoomRecorder(tmp_path, config_hashes={"a": "b"}, arm_fn=lambda: "A",
                     now_fn=lambda: FIXED_NOW, critical_sink=bad_sink)
    r.capture(P1_ACCEPT, {"signal_id": "s"})       # must not raise


# ═════════════════════════════════════════════════════════════════════════════
# ⛔ NO DECISION OUTCOME CHANGES
# ═════════════════════════════════════════════════════════════════════════════

def test_recorder_off_is_byte_identical(tmp_path):
    """evidence=None must mean the code path is not entered at all."""
    from signals.signal_processor import SignalProcessor
    sp = SignalProcessor.__new__(SignalProcessor)
    sp._evidence = None
    sp._log = None                       # would explode if the helper touched it
    sp._evidence_capture(P1_ACCEPT, {"signal_id": "s"})   # returns immediately


@pytest.mark.parametrize("cls_path,helper", [
    ("signals.signal_processor:SignalProcessor", "_evidence_capture"),
    ("screening.secondary_screener:SecondaryScreener", "_evidence_capture"),
])
def test_missing_evidence_attribute_is_inert(cls_path, helper):
    """⚠️ REGRESSION. The differential gate caught this: reading `self._evidence`
    OUTSIDE the try raised AttributeError straight through a reject path on an
    object built without __init__ — three sr_v2 tests went red because the
    OBSERVER CHANGED A DECISION OUTCOME. Nothing may live outside the guard."""
    import importlib
    mod_name, cls_name = cls_path.split(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    obj = cls.__new__(cls)               # no __init__, so no _evidence at all
    assert not hasattr(obj, "_evidence")
    getattr(obj, helper)(P2_REJECT, {"signal_id": "s"})   # must be silent


def test_a_payload_that_raises_cannot_escape():
    """The payload is a CALLABLE so it is built inside the guard. Evaluated at the
    call site, an unbound name would bypass the try entirely."""
    from signals.signal_processor import SignalProcessor

    class Log:
        def __init__(self): self.errors = []
        def error(self, *a, **k): self.errors.append(a)

    sp = SignalProcessor.__new__(SignalProcessor)
    sp._evidence = object()              # never reached
    sp._log = Log()

    def exploding_payload():
        raise NameError("symbol is not defined")

    sp._evidence_capture(P2_REJECT, exploding_payload)    # must not raise
    assert sp._log.errors


def test_capture_sites_pass_lazy_payloads():
    """Structural: every call site must hand over a callable, not a built dict."""
    sp = (REPO / "signals" / "signal_processor.py").read_text(encoding="utf-8")
    ss = (REPO / "screening" / "secondary_screener.py").read_text(encoding="utf-8")
    for name, txt in (("signal_processor", sp), ("secondary_screener", ss)):
        calls = txt.count("self._evidence_capture(\"")
        lazy = txt.count("self._evidence_capture(\"P1_ACCEPT\", lambda:") \
            + txt.count("self._evidence_capture(\"P2_REJECT\", lambda:") \
            + txt.count("self._evidence_capture(\"P3_SCREEN\", lambda:")
        assert calls == lazy, f"{name}: {calls - lazy} call site(s) build the payload eagerly"


def test_capture_failure_does_not_change_the_outcome(tmp_path):
    """The helper swallows even a recorder that raises — trading is untouched."""
    from signals.signal_processor import SignalProcessor

    class Boom:
        def capture(self, *a, **k):
            raise RuntimeError("boom")

    class Log:
        def __init__(self): self.errors = []
        def error(self, *a, **k): self.errors.append(a)

    sp = SignalProcessor.__new__(SignalProcessor)
    sp._evidence = Boom()
    sp._log = Log()
    sp._evidence_capture(P2_REJECT, {"signal_id": "s"})   # must not raise
    assert sp._log.errors, "a swallowed failure must still be logged"


# ═════════════════════════════════════════════════════════════════════════════
# §6.1 — THE INVARIANT: no rejected path may silently bypass capture
# ═════════════════════════════════════════════════════════════════════════════

def test_no_reject_path_bypasses_capture():
    """Structural, not a comment. Every `update_signal_status(..., REJECTED_...)`
    in signal_processor must be followed closely by a P2_REJECT capture.

    ⚠️ This is the test that would have caught the original plan. Capturing only
    at the `_process_one` handler would have left admit_prepared, the price gate,
    the retest resume and the allocator pre-check silently uncaptured.
    """
    src = (REPO / "signals" / "signal_processor.py").read_text(encoding="utf-8").splitlines()
    rejects = [i for i, l in enumerate(src)
               if "update_signal_status(" in l and "REJECTED_" in "".join(src[i:i + 3])]
    captures = [i for i, l in enumerate(src) if '_evidence_capture("P2_REJECT"' in l]
    assert rejects, "the scan found no reject sites at all — the test has gone blind"
    uncaptured = [i + 1 for i in rejects
                  if not any(0 < c - i <= 8 for c in captures)]
    assert uncaptured == [], f"reject paths with no evidence capture: {uncaptured}"
    assert len(captures) >= len(rejects)


def test_all_four_terminal_pipeline_reject_handlers_are_covered():
    """The four TERMINAL `except _PipelineReject` handlers each write a rejected
    status; two further handlers only re-raise and must NOT be counted."""
    src = (REPO / "signals" / "signal_processor.py").read_text(encoding="utf-8").splitlines()
    handlers = [i for i, l in enumerate(src) if "except _PipelineReject" in l]
    terminal = [i for i in handlers
                if any("update_signal_status(" in src[j] for j in range(i, min(i + 12, len(src))))]
    assert len(handlers) == 6, f"handler count changed: {len(handlers)}"
    assert len(terminal) == 4, f"terminal handler count changed: {len(terminal)}"
    for i in terminal:
        window = "".join(src[i:i + 14])
        assert '_evidence_capture("P2_REJECT"' in window, \
            f"terminal handler at line {i+1} has no capture"


def test_p1_and_p3_capture_sites_exist():
    sp = (REPO / "signals" / "signal_processor.py").read_text(encoding="utf-8")
    ss = (REPO / "screening" / "secondary_screener.py").read_text(encoding="utf-8")
    # three placement paths, three P1 captures -- the gate and retest resumes are
    # dormant today; see test_every_placement_dispatch_emits_p1
    assert sp.count('_evidence_capture("P1_ACCEPT"') == 3
    assert ss.count('_evidence_capture("P3_SCREEN"') == 1


def test_step_statuses_is_captured_at_p3():
    """§6.3 — populated in memory, persisted nowhere else. Written from the LIVE
    value; ⛔ never reconstructed."""
    ss = (REPO / "screening" / "secondary_screener.py").read_text(encoding="utf-8")
    i = ss.index('_evidence_capture("P3_SCREEN"')
    assert '"step_statuses": result.step_statuses' in ss[i:i + 1400]
    store = (REPO / "core" / "state_store.py").read_text(encoding="utf-8")
    assert "step_statuses" not in store, \
        "if state_store now persists it, this capture may be redundant — re-check"


# ═════════════════════════════════════════════════════════════════════════════
# §6.8 — retention exclusion and the backup set
# ═════════════════════════════════════════════════════════════════════════════

def test_evidence_is_excluded_from_output_retention():
    txt = (REPO / "scripts" / "output_retention.py").read_text(encoding="utf-8")
    i = txt.index("OUT OF SCOPE ON PURPOSE")
    block = txt[i:i + 900]
    assert "data_store/evidence/*.jsonl" in block
    assert "Never delete" in block


def test_evidence_enters_the_backup_set(tmp_path):
    """Excluded-from-pruning is NOT backed-up. This proves the copy happens."""
    from scripts.backup_evidence import backup_once
    src = tmp_path / "data_store" / "evidence"
    src.mkdir(parents=True)
    f = src / "signal_evidence_LFL836_2026-09-11.jsonl"
    f.write_text('{"signal_id":"a"}\n', encoding="utf-8")
    dst = tmp_path / "data_store" / "backups" / "evidence"

    copied, skipped, failures = backup_once(src, dst)
    assert (copied, skipped, failures) == (1, 0, [])
    assert (dst / f.name).read_text(encoding="utf-8") == f.read_text(encoding="utf-8")
    assert (dst / f.name).name == f.name, "the ARM and DATE must survive into the backup"

    copied2, skipped2, _ = backup_once(src, dst)     # idempotent
    assert (copied2, skipped2) == (0, 1)


def test_backup_never_deletes_or_truncates_the_source(tmp_path):
    from scripts.backup_evidence import backup_once
    src = tmp_path / "data_store" / "evidence"; src.mkdir(parents=True)
    f = src / "signal_evidence_A_2026-09-11.jsonl"
    payload = '{"x":1}\n{"x":2}\n'
    f.write_text(payload, encoding="utf-8")
    backup_once(src, tmp_path / "bk")
    assert f.exists() and f.read_text(encoding="utf-8") == payload


# ═════════════════════════════════════════════════════════════════════════════
# §6.10 — no lookahead, no backfill
# ═════════════════════════════════════════════════════════════════════════════

def test_only_what_the_caller_supplied_is_recorded(tmp_path):
    """⛔ The recorder must not enrich, derive or look anything up."""
    r = rec_at(tmp_path)
    r.capture(P3_SCREEN, {"signal_id": "sig_1", "score_total": 61})
    row = read_records(tmp_path)[0]
    assert row["score_total"] == 61
    assert row["tier"] is None, "an unsupplied field must stay None, never be derived"
    assert row["market_data_snapshot"] is None


def test_ts_defaults_to_capture_time_but_never_overwrites_the_decision_time(tmp_path):
    r = rec_at(tmp_path)
    r.capture(P3_SCREEN, {"signal_id": "a", "ts": "2026-09-11T09:15:00+05:30"})
    r.capture(P3_SCREEN, {"signal_id": "b"})
    rows = read_records(tmp_path)
    assert rows[0]["ts"] == "2026-09-11T09:15:00+05:30"
    assert rows[0]["captured_at"] == FIXED_NOW.isoformat()
    assert rows[1]["ts"] == rows[1]["captured_at"]


# ═════════════════════════════════════════════════════════════════════════════
# §2 — THE ARTEFACT MANIFEST IS FROZEN
# ═════════════════════════════════════════════════════════════════════════════

MANIFEST = REPO / "core" / "evidence_artefact_manifest.txt"


def _enumerate_artefacts():
    from core.evidence_contract import (_FINGERPRINT_EXTRA_FILES,
                                        _FINGERPRINT_PACKAGES)
    paths = []
    for pkg in _FINGERPRINT_PACKAGES:
        d = REPO / pkg
        if d.is_dir():
            paths += [p for p in d.rglob("*.py") if "__pycache__" not in p.parts]
    for e in _FINGERPRINT_EXTRA_FILES:
        if (REPO / e).is_file():
            paths.append(REPO / e)
    return sorted(p.relative_to(REPO).as_posix() for p in paths)


def test_artefact_manifest_matches_the_enumeration():
    """⭐ THE FREEZE. code_fingerprint is only meaningful if WHAT it hashes is
    fixed. If the import closure changes, this fails loudly and the manifest must
    be updated deliberately — a visible diff, never a silent drift in what the
    fingerprint MEANS.

    ⚠️ A previous report carried two counts, 109 and 110. Both were correct for
    their tree: 109 predated core/evidence_contract.py, which lives in core/ and
    is therefore inside the set. This test exists so that can never be ambiguous
    again.
    """
    assert MANIFEST.exists(), "the frozen manifest is missing"
    frozen = [l for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]
    live = _enumerate_artefacts()
    added = sorted(set(live) - set(frozen))
    removed = sorted(set(frozen) - set(live))
    assert not added and not removed, (
        f"the fingerprint artefact set CHANGED.\n  added:   {added}\n  removed: {removed}\n"
        f"If this is intended, regenerate core/evidence_artefact_manifest.txt and say so "
        f"in the commit — code_fingerprint now means something different."
    )
    assert frozen == live, "manifest ordering drifted from the enumeration's sort"


def test_manifest_count_and_hash_are_pinned():
    frozen = [l for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]
    listing = "\n".join(frozen) + "\n"
    import hashlib
    assert len(frozen) == 110, f"artefact count is {len(frozen)}, pinned at 110"
    assert hashlib.sha256(listing.encode()).hexdigest() == (
        "ce6b7654116a2faeab6f82c32b66b4a117ab7c93d3bd370dbe13df8adaf357c1"
    ), "the manifest LIST hash changed"


def test_manifest_itself_is_not_in_the_artefact_set():
    """It is a .txt, so the .py-only rule excludes it — no chicken-and-egg where
    updating the manifest changes the fingerprint that the manifest describes."""
    assert "core/evidence_artefact_manifest.txt" not in _enumerate_artefacts()


# ═════════════════════════════════════════════════════════════════════════════
# §3 — P3 IDENTITY IS MANDATORY, AND ITS ABSENCE IS LOUD
# ═════════════════════════════════════════════════════════════════════════════

def test_p3_identity_fields_are_required_not_optional(tmp_path):
    from core.evidence_contract import REQUIRED_BY_CAPTURE_POINT
    assert REQUIRED_BY_CAPTURE_POINT[P3_SCREEN] == ("symbol", "strategy")
    assert REQUIRED_BY_CAPTURE_POINT[P1_ACCEPT] == ("symbol", "strategy")


@pytest.mark.parametrize("missing", ["symbol", "strategy"])
def test_p3_missing_identity_is_FAILED_and_fires_the_sentinel(tmp_path, missing):
    """§3.2/§3.3 — a missing identity field at P3 must be a PROVENANCE FAILURE
    that reaches the sentinel, ⛔ not a PARTIAL that sits where nobody looks."""
    alerts = []
    r = rec_at(tmp_path, critical_sink=lambda s, d: alerts.append((s, d)))
    payload = {"signal_id": "sig_1", "symbol": "ACME", "strategy": "gap_fade"}
    payload[missing] = None
    r.capture(P3_SCREEN, payload)

    row = read_records(tmp_path)[0]
    assert row["record_status"] == FAILED
    assert row["record_status"] != PARTIAL
    assert missing in row["missing_required"]
    assert len(alerts) == 1, "a provenance failure must reach the sentinel"
    assert alerts[0][0] == "EVIDENCE_CAPTURE_FAILED"
    assert r.failure_summary()["capture_points"] == {P3_SCREEN: 1}


def test_p3_with_identity_present_is_not_FAILED(tmp_path):
    r = rec_at(tmp_path)
    r.capture(P3_SCREEN, {"signal_id": "s", "symbol": "ACME", "strategy": "gap_fade"})
    assert read_records(tmp_path)[0]["record_status"] != FAILED


def test_p3_call_sites_supply_identity_from_the_enclosing_scope():
    """§3.1 traced structurally: all 15 _persist call sites pass symbol and the
    strategy name, and both enclosing methods bind them."""
    ss = (REPO / "screening" / "secondary_screener.py").read_text(encoding="utf-8")
    assert ss.count("self._persist(signal_id, result, symbol=symbol,"
                    ' strategy_name=getattr(strategy, "name", None)') == 15
    i = ss.index('_evidence_capture("P3_SCREEN"')
    block = ss[i:i + 1600]
    assert '"symbol": symbol,' in block
    assert '"strategy": strategy_name,' in block
    assert '"symbol": None' not in block, "the silent-None placeholder must be gone"


def test_p2_is_documented_as_not_yet_tightened():
    """⚠️ P2 is deliberately NOT in REQUIRED_BY_CAPTURE_POINT yet — two of its five
    sites read getattr(candidate, "symbol", None). The reason must stay written
    down, or a future reader will think it was forgotten."""
    from core.evidence_contract import REQUIRED_BY_CAPTURE_POINT
    assert P2_REJECT not in REQUIRED_BY_CAPTURE_POINT
    src = (REPO / "core" / "evidence_contract.py").read_text(encoding="utf-8")
    assert "P2_REJECT is deliberately NOT here yet" in src


# ═════════════════════════════════════════════════════════════════════════════
# RE-CHECK 10-Sep night — §6.7's sentinel must reach the REAL alert officer
# ═════════════════════════════════════════════════════════════════════════════

def test_the_critical_sink_writes_a_real_sentinel_through_the_real_notifier(tmp_path):
    """⚠️ REGRESSION. The first build wired `critical_sink` as a lambda calling
    `notifier.send(severity=, title=, body=)` -- WITHOUT the required positional
    `source_module`. Every call raised TypeError, which the recorder swallows by
    design, so the §6.7 sentinel could never fire in production while every test
    (all of them used a fake sink) stayed green. This one uses the REAL
    TelegramNotifier -- paper mode writes the sentinel and sends no HTTP."""
    from alerts.telegram_notifier import TelegramNotifier
    from core.evidence_contract import notifier_critical_sink

    sent = tmp_path / "sentinels"
    notifier = TelegramNotifier(
        bot_token="test-token", chat_ids=["1"],
        failed_alerts_log_path=tmp_path / "failed_alerts.log",
        sentinel_dir=sent, paper_mode=True, send_in_paper_mode=False,
    )
    r = rec_at(tmp_path, critical_sink=notifier_critical_sink(notifier))
    r.capture(P3_SCREEN, {"signal_id": "sig_1", "symbol": None, "strategy": "gap_fade"})

    flags = sorted(sent.glob("critical_alert_*.flag"))
    assert len(flags) == 1, "the first failure of the day must leave ONE real sentinel"
    body = json.loads(flags[0].read_text(encoding="utf-8"))
    assert body["source_module"] == "evidence_contract"
    assert "EVIDENCE_CAPTURE_FAILED" in body["title"]

    # ...and exactly once: the second failure is counted, never alerted.
    r.capture(P3_SCREEN, {"signal_id": "sig_2", "symbol": None, "strategy": "gap_fade"})
    assert len(sorted(sent.glob("critical_alert_*.flag"))) == 1
    assert r.failure_summary()["total_failures"] == 2


def test_main_builds_the_recorder_guarded_and_with_the_real_sink():
    """main.py must (1) bind the sink through notifier_critical_sink -- never a
    hand-rolled lambda that can drift from TelegramNotifier.send's signature -- and
    (2) build the recorder INSIDE a try, so an observer that cannot be constructed
    can never stop the boot (§6.7: trading is never blocked)."""
    import ast
    tree = ast.parse((REPO / "main.py").read_text(encoding="utf-8"))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "EvidenceRecorder"]
    assert len(calls) == 1, f"expected ONE EvidenceRecorder(...) in main.py, found {len(calls)}"
    sink = {k.arg: k.value for k in calls[0].keywords}.get("critical_sink")
    assert isinstance(sink, ast.Call) and getattr(sink.func, "id", None) == "notifier_critical_sink", \
        "critical_sink must be notifier_critical_sink(notifier), not a hand-rolled lambda"
    node, guarded = calls[0], False
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            break
        if isinstance(node, ast.Try):
            guarded = True
            break
    assert guarded, "EvidenceRecorder(...) must be constructed inside a try in _main_locked"


# ═════════════════════════════════════════════════════════════════════════════
# RE-CHECK — §6.7: every observer failure is counted, alerted once, PERSISTED
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("cls_path,log_attr", [
    ("signals.signal_processor:SignalProcessor", "_log"),
    ("screening.secondary_screener:SecondaryScreener", "_logger"),
])
def test_a_payload_that_cannot_be_built_is_counted_alerted_and_leaves_a_hole(
        tmp_path, cls_path, log_attr):
    """⚠️ RE-CHECK FINDING. A payload that raised while being BUILT (an unbound
    name, a bad attribute) was swallowed by the caller's guard with ONE log line:
    never counted, never alerted, no row -- exactly the silent loss §6.7 exists to
    prevent. Now: counted, first-of-day sentinel, persisted, and a FAILED row."""
    import importlib
    mod_name, cls_name = cls_path.split(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    alerts = []
    r = rec_at(tmp_path, critical_sink=lambda s, d: alerts.append(s))

    class Log:
        def __init__(self): self.errors = []
        def error(self, *a, **k): self.errors.append(a)

    obj = cls.__new__(cls)
    obj._evidence = r
    setattr(obj, log_attr, Log())

    def payload():
        raise NameError("name 'strategy_name' is not defined")

    obj._evidence_capture(P1_ACCEPT, payload)          # must not raise
    s = r.failure_summary()
    assert s["total_failures"] == 1 and s["failure_classes"] == {"NameError": 1}
    assert alerts == ["EVIDENCE_CAPTURE_FAILED"], "a payload failure must reach the sentinel"
    rows = read_records(tmp_path)
    assert len(rows) == 1 and rows[0]["record_status"] == FAILED
    assert rows[0]["capture_point"] == P1_ACCEPT
    assert rows[0]["observer_error"].startswith("NameError")
    assert getattr(obj, log_attr).errors, "and it is still logged"


def test_the_failure_ledger_is_persisted_one_line_per_event(tmp_path):
    """§6.7 says PERSIST total failures, first and last timestamps, capture points
    and failure class. The first build kept them in memory only -- gone at the
    ~17:35 self-exit. One append-only line per event makes each one derivable."""
    from core.evidence_contract import failure_log_path
    r = BoomRecorder(tmp_path, config_hashes={"a": "b"}, arm_fn=lambda: "A",
                     now_fn=lambda: FIXED_NOW)
    for point, sid in ((P1_ACCEPT, "s1"), (P2_REJECT, "s2"), (P2_REJECT, "s3")):
        r.capture(point, {"signal_id": sid})
    p = failure_log_path(tmp_path, "A", FIXED_NOW.date())
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
    assert [l["total_today"] for l in lines] == [1, 2, 3]
    assert [l["first_of_day"] for l in lines] == [True, False, False]
    assert [l["capture_point"] for l in lines] == [P1_ACCEPT, P2_REJECT, P2_REJECT]
    assert {l["failure_class"] for l in lines} == {"RuntimeError"}
    assert {l["arm"] for l in lines} == {"A"}
    assert lines[0]["ts"] == FIXED_NOW.isoformat() and lines[-1]["ts"]


def test_a_failure_ledger_that_cannot_be_written_still_does_not_raise(tmp_path):
    """If the disk is the failure, the ledger write fails too -- and must be
    swallowed; the sentinel is the channel that remains."""
    (tmp_path / "data_store").write_text("a FILE where the directory should be",
                                         encoding="utf-8")
    alerts = []
    r = BoomRecorder(tmp_path, config_hashes={"a": "b"}, arm_fn=lambda: "A",
                     now_fn=lambda: FIXED_NOW, critical_sink=lambda s, d: alerts.append(s))
    r.capture(P1_ACCEPT, {"signal_id": "s"})          # must not raise
    assert r.failure_summary()["total_failures"] == 1
    assert alerts == ["EVIDENCE_CAPTURE_FAILED"]


# ═════════════════════════════════════════════════════════════════════════════
# RE-CHECK — P3 must not depend on the DB write succeeding
# ═════════════════════════════════════════════════════════════════════════════

def test_p3_is_captured_even_when_the_db_write_fails(tmp_path):
    """⚠️ RE-CHECK FINDING. P3 was captured AFTER the two DB writes and INSIDE
    their try, so a locked DB or a full disk silently took the evidence record
    with it -- the very swallowed-write loss (§8) the contract exists to end."""
    from screening.secondary_screener import ScreeningResult, SecondaryScreener

    class Store:
        def update_signal_status(self, *a, **k):
            raise RuntimeError("database is locked")

        def insert_screener_result(self, *a, **k):
            raise AssertionError("unreachable once the first write has failed")

    class Log:
        def __init__(self): self.errors = []
        def error(self, *a, **k): self.errors.append(a)

    class Fx:
        def inc(self): pass

    ss = SecondaryScreener.__new__(SecondaryScreener)
    ss._fx_verdict, ss._state_store, ss._logger = Fx(), Store(), Log()
    ss._evidence = rec_at(tmp_path)
    res = ScreeningResult(
        passed=False, status="REJECTED_SCORE_57", score=57, tier="LOW",
        rejected_step=None, step_results={"volume_surge": 1.8},
        step_statuses={"volume_surge": "PASSED"}, error_steps=[],
        latencies_ms={}, market_data_snapshot={"ltp": 100.0})
    ss._persist("sig_1", res, symbol="ACME", strategy_name="gap_go_long")

    rows = read_records(tmp_path)
    assert len(rows) == 1, "a failed DB write must not take the evidence record with it"
    assert rows[0]["capture_point"] == P3_SCREEN
    assert (rows[0]["status"], rows[0]["symbol"], rows[0]["strategy"]) == \
        ("REJECTED_SCORE_57", "ACME", "gap_go_long")
    assert rows[0]["step_statuses"] == {"volume_surge": "PASSED"}
    assert ss._logger.errors, "the DB failure itself is still logged"


# ═════════════════════════════════════════════════════════════════════════════
# RE-CHECK — `reanchored` must come from the re-anchor itself
# ═════════════════════════════════════════════════════════════════════════════

def _drive_to_p1(tmp_path, *, strategy, quote_prices, stale=2500.0):
    """ONE webhook signal through the REAL _process_one (the FIX-067 harness) with
    a real recorder attached. Returns (placer, the P1 rows)."""
    from tests.unit.test_fix067_ms1_fresh_anchor import (
        _CaptureFM, _CapturePlacer, _CaptureSizer, _list_quote_fn)
    from tests.unit.test_signal_processor import (
        _insert_queued_signal, _make_proc, _make_store, _now_tup)
    store, _ = _make_store()
    _insert_queued_signal(store, "sig_p1", symbol="RELIANCE", scanner="gap_go_long")
    placer = _CapturePlacer()
    proc, _, _ = _make_proc(
        store=store, placer=placer, sizer=_CaptureSizer(), fm=_CaptureFM(),
        strategies={"gap_go_long_v1": strategy},
        scan_webhook_map={"gap_go_long": {"strategy": "gap_go_long_v1"}})
    proc._quote_fn = _list_quote_fn(quote_prices)
    proc._evidence = rec_at(tmp_path)
    proc._process_one_safe(_now_tup("sig_p1", scanner="gap_go_long", symbol="RELIANCE",
                                    price=stale))
    return placer, [r for r in read_records(tmp_path) if r["capture_point"] == P1_ACCEPT]


@pytest.mark.parametrize("case,pullback,quotes,expected", [
    ("momentum, live LTP re-anchors",       False, {"RELIANCE": 2550.0}, True),
    ("momentum, quote unavailable (stale)", False, {},                   False),
    ("pullback, never fetches a quote",     True,  {"RELIANCE": 2550.0}, False),
])
def test_reanchored_comes_from_the_reanchor_itself(tmp_path, case, pullback, quotes, expected):
    """⚠️ RE-CHECK FINDING. P1 recorded `reanchored = entry_price != trigger_price`.
    Every strategy in config/strategies is LIMIT with a 0.1-0.2 % entry offset, so
    entry != trigger on EVERY signal: the field read True 100 % of the time --
    present, never correct. It now comes from the M-S1 branch that re-anchors."""
    from tests.unit.test_signal_processor import _MockStrategy
    strat = _MockStrategy(name="gap_go_long_v1", direction="LONG", entry_method="LIMIT",
                          entry_offset_pct=0.001, pullback_wait_enabled=pullback)
    placer, p1 = _drive_to_p1(tmp_path, strategy=strat, quote_prices=quotes)
    assert placer.calls, f"{case}: nothing was placed -- the harness never reached P1"
    assert len(p1) == 1, f"{case}: expected ONE P1 record, got {len(p1)}"
    assert p1[0]["reanchored"] is expected, case
    # ...and the old inference would have said True in EVERY one of these cases:
    assert p1[0]["entry_price_final"] != p1[0]["trigger_price"]


# ═════════════════════════════════════════════════════════════════════════════
# RE-CHECK — §6.1 widened: no rejected-status write ANYWHERE bypasses capture
# ═════════════════════════════════════════════════════════════════════════════

#: The live pipeline, not the tooling around it: ops_dashboard only READS
#: signals, scripts/ are offline tools, tests/ are tests.
_REJECT_SCAN_EXCLUDE = {"tests", "venv", "ops_dashboard", "scripts", ".git", "docs"}

#: A rejected-status write may have no capture ONLY with a stated reason AND a
#: guard test that fails the moment the reason stops being true.
_REJECT_BYPASS_ALLOWED = {
    ("screening/retest_monitor.py", "REJECTED_RETEST_DUP"):
        "DORMANT: the SNR-V2 retest divert runs only when wait_for_retest_enabled "
        "is true -- it is false. A P2 capture is OWED before that flag is enabled.",
}


def _reject_status_writes():
    """Every update_signal_status(...) in a first-party module whose status is a
    REJECTED literal or f-string, with the P2 capture lines of the same file."""
    import ast
    found = []
    for py in sorted(REPO.rglob("*.py")):
        rel = py.relative_to(REPO).as_posix()
        if rel.split("/")[0] in _REJECT_SCAN_EXCLUDE:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        captures = [n.lineno for n in ast.walk(tree)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "_evidence_capture"
                    and n.args and isinstance(n.args[0], ast.Constant)
                    and n.args[0].value == "P2_REJECT"]
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "update_signal_status"):
                continue
            st = n.args[1] if len(n.args) > 1 else next(
                (k.value for k in n.keywords if k.arg == "status"), None)
            lit = None
            if isinstance(st, ast.Constant) and isinstance(st.value, str):
                lit = st.value
            elif (isinstance(st, ast.JoinedStr) and st.values
                  and isinstance(st.values[0], ast.Constant)):
                lit = str(st.values[0].value) + "{...}"
            if lit is not None and lit.startswith("REJECTED"):
                found.append((rel, n.lineno, lit, captures))
    return found


def test_no_rejected_status_write_anywhere_bypasses_capture():
    """⭐ §6.1, WIDENED. The first scan read ONE file and matched "REJECTED_"
    on a line window -- so the bare "REJECTED" QUEUE_FULL write, and every module
    other than signal_processor, were invisible to it. This parses every
    first-party module and finds every update_signal_status(...) whose status is
    a REJECTED literal or f-string. The census is pinned EXACTLY: a new reject
    path is a visible diff here, never a silent hole."""
    from collections import Counter
    writes = _reject_status_writes()
    census = Counter((rel, lit) for rel, _, lit, _ in writes)
    assert census == Counter({
        ("signals/signal_processor.py", "REJECTED"): 1,         # QUEUE_FULL
        ("signals/signal_processor.py", "REJECTED_{...}"): 5,   # 4 handlers + allocator pre-check
        ("screening/retest_monitor.py", "REJECTED_RETEST_DUP"): 1,
    }), f"the rejected-status census changed -- re-check capture coverage: {dict(census)}"
    uncaptured = [f"{rel}:{line} {lit}" for rel, line, lit, caps in writes
                  if not any(abs(c - line) <= 12 for c in caps)
                  and (rel, lit) not in _REJECT_BYPASS_ALLOWED]
    assert uncaptured == [], f"rejected-status writes with no P2 capture: {uncaptured}"


def test_every_allowed_bypass_is_still_dormant():
    """The allow-list is honest only while its reason holds. If the retest divert
    is ever switched on, this fails -- the capture is owed first."""
    import re
    cfg = (REPO / "config" / "system_config.yaml").read_text(encoding="utf-8")
    assert re.search(r"^\s*wait_for_retest_enabled:\s*false\b", cfg, re.M), \
        "wait_for_retest_enabled is no longer false: REJECTED_RETEST_DUP needs a P2 capture NOW"


def test_queue_full_reject_is_captured(tmp_path):
    """⚠️ RE-CHECK FINDING. _process_one_safe rejects a signal it cannot
    re-queue with the bare status "REJECTED" -- no suffix -- and it had no capture."""
    import queue as _queue
    from tests.unit.test_signal_processor import (
        _insert_queued_signal, _make_proc, _make_store, _now_tup)
    store, _ = _make_store()
    _insert_queued_signal(store, "sig_qf", symbol="RELIANCE", scanner="gap_go_long")
    proc, _, _ = _make_proc(store=store)

    class Exhausted:
        def try_acquire(self, bucket): return False

    class FullQueue:
        def put(self, *a, **k): raise _queue.Full()

    proc._rate_limiter, proc._queue = Exhausted(), FullQueue()
    proc._evidence = rec_at(tmp_path)
    proc._process_one_safe(_now_tup("sig_qf", scanner="gap_go_long", symbol="RELIANCE"))

    rows = read_records(tmp_path)
    assert len(rows) == 1 and rows[0]["capture_point"] == P2_REJECT
    assert (rows[0]["status"], rows[0]["reject_reason"]) == ("REJECTED", "QUEUE_FULL")
    row = store.fetch_one(
        "SELECT status, rejection_reason FROM signals WHERE signal_id = ?", ("sig_qf",))
    assert (row["status"], row["rejection_reason"]) == ("REJECTED", "QUEUE_FULL")


# ═════════════════════════════════════════════════════════════════════════════
# RE-CHECK — the accept side of §6.1: every placement dispatch emits P1
# ═════════════════════════════════════════════════════════════════════════════

def test_every_placement_dispatch_emits_p1():
    """The accept-side twin of §6.1. Every `self._placer.place(` in
    signal_processor must be preceded by a P1_ACCEPT capture. The first build
    covered ONE of the three placement paths; the other two (the gate and retest
    resumes) are dormant today -- which is exactly when a hole gets opened without
    anyone noticing: re-activating either would have produced accepted trades
    with no P1 row, and the first build's own test pinned exactly ONE P1 site."""
    src = (REPO / "signals" / "signal_processor.py").read_text(encoding="utf-8").splitlines()
    places = [i for i, l in enumerate(src) if "self._placer.place(" in l]
    p1 = [i for i, l in enumerate(src) if '_evidence_capture("P1_ACCEPT"' in l]
    assert len(places) == 3, f"placement call count changed ({len(places)}) -- re-check P1"
    assert len(p1) == len(places)
    for i in places:
        assert any(0 < i - c <= 25 for c in p1), \
            f"place() at line {i + 1} has no P1_ACCEPT capture before it"


def test_the_gate_resume_emits_p1(tmp_path):
    """Behavioural, on the dormant gate path: a released WatchEntry that reaches
    placement leaves exactly one P1 row, identity present, reanchored unknown."""
    from datetime import datetime as _dt
    from screening.entry_gate import WatchEntry
    from tests.unit.test_signal_processor import _make_proc

    class _Placer:
        def __init__(self): self.calls = []
        def place(self, **kw): self.calls.append(kw)

    placer = _Placer()
    proc, _, store = _make_proc(placer=placer)
    proc._evidence = rec_at(tmp_path)
    with store.transaction() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO signals (signal_id, symbol, scanner, strategy, "
            "triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sig_gate_p1", "RELIANCE", "gap_go_long", "gap_go_long_v1",
             "2026-04-15 10:00:00", "2026-04-15 10:00:00", "2026-04-15 10:01:00",
             "PROCESSING", "fp_gate_p1", "2026-04-15"))
    proc.continue_from_gate(WatchEntry(
        signal_id="sig_gate_p1", symbol="RELIANCE", direction="LONG",
        trigger_price=2500.0, entry_price=2495.0, sl_price=2445.0, tgt_price=2595.0,
        tolerance_pct=0.005, timeout_sec=300, strategy_name="gap_go_long_v1",
        tier="HIGH", scanner_name="gap_go_long", intent="INTRADAY",
        added_at=_dt.now()))
    assert placer.calls, "the gate resume never reached placement"
    p1 = [r for r in read_records(tmp_path) if r["capture_point"] == P1_ACCEPT]
    assert len(p1) == 1
    assert (p1[0]["signal_id"], p1[0]["symbol"], p1[0]["strategy"]) == \
        ("sig_gate_p1", "RELIANCE", "gap_go_long_v1")
    assert p1[0]["record_status"] != FAILED
    assert p1[0]["reanchored"] is None, "M-S1 never runs on the gate path -- unknown, not False"


# ═════════════════════════════════════════════════════════════════════════════
# RE-CHECK — the backup may never destroy the last good copy
# ═════════════════════════════════════════════════════════════════════════════

def test_backup_extends_an_older_backup_when_the_source_has_grown(tmp_path):
    from scripts.backup_evidence import backup_once
    src = tmp_path / "evidence"
    src.mkdir()
    dst = tmp_path / "bk"
    f = src / "signal_evidence_A_2026-09-11.jsonl"
    f.write_bytes(b'{"n":1}\n')
    assert backup_once(src, dst)[0] == 1
    f.write_bytes(b'{"n":1}\n{"n":2}\n')                    # append-only growth
    assert backup_once(src, dst) == (1, 0, [])
    assert (dst / f.name).read_bytes() == f.read_bytes()
    assert not list(dst.glob("*.tmp-*")), "no temp file may be left behind"


def test_backup_refuses_to_overwrite_when_the_source_is_not_an_extension(tmp_path):
    """⚠️ RE-CHECK FINDING. The first version overwrote the backup in place
    whenever the hashes differed -- so a truncated or corrupted source would have
    replaced the LAST GOOD COPY. The evidence is append-only: a backup is now
    replaced only by a byte-extension of itself; anything else is REFUSED."""
    from scripts.backup_evidence import backup_once
    src = tmp_path / "evidence"
    src.mkdir()
    dst = tmp_path / "bk"
    f = src / "signal_evidence_A_2026-09-11.jsonl"
    good = b'{"n":1}\n{"n":2}\n'
    f.write_bytes(good)
    backup_once(src, dst)

    f.write_bytes(b'{"n":1}\n')                              # the source SHRANK
    copied, skipped, failures = backup_once(src, dst)
    assert copied == 0 and len(failures) == 1 and "REFUSED" in failures[0]
    assert (dst / f.name).read_bytes() == good, "the last good copy must survive"

    f.write_bytes(b'{"n":9}\n{"n":2}\n{"n":3}\n')            # longer, but REWRITTEN
    copied, skipped, failures = backup_once(src, dst)
    assert copied == 0 and len(failures) == 1 and "REFUSED" in failures[0]
    assert (dst / f.name).read_bytes() == good


def test_the_failure_ledger_is_backed_up_too(tmp_path):
    from scripts.backup_evidence import backup_once
    src = tmp_path / "evidence"
    src.mkdir()
    (src / "signal_evidence_A_2026-09-11.jsonl").write_text("{}\n", encoding="utf-8")
    (src / "evidence_failures_A_2026-09-11.jsonl").write_text("{}\n", encoding="utf-8")
    (src / "unrelated.jsonl").write_text("{}\n", encoding="utf-8")
    dst = tmp_path / "bk"
    assert backup_once(src, dst) == (2, 0, [])
    assert sorted(p.name for p in dst.iterdir()) == [
        "evidence_failures_A_2026-09-11.jsonl", "signal_evidence_A_2026-09-11.jsonl"]
