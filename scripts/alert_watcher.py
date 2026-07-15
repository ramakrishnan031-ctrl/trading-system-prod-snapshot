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
import signal
import sys
import threading
import concurrent.futures as _futures  # A.2: TimeoutError exception class
from concurrent.futures import ThreadPoolExecutor, as_completed

# A.2 (2026-04-25): hard wall-time bound on a single SMTP batch. Stuck
# tasks past this deadline are cancelled best-effort and treated as
# recoverable timeouts (counter increment, retry next pass). Tunable
# for tests; production value caps a watcher pass so a Gmail rate-limit
# window cannot stall the next pass.
_SMTP_TASK_TIMEOUT_SEC: float = 30.0
from datetime import datetime
from email.mime.multipart import MIMEMultipart
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
from core.time_authority import now_ist

# Broker account tag for alert subjects (the locked primary account in
# config/accounts.csv, is_primary=TRUE). Surfaced in every alert subject so the
# recipient can identify the account at a glance: "[LFL836] CRITICAL — <title>".
_ACCOUNT_TAG = "LFL836"

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
):
    """Build the email for the sentinel data (AW6).

    Plain text by default. When ``content_type == "text/html"`` the sentinel
    carries an HTML body + a required ``plain_fallback`` (Cron Officer rich
    report) and we send a multipart/alternative message. Subject defaults to
    ``[LFL836] <SEVERITY> — <title>`` unless the sentinel supplies a verbatim
    ``subject`` (Cron Officer severity/ban prefixes).
    """
    severity = data.get("context", {}).get("severity", "CRITICAL")
    title = data.get("title", "(no title)")
    subject = data.get("subject") or f"[{_ACCOUNT_TAG}] {severity} — {title}"

    content_type = data.get("content_type", "text/plain")
    if content_type == "text/html":
        # Foundation Rule "Fail Fast": HTML without a plain mirror is rejected
        # loudly so a render bug can't ship an unreadable email.
        plain = data.get("plain_fallback")
        if not plain:
            raise ValueError(
                "sentinel content_type=text/html requires a non-empty plain_fallback"
            )
        html = data.get("html_body") or plain
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))  # last part = preferred
    else:
        # Compact context (one line, severity omitted since it's already in subject).
        ctx = {k: v for k, v in data.get("context", {}).items() if k != "severity"}
        body_lines = [
            (data.get("body", "") or "").strip() or "(no details)",
            "",
            f"Severity: {severity} | Module: {data.get('source_module', '?')}",
            f"Time: {data.get('ts', '?')} | Alert: {data.get('id', '?')}",
        ]
        if ctx:
            body_lines.append("Context: " + " | ".join(f"{k}={v}" for k, v in ctx.items()))
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
    subject = f"[{_ACCOUNT_TAG}] CRITICAL — DIGEST: {count} alerts"

    body_lines = [
        f"{count} CRITICAL alerts pending — {datetime.now().strftime('%Y-%m-%d %H:%M')}:",
        "",
    ]

    for i, (sentinel_path, data) in enumerate(alerts, start=1):
        severity = data.get("context", {}).get("severity", "CRITICAL")
        title = data.get("title", "(no title)")
        timestamp = data.get("ts", "?")
        summary = (data.get("body", "") or "").strip().splitlines()
        first_line = summary[0][:160] if summary else ""

        body_lines.append(f"{i}. [{severity}] {title} — {timestamp}")
        if first_line:
            body_lines.append(f"   {first_line}")

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
# F1 (15-Jul-2026): SMTP-failure robustness — Telegram fallback + backoff +
# degraded telemetry.
#
# Root cause: on an SMTP auth failure run_once returned 2 → systemd (Restart=always,
# 10s) crash-looped, the log grew every pass, and — being email-ONLY — the sentinels
# were unrecoverable. Now a DELIVERY failure keeps the watcher alive (it never exits
# non-zero for a delivery fault), routes each stuck sentinel to the proven direct-
# Telegram channel (CLASS 2 closed), backs off the dead SMTP (no per-pass spam), and
# publishes a machine-visible "delivery degraded" marker the canary/Officer can see.
# ------------------------------------------------------------------------------

