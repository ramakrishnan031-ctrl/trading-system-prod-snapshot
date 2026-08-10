"""ITEM 6 — the strategy-direction registry: SEED (tracked) vs STATE (runtime).

THE DEFECT (measured 10-Aug-2026): `scripts/strategy_registry_officer.py` wrote its
runtime state into `config/strategy_direction_registry.yaml`, a GIT-TRACKED file. The
deployed work tree therefore diverged from HEAD permanently, so
`system_manager.deployed_tree_check` raised a violation, and `system_manager.main`'s
severity rule (`CRITICAL if (violations or reasons) else "INFO"`) turned the whole EOD
report CRITICAL every day — training the reader to skip its severity line.

⛔ The check is RIGHT and is NOT weakened here. `scripts/system_manager.py` is NOT
modified by this build at all. What changes is that production stops writing to a
tracked file: the tracked YAML becomes a READ-ONLY SEED and runtime state moves to
`data_store/` (already gitignored — no new ignore line, no new config key).

PARITY: this is a CRON/REPORT path. It is MODE-INDEPENDENT — it reads no broker
quantity and takes no trading decision, so paper/live parity is not a question here
rather than a thing claimed to be covered.

RED-first status against the pre-fix tree is recorded per-test below.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

import pytest

from core import db_connect
from core import strategy_direction as sd
import scripts.strategy_registry_officer as officer
from scripts.system_manager import deployed_tree_check, generate_full_report

_REPO_CONFIG = Path("config")
_SEED_NAME = "strategy_direction_registry.yaml"


# ── helpers ─────────────────────────────────────────────────────────────────────

def _mk_db(path: Path) -> None:
    """A DB in which `vwap_bounce_long` has a FILLED trade (so it must CONFIRM)."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE signals (signal_id TEXT, scanner TEXT, strategy TEXT)")
    conn.execute("CREATE TABLE trades (trade_id TEXT, strategy TEXT, direction TEXT, qty_filled INTEGER)")
    conn.execute("INSERT INTO signals VALUES ('s1','vwap_bounce_long','vwap_bounce_long')")
    conn.execute("INSERT INTO trades VALUES ('t1','vwap_bounce_long','LONG',10)")
    conn.commit()
    conn.close()
    db_connect.init_analytics_schema(path)


def _mk_config_dir(tmp_path: Path) -> Path:
    """A REAL config dir copy — the officer needs valid StrategyConfig YAMLs.

    ⛔ Deliberately NOT the repo's own `config/`: on the PRE-FIX tree the officer
    writes the registry into the config dir, and pointing a test at the repo would
    dirty the working tree it is trying to measure.
    """
    cfg = tmp_path / "config"
    (cfg).mkdir()
    shutil.copytree(_REPO_CONFIG / "strategies", cfg / "strategies")
    shutil.copyfile(_REPO_CONFIG / _SEED_NAME, cfg / _SEED_NAME)
    return cfg


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def _mk_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo whose tracked content includes the SEED, with data_store/ ignored."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.autocrlf", "false")
    cfg = _mk_config_dir(repo)          # config/ lands INSIDE the repo
    (repo / ".gitignore").write_text("data_store/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo, cfg


# ── 1. THE DEFECT ITSELF — RED on the pre-fix tree ──────────────────────────────

def test_officer_default_run_does_not_write_into_the_tracked_config_dir(tmp_path, monkeypatch):
    """RED pre-fix: the officer's DEFAULT registry path was `<config-dir>/<name>`, so an
    ordinary run mutated a git-tracked file. The seed must come back BYTE-IDENTICAL."""
    cfg = _mk_config_dir(tmp_path)
    db = tmp_path / "trading_system.db"
    _mk_db(db)
    seed = cfg / _SEED_NAME
    before = seed.read_bytes()

    monkeypatch.setattr(officer, "_ROOT", tmp_path)      # state root, not the real repo
    monkeypatch.setattr(officer, "_notify", lambda *a, **k: None)
    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])

    assert seed.read_bytes() == before, (
        "the officer wrote into the TRACKED config dir — this is the daily-CRITICAL defect"
    )


