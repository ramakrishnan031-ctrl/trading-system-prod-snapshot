"""
regime/runner.py — Trading System v2 · V3 03.02 Market Regime Engine

The SHADOW runner: computes the index regime ONCE PER CYCLE during market hours,
logs it, and persists the latest state (for later next-day / delivery use as
prior_day_regime). It gates NOTHING — no order, score, size, or kill-switch is
touched. Default-off; built + wired dormant like the sr_detector observer.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Optional

from regime.models import RegimeState


class MarketRegimeShadowRunner:
    def __init__(
        self,
        *,
        engine,
        logger,
        now_fn,
        market_windows,
        interval_sec: float = 60.0,
        persist_path: Optional[str] = None,
    ) -> None:
        self._engine = engine
        self._log = logger
        self._now_fn = now_fn
        self._mw = market_windows
        self._interval_sec = max(5.0, float(interval_sec))
        self._persist_path = persist_path
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest: Optional[RegimeState] = None

    @property
    def latest(self) -> Optional[RegimeState]:
        return self._latest

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not getattr(self._engine, "enabled", False) or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="market-regime", daemon=True)
        self._thread.start()
        self._safe_log("info", "market_regime: shadow runner started (interval=%ss)", self._interval_sec)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)
        self._thread = None

    # ── one cycle (public + synchronous so tests drive it deterministically) ──

    def run_once(self) -> RegimeState:
        state = self._engine.compute()
        self._latest = state
        self._safe_log(
            "info",
            "market_regime[%s]: dir=%s/%s vol=%s/%s day=%s/%s pref=%.2f extreme=%s",
            state.status, state.direction.value, state.direction.confidence,
            state.volatility.value, state.volatility.confidence,
            state.day_type.value, state.day_type.confidence,
            state.preference_multiplier, state.extreme_flag,
        )
        self._persist(state)
        return state

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                now = self._now_fn()
                if self._mw is None or self._mw.is_market_open(now):
                    self.run_once()
            except Exception as exc:   # a shadow runner must never die/raise
                self._safe_log("error", "market_regime: cycle error (%s)", exc)
            self._stop.wait(timeout=self._interval_sec)

    # ── persistence (best-effort; never blocks the cycle) ─────────────────────

    def _persist(self, state: RegimeState) -> None:
        if not self._persist_path:
            return
        try:
            payload = {"date": (state.ts or "")[:10], "regime": state.to_dict()}
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            tmp = f"{self._persist_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, default=str)
            os.replace(tmp, self._persist_path)
        except Exception as exc:
            self._safe_log("warning", "market_regime: persist failed (%s)", exc)

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass


def read_persisted_regime(persist_path: str) -> Optional[dict]:
    """Read the last-persisted regime payload ({date, regime}) or None. For later
    prior_day_regime use; a consumer checks the `date` field. Never raises."""
    try:
        with open(persist_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None
