"""
allocation/portfolio_allocator.py — V3 03.05 Portfolio Allocator (ranked batch admission).

Modes (config.allocator_mode; default off → this object is not even built by main.py):
  • off      — never constructed (signal_processor gets allocator=None). FCFS byte-identical.
  • shadow   — a non-blocking observer buffers each screened+sized candidate and, once
               per wall-clock window, computes what RANKED admission WOULD have taken vs
               arrival-order FCFS (against the same start-of-window snapshot) and logs
               three regret metrics (A5). It NEVER reserves or places — live FCFS still
               governs, unchanged, with zero added latency.
  • enforce  — a single admission worker drains the window, ranks it (A7), and admits
               greedily under ONE portfolio_lock via the injected admit callback (which
               is signal_processor._admit_and_place — the SAME reserve+place code the
               fused path uses; sits on the existing primitives, reimplements none).

The greedy simulation + the enforce loop share `_admit_scan` for the portfolio-level
pre-checks (one-per-symbol, shared concentration cap A3, long/short skew A6). Both are
INERT by default (cap None / skew None).

See docs/v3/V3_STEP6_PORTFOLIO_ALLOCATOR_PLAN.md.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable, List, Optional, Tuple

from allocation.models import RegretRecord, ScoredCandidate
from allocation.ranker import arrival_ordered, rank_candidates
from allocation.window_buffer import WindowBuffer


class PortfolioAllocator:
    def __init__(
        self,
        *,
        config,                         # PortfolioAllocatorConfig
        fund_manager,                   # for get_snapshot() (capital/deployed/total)
        max_open: int,                  # risk_engine max_open_positions (for free slots)
        active_count_fn: Callable[[], int],   # store.count_active_positions
        logger,
        now_fn: Callable[[], Any],      # now_ist (aware IST datetime)
        portfolio_lock=None,            # fund_manager.portfolio_lock (enforce batch atomicity)
        enforce_admit_fn: Optional[Callable[[ScoredCandidate], bool]] = None,  # signal_processor.admit_prepared
        enforce_reject_fn: Optional[Callable[[ScoredCandidate, str], None]] = None,  # signal_processor.reject_prepared
        v3_scope_fn: Optional[Callable[[Any], bool]] = None,  # is this strategy a V3-playbook one?
    ) -> None:
        self._cfg = config
        self._fm = fund_manager
        self._max_open = int(max_open)
        self._active_count_fn = active_count_fn
        self._log = logger
        self._now = now_fn
        self._portfolio_lock = portfolio_lock
        self._enforce_admit_fn = enforce_admit_fn
        self._enforce_reject_fn = enforce_reject_fn
        self._v3_scope_fn = v3_scope_fn or (lambda _s: False)
        self._buffer = WindowBuffer()
        self._running = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── signal_processor-facing interface ────────────────────────────────────
    @property
    def mode(self) -> str:
        return self._cfg.allocator_mode

    def in_scope(self, strategy_obj) -> bool:
        """A2/A4: whether this candidate's strategy is governed by the allocator.
        `all` = every strategy; `v3_only` = only V3-playbook strategies (none exist
        yet → enforce governs nothing even if flipped)."""
        if self._cfg.enforce_scope == "all":
            return True
        try:
            return bool(self._v3_scope_fn(strategy_obj))
        except Exception:
            return False

    def should_handoff(self, strategy_obj) -> bool:
        """True only for enforce + in-scope — the fused path must NOT self-admit."""
        return self.mode == "enforce" and self.in_scope(strategy_obj)

    def observe(self, candidate: ScoredCandidate) -> None:
        """SHADOW: fire-and-forget append (guarded; never raises, never blocks the
        live path). No-op unless mode == shadow."""
        if self.mode != "shadow":
            return
        try:
            self._buffer.append(candidate)
        except Exception as exc:   # observability must never break admission
            self._log.error("allocator.observe error (ignored): %s", exc)

    def submit(self, candidate: ScoredCandidate) -> None:
        """ENFORCE: hand a prepared in-scope candidate to the admission worker.
        The receiver's symbol in-flight claim stays held until the worker admits or
        rejects it (A8)."""
        self._buffer.append(candidate)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._running or self.mode == "off":
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_worker, name="allocator-window", daemon=True
        )
        self._running = True
        self._thread.start()
        self._log.info(
            "PortfolioAllocator started (mode=%s scope=%s interval=%.1fs tail=%.1fs)",
            self.mode, self._cfg.enforce_scope,
            self._cfg.candle_interval_seconds, self._cfg.drain_tail_seconds,
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
        self._running = False
        self._log.info("PortfolioAllocator stopped")

    # ── window timer (A1: wall-clock aligned + short drain tail) ──────────────
    def _run_worker(self) -> None:
        interval = float(self._cfg.candle_interval_seconds)
        tail = float(self._cfg.drain_tail_seconds)
        while not self._stop.is_set():
            try:
                epoch = float(self._now().timestamp())
            except Exception:
                epoch = 0.0
            # next wall-clock boundary + the drain tail
            next_boundary = (int(epoch // interval) + 1) * interval
            wait = max(0.0, (next_boundary + tail) - epoch)
            if self._stop.wait(timeout=wait):
                break
            try:
                self._process_window()
            except Exception as exc:   # a broken window must NEVER kill the loop
                self._log.error("allocator window error (ignored): %s", exc)

    def _process_window(self) -> None:
        drained = self._buffer.drain()
        if not drained:
            return
        free_slots, avail, deployed, total = self._capacity_snapshot()
        if self.mode == "shadow":
            self._log_regret(drained, free_slots, avail, deployed, total)
        elif self.mode == "enforce":
            self._run_enforce_batch(drained, free_slots, avail, deployed, total)

    def _capacity_snapshot(self) -> Tuple[int, float, float, float]:
        """Start-of-window capacity: (free_slots, avail_intraday, deployed, total)."""
        snap = self._fm.get_snapshot()
        total = float(snap.total)
        avail = float(snap.intraday_avail)
        deployed = max(0.0, total - (float(snap.intraday_avail) + float(snap.positional_avail)))
        try:
            free = max(0, self._max_open - int(self._active_count_fn()))
        except Exception:
            free = self._max_open
        return free, avail, deployed, total

    # ── portfolio-level pre-checks (shared by simulation + enforce) ───────────
    def _passes_portfolio_rules(
        self, c: ScoredCandidate, *, admitted_symbols: set, deployed: float,
        total: float, long_count: int, short_count: int,
    ) -> Optional[str]:
        """Return None if the candidate passes the portfolio-level pre-checks, else a
        reject-reason label. INERT by default (cap None / skew None). Does NOT touch
        risk_engine gate 8 (per-sector 0.40 is enforced there, independently)."""
        if c.symbol in admitted_symbols:
            return "DUPLICATE_SYMBOL"      # one-per-symbol within the batch
        cap = self._cfg.max_portfolio_deployment_pct
        if cap is not None and total > 0:
            if deployed + c.margin_required > cap * total:
                return "SHARED_CONCENTRATION"
        skew = self._cfg.long_short_skew_max
        if skew is not None:
            longs = long_count + (1 if c.side == "BUY" else 0)
            shorts = short_count + (1 if c.side == "SELL" else 0)
            n = longs + shorts
            # Bootstrap-lenient: cap the majority at ceil(skew*n) so a one-sided burst
            # isn't blocked from the first admit (1/1 = 100% would otherwise reject
            # everything). A6 is inert by default and its exact rule is a flagged open
            # question (plan P10) — this is a documented, sane default.
            import math
            if n > 0 and max(longs, shorts) > math.ceil(skew * n):
                return "LONG_SHORT_SKEW"
        return None

    # ── shadow regret (A5) ────────────────────────────────────────────────────
    def _simulate(
        self, ordered: List[ScoredCandidate], free_slots: int, avail: float,
        deployed: float, total: float,
    ) -> List[ScoredCandidate]:
        """Greedy admission simulation over `ordered` against a fixed snapshot.
        Consumes a slot + capital per admit; honours the portfolio pre-checks. Pure —
        used only for the regret counterfactual."""
        admitted: List[ScoredCandidate] = []
        symbols: set = set()
        slots = free_slots
        cash = avail
        dep = deployed
        longs = shorts = 0
        for c in ordered:
            if slots <= 0:
                break
            if c.margin_required > cash:
                continue
            if self._passes_portfolio_rules(
                c, admitted_symbols=symbols, deployed=dep, total=total,
                long_count=longs, short_count=shorts,
            ) is not None:
                continue
            admitted.append(c)
            symbols.add(c.symbol)
            slots -= 1
            cash -= c.margin_required
            dep += c.margin_required
            longs += 1 if c.side == "BUY" else 0
            shorts += 1 if c.side == "SELL" else 0
        return admitted

    def _compute_regret(
        self, drained: List[ScoredCandidate], free_slots: int, avail: float,
        deployed: float, total: float,
    ) -> RegretRecord:
        """Pure (given a snapshot): the three regret metrics (A5). Simulates BOTH ranked
        and arrival-order admission against the SAME start-of-window snapshot — the
        counterfactual is directional (it does not replay the live FCFS interleaving)."""
        ranked_admit = self._simulate(rank_candidates(drained), free_slots, avail, deployed, total)
        fcfs_admit = self._simulate(arrival_ordered(drained), free_slots, avail, deployed, total)
        ranked_syms = {c.symbol for c in ranked_admit}
        fcfs_syms = {c.symbol for c in fcfs_admit}
        crowd_out = len(fcfs_syms - ranked_syms)     # FCFS took, ranked would drop
        starvation = len(ranked_syms - fcfs_syms)    # ranked would take, FCFS starved
        swr = sum(c.score for c in ranked_admit) - sum(c.score for c in fcfs_admit)
        try:
            now = self._now()
            window_key = int(float(now.timestamp()) // float(self._cfg.candle_interval_seconds))
            ts_iso = now.isoformat()
        except Exception:
            window_key, ts_iso = 0, ""
        return RegretRecord(
            window_key=window_key, ts_iso=ts_iso, n_candidates=len(drained),
            n_ranked_admit=len(ranked_admit), n_fcfs_admit=len(fcfs_admit),
            crowd_out=crowd_out, starvation=starvation, score_weighted_regret=swr,
            free_slots=free_slots, avail_capital=avail,
            ranked_admit_symbols=sorted(ranked_syms), fcfs_admit_symbols=sorted(fcfs_syms),
        )

    def _log_regret(
        self, drained: List[ScoredCandidate], free_slots: int, avail: float,
        deployed: float, total: float,
    ) -> None:
        rec = self._compute_regret(drained, free_slots, avail, deployed, total)
        self._log.info(
            "allocator.shadow_regret window=%s candidates=%d ranked_admit=%d fcfs_admit=%d "
            "crowd_out=%d starvation=%d score_weighted_regret=%.2f",
            rec.window_key, rec.n_candidates, rec.n_ranked_admit, rec.n_fcfs_admit,
            rec.crowd_out, rec.starvation, rec.score_weighted_regret,
        )
        self._persist_regret(rec)

    def _persist_regret(self, rec: RegretRecord) -> None:
        path = self._cfg.regret_log_path
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_json_dict()) + "\n")
        except Exception as exc:   # persistence is best-effort; never break the loop
            self._log.error("allocator regret persist failed (ignored): %s", exc)

    # ── enforce batch admission (BUILT, not activated this step) ──────────────
    def _run_enforce_batch(
        self, drained: List[ScoredCandidate], free_slots: int, avail: float,
        deployed: float, total: float,
    ) -> None:
        """Rank + greedily admit the window under ONE portfolio_lock acquisition (the
        SAME RLock the fused path uses — the per-candidate admit re-enters it harmlessly,
        while this outer hold keeps the whole batch atomic against interleaving FCFS
        candidates). Each admit runs via the injected `enforce_admit_fn`
        (= signal_processor.admit_prepared → the shared _admit_and_place). Counts/capital
        stay reservation-aware automatically (reserve mutates the live snapshot)."""
        if self._enforce_admit_fn is None or self._portfolio_lock is None:
            self._log.error("allocator enforce: admit_fn/portfolio_lock not wired; dropping %d", len(drained))
            return
        ranked = rank_candidates(drained)
        admitted_symbols: set = set()
        long_count = short_count = 0
        with self._portfolio_lock:
            for c in ranked:
                # live deployed/slots (reservation-aware after each admit)
                snap = self._fm.get_snapshot()
                dep = max(0.0, float(snap.total) - (float(snap.intraday_avail) + float(snap.positional_avail)))
                reason = self._passes_portfolio_rules(
                    c, admitted_symbols=admitted_symbols, deployed=dep,
                    total=float(snap.total), long_count=long_count, short_count=short_count,
                )
                if reason is not None:
                    self._log.info("allocator.enforce reject %s (%s) at %s",
                                   c.symbol, c.signal_id, reason)
                    self._reject_out_of_batch(c, reason)
                    continue
                admitted = False
                try:
                    admitted = bool(self._enforce_admit_fn(c))
                except Exception as exc:
                    self._log.error("allocator.enforce admit error %s: %s", c.symbol, exc)
                if admitted:
                    admitted_symbols.add(c.symbol)
                    long_count += 1 if c.side == "BUY" else 0
                    short_count += 1 if c.side == "SELL" else 0

    def _reject_out_of_batch(self, c: ScoredCandidate, reason: str) -> None:
        """A candidate the portfolio pre-checks dropped BEFORE admit — release its
        in-flight claim + set signal status so it is treated exactly like an FCFS
        reject (A8: admit-or-reject within the window). Delegated to signal_processor
        via the injected reject callback."""
        if self._enforce_reject_fn is None:
            return
        try:
            self._enforce_reject_fn(c, reason)
        except Exception as exc:
            self._log.error("allocator.enforce reject-hook error %s: %s", c.symbol, exc)