# Skip re-hitting a dead SMTP for a growing window (base × 2^(n-1), capped) after
# consecutive auth failures, so a broken credential cannot spam the log/CPU on every
# ~10s pass; new sentinels route straight to Telegram during the window.
_SMTP_BACKOFF_BASE_SEC = 60.0
_SMTP_BACKOFF_MAX_SEC = 1800.0
_SMTP_STATE_FILE = "alert_watcher_smtp_state.json"
_DEGRADED_MARKER_FILE = "alert_watcher_degraded.json"


def _load_smtp_state(sentinel_dir: Path) -> dict:
    p = sentinel_dir / _SMTP_STATE_FILE
    if not p.exists():
        return {"consecutive_auth_fails": 0, "last_fail_iso": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"consecutive_auth_fails": 0, "last_fail_iso": None}


def _save_smtp_state(sentinel_dir: Path, state: dict) -> None:
    p = sentinel_dir / _SMTP_STATE_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _smtp_in_backoff(state: dict) -> bool:
    """True if an auth failure occurred within the current backoff window — skip the
    dead SMTP this pass and go straight to Telegram."""
    n = int(state.get("consecutive_auth_fails", 0) or 0)
    last = state.get("last_fail_iso")
    if n <= 0 or not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return False
    window = min(_SMTP_BACKOFF_BASE_SEC * (2 ** (n - 1)), _SMTP_BACKOFF_MAX_SEC)
    return (now_ist() - last_dt).total_seconds() < window


def _record_smtp_failure(sentinel_dir: Path, state: dict) -> None:
    state["consecutive_auth_fails"] = int(state.get("consecutive_auth_fails", 0) or 0) + 1
    state["last_fail_iso"] = now_ist().isoformat()
    _save_smtp_state(sentinel_dir, state)


def _reset_smtp_state(sentinel_dir: Path) -> None:
    _save_smtp_state(sentinel_dir, {"consecutive_auth_fails": 0, "last_fail_iso": None})


def _telegram_notifier(log: logging.Logger):
    """Build the proven direct-Telegram notifier (reuses TelegramNotifier.from_env —
    the same path the emitters use). None if telegram is unconfigured/disabled → the
    caller leaves the sentinel .flag for the next pass (never silently drops it)."""
    try:
        from alerts.telegram_notifier import TelegramNotifier
        return TelegramNotifier.from_env(logger=log)
    except Exception as exc:  # noqa: BLE001 — telegram-build failure must not crash the watcher
        log.error("telegram fallback unavailable: %s", exc)
        return None


def _telegram_fallback_batch(pending, notifier, log: logging.Logger) -> tuple[int, int]:
    """Deliver each still-pending sentinel via Telegram (write_sentinel=False — the
    sentinel already exists on disk). mark_delivered on success. Returns (delivered, stuck)."""
    if notifier is None:
        return 0, len(pending)
    delivered = stuck = 0
    for sp in pending:
        try:
            data = read_sentinel(sp)
        except (OSError, ValueError):
            stuck += 1
            continue
        sev = data.get("context", {}).get("severity", "CRITICAL")
        res = notifier.send(
            severity=sev if sev in ("CRITICAL", "ERROR", "WARNING", "INFO") else "CRITICAL",
            title=data.get("title", "(no title)"),
            body=(data.get("body", "") or "")[:3500],
            source_module=data.get("source_module", "alert_watcher"),
            write_sentinel=False,   # the sentinel already exists — do not rewrite it
        )
        if getattr(res, "success", False):
            try:
                mark_delivered(sp)
                delivered += 1
                log.info("Delivered %s via TELEGRAM fallback -> .delivered", sp.name)
            except OSError:
                stuck += 1
        else:
            stuck += 1
    return delivered, stuck


