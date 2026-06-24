"""
capital/strategy_governor.py -- Trading System v2  FIX-130 Item 6

Intraday strategy circuit breaker: pauses a strategy for the rest of the
trading day when its cumulative daily P&L drops below
  –(loss_multiplier × |avg_daily_loss|)
before the configured cutoff time (default 12:00 IST).

Pause state is in-memory only; resets on process restart / new day.
"""
from __future__ import annotations

import logging
from datetime import time as _time
from typing import Optional, Tuple


class StrategyGovernor:
    """
    Intraday strategy circuit breaker (FIX-130 Item 6).

    On each incoming signal the signal_processor calls check(strategy_name, now)
    which returns (paused, reason). The caller raises _PipelineReject on (True, ...).
    """

    def __init__(
        self,
        store,                              # StateStore
        config,                             # StrategyCircuitBreakerConfig
        notifier=None,
        logger: Optional[logging.Logger] = None,
        mode: str = "LIVE",
    ) -> None:
        self._store = store
        self._enabled: bool = bool(getattr(config, "enabled", True))
        self._loss_multiplier: float = float(getattr(config, "loss_multiplier", 2.0))
        self._cutoff_time: _time = _parse_hhmm(getattr(config, "cutoff_time", "12:00"))
        self._lookback_days: int = int(getattr(config, "lookback_days", 10))
        self._notifier = notifier
        self._log = logger or logging.getLogger(__name__)
        self._mode = mode
        self._paused_today: set[str] = set()  # in-memory; clears on restart/new day

    # ── public API ─────────────────────────────────────────────────────────────

    def is_paused(self, strategy_name: str) -> bool:
        return strategy_name in self._paused_today

    def check(self, strategy_name: str, now_time: _time) -> Tuple[bool, str]:
        """
        Return (paused, reason).

        Returns (True, reason) if the strategy should be rejected today.
        Returns (False, "") if the strategy is allowed to proceed.
        Fails open on any DB error to avoid blocking legitimate trades.
        """
        if not self._enabled:
            return False, ""

        if strategy_name in self._paused_today:
            return True, "strategy paused today by circuit breaker"

        # After cutoff time we never pause (too late to matter)
        if now_time >= self._cutoff_time:
            return False, ""

        try:
            today_pnl = self._get_today_pnl(strategy_name)
            avg_loss = self._get_avg_daily_loss(strategy_name)
        except Exception as exc:
            self._log.error("strategy_governor.check_db_error: %s", exc)
            return False, ""  # fail-open

        if avg_loss >= 0.0:
            return False, ""  # no losing history or net-positive average

        threshold = self._loss_multiplier * avg_loss  # threshold is negative

        if today_pnl < threshold:
            reason = (
                f"daily loss {today_pnl:.0f} < "
                f"{self._loss_multiplier}x avg_daily_loss {avg_loss:.0f}"
            )
            self._pause_strategy(strategy_name, today_pnl, avg_loss)
            return True, reason

        return False, ""

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _get_today_pnl(self, strategy_name: str) -> float:
        from core.time_authority import today_ist
        today = today_ist()  # "YYYY-MM-DD" in IST
        row = self._store.fetch_one(
            """SELECT COALESCE(SUM(gross_pnl), 0.0) AS pnl
               FROM trades
               WHERE strategy = ?
                 AND status IN ('CLOSED', 'CLOSED_MANUAL')
                 AND updated_at LIKE ?""",
            (strategy_name, f"{today}%"),
        )
        return float(row["pnl"]) if row else 0.0

    def _get_avg_daily_loss(self, strategy_name: str) -> float:
        """Average daily P&L across losing PAST days in the lookback window (today excluded)."""
        from core.time_authority import today_ist
        from datetime import datetime, timedelta
        today = today_ist()  # "YYYY-MM-DD" in IST
        start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=self._lookback_days)).strftime("%Y-%m-%d")
        row = self._store.fetch_one(
            """SELECT AVG(daily_pnl) AS avg_loss
               FROM (
                   SELECT SUBSTR(updated_at, 1, 10) AS trade_date,
                          SUM(gross_pnl)            AS daily_pnl
                   FROM trades
                   WHERE strategy = ?
                     AND status IN ('CLOSED', 'CLOSED_MANUAL')
                     AND updated_at >= ?
                     AND SUBSTR(updated_at, 1, 10) < ?
                   GROUP BY trade_date
                   HAVING SUM(gross_pnl) < 0
               ) AS losing_days""",
            (strategy_name, f"{start}T00:00:00", today),
        )
        if row is None or row["avg_loss"] is None:
            return 0.0
        return float(row["avg_loss"])

    # ── internal ───────────────────────────────────────────────────────────────

    def _pause_strategy(self, strategy_name: str, today_pnl: float, avg_loss: float) -> None:
        self._paused_today.add(strategy_name)
        self._log.warning(
            "strategy_governor.circuit_breaker_fired",
            extra={
                "strategy": strategy_name,
                "today_pnl": round(today_pnl, 2),
                "avg_daily_loss": round(avg_loss, 2),
                "loss_multiplier": self._loss_multiplier,
            },
        )
        if self._notifier is not None:
            try:
                body = (
                    f"Daily loss {today_pnl:.0f} exceeded "
                    f"{self._loss_multiplier}x avg daily loss ({avg_loss:.0f}).\n"
                    f"Strategy paused for the rest of today."
                )
                self._notifier.send(
                    # WARNING (not CRITICAL): a single-strategy daily-loss pause is
                    # a protective action working as designed (other strategies keep
                    # trading), matching the convention (kill_switch HALT = CRITICAL;
                    # degraded-but-operating = WARNING). Without this REQUIRED arg the
                    # call raised TypeError every trip and the circuit-breaker alert
                    # was silently lost (surfaced 24-Jun).
                    severity="WARNING",
                    title=f"[{self._mode}] STRATEGY PAUSED -- {strategy_name}",
                    body=body,
                    source_module="strategy_governor",
                )
            except Exception as exc:
                self._log.error("strategy_governor.notifier_failed: %s", exc)


def _parse_hhmm(s: str) -> _time:
    try:
        h, m = str(s).split(":")
        return _time(int(h), int(m))
    except (ValueError, AttributeError):
        return _time(12, 0)  # safe default
