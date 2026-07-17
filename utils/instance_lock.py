"""
utils/instance_lock.py -- Trading System v2

Single-instance enforcement via an OS-owned file lock + a persistent socket lock.

Two instances trading the same book would double every order, so this guard must
hold. It must equally never REFUSE a legitimate restart: main() exits 1 when the
lock is refused, and the unit's RestartPreventExitStatus is "3 4" — so exit 1 is
restarted, and a lock that wrongly reports "already running" becomes a restart
loop, i.e. a full outage. Both properties are load-bearing; X5 tests both.

FIX-081 bound a persistent socket to a lock port (default 5001), held open for
the process lifetime, replacing a TOCTOU-prone check_port_available() call.

X5 (17-Jul-2026) — what that left, and what changed:

  * The PID-liveness check was the ENFORCEMENT layer, and PIDs are recycled. A
    crash leaves the file behind (release never runs); if the OS has since reused
    that PID for ANY unrelated process, _pid_is_alive() says True and the restart
    is refused forever. Enforcement now comes from a lock the KERNEL owns and
    drops when the process dies — including on SIGKILL, OOM-kill or power loss.
    A stale lock is therefore not possible, rather than merely unlikely. The PID
    in the file is now informational only: it names the holder in the operator
    message, and nothing branches on whether it is alive.

  * SO_REUSEADDR was removed. Measured, both platforms, second bind of a
    LISTENING 127.0.0.1 port:
        Linux   (prod VM) : REFUSED, EADDRINUSE(98) — with or without it
        Windows (dev PC)  : SUCCEEDS with it, REFUSED without it
    So SO_REUSEADDR bought nothing on Linux (it only permits rebinding a
    TIME_WAIT port, and a lock socket that never accept()s never has a
    connection to leave one) while on Windows it actively let a second instance
    bind. SO_EXCLUSIVEADDRUSE is the Windows spelling of the Linux default.

  * The lock file is never unlinked. The lock lives on the INODE via an open fd,
    so unlinking the path while another process holds it would let the next start
    create a fresh inode, lock that, and run alongside. Leaving a 64-byte file in
    the lock dir is the cost of closing that race; correctness depends on the
    lock, never on the file's existence.
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

# X5: the fd carrying the OS lock. Held open for the whole process lifetime —
# closing it releases the lock, which is exactly what must not happen early.
_lock_fd: Optional[int] = None

# Windows byte-range locks are MANDATORY: a locked region cannot be read by the
# other process. Lock a byte far past the PID text so a refused instance can
# still read the PID to name the holder. Windows permits locking beyond EOF.
_LOCK_BYTE_OFFSET = 1024
_PID_FIELD_WIDTH = 64   # fixed-width so the PID can be rewritten without truncating


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


def _lock_fd_exclusive(fd: int) -> bool:
    """Take the OS-owned exclusive, non-blocking lock on `fd`. True if acquired.

    The kernel owns this lock and drops it when the fd closes — on clean exit,
    SIGKILL, OOM-kill or power loss alike. That is the entire point: a lock that
    cannot survive its holder cannot go stale, so it cannot block a restart."""
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, _LOCK_BYTE_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False   # held by a live process


def _unlock_fd(fd: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, _LOCK_BYTE_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass   # closing the fd releases it regardless


def _read_holder_pid(fd: int) -> str:
    """The PID recorded in the lock file — for the operator message only."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, _PID_FIELD_WIDTH).decode("ascii", "replace").strip() or "unknown"
    except OSError:
        return "unknown"


def acquire_instance_lock(lock_port: int = _LOCK_PORT) -> tuple[bool, str]:
    """
    Attempt to acquire the single-instance lock.

    Layer 1 (X5, authoritative): an OS-owned exclusive lock on _LOCK_FILE, held
    for the process lifetime. A second concurrent start cannot take it; a dead
    instance cannot keep it.

    Layer 2 (FIX-081): a persistent socket bound to `lock_port`, also held for
    the process lifetime, which additionally reserves the port itself.

    Args:
        lock_port: TCP port to bind as the lock socket (default 5001, configurable
                   via system_config.yaml or passed explicitly).

    Returns:
        (True, "") on success — lock acquired, caller proceeds.
        (False, reason) if another instance is running or lock failed.
    """
    global _lock_socket, _lock_fd

    # Step 1: OS-owned file lock (authoritative single-instance guard).
    try:
        fd = os.open(str(_LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        # Fail CLOSED, as before: if the guard cannot run, do not trade.
        return False, f"Cannot open lock file {_LOCK_FILE}: {exc}"

    if not _lock_fd_exclusive(fd):
        holder = _read_holder_pid(fd)
        try:
            os.close(fd)
        except OSError:
            pass
        return False, (
            f"Another instance is running (PID {holder}, lock={_LOCK_FILE}). "
            f"Kill it first: kill {holder}"
        )

    # Record our PID for the operator message. Fixed-width and never truncated:
    # truncation would drop the locked byte at _LOCK_BYTE_OFFSET on Windows.
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).ljust(_PID_FIELD_WIDTH).encode("ascii"))
    except OSError:
        pass   # informational only — never fail an acquired lock over this

    _lock_fd = fd   # keep open: closing it would release the lock

    # Step 2: FIX-081 — bind persistent socket lock.
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows SO_REUSEADDR lets a second socket bind a LISTENING port;
            # SO_EXCLUSIVEADDRUSE restores the semantics Linux gives by default.
            _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
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
        _release_file_lock()   # do not hold layer 1 after refusing the start
        return False, (
            f"Port {lock_port} already in use (another instance running). "
            f"Original error: {exc}"
        )

    return True, ""


def _release_file_lock() -> None:
    """Drop the OS lock. The file itself is deliberately left in place: the lock
    is on the inode, and unlinking a path another instance already holds would
    let the next start lock a fresh inode and run alongside it."""
    global _lock_fd
    if _lock_fd is None:
        return
    _unlock_fd(_lock_fd)
    try:
        os.close(_lock_fd)
    except OSError:
        pass
    _lock_fd = None


def release_instance_lock() -> None:
    """
    Release both lock layers on clean shutdown.

    Correctness does not depend on this running: the kernel drops the file lock
    and the OS reclaims the socket when the process dies by any means. This
    releases them promptly so an immediate restart need not wait.
    """
    global _lock_socket

    # Close socket lock (FIX-081)
    if _lock_socket is not None:
        try:
            _lock_socket.close()
        except Exception:  # FIX-106: Don't suppress KeyboardInterrupt/SystemExit
            pass
        _lock_socket = None

    _release_file_lock()


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
