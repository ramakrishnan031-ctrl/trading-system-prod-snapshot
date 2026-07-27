"""
alerts/telegram_notifier.py -- Trading System v2

Purpose:
    Send formatted alert messages to Telegram via Bot API. Tier-aware failure
    handling per G8: INFO/WARN drop on failure; ERROR writes to failed_alerts.log;
    CRITICAL always writes a sentinel file via alerts.critical FIRST, then
    attempts Telegram send.

Locked Design Decisions:
    TG1  -- Tier-aware alert delivery. Single class: TelegramNotifier.
    TG2  -- Constructor args: bot_token, chat_ids, failed_alerts_log_path,
            sentinel_dir, logger, timeout_sec, max_retries, paper_mode.
    TG3  -- send() -> SendResult dataclass with 6 fields.
    TG4  -- INFO/WARN: drop on failure. ERROR: write failed_alerts.log.
            CRITICAL: write sentinel FIRST (unconditional), then send.
    TG5  -- CRITICAL path: sentinel write first, telegram second.
            If sentinel write fails: log.error, continue to telegram.
    TG6  -- HTTP POST to Bot API. 429: sleep Retry-After (max 5s), retry once.
            5xx/timeout: exponential backoff 0.5s/1s, up to max_retries.
            4xx (other than 429): permanent fail, no retry.
    TG7  -- Message format: "[<SEV>] <title>" header, HTML body, 4096-char limit.
    TG8  -- failed_alerts.log: JSON-lines, append-only.
    TG9  -- paper_mode: no HTTP calls; CRITICAL still writes sentinel.
    TG10 -- Constructor validation: empty token or chat_ids raises ValueError.
    TG11 -- Layer 5. Imports: stdlib, requests, alerts.critical, core.time_authority.
    TG12 -- System config additions: telegram + alerts sections in SystemConfig.
"""
from __future__ import annotations

import collections
import html
import json
import logging
import os
import smtplib
import threading
import time
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

import requests

from alerts.critical import write_critical_sentinel
from core.logger import SafeJSONEncoder
from core.time_authority import now_ist

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_MSG_MAX = 4096
_TRUNCATION_MARKER = "\n...[truncated]"


def _safe_html_truncate(s: str, limit: int) -> str:
    """Truncate HTML `s` to at most `limit` chars WITHOUT ending inside an HTML entity
    (&...;) or a tag (<...>).

    M-A1: Telegram parses parse_mode=HTML strictly and rejects the whole message with a
    400 if a naive slice left an entity or tag half-open (e.g. "&am" or "<b"). That 400 is
    treated as a permanent failure, so a long CRITICAL alert (kill-switch / naked position /
    SYSTEM_OVERSELL) would be silently lost. Back off the cut to the last safe boundary.
    """
    if limit <= 0:
        return ""
    if len(s) <= limit:
        return s
    cut = s[:limit]
    # Inside an unterminated tag "<..." (last '<' after last '>') → drop from the '<'.
    lt, gt = cut.rfind("<"), cut.rfind(">")
    if lt > gt:
        cut = cut[:lt]
    # Inside an unterminated entity "&..." → drop from the '&'. HTML entities are short
    # (&amp; &lt; &#1234; …); only treat a nearby, unclosed '&' as a split entity.
    amp, semi = cut.rfind("&"), cut.rfind(";")
    if amp > semi and (len(cut) - amp) <= 12:
        cut = cut[:amp]
    return cut


def _read_telegram_enabled(config_dir: str | Path = "config") -> bool:
    """
    Read alerts.telegram.enabled from system_config.yaml (TASK-10 Item A).

    Used by the from_env()/from_config() factories so cron scripts honor the
    master switch. FAIL-OPEN: any error (missing file, parse error, missing key)
    returns True — better to alert than to silently skip alerts.
    """
    try:
        import yaml  # local import: keep module-level import surface minimal (TG11)

        path = Path(config_dir) / "system_config.yaml"
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        enabled = (
            data.get("alerts", {})
            .get("telegram", {})
            .get("enabled", True)
        )
        return bool(enabled)
    except Exception:  # noqa: BLE001 — fail-open on any error
        return True


# ------------------------------------------------------------------------------
# Rate limiter (FIX-131 Item 18)
# ------------------------------------------------------------------------------

