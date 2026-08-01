"""
core/effect_telemetry.py — Trading System v2

Purpose:
    Ledger item #1 (IA-XARCH-01): effect-verification telemetry for families
    beta (structural starvation) and gamma (algebraic unreachability).
    OBSERVATIONAL ONLY — nothing here alters behaviour, and nothing here may
    perturb the money path.

    THE AUTHORITY is the frozen Phase-A contract:
        docs/audit/effect_verification_contract_01aug2026.md
    and its C2/C6 single source of truth:
        config/expected_managers.yaml
    ⛔ Review rule (C2): a new manager ctor in main.py REQUIRES a registry
    entry in the SAME diff, plus a handle()/register_constructed() call.

Mechanics (C4 — the hot path cannot raise, allocate, log, or do I/O):
    - handle(name) is called ONCE, at construction time (manager __init__ or
      the main.py composition root). It resolves the pre-allocated counter
      handle and marks the unit CONSTRUCTED. Cold path; takes the module lock.
    - The hot path is EffectCounter.inc(): a single integer increment on an
      already-resolved attribute. No dict lookup, no allocation beyond the
      int itself, no branching inside this module, cannot raise.
    - Counters "reset per trading day" by PROCESS LIFECYCLE: the service boots
      08:15 and self-exits 17:35 (FIX-189), so a process spans exactly one
      trading day. There is deliberately no timer-based reset.

Census + assertion semantics (frozen — B2/B4 of the Phase-B card):
    - assert_composition(): at end of boot, the registry's expectation is
      compared with what actually registered.
        dev/paper  -> raise EffectCompositionError (FAIL FAST)
        live       -> CRITICAL alert + continue (NEVER crash a live boot)
    - emit_census(): at _shutdown() entry (main.py — the single clean-stop
      convergence point), one stable-ordered line per counter-bearing /
      covered-existing unit, then the MISMATCH block:
        (i)   expected-active   & acted == 0
        (ii)  expected-dormant  & acted  > 0   (the tripwire)
        (iii) expected-absent   & CONSTRUCTED
        (iv)  constructed       & not in the registry
      infra / covered-existing / event-driven never enter the mismatch block.
      A crash day produces no census (the census heartbeat is deferred — B5).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "EffectCounter",
    "EffectCompositionError",
    "handle",
    "register_constructed",
    "set_mode",
    "assert_composition",
    "emit_census",
    "reset_for_tests",
]

# Registry file location (relative to the repo root / process CWD, matching
# how the rest of the system resolves config/ — see config_loader).
_DEFAULT_REGISTRY_PATH = Path("config") / "expected_managers.yaml"

# States whose entries carry a counter (B1).
_COUNTER_STATES = ("expected-active", "expected-dormant", "expected-event-driven")
# States that must be CONSTRUCTED (registered) at boot for the B2 assertion.
_CONSTRUCT_STATES = _COUNTER_STATES + ("infra", "covered-existing")


class EffectCompositionError(RuntimeError):
    """B2 startup assertion failure (dev/paper fail-fast path)."""


class EffectCounter:
    """One pre-allocated counter handle. inc() is the hot path (C4)."""

    __slots__ = ("name", "n")

    def __init__(self, name: str) -> None:
        self.name = name
        self.n = 0

    def inc(self) -> None:
        # Single integer increment on a resolved handle. Cannot raise:
        # `n` always exists (slot, initialised 0) and is only ever an int.
        self.n += 1

    def add(self, k: int) -> None:
        # Batch form for list-shaped effects (reconciler actions). Cold-ish
        # path (once per reconcile cycle); same guarantees as inc().
        self.n += k


_lock = threading.Lock()
_counters: Dict[str, EffectCounter] = {}
_constructed: set = set()
_is_paper: Optional[bool] = None
_registry_cache: Optional[List[dict]] = None


def handle(name: str) -> EffectCounter:
    """
    Resolve (or create) the single counter for `name` and mark it CONSTRUCTED.
    Called once per unit at construction time — cold path. Idempotent: the
    same object is returned for the life of the process.
    """
    with _lock:
        c = _counters.get(name)
        if c is None:
            c = EffectCounter(name)
            _counters[name] = c
        _constructed.add(name)
        return c


def register_constructed(name: str) -> None:
    """B2 registration for units that carry NO counter (infra/covered-existing)."""
    with _lock:
        _constructed.add(name)


def set_mode(is_paper: bool) -> None:
    """Record the process mode once at boot; assertion/census consequences differ."""
    global _is_paper
    _is_paper = bool(is_paper)


def _load_registry(registry_path: Optional[Path] = None) -> List[dict]:
    """Load config/expected_managers.yaml (cached). File order is preserved —
    it IS the census's deterministic order (C5)."""
    global _registry_cache
    if _registry_cache is not None and registry_path is None:
        return _registry_cache
    import yaml  # local import: this module must stay import-light for the hot path

    path = Path(registry_path) if registry_path is not None else _DEFAULT_REGISTRY_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = list(data.get("managers") or [])
    if registry_path is None:
        _registry_cache = entries
    return entries


