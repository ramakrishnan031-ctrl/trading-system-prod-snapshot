"""
broker/token_monitor.py — Trading System v2  (FIX-128 Fix E)

Purpose:
    Background thread that periodically validates the Zerodha access token
    during market hours. If the token expires or the broker profile call fails,
    fires SOFT_KILL and sends a Telegram alert. Does NOT exit the process —
    lets order_monitor continue managing open SL/TGT exit orders.

Locked Design (FIX-128):
    TM1 -- Polls kite.profile() every check_interval_sec during market hours.
    TM2 -- On expiry: CRITICAL log + Telegram alert + SOFT_KILL.
    TM3 -- Does NOT trigger hard_kill: open positions may still be managed
           by order_monitor (SL/TGT orders remain active at the broker).
    TM4 -- Paper mode: no-op (no real token needed; start() is a no-op).
    TM5 -- Thread-safe: single daemon thread; stop() joins with 5s timeout.
    TM6 -- check_interval_sec defaults to 1800 (30 minutes).
    TM7 -- market_hours_only: if True, only checks during [market_open, market_close].

What This Module Does NOT Do:
    - Does not attempt to re-login or refresh the token automatically.
    - Does not close positions (order_monitor handles open exit orders).
    - Does not call hard_kill (that is reserved for capital/DB drift).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import time as _time
from typing import Callable, Optional

from core.logger import log_exception
from core.market_windows import is_within_market_hours
from core.time_authority import now_ist


class TokenMonitor:
    """
    Periodic background check that the Zerodha access token is still valid.

    Usage::
        monitor = TokenMonitor(
            profile_fn=adapter._kite.profile,
            on_expiry=lambda: kill_switch.soft_kill(...),
            logger=get_logger("token_monitor"),
            check_interval_sec=1800,
            paper_mode=False,
        )
        monitor.start()
        # ... trading session ...
        monitor.stop()
    """

    def __init__(
        self,
        profile_fn: Callable[[], object],
        on_expiry: Callable[[], None],
        logger: logging.Logger,
        check_interval_sec: int = 1800,
        paper_mode: bool = False,
        market_open: Optional[str] = None,   # "HH:MM" IST; None = always check
        market_close: Optional[str] = None,  # "HH:MM" IST; None = always check
        notifier: Optional[object] = None,   # TelegramNotifier; optional
        mode: str = "LIVE",
    ) -> None:
        self._profile_fn = profile_fn
        self._on_expiry = on_expiry
        self._log = logger
        self._check_interval = check_interval_sec
        self._paper = paper_mode
        self._notifier = notifier
        self._mode = mode
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._expiry_fired = False  # fire once per session

        import datetime as _dt
        self._market_open_t: Optional[_dt.time] = None
        self._market_close_t: Optional[_dt.time] = None
        if market_open:
            try:
                h, m = market_open.split(":")
                self._market_open_t = _dt.time(int(h), int(m))
            except Exception:
                pass
        if market_close:
            try:
                h, m = market_close.split(":")
                self._market_close_t = _dt.time(int(h), int(m))
            except Exception:
                pass

    def start(self) -> None:
        """Launch the daemon polling thread (TM5). No-op in paper mode (TM4)."""
        if self._paper:
            self._log.info("token_monitor.start: paper_mode=True — no-op")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._check_loop,
            name="token-monitor",
            daemon=True,
        )
        self._thread.start()
        self._log.info(
            "token_monitor.start",
            extra={"check_interval_sec": self._check_interval, "mode": self._mode},
        )

    def stop(self) -> None:
        """Signal the polling thread to exit and join with 5s timeout (TM5)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._log.info("token_monitor.stop")

    def check_now(self) -> bool:
        """
        Perform one synchronous token check. Returns True if token is valid.
        This is the testable core of the monitor.

        In paper mode always returns True (TM4).
        """
        if self._paper:
            return True

        try:
            self._profile_fn()
            self._log.debug("token_monitor.check_ok")
            return True
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "token_monitor.token_expired_or_invalid",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            self._handle_expiry(str(exc))
            return False

    def _is_market_hours(self) -> bool:
        """Return True if current time is within configured market hours.
        FIX-169 F18: delegates to shared is_within_market_hours()."""
        if self._market_open_t is None or self._market_close_t is None:
            return True
        return is_within_market_hours(now_ist().time(), self._market_open_t, self._market_close_t)

    def _handle_expiry(self, reason: str) -> None:
        """Fire expiry sequence once per session."""
        if self._expiry_fired:
            return
        self._expiry_fired = True

        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="CRITICAL",
                    title=f"[{self._mode}] TOKEN EXPIRED",
                    body=(
                        "Zerodha access token has expired or is invalid.\n"
                        "No new orders will be placed.\n"
                        "Order monitor continues managing existing SL/TGT orders.\n"
                        f"Error: {reason}"
                    ),
                    source_module="token_monitor",
                )
            except Exception as ne:
                self._log.error("token_monitor: notifier.send failed: %s", ne)

        try:
            self._on_expiry()
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "token_monitor.on_expiry_callback_failed",
                extra={"error": str(exc)},
            )

    def _check_loop(self) -> None:
        """Main loop: wait check_interval_sec, then validate token if in market hours."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._check_interval)
            if self._stop_event.is_set():
                break
            if not self._is_market_hours():
                self._log.debug("token_monitor.outside_market_hours: skipping check")
                continue
            self.check_now()
