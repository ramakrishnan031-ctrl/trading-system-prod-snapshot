"""
utils/instance_lock.py -- Trading System v2

Single-instance enforcement via PID lock file + persistent socket lock.

FIX-081: Replaces TOCTOU-prone check_port_available() with a persistent socket
bound to a dedicated lock port (default 5001). The socket is held open for the
entire process lifetime. If binding fails (OSError: Address already in use),
another instance is running. This eliminates the race where something binds
the port between check and Flask startup.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Optional


_LOCK_DIR = Path("/tmp") if sys.platform != "win32" else Path(os.environ.get("TEMP", "C:/Temp"))
_LOCK_FILE = _LOCK_DIR / "trading-system.lock"

# FIX-081: Persistent socket lock (held for entire process lifetime)
_lock_socket: Optional[socket.socket] = None
_LOCK_PORT = 5001  # Configurable via acquire_instance_lock(lock_port=...)


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


def acquire_instance_lock(lock_port: int = _LOCK_PORT) -> tuple[bool, str]:
    """
    Attempt to acquire the single-instance lock.

    FIX-081: Binds a persistent socket to `lock_port` (default 5001) and holds
    it open for the entire process lifetime. If binding fails, another instance
    is running. Eliminates TOCTOU race between check and Flask startup.

    Args:
        lock_port: TCP port to bind as the lock socket (default 5001, configurable
                   via system_config.yaml or passed explicitly).

    Returns:
        (True, "") on success — lock acquired, caller proceeds.
        (False, reason) if another instance is running or lock failed.
    """
    global _lock_socket

    # Step 1: Check PID lock file
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

    # Step 2: Write PID lock file
    try:
        _LOCK_FILE.write_text(str(os.getpid()))
    except OSError as exc:
        return False, f"Cannot write lock file {_LOCK_FILE}: {exc}"

    # Step 3: FIX-081 — Bind persistent socket lock
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _lock_socket.bind(("127.0.0.1", lock_port))
        # listen(1) minimal backlog; we never accept() on this socket
        _lock_socket.listen(1)
    except OSError as exc:
        # Address already in use → another instance is running
        if _lock_socket is not None:
            try:
                _lock_socket.close()
            except Exception:  # FIX-106: Don't suppress KeyboardInterrupt/SystemExit
                pass
            _lock_socket = None
        _LOCK_FILE.unlink(missing_ok=True)  # Clean up PID file
        return False, (
            f"Port {lock_port} already in use (another instance running). "
            f"Original error: {exc}"
        )

    return True, ""


def release_instance_lock() -> None:
    """
    Remove the lock file and close the lock socket on clean shutdown.

    FIX-081: Closes the persistent socket lock so the port is released.
    """
    global _lock_socket

    # Close socket lock (FIX-081)
    if _lock_socket is not None:
        try:
            _lock_socket.close()
        except Exception:  # FIX-106: Don't suppress KeyboardInterrupt/SystemExit
            pass
        _lock_socket = None

    # Remove PID lock file
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
