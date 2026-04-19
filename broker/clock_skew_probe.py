"""
broker/clock_skew_probe.py — Trading System v2 (BL-21, Phase D.2)

Purpose:
    Periodic driver for core/time_authority.py record_broker_skew(). Every
    probe_interval_sec, the daemon thread calls adapter.get_server_time()
    and hands the result to time_authority, which runs the existing 4-tier
    G4 classification (NORMAL / WARN / ALERT / HALT), maintains a rolling
    deque(maxlen=10) average, and fires the appropriate skew callback
    (on_critical_skew is wired to kill_switch.soft_kill by main.py).

Design Refs:
    - BL-21 / Phase D.2 (Path X): thin driver over existing G4 infra.
      This class does NOT implement tier logic, threshold evaluation, or
      callback dispatch -- time_authority owns all of that. We only
      schedule the probe, measure RTT for a mid-point reference, and
      gracefully degrade on transient broker errors.

Honest Caveat:
    adapter.get_server_time() currently returns the adapter's local time
    AFTER a broker round-trip, NOT a parsed broker response timestamp.
    This probe therefore measures network-path wall-clock consistency
    (via RTT anomalies against the local clock) rather than true broker
    clock skew. Adequate for surfacing anomalies during the paper trial.
    A future improvement (Phase E or F) would parse HTTP Date headers or
    cross-check against NTP to measure true broker skew.

Paper-mode:
    Skipped at the main.py wire site (BrokerClockSkewProbe is not
    constructed when args.mode == "paper"). In paper mode,
    adapter.get_server_time() returns now_ist() directly, so skew would
    always read as ~0 microseconds -- no signal, just log noise.

What This Module Does NOT Do:
    - Does not classify skew severity (time_authority does)
    - Does not fire the critical-skew callback (time_authority does)
    - Does not halt the system (time_authority's callback → soft_kill does)
    - Does not attempt clock correction (clock sync is a host-level
      concern: NTP, systemd-timesyncd)
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from core.config_loader import ClockSkewProbeConfig
from core.exceptions import BrokerAuthError, BrokerRateLimit429Error, BrokerTimeoutError
from core.logger import log_exception


class BrokerClockSkewProbe:
    """BL-21 / Path X: periodic driver for time_authority.record_broker_skew."""

    def __init__(
        self,
        adapter,
        time_authority,
        config: ClockSkewProbeConfig,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._adapter = adapter
        self._time_auth = time_authority
        self._config = config
        self._log = logger or logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self._config.enabled:
            self._log.info("clock_skew_probe disabled by config")
            return
        if self._thread is not None and self._thread.is_alive():
            self._log.warning("clock_skew_probe already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="broker_clock_skew_probe",
            daemon=True,
        )
        self._thread.start()
        self._log.info(
            "clock_skew_probe started, interval=%ds",
            self._config.probe_interval_sec,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        # First probe runs immediately, THEN we sleep. Faster visibility
        # at startup than "sleep first, probe after interval".
        while not self._stop_event.is_set():
            try:
                self._probe_once()
            except Exception as exc:
                log_exception(self._log, exc)
                self._log.error(
                    "clock_skew_probe: probe_once raised, continuing loop"
                )
            # wait() returns immediately when stop_event is set; on timeout
            # it returns False and we go around again.
            self._stop_event.wait(timeout=self._config.probe_interval_sec)

    def _probe_once(self) -> None:
        t_before = self._time_auth.now_ist()
        try:
            broker_ts = self._adapter.get_server_time()
        except BrokerAuthError as exc:
            self._log.error(
                "clock_skew_probe: auth error, skipping cycle",
                extra={"error": str(exc)},
            )
            return
        except BrokerRateLimit429Error:
            self._log.debug("clock_skew_probe: 429, skipping cycle")
            return
        except BrokerTimeoutError:
            self._log.debug("clock_skew_probe: timeout, skipping cycle")
            return
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "clock_skew_probe: unexpected broker error, skipping cycle",
                extra={"error_type": type(exc).__name__},
            )
            return
        t_after = self._time_auth.now_ist()
        mid_local = t_before + (t_after - t_before) / 2

        # Hand off to existing G4 infrastructure. time_authority handles
        # tier classification, deque averaging, and callback dispatch
        # (soft_kill for HALT, Telegram for ALERT, log for WARN).
        self._time_auth.record_broker_skew(
            broker_timestamp=broker_ts,
            local_ref_ts=mid_local,
        )
