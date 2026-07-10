"""
strategies/control.py  --  Slice 2 strategy-control resolver (single source of truth).

ONE pure function, ``strategy_will_trade()``, is the SOLE authority for the
question "will this strategy trade today?" — imported by BOTH the entry gate
(``signals/signal_processor``) and the status table (``scripts/cron_officer`` /
pre-flight), so the gate and the displayed table can NEVER disagree.

The 3 control layers (+ the pre-existing emergency breaker, LAYER 0):

  LAYER 0  ``force_intraday_only``  — emergency breaker. Option A (10-Jul-2026): the
           loader NO LONGER rewrites intent at load (declared intent is preserved), so
           the ``force + DELIVERY`` branch below is now the LIVE mechanism that dormants
           a DELIVERY strategy while the breaker is on — it returns WON'T TRADE so the
           strategy never reaches sizing/placement. MIS-only for anything that DOES
           trade is guaranteed separately at the broker product chokepoint
           (``zerodha_adapter.place_order`` coerces to MIS under the breaker).
  LAYER 1  ``system_config.trade_type``  INTRADAY | DELIVERY | BOTH — master
           product gate (which product type may trade today).
  LAYER 2  ``strategy.intent``  INTRADAY | DELIVERY — the strategy's product type.
  LAYER 3  ``strategy.enabled``  — per-strategy ON/OFF switch.

THE RULE — a strategy WILL TRADE iff ALL hold:
    enabled  AND  (intent permitted by trade_type)  AND  (breaker doesn't block it).

Pure: no I/O, no mode branch — paper and live evaluate identically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

VALID_TRADE_TYPES = {"INTRADAY", "DELIVERY", "BOTH"}

# SLICE2.5-PHASE-4: machine-readable cause codes on the Verdict so the entry gate
# can split a trade_type×intent mismatch into its OWN reject label WITHOUT string-
# matching the human-readable reason. CAUSE_TRADE_TYPE is the ONLY one the gate
# promotes to a distinct "TRADE_TYPE" reject label (so a delivery go-live shows
# "rejected because trade_type disallows" distinctly); every other cause keeps the
# generic "STRATEGY_CONTROL" label. The reason strings are unchanged.
CAUSE_OK = "OK"                       # will_trade=True
CAUSE_DISABLED = "DISABLED"           # LAYER 3 — per-strategy switch off
CAUSE_FORCE_BREAKER = "FORCE_BREAKER" # LAYER 0 — force_intraday_only blocks raw DELIVERY
CAUSE_TRADE_TYPE = "TRADE_TYPE"       # LAYER 1×2 — master trade_type ≠ strategy intent


@dataclass(frozen=True)
class Verdict:
    """Outcome of :func:`strategy_will_trade`.

    will_trade : the gate proceeds iff True; the table renders WILL/WON'T TRADE.
    reason     : human-readable full-words explanation (logs / table / Telegram).
    product    : the effective product the strategy WOULD place given the inputs
                 the resolver saw ("INTRADAY"/"DELIVERY"); None only on a missing
                 intent. NB this is the EFFECTIVE (post-force-rewrite) product the
                 gate's placement path uses — the status table sources the strategy's
                 TRUE declared intent separately for its "Type" column.
    """
    will_trade: bool
    reason: str
    product: Optional[str]
    # SLICE2.5-PHASE-4: machine-readable cause (default OK so any 3-arg construction
    # still works). The gate maps CAUSE_TRADE_TYPE -> a distinct reject label.
    cause: str = CAUSE_OK


def strategy_will_trade(
    strategy: Any,
    *,
    trade_type: str,
    force_intraday_only: bool,
) -> Verdict:
    """The single authority. ``strategy`` is a StrategyConfig (anything with
    ``.intent`` + ``.enabled``); ``trade_type`` + ``force_intraday_only`` come from
    SystemConfig. See module docstring for THE RULE."""
    intent = getattr(strategy, "intent", None)
    enabled = bool(getattr(strategy, "enabled", True))

    # LAYER 3 — the switch. Checked first: a disabled strategy never trades,
    # regardless of product gating.
    if not enabled:
        return Verdict(False, "WON'T TRADE — switch disabled", intent,
                       cause=CAUSE_DISABLED)

    # LAYER 0 — emergency breaker. Option A (10-Jul): the loader preserves declared
    # intent, so this branch is the LIVE dormancy for a DELIVERY strategy while the
    # breaker is on (it no longer only fires under test). Returns WON'T TRADE so the
    # strategy never reaches placement; MIS-only for survivors is enforced at the
    # broker product chokepoint.
    if force_intraday_only and intent == "DELIVERY":
        return Verdict(
            False,
            "WON'T TRADE — emergency breaker (force_intraday_only) forces "
            "intraday; delivery strategy dormant",
            "INTRADAY",
            cause=CAUSE_FORCE_BREAKER,
        )

    # LAYER 1 × LAYER 2 — master trade_type vs the strategy's (effective) intent.
    if trade_type == "INTRADAY" and intent != "INTRADAY":
        return Verdict(
            False,
            "WON'T TRADE — master INTRADAY blocks this DELIVERY strategy",
            intent,
            cause=CAUSE_TRADE_TYPE,
        )
    if trade_type == "DELIVERY" and intent != "DELIVERY":
        return Verdict(
            False,
            "WON'T TRADE — master DELIVERY blocks this INTRADAY strategy",
            intent,
            cause=CAUSE_TRADE_TYPE,
        )
    # trade_type == "BOTH", or a matching intent → permitted.

    return Verdict(True, f"WILL TRADE — enabled, master allows {intent}", intent,
                   cause=CAUSE_OK)
