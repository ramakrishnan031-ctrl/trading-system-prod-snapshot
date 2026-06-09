"""
capital/performance_allocator.py -- Trading System v2  FIX-132 Item 9

Purpose:
    Compute per-strategy position-size multipliers from recent win rates,
    so outperforming strategies receive more capital than underperformers.

Locked Design Decisions:
    PA1  -- Standalone read-only module; no state mutations.
    PA2  -- win_rate = (CLOSED trades with net_pnl > 0) / (total CLOSED trades).
    PA3  -- raw_weight = max(min_weight, min(max_weight, win_rate / avg_win_rate)).
    PA4  -- Normalized so weights average 1.0 (neutral: same as equal-weight baseline).
    PA5  -- Fallback: < min_history_days days of CLOSED trades for a strategy → weight = 1.0.
    PA6  -- Global avg_win_rate uses all strategies with sufficient history.
    PA7  -- Thread-safe: compute_weights() is pure; cached at session start in main.py.
    PA8  -- Weights never exceed max_weight, never go below min_weight (after normaliz.).
    PA9  -- ConfigModel: CapitalAllocationConfig in config_loader.py.
    PA10 -- No DB writes; queries trades table (CLOSED status, net_pnl column).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class _StrategyStats:
    strategy: str
    closed_trades: int
    wins: int
    trading_days: int
    win_rate: float


class PerformanceAllocator:
    """
    Computes per-strategy position-size multipliers from last-N-days win rates.

    Usage (called once at session start)::
        allocator = PerformanceAllocator(state_store, logger)
        weights = allocator.compute_weights(
            strategy_names=["momentum_long", "mean_reversion_short"],
            lookback_days=10,
            min_history_days=5,
            min_weight=0.5,
            max_weight=2.0,
        )
        # weights == {"momentum_long": 1.3, "mean_reversion_short": 0.7}
    """

    def __init__(self, state_store: Any, logger: Any) -> None:
        self._store = state_store
        self._log = logger

    def compute_weights(
        self,
        strategy_names: Sequence[str],
        lookback_days: int = 10,
        min_history_days: int = 5,
        min_weight: float = 0.5,
        max_weight: float = 2.0,
    ) -> Dict[str, float]:
        """
        PA3-PA6: Return {strategy_name: position_size_multiplier}.

        Strategies with < min_history_days of trade data get weight 1.0 (PA5).
        Weights are normalized so their mean = 1.0 (PA4), then re-clamped.
        """
        if not strategy_names:
            return {}

        today = date.today()
        since = (today - timedelta(days=lookback_days)).isoformat()

        stats = self._fetch_stats(list(strategy_names), since)

        # Strategies with enough history get a computed weight; others get 1.0.
        raw: Dict[str, float] = {}
        sufficient: List[_StrategyStats] = []
        for name in strategy_names:
            st = stats.get(name)
            if st is None or st.trading_days < min_history_days or st.closed_trades == 0:
                raw[name] = 1.0  # PA5 fallback
            else:
                sufficient.append(st)

        if not sufficient:
            # No strategy has enough data — equal weights.
            self._log.info(
                "performance_allocator.equal_weights",
                extra={"reason": "no_sufficient_history", "strategies": list(strategy_names)},
            )
            return {name: 1.0 for name in strategy_names}

        # PA6: global average win rate across strategies with sufficient history
        avg_win_rate = sum(s.win_rate for s in sufficient) / len(sufficient)
        if avg_win_rate <= 0.0:
            avg_win_rate = 0.5  # avoid division by zero; fallback to 50%

        for st in sufficient:
            raw[st.strategy] = max(min_weight, min(max_weight, st.win_rate / avg_win_rate))

        # PA4: normalize so mean = 1.0 (strategies with fallback weight 1.0 are included)
        all_weights = list(raw.values())
        mean_w = sum(all_weights) / len(all_weights)
        if mean_w > 0:
            normalized = {name: w / mean_w for name, w in raw.items()}
        else:
            normalized = {name: 1.0 for name in raw}

        # Re-clamp after normalization (PA8)
        final: Dict[str, float] = {
            name: max(min_weight, min(max_weight, w))
            for name, w in normalized.items()
        }

        self._log.info(
            "performance_allocator.weights_computed",
            extra={
                "lookback_days": lookback_days,
                "avg_win_rate": round(avg_win_rate, 3),
                "weights": {k: round(v, 3) for k, v in final.items()},
            },
        )
        return final

    # ── internals ──────────────────────────────────────────────────────────────

    def _fetch_stats(
        self, strategy_names: List[str], since_date_iso: str
    ) -> Dict[str, _StrategyStats]:
        """Query closed trades per strategy since since_date_iso."""
        placeholders = ",".join("?" * len(strategy_names))
        try:
            rows = self._store.fetch_all(
                f"""
                SELECT
                    strategy,
                    COUNT(*) AS closed_trades,
                    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    COUNT(DISTINCT DATE(exit_time)) AS trading_days
                FROM trades
                WHERE status IN ('CLOSED', 'CLOSED_MANUAL')
                  AND net_pnl IS NOT NULL
                  AND DATE(created_at) >= ?
                  AND strategy IN ({placeholders})
                GROUP BY strategy
                """,
                (since_date_iso, *strategy_names),
            )
        except Exception as exc:
            self._log.error(
                "performance_allocator.fetch_failed",
                extra={"error": str(exc)},
            )
            return {}

        result: Dict[str, _StrategyStats] = {}
        for row in rows:
            row_d = dict(row)
            name = row_d["strategy"]
            closed = int(row_d["closed_trades"] or 0)
            wins = int(row_d["wins"] or 0)
            days = int(row_d["trading_days"] or 0)
            win_rate = wins / closed if closed > 0 else 0.0
            result[name] = _StrategyStats(
                strategy=name,
                closed_trades=closed,
                wins=wins,
                trading_days=days,
                win_rate=win_rate,
            )
        return result
