"""NI-15 L1 — a None observation must NEVER overwrite a valid baseline.

THE DEFECT (measured 23-Aug-2026, security_monitor.py:730-735 @ 742d9da):

    cur = sha256_file(path)
    prev = hashes.get(path)
    hashes[path] = cur        # <- baseline overwritten FIRST ...
    if cur is None:
        continue              # <- ... and only THEN is the None skipped

So an unreadable-or-absent file destroyed its own baseline. The consequence is
larger than "a delete raises no alert": on a later pass the file can be
re-created with DIFFERENT content, `prev` is None, and the change is NEVER
reported. rm-then-write was a COMPLETE SILENT BYPASS of this control for every
watched path -- .env, sshd_config, both unit files, the configs.

THE CONTRACT PINNED HERE:
  * HASH_A -> gone -> HASH_B      => MUST alert (it IS a change)
  * HASH_A -> gone -> HASH_A      => must NOT alert. This is a CONTENT integrity
        check and the content is unchanged. Alerting on the vanish itself would
        be a NEW alert type, which is outside L1. The gap is recorded in state
        instead.
  * HASH_A -> unreadable -> HASH_A => the baseline must SURVIVE the window.

OUT OF SCOPE, AND STATED SO NO READER INFERS OTHERWISE: /etc/sudoers is
0440 root:root while the watcher runs as User=ubuntu, so it remains unreadable
and remains skipped. L1 makes the SKIP NON-DESTRUCTIVE; it does NOT make the
file visible. That is L2, and L2 is not authorised.

NOTE ON THE UNREADABLE CASES: chmod-to-unreadable is not portable (on Windows
os.chmod only toggles the read-only bit), so "unreadable" is simulated by
forcing sha256_file to None -- which is exactly the branch L1 changed.
"""
from __future__ import annotations

import scripts.security_monitor as sm
from scripts.security_monitor import SecConfig, check_watched_files


def _cfg(path):
    c = SecConfig()
    c.watched_files = [{"path": str(path), "severity": "CRITICAL", "label": "dotenv"}]
    return c


def _run(cfg, state):
    return check_watched_files(cfg, state)


# ── the lifecycle: delete then recreate DIFFERENT ────────────────────────────
def test_delete_then_recreate_with_different_content_alerts(tmp_path):
    """THE ONE THAT FAILS ON THE PRE-FIX TREE."""
    f = tmp_path / ".env"
    f.write_text("SECRET=A")
    cfg, state = _cfg(f), {}

    assert _run(cfg, state) == []                       # 1st pass: baseline only
    baseline = state["file_hashes"][str(f)]
    assert baseline is not None

    f.unlink()
    assert _run(cfg, state) == []                       # gone: no alert, but ...
    assert state["file_hashes"].get(str(f)) == baseline, "the baseline must SURVIVE"
    assert state["file_unreadable"].get(str(f)) == "missing"

    f.write_text("SECRET=B")
    out = _run(cfg, state)
    assert len(out) == 1, "HASH_A -> gone -> HASH_B IS a change and must alert"
    assert out[0].severity == "CRITICAL"
    assert "content changed" in out[0].body
    assert out[0].title == "Sensitive file changed: dotenv"
    assert state["file_unreadable"] == {}               # cleared once readable


# ── delete then recreate IDENTICAL: contract pinned ──────────────────────────
def test_delete_then_recreate_with_identical_content_does_not_alert(tmp_path):
    """CONTRACT: no alert. This is a CONTENT check and the content is unchanged.

    NOTE, stated rather than hidden: this test PASSES on the pre-fix tree too --
    it is a PINNING test that fixes the contract, NOT a defect test. It is the
    one case in this file that does not go red on 2f67bb8, by nature."""
    f = tmp_path / ".env"
    f.write_text("SECRET=A")
    cfg, state = _cfg(f), {}
    _run(cfg, state)
    baseline = state["file_hashes"][str(f)]

    f.unlink()
    _run(cfg, state)
    f.write_text("SECRET=A")
    out = _run(cfg, state)

    assert out == [], "identical content is not a content change"
    assert state["file_hashes"][str(f)] == baseline


# ── unreadable window: the baseline must survive it ──────────────────────────
def test_baseline_survives_an_unreadable_window(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("SECRET=A")
    cfg, state = _cfg(f), {}
    _run(cfg, state)
    baseline = state["file_hashes"][str(f)]

    monkeypatch.setattr(sm, "sha256_file", lambda p: None)   # simulate 0400 root
    assert _run(cfg, state) == []
    assert state["file_hashes"].get(str(f)) == baseline, "baseline lost while unreadable"
    assert state["file_unreadable"].get(str(f)) == "unreadable"   # present, not missing

    monkeypatch.undo()
    f.write_text("SECRET=B")
    out = _run(cfg, state)
    assert len(out) == 1, "a change after an unreadable window must still alert"


def test_unreadable_is_distinguished_from_missing(tmp_path, monkeypatch):
    """Absent and unreadable are different operator problems, so the state says
    which. Before L1 both were an indistinguishable null in file_hashes."""
    f = tmp_path / ".env"
    cfg, state = _cfg(f), {}
    _run(cfg, state)
    assert state["file_unreadable"].get(str(f)) == "missing"

    f.write_text("x")
    monkeypatch.setattr(sm, "sha256_file", lambda p: None)
    _run(cfg, state)
    assert state["file_unreadable"].get(str(f)) == "unreadable"


def test_no_none_is_ever_written_into_file_hashes(tmp_path):
    """The pre-L1 artifact was a literal null in the live state file. A null now
    cannot be produced, and a legacy one is pruned rather than carried."""
    f = tmp_path / ".env"
    cfg = _cfg(f)
    state = {"file_hashes": {str(f): None, "/legacy/path": None}}   # pre-L1 shape
    _run(cfg, state)
    assert None not in state["file_hashes"].values()
    assert "/legacy/path" not in state["file_hashes"]


def test_a_permanently_unreadable_path_never_alerts_and_never_crashes(tmp_path, monkeypatch):
    """The /etc/sudoers shape. L1 does NOT make it visible -- it only stops the
    skip from destroying anything. Recorded so no reader infers L2 was done."""
    cfg = SecConfig()
    cfg.watched_files = [{"path": "/etc/sudoers", "severity": "CRITICAL", "label": "sudoers"}]
    monkeypatch.setattr(sm, "sha256_file", lambda p: None)
    state = {}
    for _ in range(3):
        assert _run(cfg, state) == []
    assert state["file_hashes"] == {}                       # still no baseline
    assert state["file_unreadable"]["/etc/sudoers"] in ("missing", "unreadable")
