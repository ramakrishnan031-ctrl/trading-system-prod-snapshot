"""
utils/instance_lock.py -- Trading System v2

Single-instance enforcement via PID lock file + port availability check.
Prevents stale manual processes from silently stealing the webhook port.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


_LOCK_DIR = Path("/tmp") if sys.platform != "win32" else Path(os.environ.get("TEMP", "C:/Temp"))
_LOCK_FILE = _LOCK_DIR / "trading-system.lock"


def _pid_is_alive(pid: int) -> bool:
    """Check if a process with given PID exists (Linux/Windows)."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def acquire_instance_lock() -> tuple[bool, str]:
    """
    Attempt to acquire the single-instance lock.

    Returns:
        (True, "") on success — lock acquired, caller proceeds.
        (False, reason) if another instance is running.
    """
    if _LOCK_FILE.exists():
        try:
            existing_pid = int(_LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            _LOCK_FILE.unlink(missing_ok=True)
        else:
            if _pid_is_alive(existing_pid):
                return False, (
                    f"Another instance running (PID {existing_pid}, lock={_LOCK_FILE}). "
                    f"Kill it first: kill {existing_pid}"
                )
            _LOCK_FILE.unlink(missing_ok=True)

    try:
        _LOCK_FILE.write_text(str(os.getpid()))
    except OSError as exc:
        return False, f"Cannot write lock file {_LOCK_FILE}: {exc}"

    return True, ""


def release_instance_lock() -> None:
    """Remove the lock file on clean shutdown."""
    try:
        if _LOCK_FILE.exists():
            pid_in_file = int(_LOCK_FILE.read_text().strip())
            if pid_in_file == os.getpid():
                _LOCK_FILE.unlink(missing_ok=True)
    except (ValueError, OSError):
        pass


def check_port_available(port: int, host: str = "127.0.0.1") -> tuple[bool, str]:
    """
    Check if a TCP port is free to bind.

    Returns:
        (True, "") if available.
        (False, reason) if already bound by another process.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        result = sock.connect_ex((host, port))
        if result == 0:
            return False, f"Port {port} already in use (another process bound to {host}:{port})"
        return True, ""
    finally:
        sock.close()
