"""tests/unit/test_system_manager_deployed_tree.py — ledger #10 / IA-P10-01.

Check 12: does the deployed work tree still equal HEAD?

These build REAL git repositories in tmp_path rather than mocking the git seam.
That is deliberate and is the whole point of the item: the defect class here
(partial checkout, stray untracked .py, hook drift) lives in git's actual
behaviour, and a mocked `subprocess.run` would assert only what we told it to
say. Cf. practices §V4 — a stubbed seam defines what the test can conclude.
"""
import os
import subprocess
from pathlib import Path

import pytest

import scripts.system_manager as sm


def _git(cwd: Path, *args: str) -> str:
    env = dict(os.environ,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                         text=True, env=env, timeout=60)
    assert out.returncode == 0, f"git {args} failed: {out.stderr}"
    return out.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repo with one committed file, clean at HEAD."""
    _git(tmp_path, "init", "-q", "-b", "main", str(tmp_path))
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


def test_clean_tree_is_ok_and_names_the_head_sha(repo):
    res = sm.deployed_tree_check(repo, git_dir=repo / ".git")
    assert res.violations == 0
    assert res.warnings == 0
    assert any("deployed tree == HEAD" in ln for ln in res.lines)


def test_modified_tracked_file_is_a_violation(repo):
    # The exact failure the item exists to catch: the running code is not the
    # audited code.
    (repo / "a.py").write_text("x = 2  # drifted\n", encoding="utf-8")
    res = sm.deployed_tree_check(repo, git_dir=repo / ".git")
    assert res.violations >= 1
    assert any("DIFFERS from HEAD" in ln for ln in res.lines)


def test_untracked_py_is_a_violation(repo):
    # `checkout -f` leaves untracked files in place, so this is importable and
    # is in no commit — the half check 11 (.pyc) does not cover.
    (repo / "stray.py").write_text("import os\n", encoding="utf-8")
    res = sm.deployed_tree_check(repo, git_dir=repo / ".git")
    assert res.violations >= 1
    assert any("UNTRACKED .py" in ln for ln in res.lines)


def test_untracked_non_py_is_ignored(repo):
    # Reports, logs and data land in the deployed tree all the time; only .py
    # can be imported, so only .py is the hazard.
    (repo / "notes.txt").write_text("hello\n", encoding="utf-8")
    res = sm.deployed_tree_check(repo, git_dir=repo / ".git")
    assert res.violations == 0


# ── the two SAFETY properties ────────────────────────────────────────────────

def test_a_dirty_tree_NEVER_trips_the_soft_kill(repo):
    """DEGRADE+ALARM, not BLOCK.

    system_manager trips tomorrow's SOFT_KILL from CheckResult.soft_kill_reason
    (generate_full_report -> trigger_soft_kill). The failure class here is
    environment-caused (partial checkout / stray file / hook drift), so this
    check must report and never halt trading on its own.
    """
    (repo / "a.py").write_text("x = 3\n", encoding="utf-8")
    (repo / "stray.py").write_text("import sys\n", encoding="utf-8")
    res = sm.deployed_tree_check(repo, git_dir=repo / ".git")
    assert res.violations >= 2
    assert res.soft_kill_reason is None


def test_the_real_index_is_never_written(repo):
    """Read-only w.r.t. the DEPLOY repo: we diff against a COPY of the index."""
    index = repo / ".git" / "index"
    before = index.stat().st_mtime_ns
    (repo / "a.py").write_text("x = 4\n", encoding="utf-8")
    sm.deployed_tree_check(repo, git_dir=repo / ".git")
    assert index.stat().st_mtime_ns == before, "the deploy repo's index was written"


def test_an_empty_index_would_have_lied_so_we_copy_it(repo):
    """Anti-vacuity for the mechanism itself.

    Pointing GIT_INDEX_FILE at a path that does not exist gives git a VALID but
    EMPTY index, and `git diff HEAD` then reports every tracked file as deleted
    — measured on the real repo as "1250 files changed, 344936 deletions(-)".
    This asserts that wrong form really is wrong, so the copy-the-index step can
    never be "simplified" away without a test going red.

    ⚠️ It must be a NON-EXISTENT path, not a zero-byte file: a zero-byte file is
    a CORRUPT index ("index file smaller than expected") and git errors out
    instead of misreporting. Different failure, different lesson.
    """
    absent_index = repo / "does_not_exist_index"
    assert not absent_index.exists()
    env = dict(os.environ, GIT_INDEX_FILE=str(absent_index))
    out = subprocess.run(
        ["git", f"--git-dir={repo / '.git'}", f"--work-tree={repo}",
         "diff", "--stat", "HEAD"],
        capture_output=True, text=True, env=env, timeout=60)
    assert "deletion" in out.stdout, (
        "the empty-index form no longer misreports; re-check whether the "
        "copy-the-index step is still required"
    )
    # ...while the real check, on the same clean tree, says clean:
    res = sm.deployed_tree_check(repo, git_dir=repo / ".git")
    assert res.violations == 0


# ── DEGRADE cases: never a violation ─────────────────────────────────────────

def test_missing_git_dir_degrades_to_a_warning(tmp_path):
    res = sm.deployed_tree_check(tmp_path, git_dir=tmp_path / "nope.git")
    assert res.violations == 0
    assert res.warnings == 1


def test_bare_repo_with_no_index_degrades_to_a_warning(tmp_path):
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    res = sm.deployed_tree_check(tmp_path, git_dir=bare)
    assert res.violations == 0
    assert res.warnings == 1
    assert any("index does not exist" in ln for ln in res.lines)


def test_resolver_prefers_the_bare_deploy_repo_then_falls_back(repo, monkeypatch):
    # VM: ~/trading-system.git exists and the work tree has no .git of its own.
    fake_home = repo / "home"
    (fake_home / "trading-system.git").mkdir(parents=True)
    (fake_home / "trading-system.git" / "HEAD").write_text("ref: refs/heads/main\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert sm._resolve_deploy_git_dir(repo) == fake_home / "trading-system.git"
    # PC: no bare repo -> the tree's own .git.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: repo / "empty_home"))
    assert sm._resolve_deploy_git_dir(repo) == repo / ".git"


def test_check_12_is_wired_into_run_and_titled(repo):
    import inspect
    src = inspect.getsource(sm.run)
    assert "deployed_tree_check(root)" in src, "check 12 is not wired into run()"
    assert "DEPLOYED TREE vs HEAD" in src, "check 12 has no title in run()"
