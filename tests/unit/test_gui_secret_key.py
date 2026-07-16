"""
tests/unit/test_gui_secret_key.py

ops_dashboard/backend/app.py used `auth_cfg.get("secret_key") or secrets.token_hex(32)`
for the Flask session key. `secret_key` is configured NOWHERE (not in gui_config.yaml,
not in gui_config.local.yaml, not on the VM), so the fallback ALWAYS fired: every start
of gui-dashboard minted a fresh key, silently invalidated every session, and bounced the
operator back through login + TOTP. A session key is meant to be durable; an ephemeral
one is a logout on a timer.

The key is now generated once and persisted (0600, under the gitignored data_store/, so
it survives both restarts and the post-receive `checkout -f`), with a fail-safe: any
read/write problem degrades to the old ephemeral behaviour rather than refusing to boot.

Every test here redirects _SECRET_KEY_FILE at tmp_path — the real data_store/ is never
touched.

Run: python -m pytest tests/unit/test_gui_secret_key.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ops_dashboard.backend import app as gui_app


def _redirect(monkeypatch, tmp_path) -> Path:
    target = tmp_path / "session" / "gui_secret_key"
    monkeypatch.setattr(gui_app, "_SECRET_KEY_FILE", str(target))
    return target


def test_configured_secret_key_wins(monkeypatch, tmp_path):
    """An explicitly configured auth.secret_key is used as-is and nothing is written."""
    target = _redirect(monkeypatch, tmp_path)

    assert gui_app._resolve_secret_key("configured-key") == "configured-key"
    assert not target.exists(), "a configured key must not cause a file write"


def test_unconfigured_key_is_stable_across_restarts(monkeypatch, tmp_path):
    """THE FIX. RED ON OLD: the old expression returned a NEW token every call, so two
    starts produced two keys and every session died on restart."""
    target = _redirect(monkeypatch, tmp_path)

    first = gui_app._resolve_secret_key(None)
    second = gui_app._resolve_secret_key(None)   # a "restart"

    assert first == second, "the session key changed across restarts — operator logged out"
    assert len(first) == 64                      # token_hex(32)
    assert target.exists() and target.read_text().strip() == first


def test_persisted_key_is_owner_only(monkeypatch, tmp_path):
    """0600. Skipped on Windows, which does not honour POSIX modes — the VM is Linux."""
    target = _redirect(monkeypatch, tmp_path)
    gui_app._resolve_secret_key(None)

    if os.name == "nt":
        import pytest
        pytest.skip("Windows does not honour POSIX file modes; the VM is Linux")
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_an_existing_key_file_is_reused_not_regenerated(monkeypatch, tmp_path):
    target = _redirect(monkeypatch, tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("pre-existing-key")

    assert gui_app._resolve_secret_key(None) == "pre-existing-key"


def test_an_empty_key_file_is_regenerated(monkeypatch, tmp_path):
    """A truncated/empty file must not become an empty secret_key (Flask would accept
    it and every session cookie would be signed with nothing)."""
    target = _redirect(monkeypatch, tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("   ")

    key = gui_app._resolve_secret_key(None)

    assert key and len(key) == 64
    assert target.read_text().strip() == key


def test_unwritable_path_falls_back_to_ephemeral_and_does_not_raise(monkeypatch, tmp_path):
    """FAIL-SAFE: if the key cannot be persisted the GUI still boots with an ephemeral
    key (the old behaviour). A read-only ops dashboard that boots and logs you out beats
    one that will not boot."""
    monkeypatch.setattr(gui_app, "_SECRET_KEY_FILE", str(tmp_path / "nope" / "k"))
    monkeypatch.setattr(gui_app.os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs")))

    key = gui_app._resolve_secret_key(None)   # must not raise

    assert key and len(key) == 64