def _write_degraded_marker(sentinel_dir: Path, reason: str, via_tg: int, stuck: int,
                           log: logging.Logger) -> None:
    """Publish a machine-visible 'email delivery degraded' marker (read by the monitoring
    canary + the Cron Officer). Presence = email delivery is down; content = reason + counts."""
    p = sentinel_dir / _DEGRADED_MARKER_FILE
    payload = {"degraded": True, "reason": reason, "since": now_ist().isoformat(),
               "last_via_telegram": via_tg, "last_stuck": stuck}
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        log.error("degraded-marker write failed: %s", exc)


def _clear_degraded_marker(sentinel_dir: Path) -> None:
    (sentinel_dir / _DEGRADED_MARKER_FILE).unlink(missing_ok=True)


# ------------------------------------------------------------------------------
# Logger setup (AW8)
# ------------------------------------------------------------------------------

def _setup_watcher_log(log_path: Path) -> logging.Logger:
    """Configure the watcher's own plain-text log file (AW8).

    F4 (15-Jul-2026): DATE-EMBED the filename (logs/alert_watcher_<YYYY-MM-DD>.log) — one
    file per day, cleaned by the log_cleanup cron (`find logs -name '*.log' -mtime +30`),
    matching the system-wide convention (Foundation Rule 1.7: date-embedded names, NOT a
    RotatingFileHandler mid-day split). This bounds the previously-unbounded single
    alert_watcher.log (14 MB during the SMTP loop); F1 removes the growth SOURCE, this
    caps accumulation. (--once model recomputes the date each invocation → correct daily
    files; a long-lived --loop would roll on restart.)"""
    dated = log_path.parent / f"{log_path.stem}_{now_ist().strftime('%Y-%m-%d')}{log_path.suffix}"
    dated.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("alert_watcher")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fh = logging.FileHandler(dated, encoding="utf-8")
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
    auth_error_exit = False   # retained name; run_once no longer exits non-zero on a delivery fault
    smtp_auth_failed = False
    state = _load_smtp_state(sentinel_dir)
    # F1: after consecutive SMTP auth failures, skip the dead SMTP this pass (time-based
    # backoff) and route sentinels straight to the Telegram fallback — no per-pass 535 spam.
    skip_smtp = _smtp_in_backoff(state)
    if skip_smtp:
        log.info("SMTP in backoff (%s consecutive auth fail[s]) — routing to Telegram fallback",
                 state.get("consecutive_auth_fails", 0))
        smtp_auth_failed = True

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
    if to_send and not skip_smtp:
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
                # F1: do NOT return 2 (that crash-looped systemd). Flag for the Telegram
                # fallback + degraded telemetry below; the watcher stays alive.
                log.error("SMTP auth failure (digest): %s", exc)
                smtp_auth_failed = True

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
                        # F1: do NOT exit 2. Flag for the Telegram fallback + degraded
                        # telemetry below; cancel remaining sends (SMTP is down).
                        log.error("SMTP auth failure: %s", exc)
                        smtp_auth_failed = True
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

    # F1: Telegram fallback — a dead SMTP must never blind the operator (CLASS 2).
    # Deliver anything email could not send this pass via the proven direct-Telegram
    # channel; publish a machine-visible "degraded" marker; back off the dead SMTP.
    if smtp_auth_failed:
        if not skip_smtp:            # a FRESH auth failure (not merely a backoff-skip pass)
            _record_smtp_failure(sentinel_dir, state)
        notifier = _telegram_notifier(log)
        still_pending = list_pending_sentinels(sentinel_dir)
        via_tg, stuck = _telegram_fallback_batch(still_pending, notifier, log)
        _write_degraded_marker(sentinel_dir,
                               "SMTP auth failure — email delivery down", via_tg, stuck, log)
        log.error("EMAIL DELIVERY DEGRADED: SMTP auth failed; %d sentinel(s) delivered via "
                  "Telegram fallback, %d still pending (retry next pass)", via_tg, stuck)
    else:
        # Email healthy this pass (or nothing to send) — clear any degraded state.
        if int(state.get("consecutive_auth_fails", 0) or 0):
            _reset_smtp_state(sentinel_dir)
        _clear_degraded_marker(sentinel_dir)

    # Prune entries for files no longer pending
    counters = _prune_attempts(counters, sentinel_dir)
    _save_attempts(counter_path, counters)

    # F1: a DELIVERY failure is NOT a crash — never return non-zero for a delivery/auth
    # fault (the cause of the 10s systemd crash-loop). main() still returns 1 on config error.
    _ = auth_error_exit  # retained for API stability; delivery faults no longer exit 2
    return 0


