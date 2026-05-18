#!/usr/bin/env python
"""
scripts/alert_watcher.py -- Trading System v2

Purpose:
    Standalone CLI script invoked by systemd timer (or Windows Task Scheduler)
    every N seconds. Finds .flag sentinel files written by alerts/critical.py,
    sends each via SMTP email, renames to .delivered. Survives trading process
    crash (runs as a separate process).

Locked Design Decisions:
    AW1  -- Standalone CLI. Periodic invocation (not daemon). Survives process crash.
    AW2  -- CLI: python alert_watcher.py [--config <path>] [--once] [--dry-run]
            Default: read config, process all pending, exit.
    AW3  -- Lock file: data_store/alert_watcher.lock (pid stored inside).
            If lock exists + pid alive: exit 0 silently.
            If lock exists + pid dead (stale): clean up and proceed.
    AW4  -- Processing: list_pending -> read -> send_email -> mark_delivered.
            SmtpError -> increment counter; mark_failed at max_attempts.
    AW5  -- Attempt counter persisted in data_store/alert_watcher_attempts.json.
            Entries cleared for files no longer .flag.
    AW6  -- Email: Subject "[<SEV>] <title> [<host>:<pid>]".
            Body: plain-text. Uses smtplib.SMTP + STARTTLS or SSL.
    AW7  -- SMTP config via alerts.smtp section of system_config.yaml.
    AW8  -- Own log file: logs/alert_watcher.log (plain text, not JSON).
    AW9  -- Exit codes: 0=success, 1=config error, 2=SMTP auth failure.
    AW10 -- Layer 6. Imports: stdlib, alerts.critical, core.config_loader.
    AW11 -- Config additions: alerts.smtp + watcher_max_attempts, watcher_lock_path,
            watcher_log_path.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import socket
import sys
import concurrent.futures as _futures  # A.2: TimeoutError exception class
from concurrent.futures import ThreadPoolExecutor, as_completed

# A.2 (2026-04-25): hard wall-time bound on a single SMTP batch. Stuck
# tasks past this deadline are cancelled best-effort and treated as
# recoverable timeouts (counter increment, retry next pass). Tunable
# for tests; production value caps a watcher pass so a Gmail rate-limit
# window cannot stall the next pass.
_SMTP_TASK_TIMEOUT_SEC: float = 30.0
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

# Allow running directly from scripts/ or from project root
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from alerts.critical import (
    list_pending_sentinels,
    mark_delivered,
    mark_failed,
    read_sentinel,
)
from core.config_loader import load_all

# DUP-1 (2026-04-26 audit): _IST removed; never read locally.


# ------------------------------------------------------------------------------
# Lock file management (AW3)
# ------------------------------------------------------------------------------

def _is_process_alive(pid: int) -> bool:
    """
    FIX-105: Platform-specific process existence check.

    On Windows, os.kill(pid, 0) raises PermissionError for live processes,
    creating ambiguity. Use OpenProcess instead for reliable checking.
    """
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        # Unix: os.kill(pid, 0) works reliably
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False


def _acquire_lock(lock_path: Path) -> bool:
    """
    Try to acquire the watcher lock (AW3).

    Returns True if lock acquired, False if another live instance holds it.
    Cleans up stale lock (dead pid) automatically.
    """
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = None

        if pid is not None:
            if _is_process_alive(pid):
                # Another instance is running
                return False
            else:
                # Stale lock, clean up
                lock_path.unlink(missing_ok=True)

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock(lock_path: Path) -> None:
    """Release the lock file (AW3)."""
    lock_path.unlink(missing_ok=True)


# ------------------------------------------------------------------------------
# Attempt counter (AW5)
# ------------------------------------------------------------------------------

def _load_attempts(counter_path: Path) -> dict[str, int]:
    if not counter_path.exists():
        return {}
    try:
        return json.loads(counter_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_attempts(counter_path: Path, counters: dict[str, int]) -> None:
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(counters, indent=2)
    tmp = counter_path.with_suffix(".tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(counter_path)


def _prune_attempts(counters: dict[str, int], sentinel_dir: Path) -> dict[str, int]:
    """Remove entries for files that are no longer .flag (delivered/failed/gone)."""
    pending_names = {p.name for p in list_pending_sentinels(sentinel_dir)}
    return {k: v for k, v in counters.items() if k in pending_names}


# ------------------------------------------------------------------------------
# Email sending (AW6, AW7)
# ------------------------------------------------------------------------------

class SmtpError(Exception):
    """SMTP delivery failure (AW4)."""


class SmtpAuthError(SmtpError):
    """SMTP authentication failure -> exit 2 (AW9)."""


def _build_email(
    data: dict,
    from_address: str,
    to_addresses: list[str],
) -> MIMEText:
    """Build a plain-text MIMEText for the sentinel data (AW6)."""
    host = data.get("hostname", socket.gethostname())
    pid = data.get("pid", os.getpid())
    severity = data.get("context", {}).get("severity", "CRITICAL")
    title = data.get("title", "(no title)")

    subject = f"[{severity}] {title} [{host}:{pid}]"

    body_lines = [
        f"Alert ID   : {data.get('id', '?')}",
        f"Timestamp  : {data.get('ts', '?')}",
        f"Severity   : {severity}",
        f"Title      : {title}",
        f"Module     : {data.get('source_module', '?')}",
        f"Hostname   : {host}",
        f"PID        : {pid}",
        "",
        "--- Body ---",
        data.get("body", ""),
        "",
        "--- Context ---",
    ]
    for k, v in data.get("context", {}).items():
        body_lines.append(f"  {k}: {v}")

    msg = MIMEText("\n".join(body_lines), "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = ", ".join(to_addresses)
    return msg


def _build_digest_email(
    alerts: list[tuple[Path, dict]],
    from_address: str,
    to_addresses: list[str],
) -> MIMEText:
    """
    FIX-095: Build a digest email for multiple alerts.

    Args:
        alerts: List of (sentinel_path, alert_data) tuples, newest first
        from_address: SMTP from address
        to_addresses: SMTP to addresses

    Returns:
        MIMEText digest email
    """
    count = len(alerts)
    subject = f"[DIGEST] {count} CRITICAL ALERTS — Trading System"

    body_lines = [
        f"ALERT DIGEST: {count} critical alerts pending",
        f"Generated: {datetime.now().isoformat()}",
        "",
        "=" * 70,
        "",
    ]

    for i, (sentinel_path, data) in enumerate(alerts, start=1):
        severity = data.get("context", {}).get("severity", "CRITICAL")
        title = data.get("title", "(no title)")
        timestamp = data.get("ts", "?")
        module = data.get("source_module", "?")
        alert_id = data.get("id", "?")

        body_lines.append(f"Alert #{i} of {count}")
        body_lines.append(f"  ID       : {alert_id}")
        body_lines.append(f"  Time     : {timestamp}")
        body_lines.append(f"  Severity : {severity}")
        body_lines.append(f"  Title    : {title}")
        body_lines.append(f"  Module   : {module}")
        body_lines.append(f"  File     : {sentinel_path.name}")
        body_lines.append("")
        body_lines.append(f"  Summary  : {data.get('body', '')[:200]}")  # first 200 chars
        body_lines.append("")
        body_lines.append("-" * 70)
        body_lines.append("")

    body_lines.append("")
    body_lines.append(f"Total alerts in this digest: {count}")
    body_lines.append("")
    body_lines.append("NOTE: This is an aggregated digest. Individual alert files are")
    body_lines.append("available in the sentinel directory for detailed inspection.")

    msg = MIMEText("\n".join(body_lines), "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = ", ".join(to_addresses)
    return msg


def _send_email(smtp_cfg, data: dict, log: logging.Logger) -> None:
    """
    Send a single email via SMTP (AW6).

    Raises:
        SmtpAuthError: on authentication failure (exit 2).
        SmtpError:     on any other SMTP failure.
    """
    msg = _build_email(data, smtp_cfg.from_address, smtp_cfg.to_addresses)

    try:
        if smtp_cfg.use_tls:
            server = smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=smtp_cfg.timeout_sec)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(smtp_cfg.host, smtp_cfg.port, timeout=smtp_cfg.timeout_sec)

        try:
            # G.3 (2026-04-25): resolve password via env var when password_env
            # is configured; falls back to plaintext password (dev/test only).
            # Resolution can raise ValueError if the env var is unset; the
            # outer except clauses categorize that as SmtpError so the watcher
            # exits with the expected code path rather than crashing.
            password = smtp_cfg.resolved_password()
            server.login(smtp_cfg.username, password)
            server.sendmail(smtp_cfg.from_address, smtp_cfg.to_addresses, msg.as_string())
        finally:
            server.quit()

    except smtplib.SMTPAuthenticationError as exc:
        raise SmtpAuthError(f"SMTP authentication failed: {exc}") from exc
    except smtplib.SMTPException as exc:
        raise SmtpError(f"SMTP error: {exc}") from exc
    except OSError as exc:
        raise SmtpError(f"Network error: {exc}") from exc


# ------------------------------------------------------------------------------
# Logger setup (AW8)
# ------------------------------------------------------------------------------

def _setup_watcher_log(log_path: Path) -> logging.Logger:
    """Configure the watcher's own plain-text log file (AW8)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("alert_watcher")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    return log


