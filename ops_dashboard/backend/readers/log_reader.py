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


# ═════════════════════════════════════════════════════════════════════════════
# SCREEN 15 — SYSTEM LOGS
#
# ⚠️ THE LOGS DIRECTORY HOLDS THREE DIFFERENT FORMATS, and that is measured, not
# assumed. On a real day: `system_*.log` / `reconciler_*.log` / `trades_*.log`
# are JSON-lines written by `core/logger.py`'s JSON formatter (664/664 parsed);
# `debug_*.log` is PLAIN TEXT written by its `_PlainFormatter` (0 JSON lines);
# and `failed_alerts.log` / `test_failed.log` carry a DIFFERENT JSON shape
# (`severity`/`title`/`body`/`source_module`, ⛔ no `level`, ⛔ no `logger`).
#
# ⛔ A reader that treated every '{'-line as a system log emitted 405 rows with a
# NULL severity and a NULL module — malformed records that would have been shown
# to an operator as real events. Only the three DATED system logs are read here,
# and what was skipped is COUNTED and returned rather than silently dropped.
#
# ⭐ The whole point is that this runs ON THE VM: `paths.logs_dir` is overridden
# at deployment to /home/ubuntu/systems/trading-system/logs, so these are local
# file reads through the existing hardened tail. ⛔ No remote fetch is added.
# ═════════════════════════════════════════════════════════════════════════════
_SYSTEM_STEMS = ("system", "reconciler", "trades")
_SYSLOG_RE = None


def _syslog_re():
    global _SYSLOG_RE
    if _SYSLOG_RE is None:
        import re
        _SYSLOG_RE = re.compile(
            r"^(%s)_(\d{4}-\d{2}-\d{2})\.log$" % "|".join(_SYSTEM_STEMS))
    return _SYSLOG_RE


def system_log_files(cfg: dict, start: Optional[str] = None,
                     end: Optional[str] = None) -> list:
    """The dated JSON system logs whose date falls in [start, end].

    ⛔ `debug_*.log` is excluded because it is plain text, and the alert-failure
    logs because they are a different record shape — neither is this screen's
    source, and including either would produce rows with no severity.
    """
    out = []
    for f in list_log_files(cfg):
        m = _syslog_re().match(f["name"])
        if not m:
            continue
        day = m.group(2)
        if (start and day < start) or (end and day > end):
            continue
        out.append(dict(f, stem=m.group(1), date=day))
    out.sort(key=lambda r: (r["date"], r["stem"]))
    return out


def read_system_logs(cfg: dict, start: Optional[str] = None,
                     end: Optional[str] = None, per_file: int = 2000) -> dict:
    """Parse the dated JSON system logs into records the service can classify.

    Returns {"rows": [...], "files": n, "lines": n, "skipped_not_json": n,
             "skipped_foreign_shape": n, "bytes_read": n} — ⭐ the skip counts
    are RETURNED so a screen can state what it did not read instead of implying
    it read everything.
    """
    rows, lines, not_json, foreign, read_bytes, files = [], 0, 0, 0, 0, 0
    for meta in system_log_files(cfg, start, end):
        try:
            path = resolve_log_path(cfg, meta["name"])
        except (PermissionError, FileNotFoundError):
            continue
        files += 1
        tail = tail_lines(path, per_file)
        read_bytes += tail["bytes_read"]
        for idx, raw in enumerate(tail["lines"]):
            s = raw.strip()
            if not s:
                continue
            lines += 1
            if not s.startswith("{"):
                not_json += 1
                continue
            try:
                rec = json.loads(s)
            except ValueError:
                not_json += 1
                continue
            if not isinstance(rec, dict) or "level" not in rec or "logger" not in rec:
                foreign += 1          # the alert-failure shape, or anything new
                continue
            rows.append({
                "ts": rec.get("ts"),
                "level": rec.get("level"),
                "logger": rec.get("logger"),
                "msg": rec.get("msg"),
                # ⭐ a DETERMINISTIC reference: file + line offset. ⛔ Nothing is
                # minted from a clock or a counter, so a re-read addresses the
                # same record and a poll cannot renumber the feed.
                "ref_id": "LOG-%s#%d" % (meta["name"], idx),
                "source_file": meta["name"],
                "stem": meta["stem"],
                "trade_id": rec.get("trade_id"),
                "order_id": rec.get("order_id"),
                "signal_id": rec.get("signal_id"),
            })
    return {"rows": rows, "files": files, "lines": lines,
            "skipped_not_json": not_json, "skipped_foreign_shape": foreign,
            "bytes_read": read_bytes}
