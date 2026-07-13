"""
strategies/taxonomy.py — Trading System v2 · V3 side-task A · strategy taxonomy display.

METADATA + DISPLAY ONLY. Reads the DECLARED `pipeline` / `horizon` fields on each strategy
(added to StrategyConfig) and formats them for the operator (the status table + the EOD
report). NO routing, NO execution, NO capital split — pure/read-only. This is the
prerequisite substrate for the future two-pipeline split, but it changes NOTHING today.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple


def taxonomy_label(pipeline: Optional[str], horizon: Optional[str]) -> str:
    """A compact operator-facing category, e.g. 'INTRADAY·SAME_DAY'. Tolerant of a
    missing value (shows '—') so a display call can never raise."""
    p = str(pipeline).strip() if pipeline else "—"
    h = str(horizon).strip() if horizon else "—"
    return f"{p}·{h}"


def build_taxonomy_map(config_dir: str | Path = "config") -> Dict[str, Tuple[str, str]]:
    """{strategy_name: (pipeline, horizon)} read from the strategy YAMLs. Fail-safe: a
    YAML that fails to validate is skipped (that strategy simply displays '—'), so the
    report/table can never be broken by one malformed file. Reused by both surfaces."""
    from strategies.schema import validate_strategy
    out: Dict[str, Tuple[str, str]] = {}
    d = Path(config_dir) / "strategies"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.yaml")):
        try:
            cfg = validate_strategy(p)
            out[cfg.name] = (cfg.pipeline, cfg.horizon)
        except Exception:
            continue
    return out