# ------------------------------------------------------------------------------
# CLI entrypoint (AW2)
# ------------------------------------------------------------------------------

def _write_heartbeat(heartbeat_path: Optional[Path], log: logging.Logger) -> None:
    """P5: stamp a liveness heartbeat (IST ISO timestamp) after each --loop pass so a
    watchdog can detect a hung/dead loop. Best-effort: a write failure is logged, never
    fatal — the alert path must not die because the heartbeat file is unwritable."""
    if heartbeat_path is None:
        return
    try:
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = heartbeat_path.with_suffix(heartbeat_path.suffix + ".tmp")
        tmp.write_text(now_ist().isoformat(), encoding="utf-8")
        os.replace(tmp, heartbeat_path)   # atomic swap
    except Exception as exc:  # noqa: BLE001
        log.warning("alert_watcher heartbeat write failed (%s): %s", heartbeat_path, exc)


def run_loop(
    cfg,
    *,
    interval_sec: float,
    heartbeat_path: Optional[Path],
    dry_run: bool,
    log: logging.Logger,
    stop_event: threading.Event,
    max_iters: Optional[int] = None,   # test hook; None = run until stop_event
) -> int:
    """P5: run run_once() repeatedly with a heartbeat between an interruptible sleep,
    until `stop_event` is set (SIGTERM/SIGINT). Replaces the --once + systemd-Restart churn
    with one long-lived process. A SmtpAuthError (run_once -> 2) is a persistent config
    fault: stop the loop and return 2 so systemd/the operator sees it (never spin silently
    on a broken alert path). Returns 0 on clean stop."""
    iters = 0
    while not stop_event.is_set():
        rc = run_once(cfg, dry_run=dry_run, log=log)
        if rc == 2:
            log.error("alert_watcher loop: SmtpAuthError (persistent) — stopping loop, exit 2")
            return 2
        _write_heartbeat(heartbeat_path, log)
        iters += 1
        if max_iters is not None and iters >= max_iters:
            break
        stop_event.wait(interval_sec)   # interruptible sleep (wakes immediately on stop)
    log.info("alert_watcher loop: stop requested after %d pass(es) — exiting cleanly", iters)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Alert watcher: process critical sentinel files and send email."
    )
    parser.add_argument("--config", default=None, help="Path to config directory")
    parser.add_argument("--once", action="store_true", help="Run one pass and exit (default)")
    parser.add_argument("--loop", action="store_true",
                        help="P5: run continuously (run_once every --interval sec) with a "
                             "liveness heartbeat, instead of --once + systemd Restart")
    parser.add_argument("--interval", type=float, default=None,
                        help="P5: --loop pass interval seconds (default: alerts.watcher_interval_sec)")
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
        if args.loop:
            interval = (args.interval if args.interval is not None
                        else getattr(alerts_cfg, "watcher_interval_sec", 60))
            hb = getattr(alerts_cfg, "watcher_heartbeat_path", None)
            heartbeat_path = Path(hb) if hb else None
            stop_event = threading.Event()

            def _on_signal(signum, _frame):
                log.info("alert_watcher: received signal %s — stopping loop", signum)
                stop_event.set()

            for _sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    signal.signal(_sig, _on_signal)
                except (ValueError, OSError):
                    pass  # not main thread / unsupported platform — stop_event still ends it
            log.info("alert_watcher: --loop mode (interval=%.0fs, heartbeat=%s)",
                     interval, heartbeat_path or "off")
            return run_loop(cfg, interval_sec=interval, heartbeat_path=heartbeat_path,
                            dry_run=args.dry_run, log=log, stop_event=stop_event)
        return run_once(cfg, dry_run=args.dry_run, log=log)
    finally:
        _release_lock(lock_path)


if __name__ == "__main__":
    sys.exit(main())
