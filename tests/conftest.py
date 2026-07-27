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


class OutboundNetworkBlocked(RuntimeError):
    """Raised when a test tries to open a non-loopback connection."""


# Loopback is allowed: tests bind and probe local ports (instance-lock, healthcheck,
# the webhook self-check). Everything else is refused.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"})


def _is_loopback(address) -> bool:
    if isinstance(address, (str, bytes)):
        return True                     # AF_UNIX / abstract socket -- not the internet
    try:
        host = address[0]
    except Exception:                   # noqa: BLE001 -- unknown shape -> refuse
        return False
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    return str(host) in _ALLOWED_HOSTS or str(host).startswith("127.")


@pytest.fixture(autouse=True)
def _block_outbound_network(monkeypatch):
    """27-Jul-2026 -- NO test may open a connection to anything but loopback.

    WHY THIS IS AT THE DOOR AND NOT AT EACH SENDER. ``_isolate_real_sentinels``
    below was added 23-Jun for exactly this class -- "no test can write a CRITICAL
    alert into the live data_store (the VM alert-watcher would email it)" -- but it
    guards ONE path. ``main._send_holiday_notification`` does a raw
    ``urllib.request.urlopen`` POST straight to Telegram with the REAL
    ``TELEGRAM_BOT_TOKEN``, bypassing the sentinel->watcher chain entirely, so that
    fixture never saw it. It sent a real "MARKET IS CLOSED" message to the operator's
    live channel on ~50 days between 6-May and 27-Jul-2026 -- including trading days,
    while the system was trading -- because two tests patch ``is_trading_day`` and
    ``next_trading_day`` but not the sender.

    There are 10+ direct send sites across 8 modules (requests, smtplib, urllib).
    Guarding each one is a convention that must be remembered, and remembering is
    what failed here for eleven weeks. So this blocks the SOCKET instead: patching
    ``socket.create_connection`` covers urllib, requests and smtplib alike, and
    ``socket.socket.connect``/``connect_ex`` covers raw sockets -- including sites
    nobody has written yet.

    ⛔ PRODUCTION IS UNTOUCHED BY CONSTRUCTION. This lives in the test harness and
    is never imported by production code, so it cannot silence a real alert. That is
    a structural guarantee, not a tested one -- the same discipline
    ``_isolate_real_sentinels`` states ("This isolates TESTS only").

    A test that genuinely needs an outbound connection must mock its client.
    """
    import socket

    real_create_connection = socket.create_connection
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_create_connection(address, *args, **kwargs):
        if not _is_loopback(address):
            raise OutboundNetworkBlocked(
                f"BLOCKED outbound connection to {address!r} from a test. "
                "Mock the client instead of reaching the network."
            )
        return real_create_connection(address, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        if not _is_loopback(address):
            raise OutboundNetworkBlocked(
                f"BLOCKED outbound connect to {address!r} from a test."
            )
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        if not _is_loopback(address):
            raise OutboundNetworkBlocked(
                f"BLOCKED outbound connect_ex to {address!r} from a test."
            )
        return real_connect_ex(self, address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield


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
