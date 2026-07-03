"""
ops_dashboard/backend/services/strategy_score.py

Deterministic strategy scorecard (G2b-2 §1.1) + silence tiers (§1.2). PURE
functions — table-driven-tested; thresholds come from gui_config.yaml
`scorecard:` / `silence:` (defaults mirrored here).

Badges: GREEN Excellent / YELLOW Warning / RED Attention / GRAY (disabled —
neutral, never RED). Every non-GREEN badge carries its contributing reasons.

Silence: 🟢 ≤ green_max_min / 🟡 ≤ yellow_max_min / 🔴 beyond-or-none —
colored ONLY inside expected-activity windows; outside, plain age (no alarm).
"""
from __future__ import annotations

from typing import Optional

DEFAULT_SCORECARD = {
    "red_pnl_floor": -500.0,
    "red_failed_orders": 3,
    "red_consec_losses": 3,
    "yellow_silence_min": 90,
    "yellow_win_rate_pct": 40.0,
    "yellow_min_closed": 3,
    "yellow_failed_orders": 1,
    "yellow_capacity_pct": 90.0,
}
DEFAULT_SILENCE = {"green_max_min": 30, "yellow_max_min": 120}


def _th(thresholds: Optional[dict], key: str) -> float:
    base = dict(DEFAULT_SCORECARD)
    if thresholds:
        base.update({k: v for k, v in thresholds.items() if v is not None})
    return base[key]


def silence_tier(age_min: Optional[float], in_window: bool,
                 silence_cfg: Optional[dict] = None) -> dict:
    """Last-signal-age tier. Outside expected windows → plain (no alarm)."""
    cfg = dict(DEFAULT_SILENCE)
    if silence_cfg:
        cfg.update({k: v for k, v in silence_cfg.items() if v is not None})
    if not in_window:
        return {"age_min": age_min, "color": None, "alarm": False}
    if age_min is None or age_min > cfg["yellow_max_min"]:
        return {"age_min": age_min, "color": "RED", "alarm": True}
    if age_min > cfg["green_max_min"]:
        return {"age_min": age_min, "color": "YELLOW", "alarm": True}
    return {"age_min": age_min, "color": "GREEN", "alarm": False}


def score(inputs: dict, thresholds: Optional[dict] = None,
          silence_cfg: Optional[dict] = None) -> dict:
    """Compute the badge for one strategy.

    inputs (all pre-derived by strategy_tower; None-tolerant):
      enabled            bool|None   (None = unconfigured → treated enabled)
      expected_activity  bool        (freshness: is signal activity expected NOW)
      last_signal_age_min float|None (None = no signal today)
      failed_orders      int         (FAILED orders today, R5 'rejected')
      consec_losses      int         (per-strategy trailing losing closes)
      net_pnl            float
      win_rate           float|None  (percent over decided)
      closed_decided     int         (wins + losses)
      capacity_used_pct  float|None  (active/max_concurrent × 100)
      in_entry_window    bool
    """
    if inputs.get("enabled") is False:
        return {"badge": "GRAY", "reasons": ["strategy disabled"]}

    t = lambda k: _th(thresholds, k)  # noqa: E731
    red, yellow = [], []

    # ── RED rules ──
    sil = silence_tier(inputs.get("last_signal_age_min"),
                       bool(inputs.get("expected_activity")), silence_cfg)
    if sil["color"] == "RED":
        age = inputs.get("last_signal_age_min")
        red.append("silent while activity expected"
                   + (f" (last signal {int(age)}m ago)" if age is not None else " (no signal today)"))
    if int(inputs.get("failed_orders", 0)) >= int(t("red_failed_orders")):
        red.append(f"failed orders today ≥ {int(t('red_failed_orders'))}")
    if int(inputs.get("consec_losses", 0)) >= int(t("red_consec_losses")):
        red.append(f"consecutive losses ≥ {int(t('red_consec_losses'))}")
    if float(inputs.get("net_pnl", 0.0)) <= float(t("red_pnl_floor")):
        red.append(f"net P&L ≤ ₹{t('red_pnl_floor'):.0f}")
    if red:
        return {"badge": "RED", "reasons": red}

    # ── YELLOW rules ──
    age = inputs.get("last_signal_age_min")
    if (inputs.get("in_entry_window") and age is not None
            and age > float(t("yellow_silence_min"))):
        yellow.append(f"no signal for {int(age)}m (> {int(t('yellow_silence_min'))}m)")
    wr = inputs.get("win_rate")
    if (wr is not None and int(inputs.get("closed_decided", 0)) >= int(t("yellow_min_closed"))
            and float(wr) < float(t("yellow_win_rate_pct"))):
        yellow.append(f"win rate {wr:.0f}% < {t('yellow_win_rate_pct'):.0f}%")
    if int(inputs.get("failed_orders", 0)) >= int(t("yellow_failed_orders")):
        yellow.append("failed order(s) today")
    cap = inputs.get("capacity_used_pct")
    if cap is not None and float(cap) >= float(t("yellow_capacity_pct")):
        yellow.append(f"position capacity {cap:.0f}% used")
    if yellow:
        return {"badge": "YELLOW", "reasons": yellow}

    return {"badge": "GREEN", "reasons": []}
