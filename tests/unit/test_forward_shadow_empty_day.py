"""tests/unit/test_forward_shadow_empty_day.py -- C2 (25-Jul-2026).

THE GAP: scripts/forward_shadow_record.py instrumented three of its four exits --
the normal write records a SUCCESS heartbeat, a crash writes a CRITICAL sentinel,
and --dry-run deliberately writes nothing. The fourth,

    if not rows:
        print(...); return 0

wrote NOTHING. So on a day with no new signals the job exited 0 in silence, and
"nothing happened today" was indistinguishable from "the recorder is dead".

That matters more here than for an ordinary cron: the forward shadow is the OOS
evidence path, it only ever grows forward, and a silent stop CANNOT be backfilled.

THE FIX: a heartbeat on the empty path, EXECUTION status SUCCESS (the job ran and
did its job) with FUNCTIONAL status EMPTY_NO_DATA (the day was legitimately empty)
-- the same split generate_screened_stocks_csv already uses for header-only days.

    => AFTER THIS, A MISSING forward_shadow_record HEARTBEAT MEANS DEAD.

⛔ THIS TEST NEVER RUNS THE RECORDER FOR REAL. The recorder is the only OOS
evidence producer and a run appends a record that cannot be un-written. Every
external edge is stubbed before main() is called:

  * core.state_store.StateStore -> a stub that opens no database and returns no rows
  * OUT_PATH                    -> a tmp path that does not exist (so the
                                   already-seen scan reads nothing and writes nothing)
  * record_heartbeat            -> a spy

The `if not rows` return happens at the top of main(), BEFORE _build_kite() and
before any JSONL append, so no broker call and no file write is reachable from
here even if a stub were missed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import create_autospec

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import core.state_store as _ss
import utils.cron_heartbeat as _hb
import scripts.forward_shadow_record as fsr


class _NoRowsStore:
    """Opens nothing. Returns nothing. Records that it was asked."""

    opened = []

    def __init__(self, db_path, *a, **kw):
        _NoRowsStore.opened.append(db_path)

    def fetch_all(self, *a, **kw):
        return []

    def close(self):
        pass


class _Spy:
    """Rides as create_autospec(...).side_effect — never as the stub itself, so the
    stand-in tracks record_heartbeat's REAL signature (the 15-Jul-2026 F2 break;
    enforced by tests/unit/test_cron_heartbeat_contract.py's discovery guard)."""

    def __init__(self, explode=False):
        self.calls = []
        self._explode = explode

    def __call__(self, job_name, status="SUCCESS", duration_sec=None,
                 message=None, functional_status=None, db_path=None):
        if self._explode:
            raise RuntimeError("heartbeat DB unavailable")
        self.calls.append({"job_name": job_name, "status": status,
                           "message": message, "functional_status": functional_status})
        return True


def _isolate(monkeypatch, tmp_path, spy):
    """Stub every edge, then hand back nothing that can write."""
    monkeypatch.setattr(_ss, "StateStore", _NoRowsStore)
    monkeypatch.setattr(fsr, "OUT_PATH", tmp_path / "does_not_exist.jsonl")
    # create_autospec, never the bare spy: the stand-in must enforce
    # record_heartbeat's real signature (see _Spy's docstring).
    monkeypatch.setattr(_hb, "record_heartbeat",
                        create_autospec(_hb.record_heartbeat, side_effect=spy))


def test_empty_day_records_a_heartbeat(tmp_path, monkeypatch):
    """The fix. RED before it: spy.calls was empty -- the exit wrote nothing."""
    spy = _Spy()
    _isolate(monkeypatch, tmp_path, spy)

    rc = fsr.main(["--date", "2026-07-25", "--db", str(tmp_path / "unused.db")])

    assert rc == 0, "a legitimately empty day must still be a clean exit"
    assert len(spy.calls) == 1, f"the empty path wrote no heartbeat: {spy.calls}"
    call = spy.calls[0]
    assert call["job_name"] == "forward_shadow_record"
    assert call["status"] == "SUCCESS"
    assert call["functional_status"] == "EMPTY_NO_DATA"
    assert "2026-07-25" in call["message"]
    assert "nothing new" in call["message"]


def test_execution_status_stays_success_not_skipped(tmp_path, monkeypatch):
    """The EXECUTION status must not be downgraded. The job ran and did its job;
    only the FUNCTIONAL status says the day was empty. A SKIPPED/FAILED execution
    status here would make a normal quiet day look like a problem."""
    spy = _Spy()
    _isolate(monkeypatch, tmp_path, spy)

    fsr.main(["--date", "2026-07-25", "--db", str(tmp_path / "unused.db")])

    assert spy.calls[0]["status"] == "SUCCESS"
    assert spy.calls[0]["status"] not in ("SKIPPED", "FAILED", "PARTIAL")


def test_a_heartbeat_failure_cannot_turn_a_clean_empty_day_into_a_failure(
        tmp_path, monkeypatch):
    """B2: the heartbeat is observability. If it raises, the job must still exit 0."""
    _isolate(monkeypatch, tmp_path, _Spy(explode=True))

    rc = fsr.main(["--date", "2026-07-25", "--db", str(tmp_path / "unused.db")])

    assert rc == 0, "a heartbeat failure turned a clean empty day into a failure"


def test_the_recorder_was_never_actually_run(tmp_path, monkeypatch):
    """Anti-vacuity in the other direction: prove this test file cannot have
    appended to the real OOS dataset. The real OUT_PATH must be untouched and the
    stub store must be the only thing that was opened.

    ⚠️ The mtime pair alone cannot carry that claim: on a box where the real JSONL
    does not exist (any dev PC) it degrades to None == None and asserts nothing.
    So the sandbox containment is asserted directly — the only path the recorder
    could have written to was inside tmp_path — which holds on every machine.
    """
    real_out = Path(fsr.ROOT) / "data_store" / "v3" / "forward_shadow_fs-v1.jsonl"
    before = real_out.stat().st_mtime_ns if real_out.exists() else None

    spy = _Spy()
    _isolate(monkeypatch, tmp_path, spy)
    _NoRowsStore.opened.clear()
    fsr.main(["--date", "2026-07-25", "--db", str(tmp_path / "unused.db")])

    after = real_out.stat().st_mtime_ns if real_out.exists() else None
    assert before == after, "the real forward-shadow JSONL was modified by a test"
    assert tmp_path in Path(fsr.OUT_PATH).parents, (
        f"the recorder's only write path escaped the tmp sandbox: {fsr.OUT_PATH}")
    assert _NoRowsStore.opened, "the stub store was bypassed — a real DB may have been opened"
