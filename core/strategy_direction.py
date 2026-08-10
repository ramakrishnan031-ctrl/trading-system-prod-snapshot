"""core/strategy_direction.py — canonical strategy-direction accessor + registry I/O.

`StrategyConfig.direction` (config/strategies/<name>.yaml) is the SINGLE source of truth
for a strategy's LONG/SHORT. This module exposes it as ONE canonical accessor so every
consumer (regime, ranking, allocator, analytics, reports, GUI) reads the same STRUCTURED
value instead of parsing the `_long`/`_short` name suffix — a rename or a condition change
never changes a direction, because direction comes from the declared field, never the name.

It also reads/writes the REGISTRATION + HEALTH registry, which is SPLIT IN TWO:
the git-TRACKED SEED (config/strategy_direction_registry.yaml, read-only in
production) and the RUNTIME STATE (data_store/strategy_direction_registry.yaml,
gitignored, the only thing production writes). Three concepts are kept SEPARATE:
    direction            LONG | SHORT            (authoritative; mirrors StrategyConfig.direction)
    registration_status  PENDING | CONFIRMED
    health               OK | DIRECTION_CONFLICT
The daily scripts/strategy_registry_officer.py OWNS registry writes; nothing on a live
trading path writes it, and NOTHING reads direction FROM the registry to place a trade —
the registry mirrors the config field, it never overrides it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml

VALID_DIRECTIONS = {"LONG", "SHORT"}
VALID_STATUSES = {"PENDING", "CONFIRMED"}
VALID_HEALTH = {"OK", "DIRECTION_CONFLICT"}

# SEED (git-TRACKED, read-only in production) vs STATE (runtime, gitignored).
#
# The officer used to write its runtime state straight into the tracked config file.
# The deployed work tree then differed from HEAD permanently, so system_manager's
# `deployed_tree_check` raised a violation and its severity rule turned the whole EOD
# report CRITICAL every day — an alarm that is always red is one nobody reads.
#
# ⛔ The tracked file is now a SEED and production NEVER writes it. State lives under
# data_store/, which is already gitignored, so the deploy hook's `git checkout -f`
# leaves it alone. ⛔ Untracking the seed instead was MEASURED to DELETE the live file
# on the first deploy (checkout -f removes a path that was tracked at the old HEAD),
# which is why the seed stays tracked rather than being ignored.
DEFAULT_SEED_PATH = "config/strategy_direction_registry.yaml"
DEFAULT_STATE_PATH = "data_store/strategy_direction_registry.yaml"

# Back-compat alias: the pre-split name always meant the tracked file.
DEFAULT_REGISTRY_PATH = DEFAULT_SEED_PATH


# ── Canonical direction accessor (retires the name-suffix parse) ────────────────

def build_direction_map(config_dir: str | Path = "config") -> Dict[str, str]:
    """``{strategy_name: 'LONG'|'SHORT'}`` from config/strategies/*.yaml via the
    validated ``StrategyConfig``. Direction is the DECLARED field — never the name
    suffix. Fail-safe: a YAML that fails validation is skipped (never raises), so a
    display/consumer call can't be broken by one malformed file.
    """
    from strategies.schema import validate_strategy

    out: Dict[str, str] = {}
    d = Path(config_dir) / "strategies"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.yaml")):
        try:
            cfg = validate_strategy(p)
        except Exception:
            continue
        if cfg.direction in VALID_DIRECTIONS:
            out[cfg.name] = cfg.direction
    return out


def canonical_direction(name: str, config_dir: str | Path = "config") -> Optional[str]:
    """The canonical LONG/SHORT for ``name`` from ``StrategyConfig.direction``, or None
    if unknown. Consumers use THIS (or ``build_direction_map`` for a batch) instead of
    ``endswith('_long')``/``('_short')``. For a hot path the live code already holds a
    loaded ``StrategyLoader``; this is for the non-hot consumers (GUI/reports/officer)."""
    return build_direction_map(config_dir).get(name)


# ── Registration + health registry I/O ─────────────────────────────────────────

def _read_registry(p: Path) -> Optional[Dict[str, dict]]:
    """Parse one registry YAML. ``None`` when it is missing or unreadable — distinct from
    ``{}``, which is a file that parsed and legitimately holds no strategies."""
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        strategies = data.get("strategies", {}) or {}
        return {str(k): dict(v or {}) for k, v in strategies.items()}
    except Exception:
        return None


def load_registry(path: str | Path = DEFAULT_STATE_PATH,
                  seed_path: str | Path | None = None) -> Dict[str, dict]:
    """Return ``{name: {direction, registration_status, health, first_seen, evidence}}``.

    ``path`` is the runtime STATE file. ``seed_path``, when given, is the git-tracked
    SEED used ONLY as a fallback while the state file does not yet exist — the first
    run on a freshly deployed machine. That fallback is what makes the split lossless:
    the seed carries the original ``first_seen`` dates and the full row set, so a
    migrating officer re-announces nothing as NEW and no provenance is lost.

    ⛔ The seed is a FALLBACK, never an override: once state exists it wins outright,
    otherwise every deploy would silently revert converged runtime state.

    Missing/unreadable, with no usable seed → ``{}`` (fail-safe; the officer then treats
    every strategy as new). ``seed_path`` is opt-in, so existing callers are unchanged.
    """
    found = _read_registry(Path(path))
    if found is not None:
        return found
    if seed_path is not None:
        seeded = _read_registry(Path(seed_path))
        if seeded is not None:
            return seeded
    return {}


def save_registry(registry: Dict[str, dict], path: str | Path = DEFAULT_STATE_PATH) -> None:
    """Atomically write the registry YAML. Called ONLY by the daily officer (owns writes).

    ⛔ Writes STATE, never the tracked seed. The parent is created because data_store/ is
    gitignored, so a freshly checked-out tree may not carry it."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"strategies": dict(sorted(registry.items()))}, fh,
                       sort_keys=False, default_flow_style=False, allow_unicode=True)
    tmp.replace(p)  # atomic rename


def registered_direction(name: str, registry: Dict[str, dict]) -> Optional[str]:
    """The direction recorded in the registry for ``name`` (mirrors the config field), or None."""
    row = registry.get(name) or {}
    d = row.get("direction")
    return d if d in VALID_DIRECTIONS else None
