"""
ops_dashboard/backend/readers/log_reader.py

M13 Logs Viewer file access — READ-ONLY, hardened:
  * WHITELIST: only files directly inside cfg.paths.logs_dir. The `file` param
    must be a bare basename (no separators, no '..'); the resolved realpath is
    then re-checked against the logs dir. Anything else → PermissionError.
  * EFFICIENT TAIL: seek-from-end block reads (64 KiB) — never a full-file
    read; hard caps: 2000 lines / 8 MiB scanned. Returns bytes_read so tests
    can PROVE the 10 MB fixture was not fully read (V5).
  * JSON-lines parsed best-effort per line; download is not implemented anywhere.
"""
from __future__ import annotations

import json
import os
from typing import Optional

MAX_TAIL_LINES = 2000
_BLOCK = 64 * 1024
_MAX_SCAN_BYTES = 8 * 1024 * 1024

_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _logs_dir(cfg: dict) -> str:
    return os.path.realpath(cfg["paths"]["logs_dir"])


def list_log_files(cfg: dict) -> list:
    """Files directly inside the logs dir (no recursion): name/size/mtime."""
    root = _logs_dir(cfg)
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        out.append({"name": name, "size_bytes": st.st_size, "mtime": st.st_mtime})
    return out


def resolve_log_path(cfg: dict, filename: str) -> str:
    """Whitelist gate. Raises PermissionError on ANY traversal attempt."""
    if not filename or filename != os.path.basename(filename) or ".." in filename \
            or "/" in filename or "\\" in filename:
        raise PermissionError("invalid log filename")
    root = _logs_dir(cfg)
    path = os.path.realpath(os.path.join(root, filename))
    # realpath containment re-check (symlink / case tricks)
    if os.path.commonpath([root, path]) != root:
        raise PermissionError("path escapes the logs directory")
    if not os.path.isfile(path):
        raise FileNotFoundError(filename)
    return path


def tail_lines(path: str, n: int = 200) -> dict:
    """Last n lines via backward block reads. Never reads the whole file."""
    n = max(1, min(int(n), MAX_TAIL_LINES))
    size = os.path.getsize(path)
    lines: list = []
    bytes_read = 0
    with open(path, "rb") as fh:
        pos = size
        buf = b""
        while pos > 0 and buf.count(b"\n") <= n and bytes_read < _MAX_SCAN_BYTES:
            step = min(_BLOCK, pos)
            pos -= step
            fh.seek(pos)
            chunk = fh.read(step)
            bytes_read += step
            buf = chunk + buf
        text = buf.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    if pos > 0 and all_lines:
        all_lines = all_lines[1:]   # first line may be partial (mid-file cut)
    lines = all_lines[-n:]
    return {"lines": lines, "bytes_read": bytes_read, "file_size": size,
            "truncated_scan": bytes_read >= _MAX_SCAN_BYTES}


def parse_structured(lines: list, level: Optional[str] = None,
                     q: Optional[str] = None, ref_id: Optional[str] = None) -> list:
    """Best-effort JSON-lines parse + filters (level / free-text / id search).

    ref_id matches signal_id / trade_id / order_id fields (and raw text as a
    fallback so plain-text files are searchable too).
    """
    level = level.upper() if level and level.upper() in _LEVELS else None
    q_low = q.lower() if q else None
    out = []
    for raw in lines:
        rec = None
        s = raw.strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    rec = parsed
            except ValueError:
                rec = None
        row = {
            "raw": raw,
            "json": rec is not None,
            "ts": rec.get("ts") if rec else None,
            "level": rec.get("level") if rec else None,
            "logger": rec.get("logger") if rec else None,
            "msg": rec.get("msg") if rec else raw,
            "signal_id": rec.get("signal_id") if rec else None,
            "trade_id": rec.get("trade_id") if rec else None,
            "order_id": rec.get("order_id") if rec else None,
        }
        if level and (row["level"] or "").upper() != level:
            continue
        if q_low and q_low not in raw.lower():
            continue
        if ref_id:
            ids = (row["signal_id"], row["trade_id"], row["order_id"])
            if ref_id not in [i for i in ids if i] and ref_id not in raw:
                continue
        out.append(row)
    return out
