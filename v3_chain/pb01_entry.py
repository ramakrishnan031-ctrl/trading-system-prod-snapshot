"""
v3_chain/pb01_entry.py — Trading System v2 · V3 Step 10b · PB-01 next-morning entry stage.

The stateful FRONT-END of PB-01. On day D+1, inside the entry window (09:20-11:00 IST,
config SEEDS), it monitors the symbols captured overnight (the `pb01_watchlist`) for the
FIRST valid 5-minute retest-confirmation of the LEVEL that broke on day D. It is the
direct analog of `screening/retest_monitor.py::RetestMonitor` (extend, don't reinvent):

  • DAEMON POLL THREAD — off the hot path; never a fetch inside `_process_one`
    (G-NO-INTERFERENCE). 5-min candles come from the injected OhlcFetcher (the same
    rate-limited closure the sr_detector uses).
  • RESTART-SAFE by RE-DERIVATION — the `pb01_watchlist` ROW is the durable state
    machine: PENDING → CONSUMED | EXPIRED_WINDOW | INVALIDATED | SKIPPED_GAP. Every poll
    re-derives the verdict from the day's closed candles-SINCE-OPEN, so a restart
    mid-window reconstructs the SAME decision (no separate intermediate state to persist
    or lose). The first terminal event wins and is idempotent under re-evaluation.
  • ANTI-REHYDRATION (G-NO-REHYDRATION) — loads ONLY today's rows
    (`get_pb01_watchlist_for_date(today)`); a row stamped for another trading_date can
    NEVER fire on a later day (FIX-046 class).
  • G-FORMING-CANDLE — only CLOSED 5-min candles are ever evaluated
    (`truncate_to_asof` drops a still-forming bar). We never decide on a live candle.
  • FIRST-RETEST-ONLY — on the first confirmation the row is CONSUMED and closed; never
    a 2nd/3rd attempt (Constitution: the first controlled pullback).
  • ANALYSIS ONLY / G-NO-ORDER — on confirmation it calls `on_confirm(Pb01Confirmation)`
    → the PLACE-FREE would-be runner. This stage holds NO order/reserve/place capability
    of any kind; a PB-01 candidate can NEVER place a real order, by construction.

Decision content is governed by the V3 DECISION CONTENT SPECIFICATION v1.0 §3/§7; every
threshold is a config SEED (WatchlistConfig + V3ChainConfig), never hardcoded.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, time as _dt_time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.candle_math import atr
from screening.hard_gate import gate_confirm, gate_pullback
from v3_chain.truncate import close_ts, truncate_to_asof

_TF_5 = "5minute"
_TF_30 = "30minute"
_TF_DAY = "day"


@dataclass(frozen=True)
class Pb01Confirmation:
    """Everything the place-free would-be runner needs to build the PB-01 verdict — all
    captured AT the confirmation instant (`as_of` = the confirmation candle's CLOSE), so
    the runner re-reads NOTHING later than the decision time (NO-LOOKAHEAD)."""
    row_id: int
    symbol: str
    trading_date: str
    breakout_date: str
    source: str
    level: float
    sr_zone_json: Optional[str]
    # the confirmation 5-min candle:
    entry_price: float          # = confirmation candle close (the would-be entry)
    confirm_open: float
    confirm_high: float
    confirm_low: float
    confirm_close: float
    confirm_volume: float
    as_of: datetime             # confirmation candle CLOSE (bar-start + 5min) — the cutoff
    # pullback + volatility context at confirmation:
    session_open: float
    session_low: float          # min low since the open, up to & incl. the confirm candle
    lowest_5m_close: float
    pullback_low: float         # the retest extreme (= session_low) → retest_quality + SL
    atr30: Optional[float]
    baseline_5m_volume: Optional[float]


class Pb01EntryStage:
    def __init__(
        self,
        *,
        config,                 # WatchlistConfig (window/gap/poll)
        v3_cfg,                 # V3ChainConfig (gate SEED knobs)
        store,                  # StateStore (get/update pb01_watchlist)
        fetcher,                # sr_detector.fetch.OhlcFetcher (own instance)
        market_windows,         # MarketWindows (market_open)
        on_confirm: Callable[[Pb01Confirmation], None],  # the place-free would-be runner
        logger,
        now_fn: Callable[[], datetime],
        poll_interval_sec: Optional[float] = None,
        static_lookback_30m_days: int = 10,
    ) -> None:
        self._cfg = config
        self._v3 = v3_cfg
        self._store = store
        self._fetcher = fetcher
        self._mw = market_windows
        self._on_confirm = on_confirm
        self._log = logger
        self._now = now_fn
        self._poll = float(poll_interval_sec if poll_interval_sec is not None
                           else getattr(config, "poll_interval_sec", 20.0))
        self._static_lookback_30m_days = int(static_lookback_30m_days)
        self._entry_start = _parse_hhmm(getattr(config, "entry_start", "09:20"))
        self._entry_end = _parse_hhmm(getattr(config, "entry_end", "11:00"))
        # In-memory memo of per-(symbol, trading_date) SESSION-STATIC inputs
        # (yesterday-derived ATR30 + baseline_5m_volume). NOT the state machine — the
        # durable state is the row status. Rebuilt cheaply after a restart.
        self._static: Dict[Tuple[str, str], Tuple[Optional[float], Optional[float]]] = {}
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ── lifecycle (mirror RetestMonitor) ──────────────────────────────────────
    def start(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="pb01-entry", daemon=True)
        self._worker.start()
        self._safe_log("info", "pb01 entry stage started (window %s-%s, poll %.0fs)",
                       getattr(self._cfg, "entry_start", "?"),
                       getattr(self._cfg, "entry_end", "?"), self._poll)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        w = self._worker
        if w is not None:
            w.join(timeout=timeout)
        self._worker = None

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(timeout=self._poll):
                break
            try:
                self.poll_once()
            except Exception as exc:
                self._safe_log("error", "pb01 entry: poll error (ignored): %s", exc)

    # ── poll (public + synchronous so tests can drive it deterministically) ────
    def poll_once(self) -> None:
        now = self._now()
        t = now.time()
        if t < self._entry_start:
            return   # window not open yet (the first 5-min candle has not closed)
        today = now.date().isoformat()
        try:
            rows = self._store.get_pb01_watchlist_for_date(today, pending_only=True)
        except Exception as exc:
            self._safe_log("error", "pb01 entry: load rows failed: %s", exc)
            return
        past_window = t >= self._entry_end
        for row in rows:
            try:
                if past_window:
                    self._terminate(row, "EXPIRED_WINDOW",
                                    {"reason": "entry window elapsed with no confirmation",
                                     "at": _naive_iso(now)})
                else:
                    self._evaluate(row, now)
            except Exception as exc:
                self._safe_log("error", "pb01 entry: evaluate failed for %s: %s",
                               row.get("symbol"), exc)

    # ── per-candidate evaluation ──────────────────────────────────────────────
    def _evaluate(self, row: dict, now: datetime) -> None:
        symbol = row["symbol"]
        level = float(row["level"])
        trading_date = str(row["trading_date"])

        # 1. TODAY's 5-min candles, fresh; keep only CLOSED bars for TODAY (G-FORMING-
        #    CANDLE + the lookback=1 fetch also returns yesterday's tail → filter to today).
        c5 = self._fetch_5m(symbol)
        closed = [c for c in truncate_to_asof(c5, _TF_5, now)
                  if _ts_date(c.ts) == now.date()]
        if not closed:
            return   # no closed 5-min candle for today yet — wait

        # 2. SESSION-STATIC inputs (ATR30 + baseline 5m volume), memoized per session.
        atr30, baseline = self._static_inputs(symbol, trading_date, now)

        # 3. GAP GUARD (terminal): the open gapped too far above LEVEL to retest without
        #    chasing → SKIPPED_GAP (spec §7). Uses the day's opening candle.
        session_open = float(closed[0].open)
        gap_cap = level * (1.0 + float(self._cfg.gap_guard_pct))
        if session_open > gap_cap:
            self._terminate(row, "SKIPPED_GAP", {
                "session_open": round(session_open, 4), "level": round(level, 4),
                "gap_cap": round(gap_cap, 4), "gap_guard_pct": float(self._cfg.gap_guard_pct)})
            return

        # 4. Walk the closed candles chronologically — the FIRST terminal event wins
        #    (deterministic ⇒ restart-safe: re-deriving from the open gives the same one).
        hold_floor = (level - float(self._v3.hold_buffer_atr_mult) * atr30) if atr30 else None
        session_low: Optional[float] = None
        lowest_close: Optional[float] = None
        for c in closed:
            lo, cl = float(c.low), float(c.close)
            session_low = lo if session_low is None else min(session_low, lo)
            lowest_close = cl if lowest_close is None else min(lowest_close, cl)

            # INVALIDATION (terminal): a 5-min CLOSE below the hold floor = the S-R flip
            # failed (spec §7). Precedes confirmation on the same candle.
            if hold_floor is not None and cl < hold_floor:
                self._terminate(row, "INVALIDATED", {
                    "close": round(cl, 4), "hold_floor": round(hold_floor, 4),
                    "candle_ts": _naive_iso(c.ts)})
                return

            # CONFIRMATION: the pullback-so-far is valid AND this candle confirms. Both
            # gates are must-have (missing input → the gate FAILS → not a confirmation).
            pv = gate_pullback(
                level=level, session_low=session_low, lowest_5m_close=lowest_close,
                atr30=atr30,
                proximity_pct=float(self._v3.pullback_proximity_pct),
                proximity_atr_mult=float(self._v3.pullback_proximity_atr_mult),
                hold_buffer_atr_mult=float(self._v3.hold_buffer_atr_mult))
            if not pv.passed:
                continue
            cv = gate_confirm(
                candle_open=float(c.open), candle_high=float(c.high),
                candle_low=lo, candle_close=cl, candle_volume=float(c.volume),
                level=level, baseline_5m_volume=baseline,
                min_body_frac=float(self._v3.confirm_min_body_frac),
                volume_mult=float(self._v3.confirm_volume_mult))
            if cv.passed:
                self._confirm(row, c, session_open, session_low, lowest_close,
                              atr30, baseline)
                return
        # no terminal event this poll → stay PENDING (await the next closed candle)

    def _confirm(self, row: dict, c, session_open: float, session_low: float,
                 lowest_close: float, atr30: Optional[float],
                 baseline: Optional[float]) -> None:
        """A confirmation fired. Mark CONSUMED FIRST (so it can never re-fire — a restart
        or the next poll loads it as terminal), THEN hand the frozen context to the
        place-free would-be runner. The runner writes a record only; it holds NO order
        path (G-NO-ORDER). A runner failure is logged — we do NOT retry (first-retest-
        only; a retry would risk a duplicate would-be record)."""
        as_of = close_ts(c, _TF_5)   # the confirmation candle CLOSE = the decision instant
        conf = Pb01Confirmation(
            row_id=int(row["id"]), symbol=row["symbol"], trading_date=str(row["trading_date"]),
            breakout_date=str(row.get("breakout_date") or ""), source=str(row.get("source") or ""),
            level=float(row["level"]), sr_zone_json=row.get("sr_zone_json"),
            entry_price=float(c.close), confirm_open=float(c.open), confirm_high=float(c.high),
            confirm_low=float(c.low), confirm_close=float(c.close), confirm_volume=float(c.volume),
            as_of=as_of, session_open=float(session_open), session_low=float(session_low),
            lowest_5m_close=float(lowest_close), pullback_low=float(session_low),
            atr30=atr30, baseline_5m_volume=baseline)
        self._terminate(row, "CONSUMED", {
            "entry": round(float(c.close), 4), "level": round(float(row["level"]), 4),
            "confirm_candle_ts": _naive_iso(c.ts), "as_of": _naive_iso(as_of),
            "session_low": round(float(session_low), 4)})
        try:
            self._on_confirm(conf)
        except Exception as exc:
            self._safe_log("error", "pb01 entry: on_confirm raised for %s (record lost): %s",
                           row.get("symbol"), exc)

    # ── session-static inputs (memoized per symbol×trading_date) ───────────────
    def _static_inputs(self, symbol: str, trading_date: str,
                       now: datetime) -> Tuple[Optional[float], Optional[float]]:
        """ATR30 (30-min ATR of PRIOR bars) + baseline_5m_volume (SMA20 of PRIOR daily
        volume / candles_per_session). Both are YESTERDAY-DERIVED and truncated to the
        session open → deterministic + restart-invariant within a session. Memoized so
        the historical daily/30-min fetch runs ONCE per symbol per day (the 5-min fetch
        stays fresh each poll)."""
        key = (symbol, trading_date)
        memo = self._static.get(key)
        if memo is not None:
            return memo
        session_open_dt = self._session_open_dt(now)

        atr30: Optional[float] = None
        try:
            c30 = self._fetcher.fetch_interval(symbol, _TF_30,
                                               lookback_days=self._static_lookback_30m_days) or []
            c30 = truncate_to_asof(c30, _TF_30, session_open_dt)   # prior bars only
            if len(c30) > int(self._v3.atr30_period):
                atr30 = atr(c30, int(self._v3.atr30_period))
        except Exception as exc:
            self._safe_log("warning", "pb01 entry: ATR30 unavailable for %s: %s", symbol, exc)

        baseline: Optional[float] = None
        try:
            daily = self._fetcher.fetch_interval(
                symbol, _TF_DAY,
                lookback_days=int(getattr(self._cfg, "capture_fetch_lookback_days", 60))) or []
            daily = truncate_to_asof(daily, _TF_DAY, session_open_dt)   # strictly prior dates
            vols = [float(c.volume) for c in daily][-20:]
            if vols:
                per = float(self._v3.baseline_candles_per_session)
                if per > 0:
                    baseline = (sum(vols) / len(vols)) / per
        except Exception as exc:
            self._safe_log("warning", "pb01 entry: baseline volume unavailable for %s: %s",
                           symbol, exc)

        memo = (atr30, baseline)
        self._static[key] = memo
        return memo

    def _fetch_5m(self, symbol: str) -> List[Any]:
        try:
            return self._fetcher.fetch_interval(symbol, _TF_5, lookback_days=1) or []
        except Exception as exc:
            self._safe_log("warning", "pb01 entry: 5m fetch failed for %s: %s", symbol, exc)
            return []

    def _session_open_dt(self, now: datetime) -> datetime:
        """Today's session-open datetime (tz matched to `now`). Fail-safe → 09:15."""
        try:
            mo = getattr(self._mw, "market_open", None)
            hh, mm = (mo.hour, mo.minute) if mo is not None else (9, 15)
        except Exception:
            hh, mm = 9, 15
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    def _terminate(self, row: dict, status: str, outcome: dict) -> None:
        """Record a terminal outcome — EVERY outcome (incl. skips/expiries) is persisted;
        the skip distribution IS the evidence about whether these breakouts retest."""
        try:
            self._store.update_pb01_watchlist_status(
                int(row["id"]), status,
                outcome_json=json.dumps(outcome, default=str),
                consumed_at=_naive_iso(self._safe_now()))
            self._safe_log("info", "pb01 entry %s (%s) → %s", row.get("symbol"),
                           row.get("trading_date"), status)
        except Exception as exc:
            self._safe_log("error", "pb01 entry: status update failed for %s: %s",
                           row.get("symbol"), exc)

    def _safe_now(self):
        try:
            return self._now()
        except Exception:
            return None

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass


# ── pure helpers ──────────────────────────────────────────────────────────────

def _parse_hhmm(v: str) -> _dt_time:
    hh, mm = str(v).split(":")
    return _dt_time(int(hh), int(mm))


def _ts_date(ts):
    try:
        return ts.date()
    except Exception:
        return None


def _naive_iso(dt) -> str:
    try:
        return dt.replace(tzinfo=None).isoformat()
    except Exception:
        return ""
