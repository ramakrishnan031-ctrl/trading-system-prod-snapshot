"""
tests/conftest.py -- Root test configuration

Ensures project root is in sys.path so all imports work correctly, and (23-Jun)
isolates the REAL sentinel directory so no test can write a CRITICAL alert into
the live data_store (the VM alert-watcher would email it).
"""
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

# Add project root to sys.path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

_REAL_DATA_STORE = (project_root / "data_store").resolve()

# Modules that bind alerts.critical.write_critical_sentinel at IMPORT time (a
# module-level `from alerts.critical import write_critical_sentinel`); patching only
# the source module would miss these, so we also rebind them when they are loaded.
_MODULE_LEVEL_SENTINEL_IMPORTERS = (
    "alerts.telegram_notifier",
    "scripts.cron_watchdog",
    "scripts.cron_officer",
)


@pytest.fixture(autouse=True)
def _isolate_real_sentinels(tmp_path, monkeypatch):
    """Test isolation: NO test may write a CRITICAL sentinel into the REAL
    <project>/data_store. The live alert-watcher (running on the VM) consumes any
    ``critical_alert_*.flag`` there and EMAILS it — so a full-suite run on the VM
    was emailing test-written CRITICALs as real alerts (gemini failure sentinel,
    preflight CRITICALs).

    Any ``write_critical_sentinel`` call whose ``sentinel_dir`` resolves to the real
    data_store (including the bare ``"data_store"`` default) is redirected to a
    per-test tmp sandbox. An explicit non-real dir (a test's own ``tmp_path``) passes
    through unchanged, so sentinel-content tests still work. This isolates TESTS
    only — the production sentinel/alert path is NOT modified.
    """
    import alerts.critical as _ac
    real = _ac.write_critical_sentinel
    sandbox = tmp_path / "_sentinel_sandbox"

    def guarded(*args, **kwargs):
        if "sentinel_dir" in kwargs:
            sd, positional = kwargs["sentinel_dir"], False
        elif len(args) >= 5:
            sd, positional = args[4], True
        else:
            sd, positional = "data_store", False   # the function default -> real dir
        try:
            p = Path(sd)
            if not p.is_absolute():
                p = project_root / p
            hits_real = p.resolve() == _REAL_DATA_STORE
        except Exception:
            hits_real = False
        if hits_real:
            if positional:
                args = args[:4] + (str(sandbox),) + args[5:]
            else:
                kwargs["sentinel_dir"] = sandbox
        return real(*args, **kwargs)

    monkeypatch.setattr(_ac, "write_critical_sentinel", guarded)
    for modname in _MODULE_LEVEL_SENTINEL_IMPORTERS:
        mod = sys.modules.get(modname)
        if mod is not None and hasattr(mod, "write_critical_sentinel"):
            monkeypatch.setattr(mod, "write_critical_sentinel", guarded, raising=False)
    yield


# ═════════════════════════════════════════════════════════════════════════════
# Schema-backed test harness (Wave 1, H-1) — REUSABLE INFRA
# ═════════════════════════════════════════════════════════════════════════════
# Materializes the REAL core/schema.sql into a fresh in-memory sqlite DB so a
# query naming a column/table that does not exist FAILS exactly as it does in
# production. This is the whole point: the H-1 class (orders.broker_order_id —
# the real PK is order_id) is a query-vs-schema mismatch that a hand-written mock
# schema would hide. So: NEVER a mock schema, NEVER a _MockStore — schema.sql on
# disk is the single source of truth.
#
# Later waves attach more money-path queries here and may add the two-DB analytics
# ATTACH (core/analytics_schema.sql) if a path touches candles/system_metrics —
# not required for a single-table orders query. FK enforcement is left at the
# sqlite default (OFF) so a focused single-table test needs no parent chain; a
# test that wants it can `conn.execute("PRAGMA foreign_keys = ON")`.

_SCHEMA_PATH = project_root / "core" / "schema.sql"


def build_real_schema_db() -> sqlite3.Connection:
    """Return a fresh in-memory sqlite connection with core/schema.sql applied
    (row_factory=sqlite3.Row, mirroring StateStore). Reusable across waves."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


class RealSchemaStore:
    """Minimal StateStore-shaped accessor over a real-schema connection. NOT a
    mock: fetch_all/fetch_one/transaction run real SQL against the real schema,
    so a bad column raises OperationalError exactly as production StateStore does.
    Exposes only the surface money-path code touches."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def fetch_all(self, sql: str, params: tuple = ()):
        cur = self.conn.execute(sql, params)
        try:
            return cur.fetchall()
        finally:
            cur.close()

    def fetch_one(self, sql: str, params: tuple = ()):
        cur = self.conn.execute(sql, params)
        try:
            return cur.fetchone()
        finally:
            cur.close()

    @contextmanager
    def transaction(self):
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()


@pytest.fixture
def real_schema_db():
    """Fresh in-memory DB with the REAL schema; yields the sqlite3.Connection."""
    conn = build_real_schema_db()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def real_schema_store():
    """Fresh in-memory DB with the REAL schema; yields a RealSchemaStore over it."""
    conn = build_real_schema_db()
    try:
        yield RealSchemaStore(conn)
    finally:
        conn.close()
