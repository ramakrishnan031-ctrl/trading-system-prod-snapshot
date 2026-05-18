"""
alerts/critical.py -- Trading System v2

Purpose:
    Write sentinel files for CRITICAL alerts so a separate watcher process
    can deliver them via email even if the trading process crashes immediately
    after. Single responsibility: file write.

Locked Design Decisions:
    CR1  -- Pure sentinel writer. No email, no telegram, no logging (logger
            may be unavailable during crash sequences).
    CR2  -- Sentinel path: <sentinel_dir>/critical_alert_<id>.flag
            id format: YYYYMMDD_HHMMSS_<8hex>
            Atomic write: write to .tmp, fsync, rename.
            JSON content: id, ts, title (max 120), body, source_module,
            context, hostname, pid (8 fields).
    CR3  -- write_critical_sentinel() raises OSError on filesystem failure.
            Returns Path to the .flag file.
    CR4  -- sentinel_dir created with mkdir(parents=True, exist_ok=True).
    CR5  -- Lifecycle: .flag -> .delivered (watcher success)
                                .failed   (watcher gives up)
            critical.py is pure writer; never reads/modifies existing files.
    CR6  -- list_pending_sentinels(sentinel_dir) -> list[Path]: *.flag only,
            sorted by mtime ascending.
    CR7  -- read_sentinel(path) -> dict: parses JSON. Raises ValueError on bad
            JSON, OSError on unreadable file.
    CR8  -- mark_delivered(path) -> Path: atomic rename .flag -> .delivered.
    CR9  -- mark_failed(path, reason) -> Path: rename to .failed AND write
            sibling <stem>.reason with failure detail.
    CR10 -- Layer 4 (alerts/). Imports: stdlib and core.time_authority.
"""
from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime
from pathlib import Path

from core.time_authority import now_ist

# DUP-1 (2026-04-26 audit): _IST removed; canonical tzinfo lives in
# core.time_authority. This module never read its own _IST anyway.
_TITLE_MAX = 120


# ------------------------------------------------------------------------------
# Public writer API (CR3)
# ------------------------------------------------------------------------------

def write_critical_sentinel(
    title: str,
    body: str,
    source_module: str,
    context: dict | None = None,
    sentinel_dir: Path | str = "data_store",
) -> Path:
    """
    Write an atomic sentinel .flag file for a CRITICAL alert (CR2, CR3).

    The file is written to a .tmp path, fsynced, then renamed to .flag so
    a crash mid-write leaves no corrupt sentinel (Foundation Rule 3.2).

    Args:
        title:         Short alert title. Truncated to 120 chars (CR2).
        body:          Alert body text.
        source_module: Module that raised the alert (e.g. "capital.fund_manager").
        context:       Optional dict of structured context fields.
        sentinel_dir:  Directory for sentinel files. Created if absent (CR4).

    Returns:
        Path to the written .flag file.

    Raises:
        OSError: on any filesystem failure. Caller must handle (CR3).
    """
    sentinel_dir = Path(sentinel_dir)
    sentinel_dir.mkdir(parents=True, exist_ok=True)  # CR4

    now = now_ist()
    uid = uuid.uuid4().hex[:8]
    sentinel_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uid}"

    payload = {
        "id": sentinel_id,
        "ts": now.isoformat(),
        "title": title[:_TITLE_MAX],
        "body": body,
        "source_module": source_module,
        "context": context if context is not None else {},
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
    }

    flag_path = sentinel_dir / f"critical_alert_{sentinel_id}.flag"
    tmp_path = flag_path.with_suffix(".tmp")

    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())

    # FIX-116: Use os.replace() for atomic rename on both POSIX and Windows.
    # Path.rename() is not atomic on Windows (can fail mid-operation if target exists).
    os.replace(tmp_path, flag_path)
    return flag_path


# ------------------------------------------------------------------------------
# Watcher helpers (CR6, CR7, CR8, CR9)
# ------------------------------------------------------------------------------

def list_pending_sentinels(sentinel_dir: Path | str) -> list[Path]:
    """
    Return all *.flag files in sentinel_dir, sorted by mtime ascending (CR6).

    .delivered, .failed, and .tmp files are excluded. Returns empty list if
    sentinel_dir does not exist.
    """
    d = Path(sentinel_dir)
    if not d.exists():
        return []
    flags = [p for p in d.iterdir() if p.suffix == ".flag"]
    flags.sort(key=lambda p: p.stat().st_mtime)
    return flags


def read_sentinel(path: Path | str) -> dict:
    """
    Parse a sentinel .flag file and return its dict (CR7).

    Raises:
        OSError:    if the file cannot be read.
        ValueError: if the file content is not valid JSON.
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8")  # raises OSError on failure
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed sentinel JSON in {path}: {exc}") from exc


def mark_delivered(path: Path | str) -> Path:
    """
    Rename <name>.flag -> <name>.delivered (CR8).

    Returns the new .delivered path.

    Raises:
        OSError: if the rename fails (e.g. file already gone).
    """
    path = Path(path)
    delivered = path.with_suffix(".delivered")
    path.rename(delivered)
    return delivered


def mark_failed(path: Path | str, reason: str) -> Path:
    """
    Rename <name>.flag -> <name>.failed and write a sibling .reason file (CR9).

    The .reason file receives the plain-text reason string so operators can
    inspect why delivery was abandoned.

    Returns the new .failed path.

    Raises:
        OSError: if the rename or reason-file write fails.
    """
    path = Path(path)
    failed = path.with_suffix(".failed")
    path.rename(failed)

    reason_path = failed.with_suffix(".reason")
    reason_path.write_text(reason, encoding="utf-8")
    return failed