def _mode_exempt(entry: dict, is_paper: bool) -> bool:
    """The one sanctioned mode-conditional: entries whose `modes` field marks a
    live-only ctor are exempt from the constructed-set assertion in paper."""
    modes = str(entry.get("modes") or "")
    return is_paper and modes.startswith("live-only")


def assert_composition(
    notifier: Any = None,
    logger: Any = None,
    registry_path: Optional[Path] = None,
) -> dict:
    """
    B2: constructed-set vs registry expectation, both directions.
      missing  — expected-constructed (per registry) but never registered
      ghosts   — expected-absent but registered (mismatch class iii)
      unknown  — registered but not in the registry at all (C2 enforcement)
    dev/paper: raise. live: CRITICAL alert + continue. Returns the report.
    """
    is_paper = bool(_is_paper) if _is_paper is not None else True
    entries = _load_registry(registry_path)
    with _lock:
        constructed = set(_constructed)

    names_in_registry = {str(e.get("name")) for e in entries}
    missing = [
        str(e.get("name"))
        for e in entries
        if str(e.get("state")) in _CONSTRUCT_STATES
        and str(e.get("name")) not in constructed
        and not _mode_exempt(e, is_paper)
    ]
    ghosts = [
        str(e.get("name"))
        for e in entries
        if str(e.get("state")) == "expected-absent" and str(e.get("name")) in constructed
    ]
    unknown = sorted(n for n in constructed if n not in names_in_registry)

    report = {"missing": missing, "ghosts": ghosts, "unknown": unknown,
              "ok": not (missing or ghosts or unknown)}

    if report["ok"]:
        if logger is not None:
            logger.info(
                "effect_telemetry: composition OK (%d registered, %d expected)",
                len(constructed),
                sum(1 for e in entries if str(e.get("state")) in _CONSTRUCT_STATES),
            )
        return report

    msg = (
        "effect_telemetry composition assertion FAILED: "
        f"missing(expected-but-unregistered)={missing or '[]'} "
        f"ghosts(expected-absent-but-constructed)={ghosts or '[]'} "
        f"unknown(registered-but-not-in-registry)={unknown or '[]'} "
        "— registry: config/expected_managers.yaml (C2: a new manager ctor "
        "requires a registry entry in the same diff)"
    )
    if is_paper:
        # dev/paper: FAIL FAST — this IS the missing composition-root assertion.
        raise EffectCompositionError(msg)
    # live: never crash the boot on a registration gap.
    if logger is not None:
        logger.critical(msg)
    if notifier is not None:
        try:
            notifier.send_critical(f"EFFECT-TELEMETRY: {msg}")
        except Exception:  # noqa: BLE001 — alerting must not break a live boot
            if logger is not None:
                logger.error("effect_telemetry: CRITICAL alert send failed", exc_info=True)
    return report