class _SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter: max N calls per 60 seconds."""

    _POLL_SEC = 0.5

    def __init__(self, max_per_minute: int) -> None:
        self._max = max(1, max_per_minute)
        self._window: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """Wait for a send slot. Returns True if one was taken, False on timeout.

        M-A2: `timeout=None` keeps the original unbounded wait (the opt-out).
        With a timeout the poll gives up instead of spinning — an alert storm
        must not pin the CALLER's thread, and these callers are live paths
        (signal_processor emits its INTRADAY SIGNAL alert *before* placing the
        order, with capital already reserved).
        """
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while True:
            now = time.monotonic()
            with self._lock:
                # Drop timestamps older than 60 seconds
                while self._window and self._window[0] < now - 60.0:
                    self._window.popleft()
                if len(self._window) < self._max:
                    self._window.append(now)
                    return True
            if deadline is None:
                time.sleep(self._POLL_SEC)
                continue
            remaining = deadline - now
            if remaining <= 0.0:
                return False
            # Never sleep past the deadline — a 0.2s budget must not cost 0.5s.
            time.sleep(min(self._POLL_SEC, remaining))

    def count_recent(self) -> int:
        """Return number of messages sent in the last 60 seconds."""
        now = time.monotonic()
        with self._lock:
            while self._window and self._window[0] < now - 60.0:
                self._window.popleft()
            return len(self._window)


# ------------------------------------------------------------------------------
# Channel config (whitelist enforcement)
# ------------------------------------------------------------------------------

@dataclass
class ChannelConfig:
    """One Telegram channel entry. chat_id resolved from env at send time."""
    chat_id_env: str
    label: str
    enabled: bool = False


# ------------------------------------------------------------------------------
# Result type (TG3)
# ------------------------------------------------------------------------------

@dataclass
class SendResult:
    """Result of a TelegramNotifier.send() call (TG3)."""
    success: bool
    tier: str
    delivered_to: list[str] = field(default_factory=list)
    failed_to: list[str] = field(default_factory=list)
    sentinel_path: Path | None = None
    failed_log_written: bool = False


# ------------------------------------------------------------------------------
# TelegramNotifier (TG1)
# ------------------------------------------------------------------------------

class TelegramNotifier:
    """
    Tier-aware Telegram alert notifier (TG1, G8).

    Usage:
        notifier = TelegramNotifier(bot_token=..., chat_ids=[...], ...)
        result = notifier.send("CRITICAL", "Capital breach", "...", "fund_manager")
    """

    def __init__(
        self,
        bot_token: str,
        chat_ids: list[str] | None = None,
        failed_alerts_log_path: Path | str = "logs/failed_alerts.log",
        sentinel_dir: Path | str = "data_store",
        logger: logging.Logger | None = None,
        timeout_sec: float = 5.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        rate_limit_per_minute: int = 20,
        paper_mode: bool = False,
        channels: list[ChannelConfig] | None = None,
        send_in_paper_mode: bool = False,
        email_fallback_config: Optional[Any] = None,  # FIX-132 Item 10
        enabled: bool = True,  # TASK-10: master ON/OFF switch (telegram.enabled)
        send_deadline_seconds: float | None = 8.0,  # M-A2: whole-send wall clock (30->8, 25-Jul)
    ) -> None:
        """
        Construct a TelegramNotifier (TG2).

        Accepts either `chat_ids` (direct IDs, legacy) or `channels` (env-var-based
        whitelist). At least one must be provided (TG10).

        When `channels` is supplied the notifier resolves each chat_id from the
        environment at send time and skips channels where enabled=False.
        The personal_chat_id is never a send target — it is reserved for v2.1
        bot-command interactions.

        Raises:
            ValueError: if bot_token is empty, or neither chat_ids nor channels
                        is provided (TG10).
        """
        if not str(bot_token).strip():
            raise ValueError("bot_token must not be empty (TG10)")
        if not chat_ids and not channels:
            raise ValueError("chat_ids or channels must not be empty (TG10)")

        self._token = bot_token
        self._chat_ids: list[str] = list(chat_ids) if chat_ids else []
        self._channels: list[ChannelConfig] | None = list(channels) if channels else None
        self._failed_log = Path(failed_alerts_log_path)
        self._sentinel_dir = Path(sentinel_dir)
        self._log = logger or logging.getLogger(__name__)
        self._timeout = timeout_sec
        self._max_retries = max_retries
        self._retry_backoff = float(retry_backoff_seconds)
        self._paper_mode = paper_mode
        self._send_in_paper_mode = send_in_paper_mode
        self._enabled = enabled  # TASK-10: master switch; False = silent no-op
        # FIX-131 Item 18: sliding-window rate limiter (20 msgs/min default)
        self._rate_limiter = _SlidingWindowRateLimiter(rate_limit_per_minute)
        self._email_fallback = email_fallback_config  # FIX-132 Item 10
        # M-A2: ONE wall-clock budget for a whole send() — shared across every
        # enabled channel, and covering the rate-limit wait, the HTTP timeouts and
        # the backoff sleeps. Without it a send costs (max_retries+1) x timeout_sec
        # plus the backoffs PER CHAT (≈26 s on the shipped config) and the
        # rate-limiter wait is unbounded. None = the pre-M-A2 unbounded behaviour.
        self._send_deadline = (
            None if send_deadline_seconds is None else float(send_deadline_seconds)
        )

        if chat_ids and channels:
            self._log.warning(
                "Both chat_ids and channels provided; channels will be used (legacy chat_ids ignored)"
            )

        # Ensure failed_alerts_log parent directory exists (TG10)
        self._failed_log.parent.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------------------
    # Factory methods (FIX-158c)
    # --------------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        logger: logging.Logger | None = None,
        config_dir: str | Path = "config",
    ) -> "TelegramNotifier | None":
        """
        Build a TelegramNotifier from environment variables.

        Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_PRIMARY.
        Returns None if either is missing (cron scripts use this pattern).

        TASK-10 (Item A): the telegram.enabled master switch lives in the config
        file, but cron scripts construct via this factory rather than load_all().
        So we read alerts.telegram.enabled here and honor it — the master switch
        therefore silences ALL alerts (main app AND cron) with no exceptions.
        Fail-open: if the config is missing/unreadable, default to enabled=True.
        """
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHANNEL_PRIMARY", "")
        if not bot_token or not chat_id:
            return None
        return cls(
            bot_token=bot_token,
            chat_ids=[chat_id],
            logger=logger or logging.getLogger(__name__),
            enabled=_read_telegram_enabled(config_dir),
        )

    @classmethod
    def from_config(
        cls,
        config_dir: str | Path = "config",
        logger: logging.Logger | None = None,
    ) -> "TelegramNotifier | None":
        """
        Build a TelegramNotifier from env vars (config_dir accepted for
        API compatibility but env vars are the actual source of truth).

        The telegram.enabled master switch IS read from config_dir (TASK-10 Item A).
        """
        return cls.from_env(logger=logger, config_dir=config_dir)

    # --------------------------------------------------------------------------
    # Convenience methods (FIX-158c)
    # --------------------------------------------------------------------------

    def send_alert(self, message: str, level: str = "WARNING") -> SendResult:
        """Convenience: send a simple text alert at the given severity."""
        return self.send(
            severity=level.upper(),
            title=f"[{level.upper()}] Alert",
            body=message,
            source_module="script",
        )

    def send_critical(self, message: str) -> SendResult:
        """Convenience: send a CRITICAL alert."""
        return self.send(
            severity="CRITICAL",
            title="CRITICAL Alert",
            body=message,
            source_module="script",
        )

    def send_info(self, message: str) -> SendResult:
        """Convenience: send an INFO alert."""
        return self.send(
            severity="INFO",
            title="Info",
            body=message,
            source_module="script",
        )

    # --------------------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------------------

    def send(
        self,
        severity: str,
        title: str,
        body: str,
        source_module: str,
        context: dict[str, Any] | None = None,
        write_sentinel: bool = True,
    ) -> SendResult:
        """
        Send an alert at the given severity tier (TG3, TG4).

        Tier routing (G8):
            INFO/WARN  -> attempt send; drop silently on failure
            ERROR      -> attempt send; write failed_alerts.log on failure
            CRITICAL   -> write sentinel FIRST, then attempt send (TG5)

        write_sentinel: CRITICAL only. Default True keeps the TG5 behaviour every
            alerting caller relies on (sentinel -> alert_watcher email). Pass
            False when the CALLER owns the email backup itself (the Cron Officer
            briefing/EOD, which writes its own clean HTML email): the Telegram
            send then does NOT write a bare sentinel, so no raw text can leak
            into the inbox as a malformed email (24-Jun email-leak fix).
        """
        # TASK-10: master ON/OFF switch (telegram.enabled). Single check point;
        # when disabled the notifier is a silent no-op — no sentinel, no HTTP,
        # no failed-alerts log. Applies equally to paper and live (shared config).
        if not self._enabled:
            self._log.info(
                "telegram.disabled_via_config: skipping [%s] %s", severity, title
            )
            disabled = SendResult(success=True, tier=severity, delivered_to=[])
            self._audit_send(severity, title, source_module, disabled,
                             outcome="suppressed_disabled")
            return disabled

        result = SendResult(success=False, tier=severity)

        if severity == "CRITICAL":
            result = self._handle_critical(title, body, source_module, context or {},
                                           write_sentinel=write_sentinel)
        elif severity == "ERROR":
            result = self._handle_error(title, body, source_module, context or {})
        else:
            # INFO or WARN: attempt, drop on failure (TG4)
            result = self._handle_info_warn(severity, title, body, source_module, context or {})

        self._audit_send(severity, title, source_module, result)
        return result

    def _audit_send(self, severity: str, title: str, source_module: str,
                    result: "SendResult", outcome: str | None = None) -> None:
        """27-Jul-2026 — THE SEND-SIDE AUDIT TRAIL.

        Before this, the alert stream could not be audited at all below CRITICAL.
        CRITICAL leaves a sentinel; failed_alerts.log records only FAILURES (its last
        entry was 2-Jul); and `send()` logged nothing on the success path, so there
        was NO `telegram_notifier` line in system_<date>.log on a normal day. On
        27-Jul that made a concrete question unanswerable from the system's own
        records: three placement failures occurred (PYRAMID x2, KECL) and there was
        no way to tell locally whether each produced a message.

        ⭐ THE OUTCOME FIELD IS THE POINT, NOT THE SEND. "we tried" and "it arrived"
        are different facts, and only the first was ever knowable. SendResult already
        carries both (`delivered_to` / `failed_to`), so this reads them rather than
        re-deriving anything.

        ⛔ THIS IS A LOG, NOT AN ALERT. Nothing here pages anyone — the alert stream
        already has a noise problem and this must not add to it.

        ⛔ AND IT MUST NEVER BREAK A SEND. An alert failing because its own audit
        line failed would be the worst possible version of this, so the whole body is
        wrapped and the caller's result is untouched either way.
        """
        try:
            if outcome is None:
                if not result.success:
                    outcome = "failed"
                elif result.delivered_to:
                    outcome = "delivered"
                else:
                    outcome = "suppressed"
            self._log.info(
                "alert_send",
                extra={
                    "severity": severity,
                    "source_module": source_module,
                    "title": str(title)[:120],
                    "outcome": outcome,
                    "delivered_to": list(result.delivered_to or []),
                    "failed_to": list(result.failed_to or []),
                    "sentinel_written": result.sentinel_path is not None,
                    "failed_log_written": bool(result.failed_log_written),
                },
            )
        except Exception:  # noqa: BLE001 — an audit line must never break a send
            pass

    # --------------------------------------------------------------------------
    # Tier handlers
    # --------------------------------------------------------------------------

    def _handle_critical(
        self,
        title: str,
        body: str,
        source_module: str,
        context: dict,
        write_sentinel: bool = True,
    ) -> SendResult:
        """CRITICAL: write sentinel first, then attempt Telegram (TG5).

        write_sentinel=False suppresses BOTH the unconditional sentinel AND the
        Telegram-failure email fallback — used when the caller owns its own email
        backup (Cron Officer), so the Telegram path can never emit a bare,
        malformed-email sentinel (24-Jun email-leak fix). Telegram delivery and
        the failed-alerts log are unchanged.
        """
        sentinel_path: Path | None = None

        if self._paper_mode and not self._send_in_paper_mode:
            # paper_mode with alerts suppressed: still write sentinel but skip HTTP (TG9)
            if write_sentinel:
                try:
                    sentinel_path = write_critical_sentinel(
                        title=title, body=body,
                        source_module=source_module, context=context,
                        sentinel_dir=self._sentinel_dir,
                    )
                except OSError as exc:
                    self._log.error(
                        "CRITICAL sentinel write failed (paper_mode): %s", exc
                    )
            self._log.info(
                "[CRITICAL][paper_mode] %s -- %s", title, body
            )
            return SendResult(
                success=True,
                tier="CRITICAL",
                delivered_to=[],
                sentinel_path=sentinel_path,
            )

        # Step 1: write sentinel (TG5) — unless the caller owns the email backup
        # itself (write_sentinel=False; Cron Officer 24-Jun email-leak fix).
        if write_sentinel:
            try:
                sentinel_path = write_critical_sentinel(
                    title=title, body=body,
                    source_module=source_module, context=context,
                    sentinel_dir=self._sentinel_dir,
                )
            except OSError as exc:
                # Last-ditch: log error, still try Telegram (TG5)
                self._log.error("CRITICAL sentinel write failed: %s", exc)

        # Step 2: attempt Telegram send
        delivered, failed = self._send_to_all_chats("CRITICAL", title, body, source_module, context)

        # FIX-131 Item 18: write fallback log for CRITICAL Telegram failures too
        if failed:
            self._write_failed_log(
                severity="CRITICAL",
                title=title,
                body=body,
                source_module=source_module,
                context=context,
                telegram_error=f"CRITICAL failed to deliver to: {failed}",
                chat_ids_attempted=delivered + failed,
            )

        # FIX-132 Item 10: if Telegram failed for ALL channels, try email fallback.
        # Skipped when write_sentinel=False — the caller (Cron Officer) already
        # wrote its own clean HTML email backup, so a second email here would dupe.
        if write_sentinel and not delivered and failed:
            self._send_email_fallback(title, body, source_module)

        return SendResult(
            success=len(delivered) > 0,
            tier="CRITICAL",
            delivered_to=delivered,
            failed_to=failed,
            sentinel_path=sentinel_path,
        )

    def _handle_error(
        self,
        title: str,
        body: str,
        source_module: str,
        context: dict,
    ) -> SendResult:
        """ERROR: attempt Telegram; write failed_alerts.log on failure (TG4, TG8)."""
        if self._paper_mode and not self._send_in_paper_mode:
            self._log.info("[ERROR][paper_mode] %s -- %s", title, body)
            return SendResult(success=True, tier="ERROR", delivered_to=[])

        delivered, failed = self._send_to_all_chats("ERROR", title, body, source_module, context)
        failed_log_written = False

        if failed:
            failed_log_written = self._write_failed_log(
                severity="ERROR",
                title=title,
                body=body,
                source_module=source_module,
                context=context,
                telegram_error=f"Failed to deliver to: {failed}",
                chat_ids_attempted=delivered + failed,
            )

        return SendResult(
            success=len(delivered) > 0,
            tier="ERROR",
            delivered_to=delivered,
            failed_to=failed,
            failed_log_written=failed_log_written,
        )

    def _handle_info_warn(
        self,
        severity: str,
        title: str,
        body: str,
        source_module: str,
        context: dict,
    ) -> SendResult:
        """INFO/WARN: attempt send; drop silently on failure (TG4)."""
        if self._paper_mode and not self._send_in_paper_mode:
            self._log.info("[%s][paper_mode] %s -- %s", severity, title, body)
            return SendResult(success=True, tier=severity, delivered_to=[])

        try:
            delivered, failed = self._send_to_all_chats(severity, title, body, source_module, context)
        except Exception:  # noqa: BLE001
            # Drop silently on any unexpected error for INFO/WARN
            return SendResult(success=False, tier=severity)

        return SendResult(
            success=len(delivered) > 0,
            tier=severity,
            delivered_to=delivered,
            failed_to=failed,
        )

    # --------------------------------------------------------------------------
    # HTTP layer
    # --------------------------------------------------------------------------

    def _send_to_all_chats(
        self,
        severity: str,
        title: str,
        body: str,
        source_module: str,
        context: dict,
    ) -> tuple[list[str], list[str]]:
        """Send to whitelisted channels; return (delivered, failed) lists."""
        message = _format_message(severity, title, body, source_module, context)
        delivered: list[str] = []
        failed: list[str] = []

        # M-A2: the budget is per SEND, not per chat — three enabled channels
        # against a hung endpoint must still return in one deadline, not three.
        deadline = (
            None if self._send_deadline is None
            else time.monotonic() + self._send_deadline
        )

        if self._channels is not None:
            # Env-var-based whitelist path
            for channel in self._channels:
                if not channel.enabled:
                    continue
                chat_id = os.environ.get(channel.chat_id_env)
                if not chat_id:
                    self._log.warning(
                        "Skipping %s: env var %s not set",
                        channel.label, channel.chat_id_env,
                    )
                    continue
                ok = self._post_with_retry(chat_id, message, deadline=deadline)
                if ok:
                    delivered.append(chat_id)
                else:
                    failed.append(chat_id)
        else:
            # Legacy direct chat_ids path
            for chat_id in self._chat_ids:
                ok = self._post_with_retry(chat_id, message, deadline=deadline)
                if ok:
                    delivered.append(chat_id)
                else:
                    failed.append(chat_id)

        return delivered, failed

    @staticmethod
    def _sleep_bounded(secs: float, deadline: float | None) -> None:
        """M-A2: sleep `secs`, but never past `deadline`. A backoff must not be
        the thing that overruns the budget the rest of the ladder respects."""
        if deadline is None:
            time.sleep(secs)
            return
        time.sleep(max(0.0, min(float(secs), deadline - time.monotonic())))

    def _post_with_retry(
        self, chat_id: str, text: str, deadline: float | None = None
    ) -> bool:
        """
        POST a single message to one chat_id with retry logic (TG6).

        FIX-131 Item 18: rate limiter applied before each attempt; configurable
        backoff (retry_backoff_seconds) replaces hardcoded 0.5/1.0s ladder.

        M-A2: `deadline` is a `time.monotonic()` instant after which this call
        gives up. It bounds ALL THREE blocking parts — the rate-limit wait, the
        HTTP timeout and the backoff sleeps — because the caller is a live
        thread. Giving up costs no alert: CRITICAL has already written its
        sentinel (TG5, `_handle_critical` step 1) and ERROR falls through to
        `failed_alerts.log`. `deadline=None` = the pre-M-A2 unbounded ladder.

        Returns True on success, False on permanent failure.
        """
        url = _TELEGRAM_API.format(token=self._token)
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

        attempt = 0

        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0.0:
                self._log.warning(
                    "telegram.send_deadline_exceeded",
                    extra={"chat_id": chat_id, "attempts": attempt},
                )
                return False

            # FIX-131 Item 18: acquire rate-limit token before each HTTP attempt
            if not self._rate_limiter.acquire(timeout=remaining):
                self._log.warning(
                    "telegram.rate_limit_wait_timed_out",
                    extra={"chat_id": chat_id, "attempts": attempt},
                )
                return False

            # Re-read: the rate-limit wait consumed part of the budget.
            http_timeout = self._timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    self._log.warning(
                        "telegram.send_deadline_exceeded",
                        extra={"chat_id": chat_id, "attempts": attempt},
                    )
                    return False
                http_timeout = min(self._timeout, remaining)

            try:
                resp = requests.post(url, json=payload, timeout=http_timeout)
            except requests.Timeout:
                attempt += 1
                if attempt > self._max_retries:
                    return False
                self._sleep_bounded(self._retry_backoff, deadline)
                continue
            except requests.RequestException:
                return False

            if resp.status_code == 200:
                return True

            if resp.status_code == 429:
                # Rate limited: respect Retry-After, retry (TG6 / FIX-097)
                retry_after_raw = resp.headers.get("Retry-After", "30")
                try:
                    retry_after = float(retry_after_raw)
                except ValueError:
                    retry_after = 30.0
                    self._log.warning(
                        "telegram.429_retry_after_not_numeric",
                        extra={"retry_after_header": retry_after_raw,
                               "using_default_sec": retry_after},
                    )
                self._sleep_bounded(min(retry_after, 5.0), deadline)
                attempt += 1
                if attempt > self._max_retries:
                    return False
                continue  # back to top: re-acquire rate-limit token

            if resp.status_code >= 500:
                attempt += 1
                if attempt > self._max_retries:
                    return False
                self._sleep_bounded(self._retry_backoff, deadline)
                continue

            # 4xx (other than 429): permanent failure (TG6). M-A1: LOG the response body —
            # Telegram's JSON `description` is the ONLY signal for a markup-truncation
            # reject ("Bad Request: can't parse entities ...") that would otherwise drop a
            # CRITICAL alert with no trace. Bounded so a huge body can't bloat the log line.
            try:
                err_body = str(resp.text)[:500]
            except Exception:
                err_body = "<unreadable>"
            self._log.error(
                "telegram.4xx_permanent_failure",
                extra={"status_code": resp.status_code, "chat_id": chat_id,
                       "response_body": err_body},
            )
            return False

    # --------------------------------------------------------------------------
    # Failed alerts log (TG8)
    # --------------------------------------------------------------------------

    def _write_failed_log(
        self,
        severity: str,
        title: str,
        body: str,
        source_module: str,
        context: dict,
        telegram_error: str,
        chat_ids_attempted: list[str],
    ) -> bool:
        """Append a JSON-line to failed_alerts.log (TG8). Returns True on success."""
        record = {
            "ts": now_ist().isoformat(),
            "severity": severity,
            "title": title,
            "body": body,
            "source_module": source_module,
            "context": context,
            "telegram_error": telegram_error,
            "chat_ids_attempted": chat_ids_attempted,
        }
        try:
            with open(self._failed_log, "a", encoding="utf-8") as fh:
                # FIX-058: SafeJSONEncoder prevents serialization failures
                fh.write(json.dumps(record, ensure_ascii=False, cls=SafeJSONEncoder) + "\n")
            return True
        except OSError:
            return False

    # --------------------------------------------------------------------------
    # Email fallback for CRITICAL (FIX-132 Item 10)
    # --------------------------------------------------------------------------

    def _send_email_fallback(
        self, title: str, body: str, source_module: str
    ) -> bool:
        """Send email via SMTP when Telegram fails for CRITICAL alerts."""
        cfg = self._email_fallback
        if cfg is None or not getattr(cfg, "enabled", False):
            return False

        from_addr = os.environ.get(getattr(cfg, "from_addr_env", ""), "")
        password = os.environ.get(getattr(cfg, "password_env", ""), "")
        to_addr = os.environ.get(getattr(cfg, "to_addr_env", ""), "")

        if not from_addr or not password or not to_addr:
            self._log.warning(
                "email_fallback.missing_credentials: env vars not set"
            )
            return False

        subject = f"[CRITICAL] {title}"
        email_body = (
            f"CRITICAL ALERT -- Telegram delivery failed\n\n"
            f"Title: {title}\n"
            f"Source: {source_module}\n"
            f"Time: {now_ist().isoformat()}\n\n"
            f"{body}"
        )

        msg = MIMEText(email_body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr

        try:
            smtp_host = getattr(cfg, "smtp_host", "smtp.gmail.com")
            smtp_port = int(getattr(cfg, "smtp_port", 587))
            use_tls = getattr(cfg, "use_tls", True)

            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            if use_tls:
                server.starttls()
            server.login(from_addr, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
            server.quit()

            self._log.info(
                "email_fallback.sent",
                extra={"to": to_addr, "subject": subject},
            )
            return True
        except Exception as exc:
            self._log.error(
                "email_fallback.failed",
                extra={"error": str(exc)},
            )
            return False


# ------------------------------------------------------------------------------
# Formatting helpers (TG7)
# ------------------------------------------------------------------------------

def _format_message(
    severity: str,
    title: str,
    body: str,
    source_module: str,
    context: dict,
) -> str:
    """
    Build an HTML-formatted Telegram message (TG7).

    Truncates to 4096 chars if needed.
    """
    ts = now_ist().isoformat()
    safe_title = html.escape(title)
    safe_module = html.escape(source_module)
    safe_body = html.escape(body)

    ctx_lines = "\n".join(
        f"{html.escape(str(k))}: {html.escape(str(v))}"
        for k, v in context.items()
    )

    lines = [
        f"<b>[{severity}] {safe_title}</b>",
        f"<b>Module:</b> {safe_module}",
        f"<b>Time:</b> {ts}",
        "<b>Body:</b>",
        safe_body,
    ]
    if ctx_lines:
        lines += ["<b>Context:</b>", ctx_lines]

    message = "\n".join(lines)

    if len(message) > _MSG_MAX:
        # Truncate body to fit within limit (TG7)
        overhead = len(message) - len(safe_body)
        allowed_body = _MSG_MAX - overhead - len(_TRUNCATION_MARKER)
        if allowed_body < 0:
            allowed_body = 0
        truncated_body = _safe_html_truncate(safe_body, allowed_body) + _TRUNCATION_MARKER
        lines_t = [
            f"<b>[{severity}] {safe_title}</b>",
            f"<b>Module:</b> {safe_module}",
            f"<b>Time:</b> {ts}",
            "<b>Body:</b>",
            truncated_body,
        ]
        if ctx_lines:
            lines_t += ["<b>Context:</b>", ctx_lines]
        message = "\n".join(lines_t)
        # Hard cap if still over (context very large)
        if len(message) > _MSG_MAX:
            message = _safe_html_truncate(
                message, _MSG_MAX - len(_TRUNCATION_MARKER)) + _TRUNCATION_MARKER

    return message
