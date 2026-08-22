"""
tests/unit/test_delivery_config_inventory.py

22-Aug-2026 — JOB 1: "fill every empty delivery setting from intraday".

THE SWEEP FOUND NOTHING TO FILL, and this file is what pins that finding so it
cannot silently stop being true.

Why the sweep is closed rather than best-effort: every pydantic model in
core/config_loader.py declares `extra="forbid"`, so the set of *possible* config
keys IS the schema field set. Walking the schema therefore enumerates every
delivery-scoped setting that can exist -- there is no "and maybe some others"
tail. test_extra_forbid_closes_the_key_universe below measures that premise
instead of assuming it.

What this file asserts:

  1. the discovery is NOT vacuous (it finds fields, across files and models)
  2. THE PROPERTY -- every delivery-scoped setting is explicitly written in its
     YAML and is non-null; none is absent, none is supplied silently
  3. the property check CAN go red (a control that deletes a key and expects the
     same checker to report it)
  4. the discovered inventory equals the recorded one -- a NEW delivery-scoped
     key must be added here deliberately, and must be set in the YAML

DELIBERATELY NOT ASSERTED HERE: any particular number. The two delivery COUNT
caps (max_open_delivery_positions=3, max_daily_delivery_trades=5) are TIGHTER
than their intraday twins (5 and 10) on purpose. Writing the intraday value into
them -- the literal instruction "fill from intraday" -- would have LOOSENED two
live risk limits, 3->5 and 5->10. They were left exactly as they are; their
schema defaults are a separate defect handled as NI-4.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterator, get_args

import pytest
import yaml
from pydantic import BaseModel, ValidationError

import core.config_loader as cl

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

# A setting is DELIVERY-SCOPED when its schema field name carries one of these.
# "gtt" is included because the OCO-GTT path is CNC-only -- orders/cnc_gtt.py is
# its sole consumer -- so a gtt_* knob governs the delivery book and nothing else.
_DELIVERY_TOKENS = ("delivery", "positional", "cnc", "gtt")

# The recorded inventory, measured at d00e574 by walking the schema. Each entry is
# (yaml filename, dotted path). A new delivery-scoped key added to the schema must
# be added here too -- that is the point: the addition becomes a decision, not an
# accident, and item 4 below fails until it is made.
_RECORDED_INVENTORY: frozenset[tuple[str, str]] = frozenset({
    ("system_config.yaml", "capital.positional_bucket_pct"),
    ("system_config.yaml", "capital.gtt_sl_limit_offset_pct"),
    ("system_config.yaml", "capital.leverage_map.DELIVERY"),
    ("system_config.yaml", "position_sizing.delivery_risk_per_trade_pct"),
    ("system_config.yaml", "position_sizing.delivery_max_concentration_pct"),
    ("system_config.yaml", "position_sizing.delivery_max_position_value_pct"),
    ("system_config.yaml", "risk.max_open_delivery_positions"),
    ("system_config.yaml", "risk.max_daily_delivery_trades"),
    ("system_config.yaml", "risk.delivery_max_sector_exposure_pct"),
    ("system_config.yaml", "risk.delivery_daily_loss_limit_pct"),
    ("system_config.yaml", "delivery_enabled"),
    ("broker_costs.yaml", "zerodha.stt_cnc_pct"),
    ("broker_costs.yaml", "zerodha.stamp_duty_cnc_buy_pct"),
})

_MISSING = object()


# ── schema walk ──────────────────────────────────────────────────────────────

def _submodels(annotation: Any) -> list[type[BaseModel]]:
    """Every BaseModel subclass inside an annotation (unwraps Optional/list/dict)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    out: list[type[BaseModel]] = []
    for arg in get_args(annotation) or ():
        out.extend(_submodels(arg))
    return out


def _walk_fields(
    model: type[BaseModel], prefix: str = "", chain: tuple[type[BaseModel], ...] = (),
) -> Iterator[tuple[str, str, Any]]:
    """Yield (dotted_path, field_name, FieldInfo) for every field reachable from `model`."""
    if model in chain:   # cycle guard; no model in this schema is self-referential today
        return
    for fname, finfo in model.model_fields.items():
        path = f"{prefix}.{fname}" if prefix else fname
        yield path, fname, finfo
        for sub in _submodels(finfo.annotation):
            yield from _walk_fields(sub, path, chain + (model,))


def _discover() -> list[tuple[str, str, Any]]:
    """(yaml_filename, dotted_path, FieldInfo) for every delivery-scoped schema field."""
    found: list[tuple[str, str, Any]] = []
    for _attr, yaml_name, model in cl._CONFIG_FILES:
        for path, fname, finfo in _walk_fields(model):
            if any(tok in fname.lower() for tok in _DELIVERY_TOKENS):
                found.append((yaml_name, path, finfo))
    return found


def _raw(yaml_name: str) -> dict:
    return yaml.safe_load((_CONFIG_DIR / yaml_name).read_text(encoding="utf-8"))


def _resolve(raw: Any, dotted: str) -> Any:
    cur = raw
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _unset_keys(raw_by_file: dict[str, dict]) -> list[str]:
    """The checker under test: every delivery-scoped key that is absent or null."""
    bad: list[str] = []
    for yaml_name, path, _finfo in _discover():
        value = _resolve(raw_by_file[yaml_name], path)
        if value is _MISSING:
            bad.append(f"{yaml_name}:{path} ABSENT")
        elif value is None:
            bad.append(f"{yaml_name}:{path} NULL")
    return bad