def emit_census(
    logger: Any,
    day: str,
    webhook_day_count: Optional[Callable[[], Any]] = None,
    registry_path: Optional[Path] = None,
) -> Optional[List[str]]:
    """
    B4: the EOD census. Emits one line per counter-bearing / covered-existing /
    expected-absent unit in REGISTRY FILE ORDER (C5), then the MISMATCH block.
    Wrapped so it can NEVER break shutdown. Returns the emitted lines (for
    determinism tests), or None on internal failure.
    """
    try:
        entries = _load_registry(registry_path)
        with _lock:
            constructed = set(_constructed)
            counts = {n: c.n for n, c in _counters.items()}

        is_paper = _is_paper
        mode = "paper" if is_paper else ("live" if is_paper is not None else "unknown")
        lines: List[str] = []
        mismatches: List[str] = []

        lines.append(f"effect_census | BEGIN day={day} mode={mode} entries={len(entries)}")
        for e in entries:
            name = str(e.get("name"))
            state = str(e.get("state"))
            if state == "infra":
                continue  # registered for B2 only; no census line
            if state == "expected-absent":
                if name in constructed:
                    lines.append(f"effect_census | {name}: CONSTRUCTED | expected-absent")
                    mismatches.append(
                        f"MISMATCH(iii) {name}: expected-absent but CONSTRUCTED"
                    )
                else:
                    lines.append(f"effect_census | {name}: NEVER-CONSTRUCTED [expected]")
                continue
            if state == "covered-existing":
                derived: Any = "?"
                if name == "webhook_receiver" and webhook_day_count is not None:
                    try:
                        derived = webhook_day_count()
                    except Exception:  # noqa: BLE001 — derive failure must not break emit
                        derived = "derive-failed"
                lines.append(
                    f"effect_census | {name}: acted {derived} | covered-existing"
                )
                continue  # never enters the mismatch block
            if state in _COUNTER_STATES:
                n = counts.get(name)
                shown = n if n is not None else 0
                tag = state.replace("expected-", "")
                extra = "" if name in constructed else " NOT-CONSTRUCTED"
                lines.append(f"effect_census | {name}: acted {shown} | {tag}{extra}")
                if state == "expected-active" and (n is None or n == 0):
                    mismatches.append(
                        f"MISMATCH(i) {name}: expected-active but acted 0"
                        + (" (not constructed)" if name not in constructed else "")
                    )
                elif state == "expected-dormant" and n is not None and n > 0:
                    reason = str(e.get("reason") or "").split("—")[0].strip()
                    mismatches.append(
                        f"MISMATCH(ii) {name}: expected-dormant but acted {n}"
                        + (f" [{reason}]" if reason else "")
                    )
                continue
            # Unknown state string in the registry: surface, don't guess.
            lines.append(f"effect_census | {name}: state '{state}' UNRECOGNISED")

        names_in_registry = {str(e.get("name")) for e in entries}
        for n in sorted(constructed - names_in_registry):
            mismatches.append(f"MISMATCH(iv) {n}: constructed but not in the registry")

        if mismatches:
            for m in mismatches:
                lines.append(f"effect_census | {m}")
        else:
            lines.append(
                "effect_census | MISMATCH: NONE — every zero is an expected zero"
            )
        lines.append(f"effect_census | END day={day} mismatches={len(mismatches)}")

        for ln in lines:
            logger.info(ln)
        return lines
    except Exception:  # noqa: BLE001 — the census must never break shutdown
        try:
            logger.error("effect_census: emit failed", exc_info=True)
        except Exception:  # noqa: BLE001
            pass
        return None


def reset_for_tests() -> None:
    """Test hook: clear all module state. Never called in production."""
    global _is_paper, _registry_cache
    with _lock:
        _counters.clear()
        _constructed.clear()
    _is_paper = None
    _registry_cache = None