def test_state_is_written_to_data_store(tmp_path, monkeypatch):
    """RED pre-fix (no such file was ever produced). State belongs in the gitignored
    `data_store/`, and it must carry the CONFIRMED transition."""
    cfg = _mk_config_dir(tmp_path)
    db = tmp_path / "trading_system.db"
    _mk_db(db)

    monkeypatch.setattr(officer, "_ROOT", tmp_path)
    monkeypatch.setattr(officer, "_notify", lambda *a, **k: None)
    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])

    state = tmp_path / "data_store" / _SEED_NAME
    assert state.is_file(), "runtime state must land in data_store/"
    assert sd.load_registry(state)["vwap_bounce_long"]["registration_status"] == "CONFIRMED"


# ── 2. THE ALARM — the divergence is gone, and the CHECK still works ────────────

def test_a_normal_officer_run_leaves_the_deployed_tree_equal_to_head(tmp_path, monkeypatch):
    """RED pre-fix: this is the false CRITICAL, end to end through the REAL check."""
    repo, cfg = _mk_repo(tmp_path)
    db = repo / "trading_system.db"
    _mk_db(db)

    monkeypatch.setattr(officer, "_ROOT", repo)
    monkeypatch.setattr(officer, "_notify", lambda *a, **k: None)
    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])

    res = deployed_tree_check(repo, git_dir=repo / ".git")
    assert res.violations == 0, f"false CRITICAL survives: {res.lines}"
    _text, v, _w, _r = generate_full_report([res], date(2026, 8, 10))
    assert v == 0, "a clean tree must not contribute a violation"


def test_CONTROL_a_genuine_tracked_drift_still_goes_critical(tmp_path, monkeypatch):
    """🔑 THE ANTI-SUPPRESSION CONTROL — GREEN on BOTH trees, by design.

    If this ever goes quiet, the 'fix' has become a suppression. A REAL modification to
    a tracked file must still raise a violation, and that violation must still be what
    `system_manager.main` turns into CRITICAL (`CRITICAL if (violations or reasons)`).
    """
    repo, cfg = _mk_repo(tmp_path)
    db = repo / "trading_system.db"
    _mk_db(db)
    monkeypatch.setattr(officer, "_ROOT", repo)
    monkeypatch.setattr(officer, "_notify", lambda *a, **k: None)
    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])

    # genuine drift: someone edits the TRACKED seed on the deployed box
    (cfg / _SEED_NAME).write_text("strategies:\n  tampered: {}\n", encoding="utf-8")

    res = deployed_tree_check(repo, git_dir=repo / ".git")
    assert res.violations >= 1, "a genuine tracked-file drift MUST still violate"
    _text, v, _w, _r = generate_full_report([res], date(2026, 8, 10))
    assert v >= 1, "the violation must still reach the severity roll-up"


def test_CONTROL_an_unrelated_tracked_file_still_violates(tmp_path, monkeypatch):
    """The check's scope is unchanged: nothing was whitelisted or path-excluded."""
    repo, cfg = _mk_repo(tmp_path)
    db = repo / "trading_system.db"
    _mk_db(db)
    monkeypatch.setattr(officer, "_ROOT", repo)
    monkeypatch.setattr(officer, "_notify", lambda *a, **k: None)
    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])

    (cfg / "strategies" / "gap_fade_long.yaml").write_text("name: tampered\n", encoding="utf-8")
    res = deployed_tree_check(repo, git_dir=repo / ".git")
    assert res.violations >= 1, "an unrelated tracked file must still violate"


# ── 3. THE DEPLOY — state survives `checkout -f`, seed is restored by it ────────

def test_state_survives_the_deploy_hooks_checkout_f(tmp_path, monkeypatch):
    """`deploy/hooks/post-receive` runs `git checkout -f`. That is what DELETED the live
    registry under the untrack option; under the split it must leave the state alone."""
    repo, cfg = _mk_repo(tmp_path)
    db = repo / "trading_system.db"
    _mk_db(db)
    monkeypatch.setattr(officer, "_ROOT", repo)
    monkeypatch.setattr(officer, "_notify", lambda *a, **k: None)
    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])
    state = repo / "data_store" / _SEED_NAME
    assert state.is_file()

    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(repo, "checkout", "-f", branch)

    assert state.is_file(), "checkout -f destroyed the runtime state"
    assert sd.load_registry(state)["vwap_bounce_long"]["registration_status"] == "CONFIRMED"


