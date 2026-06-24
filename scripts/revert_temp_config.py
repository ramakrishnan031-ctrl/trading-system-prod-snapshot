"""
scripts/revert_temp_config.py -- Trading System v2  FIX-151

Purpose:
    Scan config YAML files for TEMP markers. Print proposed reverts
    in dry-run mode (default). Apply reverts with --apply --confirm.

Usage:
    python scripts/revert_temp_config.py            # dry-run, print diff
    python scripts/revert_temp_config.py --apply --confirm  # apply reverts

Exit codes:
    0 -- no TEMP values found (clean) or dry-run complete
    1 -- error
    2 -- TEMP values found (dry-run report printed)
    3 -- --apply without --confirm (safety check)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger

CONFIG_DIR = _ROOT / "config"

TEMP_REVERTS = [
    # BUILD 1 (#1, 24-Jun): the `capital.daily_loss_limit` revert entry was
    # removed — that key is deleted; daily_loss_limit_pct (below) is the sole
    # daily-loss authority now.
    {
        "file": "system_config.yaml",
        "key": "risk.max_consecutive_losses",
        "pattern": r"max_consecutive_losses:\s*\d+",
        "temp_value": "20",
        "prod_value": "4",
        "description": "Consecutive losses before kill switch",
        "risk": "System does not halt after repeated losses",
    },
    {
        "file": "system_config.yaml",
        "key": "risk.daily_loss_limit_pct",
        "pattern": r"daily_loss_limit_pct:\s*[\d.]+",
        "temp_value": "1.00",
        "prod_value": "0.05",
        "description": "Daily loss limit as % of capital (5%)",
        "risk": "System allows 100% capital loss in one day",
    },
    {
        "file": "system_config.yaml",
        "key": "order_reconciler.capital_drift_tolerance",
        "pattern": r"capital_drift_tolerance:\s*[\d.]+",
        "temp_value": "100000.0",
        "prod_value": "50.0",
        "description": "Capital drift tolerance (Rs 50)",
        "risk": "Capital accounting drift not detected until massive mismatch",
    },
    {
        "file": "system_config.yaml",
        "key": "drift_handler.log_only_threshold_rs",
        "pattern": r"log_only_threshold_rs:\s*[\d.]+",
        "temp_value": "100000.0",
        "prod_value": "250.0",
        "description": "Drift log threshold (Rs 250)",
        "risk": "Capital drift warnings suppressed",
    },
    {
        "file": "system_config.yaml",
        "key": "drift_handler.soft_kill_threshold_rs",
        "pattern": r"soft_kill_threshold_rs:\s*[\d.]+",
        "temp_value": "200000.0",
        "prod_value": "1000.0",
        "description": "Drift soft kill threshold (Rs 1000)",
        "risk": "Capital drift does not trigger soft kill",
    },
    {
        "file": "system_config.yaml",
        "key": "drift_handler.hard_kill_threshold_rs",
        "pattern": r"hard_kill_threshold_rs:\s*[\d.]+",
        "temp_value": "500000.0",
        "prod_value": "2500.0",
        "description": "Drift hard kill threshold (Rs 2500)",
        "risk": "Capital drift does not trigger hard kill",
    },
    {
        "file": "strategies/gap_fade_long.yaml",
        "key": "min_score",
        "pattern": r"min_score:\s*\d+",
        "temp_value": "30",
        "prod_value": "60",
        "description": "Minimum quality score for gap_fade_long",
        "risk": "Low-quality signals accepted, reducing win rate",
    },
]


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="revert_temp_config",
        description="FIX-151: Scan and revert TEMP config values.",
    )
    parser.add_argument("--apply", action="store_true",
                        help="Apply reverts (requires --confirm)")
    parser.add_argument("--confirm", action="store_true",
                        help="Confirm apply (safety check)")
    parser.add_argument("--config-dir", default=str(CONFIG_DIR))
    return parser.parse_args(argv)


def scan_temp_markers(config_dir: Path, log) -> list[dict]:
    found = []
    for entry in TEMP_REVERTS:
        fpath = config_dir / entry["file"]
        if not fpath.exists():
            log.warning("revert_temp: file not found: %s", fpath)
            continue

        content = fpath.read_text(encoding="utf-8")
        has_temp = "TEMP" in content and re.search(entry["pattern"], content)

        if has_temp:
            match = re.search(entry["pattern"], content)
            current_line = match.group(0) if match else "unknown"
            found.append({
                **entry,
                "current_line": current_line,
                "file_path": str(fpath),
                "status": "ACTIVE_TEMP",
            })
        else:
            found.append({
                **entry,
                "current_line": "",
                "file_path": str(fpath),
                "status": "ALREADY_REVERTED",
            })

    return found


def apply_reverts(config_dir: Path, reverts: list[dict], log) -> int:
    applied = 0
    for entry in reverts:
        if entry["status"] != "ACTIVE_TEMP":
            continue

        fpath = config_dir / entry["file"]
        content = fpath.read_text(encoding="utf-8")

        old_pattern = entry["pattern"]
        key_name = old_pattern.split(r":\s*")[0]
        new_line = f"{key_name}: {entry['prod_value']}"

        new_content = re.sub(
            entry["pattern"] + r".*?(#.*)?$",
            f"{new_line}  # REVERTED from TEMP {entry['temp_value']}",
            content,
            count=1,
            flags=re.MULTILINE,
        )

        if new_content != content:
            fpath.write_text(new_content, encoding="utf-8")
            log.info("revert_temp: APPLIED %s -> %s in %s",
                     entry["key"], entry["prod_value"], entry["file"])
            applied += 1

    return applied


def print_report(results: list[dict]) -> None:
    active = [r for r in results if r["status"] == "ACTIVE_TEMP"]
    reverted = [r for r in results if r["status"] == "ALREADY_REVERTED"]

    print(f"\n{'='*70}")
    print(f"  TEMP Config Values Report")
    print(f"  Active: {len(active)}  |  Already Reverted: {len(reverted)}")
    print(f"{'='*70}\n")

    if active:
        print("ACTIVE TEMP VALUES (need revert before production):\n")
        for r in active:
            print(f"  [{r['file']}] {r['key']}")
            print(f"    Current:    {r['temp_value']}")
            print(f"    Production: {r['prod_value']}")
            print(f"    Risk:       {r['risk']}")
            print()

    if reverted:
        print("ALREADY REVERTED:\n")
        for r in reverted:
            print(f"  [{r['file']}] {r['key']} -> {r['prod_value']}")
        print()


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("revert_temp_config")
    config_dir = Path(args.config_dir)

    try:
        results = scan_temp_markers(config_dir, log)
        active = [r for r in results if r["status"] == "ACTIVE_TEMP"]

        print_report(results)

        if not active:
            print("All TEMP values have been reverted. Config is production-ready.")
            return 0

        if not args.apply:
            print(f"Found {len(active)} active TEMP values.")
            print("Run with --apply --confirm to revert them.")
            return 2

        if args.apply and not args.confirm:
            print("ERROR: --apply requires --confirm for safety.")
            return 3

        applied = apply_reverts(config_dir, results, log)
        print(f"\nApplied {applied} reverts. Re-scan to verify:")
        print(f"  python scripts/revert_temp_config.py\n")
        return 0

    except Exception as exc:
        log.error("revert_temp_config failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