# ── 1. the discovery is not vacuous ──────────────────────────────────────────

def test_the_sweep_is_not_vacuous() -> None:
    """A discovery that silently returns nothing would make every check below green."""
    found = _discover()
    assert len(found) >= 13, f"schema walk found only {len(found)} delivery-scoped fields"
    assert len({f for f, _p, _fi in found}) >= 2, "expected hits in more than one config file"
    assert len({p.rsplit(".", 1)[0] for _f, p, _fi in found}) >= 4, (
        "expected delivery-scoped fields under several sections, got "
        f"{sorted({p.rsplit('.', 1)[0] for _f, p, _fi in found})}"
    )
    print(f"  OK schema walk discovered {len(found)} delivery-scoped settings")


def test_extra_forbid_closes_the_key_universe() -> None:
    """The sweep is only closed because no model accepts an unlisted key.

    Measured, not assumed -- and with a control proving the rejection is real.
    """
    import inspect
    models = [o for o in vars(cl).values()
              if inspect.isclass(o) and issubclass(o, BaseModel) and o is not BaseModel]
    assert models, "no pydantic models found in config_loader"
    loose = [m.__name__ for m in models if m.model_config.get("extra") != "forbid"]
    assert not loose, f"models that would accept an unlisted config key: {loose}"

    # control: an unknown delivery-shaped key must be REJECTED, not absorbed.
    with pytest.raises(ValidationError):
        cl.RiskConfig(**{
            "max_open_positions": 5, "max_daily_trades": 10,
            "max_sector_exposure_pct": 0.40, "max_consecutive_losses": 4,
            "daily_loss_limit_pct": 0.03,
            "delivery_max_sector_exposure_pct": 0.40,
            "delivery_daily_loss_limit_pct": 0.03,
            "delivery_not_a_real_key_pct": 0.5,
        })
    print(f"  OK all {len(models)} config models forbid extras; unknown key rejected")


# ── 2. THE PROPERTY ──────────────────────────────────────────────────────────

def test_every_delivery_scoped_setting_is_explicitly_set() -> None:
    """No delivery-scoped setting is absent from its YAML, and none is null.

    This is JOB 1's finding, pinned. It goes red the day someone adds a
    delivery-scoped key to the schema and does not write it into the YAML --
    which is exactly the state F1 was built to end.
    """
    raw_by_file = {name: _raw(name) for name, _p, _fi in _discover()}
    unset = _unset_keys(raw_by_file)
    assert not unset, "delivery-scoped settings that are absent or null:\n  " + "\n  ".join(unset)
    print("  OK every delivery-scoped setting is written explicitly and is non-null")


def test_the_shipped_config_loads() -> None:
    """The whole config still validates -- JOB 1 changed no value, so it must."""
    app = cl.load_all(_CONFIG_DIR)
    assert app.system.position_sizing.delivery_risk_per_trade_pct is not None
    assert app.system.risk.delivery_daily_loss_limit_pct is not None
    print("  OK load_all(config) succeeds")


# ── 3. the control: the property check can go red ────────────────────────────

@pytest.mark.parametrize("victim", [
    "position_sizing.delivery_risk_per_trade_pct",
    "risk.max_open_delivery_positions",
    "capital.gtt_sl_limit_offset_pct",
])
def test_the_property_check_can_go_red(victim: str) -> None:
    """Delete a delivery key from an in-memory copy -> the SAME checker must report it.

    Without this, test_every_delivery_scoped_setting_is_explicitly_set could be
    passing because the checker is broken rather than because the config is right.
    """
    raw_by_file = {name: _raw(name) for name, _p, _fi in _discover()}
    section, _, leaf = victim.rpartition(".")
    mutated = copy.deepcopy(raw_by_file)
    node = _resolve(mutated["system_config.yaml"], section)
    assert node is not _MISSING and leaf in node, f"control setup failed for {victim}"

    del node[leaf]
    assert any(victim in row for row in _unset_keys(mutated)), (
        f"CONTROL FAILED: deleting {victim} did not make the checker complain"
    )

    # and the null form, which is the shape F1's fail-closed rule exists for
    mutated = copy.deepcopy(raw_by_file)
    _resolve(mutated["system_config.yaml"], section)[leaf] = None
    assert any(victim in row for row in _unset_keys(mutated)), (
        f"CONTROL FAILED: nulling {victim} did not make the checker complain"
    )
    print(f"  OK control: absent and null {victim} are both caught")


# ── 4. the inventory is the recorded one ─────────────────────────────────────

def test_delivery_scoped_inventory_matches_the_record() -> None:
    """A new delivery-scoped key must be added to _RECORDED_INVENTORY on purpose.

    This is a change-detector, not a count: it names every member, so the failure
    message says WHICH key appeared or vanished.
    """
    discovered = frozenset((f, p) for f, p, _fi in _discover())
    added = sorted(discovered - _RECORDED_INVENTORY)
    gone = sorted(_RECORDED_INVENTORY - discovered)
    assert not added, (
        "NEW delivery-scoped schema field(s) -- set them in the YAML and record them "
        f"here deliberately: {added}"
    )
    assert not gone, f"delivery-scoped field(s) disappeared from the schema: {gone}"
    print(f"  OK inventory matches the record ({len(discovered)} settings)")