# ------------------------------------------------------------------------------
# Main processing loop (AW4)
# ------------------------------------------------------------------------------

def run_once(
    cfg,
    dry_run: bool = False,
    log: logging.Logger | None = None,
) -> int:
    """
    Process all pending sentinels once (AW4).

    Returns 0 on success, 2 on SmtpAuthError.
    """
    if log is None:
        log = logging.getLogger("alert_watcher")

    alerts_cfg = cfg.system.alerts
    sentinel_dir = Path(alerts_cfg.sentinel_dir)
    max_attempts = alerts_cfg.watcher_max_attempts
    counter_path = sentinel_dir / "alert_watcher_attempts.json"
    smtp_cfg = alerts_cfg.smtp
    # FIX-095: digest threshold (default 3)
    digest_threshold = getattr(alerts_cfg, 'alert_digest_threshold', 3)

    counters = _load_attempts(counter_path)

    pending = list_pending_sentinels(sentinel_dir)
    if not pending:
        log.info("No pending sentinels found.")
        return 0

    log.info("Found %d pending sentinel(s).", len(pending))
    auth_error_exit = False

    # Audit #15: parse + dry-run handling serially; fan out SMTP network I/O
    # (the slow part) via ThreadPoolExecutor. File renames and counter updates
    # run on the caller thread after each future completes to keep the
    # attempt counter JSON single-writer.
    to_send: list[tuple[Path, dict]] = []
    for sentinel_path in pending:
        fname = sentinel_path.name

        try:
            data = read_sentinel(sentinel_path)
        except (OSError, ValueError) as exc:
            log.error("Corrupt sentinel %s: %s", fname, exc)
            if not dry_run:
                try:
                    mark_failed(sentinel_path, f"corrupt: {exc}")
                except OSError:
                    pass
            continue

        if dry_run:
            log.info("[dry-run] Would send email for %s", fname)
            continue

        to_send.append((sentinel_path, data))

    # FIX-095: Check if we should send digest or individual emails
    if to_send:
        send_digest = len(to_send) > digest_threshold

        if send_digest:
            log.info("FIX-095: %d alerts exceeds threshold %d → sending digest",
                     len(to_send), digest_threshold)
            # Sort by timestamp (newest first) for digest display
            to_send_sorted = sorted(
                to_send,
                key=lambda x: x[1].get('ts', ''),
                reverse=True
            )

            # Send one digest email
            try:
                msg = _build_digest_email(to_send_sorted, smtp_cfg.from_address,
                                         smtp_cfg.to_addresses)

                if smtp_cfg.use_tls:
                    server = smtplib.SMTP(smtp_cfg.host, smtp_cfg.port,
                                         timeout=smtp_cfg.timeout_sec)
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                else:
                    server = smtplib.SMTP_SSL(smtp_cfg.host, smtp_cfg.port,
                                             timeout=smtp_cfg.timeout_sec)

                try:
                    password = smtp_cfg.resolved_password()
                    server.login(smtp_cfg.username, password)
                    server.sendmail(smtp_cfg.from_address, smtp_cfg.to_addresses,
                                   msg.as_string())
                finally:
                    server.quit()

                # Mark ALL sentinels as delivered after successful digest send
                for sentinel_path, _ in to_send:
                    mark_delivered(sentinel_path)
                    counters.pop(sentinel_path.name, None)

                log.info("Digest delivered: %d alerts → .delivered", len(to_send))

            except smtplib.SMTPAuthenticationError as exc:
                log.error("SMTP auth failure (digest): %s", exc)
                return 2

            except (smtplib.SMTPException, OSError) as exc:
                log.error("SMTP error (digest): %s", exc)
                # Increment counter for all alerts in failed digest
                for sentinel_path, _ in to_send:
                    fname = sentinel_path.name
                    count = counters.get(fname, 0) + 1
                    counters[fname] = count
                    if count >= max_attempts:
                        try:
                            mark_failed(sentinel_path, f"digest failed: {exc}")
                            counters.pop(fname, None)
                        except OSError:
                            pass
        else:
            # Send individual emails (original behavior)
            max_workers = min(8, len(to_send))
            with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="alert-smtp"
            ) as pool:
                future_to_path = {
                    pool.submit(_send_email, smtp_cfg, data, log): path
                    for path, data in to_send
                }
                # A.2 (2026-04-25): bound the wall time spent waiting on
                # futures. Pre-fix, a stuck SMTP connection (Gmail rate-limit,
                # network blackhole) would hang the worker thread until the
                # smtplib timeout fires (often default 60s+) and block the
                # watcher's next pass. We use _futures.wait with a hard batch
                # timeout: futures still pending past the deadline are cancelled
                # best-effort and treated as recoverable SmtpError-equivalent
                # (counter increments; sentinel stays .flag for next pass).
                done, not_done = _futures.wait(
                    list(future_to_path.keys()),
                    timeout=_SMTP_TASK_TIMEOUT_SEC,
                )
                for future in done:
                    sentinel_path = future_to_path[future]
                    fname = sentinel_path.name
                    try:
                        future.result()
                        mark_delivered(sentinel_path)
                        counters.pop(fname, None)
                        log.info("Delivered %s -> .delivered", fname)

                    except SmtpAuthError as exc:
                        log.error("SMTP auth failure: %s", exc)
                        auth_error_exit = True
                        # Let remaining futures finish (cancellation is best-effort
                        # and SMTP sockets are already in flight); we'll exit 2.
                        for pending_future in future_to_path:
                            pending_future.cancel()

                    except SmtpError as exc:
                        count = counters.get(fname, 0) + 1
                        counters[fname] = count
                        log.error(
                            "SMTP error for %s (attempt %d/%d): %s",
                            fname, count, max_attempts, exc,
                        )
                        if count >= max_attempts:
                            try:
                                mark_failed(sentinel_path, str(exc))
                                counters.pop(fname, None)
                                log.error(
                                    "Abandoned %s after %d attempts -> .failed",
                                    fname, max_attempts,
                                )
                            except OSError:
                                pass

                # A.2: any future still pending past _SMTP_TASK_TIMEOUT_SEC is a
                # stuck send. Cancel best-effort; the underlying SMTP socket may
                # still be held by the worker thread until the smtplib socket
                # timeout fires, but we stop waiting on it and the watcher pass
                # proceeds. Treat as recoverable so the retry ladder applies.
                for future in not_done:
                    future.cancel()
                    sentinel_path = future_to_path[future]
                    fname = sentinel_path.name
                    count = counters.get(fname, 0) + 1
                    counters[fname] = count
                    log.error(
                        "SMTP timeout for %s (attempt %d/%d): send exceeded "
                        "%.1fs -- likely a stuck connection",
                        fname, count, max_attempts, _SMTP_TASK_TIMEOUT_SEC,
                    )
                    if count >= max_attempts:
                        try:
                            mark_failed(
                                sentinel_path,
                                f"timeout after {max_attempts} attempts",
                            )
                            counters.pop(fname, None)
                        except OSError:
                            pass

    # Prune entries for files no longer pending
    counters = _prune_attempts(counters, sentinel_dir)
    _save_attempts(counter_path, counters)

    return 2 if auth_error_exit else 0


# ------------------------------------------------------------------------------
# CLI entrypoint (AW2)
# ------------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Alert watcher: process critical sentinel files and send email."
    )
    parser.add_argument("--config", default=None, help="Path to config directory")
    parser.add_argument("--once", action="store_true", help="Run one pass and exit (default)")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Log actions without sending email or renaming files")
    args = parser.parse_args()

    # Load config (AW10)
    config_dir = Path(args.config) if args.config else None
    try:
        if config_dir:
            cfg = load_all(config_dir)
        else:
            cfg = load_all()
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    alerts_cfg = cfg.system.alerts
    log_path = Path(alerts_cfg.watcher_log_path)
    log = _setup_watcher_log(log_path)

    lock_path = Path(alerts_cfg.watcher_lock_path)

    if not _acquire_lock(lock_path):
        log.info("Another alert_watcher instance is running. Exiting.")
        return 0

    try:
        return run_once(cfg, dry_run=args.dry_run, log=log)
    finally:
        _release_lock(lock_path)


if __name__ == "__main__":
    sys.exit(main())
