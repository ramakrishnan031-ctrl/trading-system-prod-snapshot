"""
I7 — hard-isolation enforcement (test-gated by mechanism, not convention):
  (a) AST gate  : no ops_dashboard backend module imports a production package.
  (b) DB gate   : a write through db_reader's connection raises (read-only).
  (c) venv gate : `pip show kiteconnect` fails in the GUI venv (no broker).
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys

import pytest

from backend.readers import db_reader

_FORBIDDEN_ROOTS = {
    "core", "orders", "capital", "strategies", "broker",
    "signals", "data", "alerts", "ops", "scripts", "main",
}
_BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")


def _iter_py(root):
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def test_a_no_production_imports():
    offenders = []
    for path in _iter_py(_BACKEND_DIR):
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            roots = []
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Only absolute imports can hit a production top-level package;
                # relative (level>0) imports stay inside ops_dashboard.
                if node.level == 0 and node.module:
                    roots = [node.module.split(".")[0]]
            for r in roots:
                if r in _FORBIDDEN_ROOTS:
                    offenders.append(f"{path}: imports {r}")
    assert not offenders, "Production imports found (isolation I1):\n" + "\n".join(offenders)


def test_b_db_connection_is_readonly(gui_config):
    conn = db_reader.open_readonly_connection(gui_config)
    try:
        with pytest.raises(Exception) as exc:
            conn.execute("INSERT INTO schema_meta(key,value) VALUES('x','y')")
            conn.commit()
        import sqlite3
        assert isinstance(exc.value, sqlite3.OperationalError)
        assert "readonly" in str(exc.value).lower() or "read-only" in str(exc.value).lower()
    finally:
        conn.close()


def test_c_venv_has_no_kiteconnect():
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "show", "kiteconnect"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode != 0, (
        "kiteconnect IS installed in the GUI venv — isolation I4 violated. "
        "Run tests inside ops_dashboard/.venv (no broker packages)."
    )


def test_d_no_cdn_or_network_refs_in_frontend():
    """V4 (G2b-3): no http(s):// in our templates/CSS except loopback.

    The GUI must be fully self-contained — no CDN loads. The two VENDORED
    minified libs (htmx.min.js / alpine.min.js) are exempt: they contain
    documentation-URL string literals in error messages, which fetch nothing;
    they are local files served from /static, not CDN references.
    """
    frontend = os.path.join(os.path.dirname(_BACKEND_DIR), "frontend")
    offenders = []
    for dirpath, _dirs, files in os.walk(frontend):
        for fname in files:
            if fname.endswith(".min.js"):
                continue   # vendored libs (see docstring)
            path = os.path.join(dirpath, fname)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        for hit in ("http://", "https://"):
                            idx = line.find(hit)
                            while idx != -1:
                                rest = line[idx + len(hit):]
                                if not (rest.startswith("127.0.0.1") or rest.startswith("localhost")):
                                    offenders.append(f"{path}:{lineno}: {line.strip()[:100]}")
                                idx = line.find(hit, idx + 1)
            except OSError:
                continue
    assert not offenders, "External URL(s) in frontend (CDN forbidden):\n" + "\n".join(offenders)
