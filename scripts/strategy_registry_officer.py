#!/usr/bin/env python3
"""scripts/strategy_registry_officer.py — daily strategy-direction registry maintainer.

Mirrors the CRON OFFICER's auto-detect/notify pattern (scripts/cron_officer.py
``_auto_discover`` + ``_roster_integrity``) for STRATEGIES instead of cron jobs:

  * enumerate the ACTUAL strategy set  = loaded YAMLs (StrategyConfig) ∪ distinct
    signals.scanner / signals.strategy;
  * diff against the runtime state (data_store/strategy_direction_registry.yaml),
    falling back to the git-tracked SEED (config/strategy_direction_registry.yaml)
    only while no state file exists yet — the first run after a deploy;
  * register each UNSEEN strategy PENDING (direction = StrategyConfig.direction) and NOTIFY
    (Telegram + email) with evidence;
  * advance PENDING -> CONFIRMED once the strategy has its first FILLED trade (silent);
  * set health = DIRECTION_CONFLICT where a realized trade side != the declared direction —
    NEVER overwriting the declared value — and NOTIFY.

The daily job OWNS registry writes (no silent runtime mutation, exactly as the cron officer
never mutates cron_registry.yaml at runtime). A no-change run is SILENT. DATA/REGISTRY ONLY —
no trading decision changes, and direction is NEVER read from the registry to place a trade.

Cron: daily post-market. Flags: --dry-run · --config-dir · --db-path · --registry.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from core import db_connect
from core.logger import get_logger
from core.strategy_direction import (
    DEFAULT_SEED_PATH, DEFAULT_STATE_PATH, build_direction_map, load_registry, save_registry,
)
from core.time_authority import today_ist

_log = get_logger("strategy_registry_officer")

_DEFAULT_DB = _ROOT / "data_store" / "trading_system.db"


# ── DB reads (read-only; the officer only writes the registry YAML) ─────────────

def _distinct_signal_names(conn) -> Set[str]:
    """Scanner + strategy names that have ever appeared in signals (a new scanner that
    fired but has no YAML/registry entry is exactly what auto-detection must catch)."""
    names: Set[str] = set()
    for col in ("scanner", "strategy"):
        try:
            for (v,) in conn.execute(f"SELECT DISTINCT {col} FROM signals WHERE {col} IS NOT NULL"):
                if v:
                    names.add(str(v))
        except Exception:  # noqa: BLE001 — a missing column must never crash the officer
            continue
    return names


def _filled_strategies(conn) -> Set[str]:
    """Strategies with at least one FILLED trade (qty_filled>0) — the CONFIRM trigger."""
    out: Set[str] = set()
    try:
        for (s,) in conn.execute(
            "SELECT DISTINCT strategy FROM trades WHERE qty_filled>0 AND strategy IS NOT NULL"
        ):
            if s:
                out.add(str(s))
    except Exception:  # noqa: BLE001
        pass
    return out


def _realized_directions(conn) -> Dict[str, Set[str]]:
    """{strategy: {realized trades.direction, ...}} over FILLED trades — the CONFLICT input."""
    out: Dict[str, Set[str]] = {}
    try:
        for s, d in conn.execute(
            "SELECT strategy, direction FROM trades "
            "WHERE qty_filled>0 AND strategy IS NOT NULL AND direction IS NOT NULL"
        ):
            if s and d:
                out.setdefault(str(s), set()).add(str(d).upper())
    except Exception:  # noqa: BLE001
        pass
    return out


# ── Pure reconcile step (no I/O — unit-testable) ────────────────────────────────

def reconcile(
    registry: Dict[str, dict],
    direction_map: Dict[str, str],
    signal_names: Set[str],
    filled: Set[str],
    realized: Dict[str, Set[str]],
    today_iso: str,
) -> Tuple[Dict[str, dict], List[str], List[str], List[str]]:
    """Return (new_registry, newly_registered, newly_confirmed, newly_conflicted).

    * NEW: a strategy in the actual set (YAMLs ∪ signal names) with a resolvable direction
      and no registry row -> add PENDING/OK.
    * CONFIRM: a PENDING row whose strategy has a filled trade -> CONFIRMED (silent).
    * CONFLICT: a realized direction != the declared/registered direction -> health
      DIRECTION_CONFLICT (declared value KEPT). Recomputed each run; notify only on the
      OK -> DIRECTION_CONFLICT transition.
    """
    reg = {k: dict(v) for k, v in registry.items()}
    new_registered: List[str] = []
    new_confirmed: List[str] = []
    new_conflicted: List[str] = []

    # Actual set = every strategy YAML ∪ every scanner/strategy seen in signals.
    actual = set(direction_map) | set(signal_names)

    for name in sorted(actual):
        if name in reg:
            continue
        # Direction from the DECLARED field; fall back to a realized side only if a
        # signal-only strategy has actually traded (a scanner with no YAML). If neither
        # resolves, we cannot classify -> skip + log (S10 boot-check owns the config gap).
        direction = direction_map.get(name)
        if direction is None:
            rz = realized.get(name) or set()
            direction = next(iter(rz)) if len(rz) == 1 else None
        if direction is None:
            _log.warning("strategy_registry: %r seen but direction unresolvable "
                         "(no YAML, no single realized side) — not registered", name)
            continue
        reg[name] = {
            "direction": direction,
            "registration_status": "PENDING",
            "health": "OK",
            "first_seen": today_iso,
            "evidence": {
                "source_yaml": (f"config/strategies/{name}.yaml"
                                if name in direction_map else "signals (no YAML)"),
                "scanner": name,
            },
        }
        new_registered.append(name)

    # CONFIRM (silent) + CONFLICT (notify on transition) over the whole registry.
    for name, row in reg.items():
        if row.get("registration_status") == "PENDING" and name in filled:
            row["registration_status"] = "CONFIRMED"
            new_confirmed.append(name)

        declared = row.get("direction")
        rz = realized.get(name) or set()
        conflict = bool(declared) and any(d != declared for d in rz)
        prev_health = row.get("health", "OK")
        if conflict:
            row["health"] = "DIRECTION_CONFLICT"
            if prev_health != "DIRECTION_CONFLICT":
                new_conflicted.append(name)
        else:
            row["health"] = "OK"

    return reg, new_registered, new_confirmed, new_conflicted


# ── Notify (reuse the cron-officer Telegram + email path; no parallel notifier) ─

def _notify(new_registered: List[str], new_conflicted: List[str],
            registry: Dict[str, dict], config_dir: Path) -> None:
    """Telegram + email, mirroring the cron officer. Silent when nothing to report.
    Never raises (notification must not crash the job)."""
    if not new_registered and not new_conflicted:
        return
    lines: List[str] = ["Strategy-Direction Registry"]
    sev = "WARNING" if new_conflicted else "INFO"
    if new_registered:
        lines.append("")
        lines.append(f"NEW strategies registered (PENDING) — {len(new_registered)}:")
        for n in new_registered:
            row = registry.get(n, {})
            lines.append(f"  • {n}  direction={row.get('direction','?')}  "
                         f"src={row.get('evidence',{}).get('source_yaml','?')}")
    if new_conflicted:
        lines.append("")
        lines.append(f"🔴 DIRECTION_CONFLICT — {len(new_conflicted)} (declared value KEPT; investigate):")
        for n in new_conflicted:
            lines.append(f"  • {n}  declared={registry.get(n,{}).get('direction','?')} "
                         f"but a realized trade side differs")
    body = "\n".join(lines)
    title = f"[LFL836] Strategy registry — {len(new_registered)} new, {len(new_conflicted)} conflict"

    # Telegram (best-effort).
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=_log, config_dir=config_dir)
        if notifier is not None:
            notifier.send(severity=sev, title=title, body=body,
                          source_module="strategy_registry_officer")
    except Exception as exc:  # noqa: BLE001
        _log.error("strategy_registry.telegram_failed", extra={"error": str(exc)})

    # Email (via the critical-sentinel path the alert-watcher delivers), mirroring the
    # cron officer's report email. A new strategy / conflict is worth an inbox record.
    try:
        from alerts.critical import write_critical_sentinel
        write_critical_sentinel(
            title="Strategy-Direction Registry update", body=body,
            source_module="strategy_registry_officer",
            subject=title, content_type="text/plain", plain_fallback=body,
            sentinel_dir=_ROOT / "data_store",
        )
    except Exception as exc:  # noqa: BLE001
        _log.error("strategy_registry.email_failed", extra={"error": str(exc)})


# ── Orchestration ───────────────────────────────────────────────────────────────

def run(config_dir: Path, db_path: Path, registry_path: Path, today_iso: str, *,
        seed_path: Optional[Path] = None,
        dry_run: bool = False, notify_fn: Optional[Callable] = None) -> dict:
    """Detect/register/confirm/conflict for one run. Returns a summary (also used by tests).
    Writes the registry + notifies only when NOT dry_run.

    ``registry_path`` is the runtime STATE file (gitignored). ``seed_path`` is the
    git-tracked SEED, read ONLY when no state exists yet — the first run after a deploy."""
    direction_map = build_direction_map(config_dir)
    registry = load_registry(registry_path, seed_path=seed_path)

    signal_names: Set[str] = set()
    filled: Set[str] = set()
    realized: Dict[str, Set[str]] = {}
    if Path(db_path).exists():
        conn = db_connect.connect(db_path)
        try:
            signal_names = _distinct_signal_names(conn)
            filled = _filled_strategies(conn)
            realized = _realized_directions(conn)
        finally:
            conn.close()

    new_reg, registered, confirmed, conflicted = reconcile(
        registry, direction_map, signal_names, filled, realized, today_iso,
    )

    changed = (new_reg != registry)
    summary = {
        "registered": registered, "confirmed": confirmed, "conflicted": conflicted,
        "total": len(new_reg), "changed": changed,
    }

    if dry_run:
        print(f"[DRY-RUN] new={registered} confirmed={confirmed} conflict={conflicted} "
              f"total={len(new_reg)} changed={changed}")
        return summary

    if changed:
        save_registry(new_reg, registry_path)
    (notify_fn or _notify)(registered, conflicted, new_reg, config_dir)
    return summary


def _parse_args(argv=None):
    p = argparse.ArgumentParser(prog="strategy_registry_officer")
    p.add_argument("--config-dir", default="config")
    p.add_argument("--db-path", default=str(_DEFAULT_DB))
    p.add_argument("--registry", default=None,
                   help="runtime STATE YAML (default: data_store/strategy_direction_registry.yaml)")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    config_dir = Path(args.config_dir)
    # STATE goes to data_store/ (gitignored) so an ordinary run never dirties the
    # git-tracked config tree; the tracked SEED is read-only and fallback-only.
    registry_path = (Path(args.registry) if args.registry
                     else _ROOT / "data_store" / Path(DEFAULT_STATE_PATH).name)
    seed_path = config_dir / Path(DEFAULT_SEED_PATH).name
    summary = run(config_dir, Path(args.db_path), registry_path, today_ist(),
                  seed_path=seed_path, dry_run=args.dry_run)
    _log.info("strategy_registry_officer done", extra=summary)
    return 0


def _cron_main(argv=None) -> int:
    """Cron entry: holiday-skip + heartbeat + per-job alert (mirrors the other cron jobs)."""
    from utils.cron_heartbeat import HeartbeatTimer, skip_if_non_trading_day
    if skip_if_non_trading_day("strategy_registry_officer"):
        return 0
    timer = HeartbeatTimer("strategy_registry_officer", alert=True)
    with timer:
        rc = main(argv)
        if rc != 0:
            timer.status = "FAILED"
            timer.message = f"exit code {rc}"
    return rc


if __name__ == "__main__":
    sys.exit(_cron_main())
