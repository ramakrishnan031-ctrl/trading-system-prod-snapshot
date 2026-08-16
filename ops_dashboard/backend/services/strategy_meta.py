"""SHARED STRATEGY IDENTITY + TRADE TYPE — the ONE path Screens 19 and 20 use.

⭐ WHY THIS MODULE EXISTS. Screens 19 (Strategy Ranking) and 20 (Strategy Health)
are two views of the same strategies, and they were built together so that
strategy identity and Trade Type resolve through ONE function rather than two
that can drift. ⛔ Neither screen reads the YAML itself.

═══════════════════════════════════════════════════════════════════════════════
⭐ TRADE TYPE — MEASURED, ⛔ NOT ASSUMED (16-Aug-2026)

  SOURCE      `config/strategies/<name>.yaml`  →  key `intent`
  ENUM        INTRADAY | DELIVERY — enforced by the production validator
              `strategies/schema.py::_val_intent` ("intent must be INTRADAY or
              DELIVERY"), read BY VALUE here, ⛔ never imported (isolation I1).
  MEASURED    16 strategy files: 13 INTRADAY, 3 DELIVERY (positional_momentum_
              long, positional_sector_rotation, positional_swing_long).
  READER      `config_reader.get_strategies()` already parses `intent`.
  NORMALISER  `strategy_tower._trade_type` already existed and is REUSED — this
              module re-exports it as the canonical one so there is exactly ONE
              definition. ⛔ A second mapping was NOT written.

⛔⛔ THREE DIFFERENT "TRADE TYPE" FUNCTIONS EXIST IN THIS CODEBASE AND THEY MEAN
DIFFERENT THINGS. Confusing them would put one quantity under another's label:

  · THIS one            — the STRATEGY's configured intent (YAML). A property of
                          the strategy, true before a single trade is placed.
  · `analytics_period._trade_type(row)`  — the TRADE's product (orders.product →
                          MIS/CNC/UNKNOWN). A property of what actually executed.
  · `db_reader._trade_type_of_product()` — the same trade-level idea, labelled
                          Intraday/Delivery.

⭐ Screen 19 shows the STRATEGY-level value (the brief's requirement) and its
Trade Type FILTER binds to that same value, so the column and the filter on one
screen can never describe two different things.

⛔ A strategy whose YAML is missing or whose `intent` is unreadable returns None
and the UI prints the project's unavailable marker — ⛔ it is never guessed, and
⛔ no strategy→type pair is hard-coded anywhere.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from typing import Optional

from ..readers import config_reader
from .strategy_tower import _trade_type as _trade_type_of_intent

#: The values the YAML validator permits, by value. Kept so a test can prove the
#: normaliser covers the real enum rather than a guessed one.
YAML_INTENTS = ("INTRADAY", "DELIVERY")

#: What the UI shows. ⛔ Not an enum of its own invention — each maps 1:1 from a
#: real `intent`, and anything else resolves to None (unavailable).
TRADE_TYPES = ("Intraday", "Delivery")


def trade_type_of_intent(intent) -> Optional[str]:
    """The canonical strategy-level Trade Type. ⭐ Delegates to the normaliser
    that already existed, so there is ONE definition in the codebase."""
    return _trade_type_of_intent(intent)


def strategy_meta(cfg: dict) -> dict:
    """{strategy_name: {name, display_name, trade_type, direction, enabled}}.

    ⭐ The single strategy-identity source for Screens 19 and 20. Read-only over
    `config_reader.get_strategies`, which parses the per-strategy YAML.
    ⛔ `trade_type` is None when the YAML carries no readable `intent`; the UI
    renders the unavailable marker rather than a guess.
    """
    out: dict = {}
    for name, conf in (config_reader.get_strategies(cfg) or {}).items():
        out[name] = {
            "name": name,
            "display_name": conf.get("display_name") or name,
            "trade_type": trade_type_of_intent(conf.get("intent")),
            "intent": conf.get("intent"),          # kept verbatim for audit
            "direction": conf.get("direction"),
            "enabled": conf.get("enabled"),
        }
    return out


def trade_type_options(meta: dict) -> list:
    """The Trade Type values ACTUALLY present in the configured strategies, in
    the approved order. ⭐ A filter that offered a value no strategy has would
    return nothing and read as a broken control."""
    present = {m.get("trade_type") for m in (meta or {}).values() if m.get("trade_type")}
    return [t for t in TRADE_TYPES if t in present]
