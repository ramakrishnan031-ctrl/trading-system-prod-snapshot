"""
strategies/control.py  --  Slice 2 strategy-control resolver (single source of truth).

ONE pure function, ``strategy_will_trade()``, is the SOLE authority for the
question "will this strategy trade today?" — imported by BOTH the entry gate
(``signals/signal_processor``) and the status table (``scripts/cron_officer`` /
pre-flight), so the gate and the displayed table can NEVER disagree.

The 3 control layers (+ the pre-existing emergency breaker, LAYER 0):

  LAYER 0  ``force_intraday_only``  — emergency breaker. Rewrites every strategy's
           intent to INTRADAY at LOAD (``strategies/loader``). By the time this
           resolver runs, a force-on DELIVERY strategy already carries
           intent=INTRADAY and trades as intraday. The defensive ``force +
           DELIVERY`` branch below therefore never fires in production (the loader
           pre-rewrites) — it exists to LOCK the breaker's "block live delivery"
           semantics under unit test.
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
        return Verdict(False, "WON'T TRADE — switch disabled", intent)

    # LAYER 0 — emergency breaker (defensive). In production the loader has already
    # rewritten a DELIVERY strategy's intent to INTRADAY when the breaker is on, so
    # this only fires if a RAW DELIVERY intent reaches the resolver — it locks the
    # "breaker blocks live delivery" guarantee for tests.
    if force_intraday_only and intent == "DELIVERY":
        return Verdict(
            False,
            "WON'T TRADE — emergency breaker (force_intraday_only) forces "
            "intraday; delivery strategy dormant",
            "INTRADAY",
        )

    # LAYER 1 × LAYER 2 — master trade_type vs the strategy's (effective) intent.
    if trade_type == "INTRADAY" and intent != "INTRADAY":
        return Verdict(
            False,
            "WON'T TRADE — master INTRADAY blocks this DELIVERY strategy",
            intent,
        )
    if trade_type == "DELIVERY" and intent != "DELIVERY":
        return Verdict(
            False,
            "WON'T TRADE — master DELIVERY blocks this INTRADAY strategy",
            intent,
        )
    # trade_type == "BOTH", or a matching intent → permitted.

    return Verdict(True, f"WILL TRADE — enabled, master allows {intent}", intent)