# ── 4. MIGRATION — zero loss, zero alert burst (why (B) was chosen over (A)) ────

def test_first_run_with_no_state_falls_back_to_the_seed_silently(tmp_path, monkeypatch):
    """⭐ THE ZERO-LOSS PROOF. With no state file yet (the VM on deploy day), the officer
    must read the tracked SEED — NOT an empty registry. So: no strategy is re-registered
    as NEW (no alert burst), and `first_seen` keeps its original value."""
    cfg = _mk_config_dir(tmp_path)
    db = tmp_path / "trading_system.db"
    _mk_db(db)
    seeded = sd.load_registry(cfg / _SEED_NAME)
    assert seeded, "precondition: the tracked seed is non-empty"
    a_name = next(iter(seeded))
    original_first_seen = seeded[a_name]["first_seen"]

    captured: dict = {}
    monkeypatch.setattr(officer, "_ROOT", tmp_path)
    monkeypatch.setattr(officer, "_notify",
                        lambda new, conflict, reg, cd: captured.update(new=list(new)))
    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])

    assert captured["new"] == [], "a migration must not re-announce every strategy as NEW"
    state = sd.load_registry(tmp_path / "data_store" / _SEED_NAME)
    assert set(state) == set(seeded), "the seed's full row set must carry over"
    assert state[a_name]["first_seen"] == original_first_seen, "first_seen provenance lost"


def test_state_takes_precedence_over_the_seed_once_it_exists(tmp_path):
    """The seed is a FALLBACK, never an override — otherwise a deploy would silently
    revert converged runtime state."""
    seed = tmp_path / "seed.yaml"
    state = tmp_path / "state.yaml"
    sd.save_registry({"s": {"direction": "LONG", "registration_status": "PENDING", "health": "OK"}}, seed)
    sd.save_registry({"s": {"direction": "LONG", "registration_status": "CONFIRMED", "health": "OK"}}, state)
    assert sd.load_registry(state, seed_path=seed)["s"]["registration_status"] == "CONFIRMED"


def test_seed_fallback_is_opt_in_and_absence_is_still_empty(tmp_path):
    """Existing callers are unchanged: with no seed_path, a missing file is `{}` — the
    fail-safe `load_registry` already documented."""
    assert sd.load_registry(tmp_path / "nope.yaml") == {}
    assert sd.load_registry(tmp_path / "nope.yaml", seed_path=tmp_path / "also_nope.yaml") == {}


# ── 5. THE OFFICER STILL WORKS ─────────────────────────────────────────────────

def test_officer_still_converges_second_run_is_a_no_change(tmp_path, monkeypatch):
    """`save_registry` is conditional (`if changed`); convergence must be preserved."""
    cfg = _mk_config_dir(tmp_path)
    db = tmp_path / "trading_system.db"
    _mk_db(db)
    monkeypatch.setattr(officer, "_ROOT", tmp_path)
    monkeypatch.setattr(officer, "_notify", lambda *a, **k: None)
    state = tmp_path / "data_store" / _SEED_NAME

    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])
    first = state.read_bytes()
    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])
    assert state.read_bytes() == first, "a converged officer must stop rewriting"


def test_the_tracked_seed_stays_reproducible(tmp_path, monkeypatch):
    """The seed remains a valid, self-consistent registry after the split — it is still
    what a fresh machine boots from."""
    cfg = _mk_config_dir(tmp_path)
    db = tmp_path / "trading_system.db"
    _mk_db(db)
    monkeypatch.setattr(officer, "_ROOT", tmp_path)
    monkeypatch.setattr(officer, "_notify", lambda *a, **k: None)
    officer.main(["--config-dir", str(cfg), "--db-path", str(db)])

    reg = sd.load_registry(cfg / _SEED_NAME)
    dmap = sd.build_direction_map(cfg)
    assert set(reg) == set(dmap)
    for name, d in dmap.items():
        assert reg[name]["direction"] == d
