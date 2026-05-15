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

import html
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from alerts.critical import write_critical_sentinel
from core.logger import SafeJSONEncoder
from core.time_authority import now_ist

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_MSG_MAX = 4096
_TRUNCATION_MARKER = "\n...[truncated]"


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
        max_retries: int = 2,
        paper_mode: bool = False,
        channels: list[ChannelConfig] | None = None,
        send_in_paper_mode: bool = False,
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
        self._paper_mode = paper_mode
        self._send_in_paper_mode = send_in_paper_mode

        if chat_ids and channels:
            self._log.warning(
                "Both chat_ids and channels provided; channels will be used (legacy chat_ids ignored)"
            )

        # Ensure failed_alerts_log parent directory exists (TG10)
        self._failed_log.parent.mkdir(parents=True, exist_ok=True)

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
    ) -> SendResult:
        """
        Send an alert at the given severity tier (TG3, TG4).

        Tier routing (G8):
            INFO/WARN  -> attempt send; drop silently on failure
            ERROR      -> attempt send; write failed_alerts.log on failure
            CRITICAL   -> write sentinel FIRST, then attempt send (TG5)
        """
        result = SendResult(success=False, tier=severity)

        if severity == "CRITICAL":
            result = self._handle_critical(title, body, source_module, context or {})
        elif severity == "ERROR":
            result = self._handle_error(title, body, source_module, context or {})
        else:
            # INFO or WARN: attempt, drop on failure (TG4)
            result = self._handle_info_warn(severity, title, body, source_module, context or {})

        return result

    # --------------------------------------------------------------------------
    # Tier handlers
    # --------------------------------------------------------------------------

    def _handle_critical(
        self,
        title: str,
        body: str,
        source_module: str,
        context: dict,
    ) -> SendResult:
        """CRITICAL: write sentinel first, then attempt Telegram (TG5)."""
        sentinel_path: Path | None = None

        if self._paper_mode and not self._send_in_paper_mode:
            # paper_mode with alerts suppressed: still write sentinel but skip HTTP (TG9)
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

        # Step 1: write sentinel unconditionally (TG5)
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
                ok = self._post_with_retry(chat_id, message)
                if ok:
                    delivered.append(chat_id)
                else:
                    failed.append(chat_id)
        else:
            # Legacy direct chat_ids path
            for chat_id in self._chat_ids:
                ok = self._post_with_retry(chat_id, message)
                if ok:
                    delivered.append(chat_id)
                else:
                    failed.append(chat_id)

        return delivered, failed

    def _post_with_retry(self, chat_id: str, text: str) -> bool:
        """
        POST a single message to one chat_id with retry logic (TG6).

        Returns True on success, False on permanent failure.
        """
        url = _TELEGRAM_API.format(token=self._token)
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

        attempt = 0
        delay = 0.5

        while True:
            try:
                resp = requests.post(url, json=payload, timeout=self._timeout)
            except requests.Timeout:
                # Transient: retry with backoff
                attempt += 1
                if attempt > self._max_retries:
                    return False
                time.sleep(delay)
                delay *= 2
                continue
            except requests.RequestException:
                return False

            if resp.status_code == 200:
                return True

            if resp.status_code == 429:
                # Rate limited: respect Retry-After, retry once (TG6)
                retry_after = float(resp.headers.get("Retry-After", "5"))
                time.sleep(min(retry_after, 5.0))
                try:
                    resp2 = requests.post(url, json=payload, timeout=self._timeout)
                    return resp2.status_code == 200
                except requests.RequestException:
                    return False

            if resp.status_code >= 500:
                # Transient server error: retry with backoff (TG6)
                attempt += 1
                if attempt > self._max_retries:
                    return False
                time.sleep(delay)
                delay *= 2
                continue

            # 4xx (other than 429): permanent failure (TG6)
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
        truncated_body = safe_body[:allowed_body] + _TRUNCATION_MARKER
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
            message = message[:_MSG_MAX - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER

    return message
