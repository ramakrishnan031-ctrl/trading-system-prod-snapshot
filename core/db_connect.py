"""
core/db_connect.py -- Trading System v2  (O6: analytics DB split)

The high-volume, non-critical analytics tables (``candles``, ``system_metrics``,
``system_metrics_daily``) live in a SEPARATE SQLite file, ``analytics.db``, a
sibling of the main ``trading_system.db``. This keeps the trading DB small so
the nightly ``.backup`` is near-instant and WAL checkpoints on the hot DB stay
short (db_schema_review 15-Jun-2026, observation O6).

The split is transparent to query code: every connection to the trading DB
ATTACHes ``analytics.db`` as schema ``analytics``. Because the analytics tables
exist ONLY in that file (never in main), SQLite resolves an unqualified
``SELECT ... FROM candles`` to ``analytics.candles`` automatically — so existing
SQL keeps working unchanged.

Single source of truth for: the analytics file location, the set of relocated
tables, and the connect+attach sequence. ``StateStore`` and every raw-sqlite
script use this so the wiring can't drift.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Tables that live in analytics.db, NOT in trading_system.db.
ANALYTICS_TABLES: tuple[str, ...] = (
    "candles",
    "system_metrics",
    "system_metrics_daily",
)

ANALYTICS_DB_FILENAME = "analytics.db"
ANALYTICS_SCHEMA_NAME = "analytics"  # the ATTACH alias

# PRAGMAs applied to the attached analytics DB on each connection. WAL is
# persistent (set once on the file); synchronous is per-connection. We match
# the main DB's durability so a crash can't corrupt candle/metric history.
_ANALYTICS_PRAGMAS = (
    f"PRAGMA {ANALYTICS_SCHEMA_NAME}.journal_mode = WAL",
    f"PRAGMA {ANALYTICS_SCHEMA_NAME}.synchronous = FULL",
)

_SCHEMA_DIR = Path(__file__).resolve().parent
ANALYTICS_SCHEMA_PATH = _SCHEMA_DIR / "analytics_schema.sql"


def analytics_path_for(main_db_path: str | Path) -> Path:
    """Return the analytics.db path that sits beside ``main_db_path``."""
    return Path(main_db_path).parent / ANALYTICS_DB_FILENAME


def attach_analytics(conn: sqlite3.Connection, main_db_path: str | Path) -> Path:
    """
    ATTACH analytics.db onto an open connection and apply its PRAGMAs. Returns
    the analytics path. ATTACH creates the file if it does not yet exist.
    """
    apath = analytics_path_for(main_db_path)
    apath.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(
        f"ATTACH DATABASE ? AS {ANALYTICS_SCHEMA_NAME}", (str(apath),)
    )
    for pragma in _ANALYTICS_PRAGMAS:
        conn.execute(pragma)
    return apath


def init_analytics_schema(
    main_db_path: str | Path,
    schema_path: str | Path = ANALYTICS_SCHEMA_PATH,
) -> None:
    """
    Ensure analytics.db exists and has its tables. Applies analytics_schema.sql
    against a DIRECT connection to analytics.db (where the unqualified
    ``CREATE TABLE`` statements land in that file). Idempotent — the schema uses
    ``CREATE TABLE IF NOT EXISTS``.

    Run this BEFORE attaching analytics on a working connection, so relocation /
    inserts have the tables to write into.
    """
    apath = analytics_path_for(main_db_path)
    apath.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    conn = sqlite3.connect(str(apath))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.executescript(schema_sql)
    finally:
        conn.close()


def connect(
    main_db_path: str | Path,
    *,
    attach: bool = True,
    timeout: float = 30.0,
    **kwargs,
) -> sqlite3.Connection:
    """
    Open a raw sqlite3 connection to the trading DB with analytics ATTACHed.

    Drop-in replacement for ``sqlite3.connect(main_db_path)`` in scripts that
    read or write the analytics tables. ``busy_timeout`` is set for lock safety.
    The caller may still set ``conn.row_factory`` afterwards as before.
    """
    conn = sqlite3.connect(str(main_db_path), timeout=timeout, **kwargs)
    conn.execute("PRAGMA busy_timeout = 30000")
    if attach:
        attach_analytics(conn, main_db_path)
    return conn
