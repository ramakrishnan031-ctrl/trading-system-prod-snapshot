"""
tests/crash_test/scenario_runner.py — Tool 5: Orchestrates individual test scenarios.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import (
    ist_now_iso, format_result, load_master_tracker, update_master_tracker,
    SCENARIOS_DIR, RESULTS_DIR, SNAPSHOTS_DIR, BASE_DIR,
)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

TOOL_DIR = Path(__file__).resolve().parent

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

CLASSIFICATION_COLORS = {
    "PASS": GREEN,
    "PASS_WITH_RISK": YELLOW,
    "FAIL": RED,
    "KNOWN_LIMITATION": YELLOW,
    "DESIGN_GAP": YELLOW,
    "CANNOT_TEST": CYAN,
    "NOT_APPLICABLE": CYAN,
}


def _load_scenario(scenario_id: str) -> Optional[Dict]:
    """Load scenario YAML by ID."""
    if yaml is None:
        print("ERROR: PyYAML not installed. pip install pyyaml")
        return None
    path = SCENARIOS_DIR / f"{scenario_id.lower()}.yaml"
    if not path.exists():
        # Try uppercase
        path = SCENARIOS_DIR / f"{scenario_id.upper()}.yaml"
    if not path.exists():
        print(f"ERROR: Scenario file not found: {path}")
        return None
    with open(path, "r") as f:
        return yaml.safe_load(f)


def _run_tool(tool_name: str, args: List[str], cwd: Optional[str] = None) -> tuple:
    """Run a crash test tool and return (returncode, stdout, stderr)."""
    cmd = [sys.executable, str(TOOL_DIR / tool_name)] + args
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or str(BASE_DIR))
    return result.returncode, result.stdout, result.stderr


def _run_shell(command: str) -> tuple:
    """Run a shell command and return (returncode, stdout, stderr).

    Kill commands may return non-zero when the target process dies before the
    signal is delivered; append '|| true' to prevent false ERROR classification.
    """
    if ('kill -9' in command or 'kill -SIGKILL' in command) and '|| true' not in command:
        command = command + ' || true'
    result = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=str(BASE_DIR))
    return result.returncode, result.stdout, result.stderr


def _execute_step(step: Dict, scenario_id: str, pause: bool = False, dry_run: bool = False) -> Dict:
    """Execute a single scenario step."""
    action = step.get("action", "")
    params = step.get("params", {})
    step_num = step.get("step", "?")
    result = {"step": step_num, "action": action, "status": "OK", "output": ""}

    if dry_run:
        result["status"] = "DRY_RUN"
        result["output"] = f"Would execute: {action} with {params}"
        print(f"  [DRY-RUN] Step {step_num}: {action} {params}")
        return result

    if pause and sys.stdin.isatty():
        input(f"  [PAUSE] Step {step_num}: {action} — Press Enter to continue...")

    print(f"  Step {step_num}: {action}...", end=" ", flush=True)

    try:
        if action == "snapshot_before" or action == "snapshot":
            label = params.get("label", f"before_{scenario_id}")
            rc, out, err = _run_tool("state_inspector.py", ["--snapshot", label])
            result["output"] = out.strip()

        elif action == "snapshot_after":
            label = params.get("label", f"after_{scenario_id}")
            rc, out, err = _run_tool("state_inspector.py", ["--snapshot", label])
            result["output"] = out.strip()

        elif action == "inject_signal":
            args = ["--mode", params.get("mode", "single")]
            if "symbol" in params:
                args += ["--symbol", params["symbol"]]
            if "scanner" in params:
                args += ["--scanner", params["scanner"]]
            if "price" in params and params["price"] != "auto":
                args += ["--price", str(params["price"])]
            if "count" in params:
                args += ["--count", str(params["count"])]
            if "delay_ms" in params:
                args += ["--delay-ms", str(params["delay_ms"])]
            rc, out, err = _run_tool("signal_injector.py", args)
            result["output"] = out.strip()
            if rc != 0:
                result["status"] = "ERROR"
                result["error"] = err.strip()

        elif action == "wait":
            seconds = params.get("seconds", 5)
            time.sleep(seconds)
            result["output"] = f"Waited {seconds}s"

        elif action == "run_command":
            cmd = params.get("command", "")
            expect_fail = params.get("expect_fail", False)
            allow_nonzero = params.get("allow_nonzero", False)
            rc, out, err = _run_shell(cmd)
            result["output"] = out.strip()
            if rc != 0 and not expect_fail:
                if allow_nonzero or "kill -" in cmd:
                    result["status"] = "OK"
                    result["output"] = f"(rc={rc}, allowed) {out.strip()}"
                else:
                    result["status"] = "ERROR"
                    result["error"] = err.strip()

        elif action == "assert_db":
            from tests.crash_test.ct_utils import get_db_connection
            conn = get_db_connection(readonly=True)
            query = params.get("query", "")
            expected = params.get("expected")
            rows = conn.execute(query).fetchall()
            conn.close()
            actual = [dict(r) for r in rows]
            result["output"] = json.dumps(actual, default=str)
            if expected is not None:
                if len(actual) != expected.get("count", len(actual)):
                    result["status"] = "ASSERTION_FAILED"
                    result["error"] = f"Expected count={expected.get('count')}, got {len(actual)}"

        elif action == "assert_http":
            from tests.crash_test.ct_utils import http_get
            url = params.get("url", "http://localhost:5000/health")
            resp = http_get(url)
            result["output"] = json.dumps(resp, default=str) if resp else "No response"
            expected_status = params.get("expected_status")
            if expected_status and resp is None:
                result["status"] = "ASSERTION_FAILED"

        elif action == "network_block":
            preset = params.get("preset", "block_zerodha_rest")
            duration = params.get("duration", 30)
            rc, out, err = _run_tool("network_controller.py",
                                      ["--preset", preset, "--duration", str(duration)])
            result["output"] = out.strip()

        elif action == "network_unblock":
            rc, out, err = _run_tool("network_controller.py", ["--unblock-all"])
            result["output"] = out.strip()

        elif action == "kill_process":
            sig = params.get("signal", "SIGINT")
            rc, out, err = _run_shell(f"pkill -{sig.replace('SIG','')} -f main.py")
            result["output"] = f"Sent {sig}"

        elif action == "restart_service":
            rc, out, err = _run_shell("sudo systemctl restart trading-system")
            result["output"] = out.strip()
            time.sleep(params.get("wait_after", 15))

        elif action == "manual_step":
            instruction = params.get("instruction", "Manual step required")
            print(f"\n  >>> MANUAL: {instruction}")
            if sys.stdin.isatty():
                input("  Press Enter when done...")
            result["output"] = "Manual step completed"

        elif action == "cleanup":
            mode = params.get("mode", "--soft")
            force = ["--force"] if params.get("force") else []
            rc, out, err = _run_tool("cleanup.py", [mode] + force)
            result["output"] = out.strip()

        else:
            result["status"] = "UNKNOWN_ACTION"
            result["output"] = f"Unknown action: {action}"

        if result["status"] == "OK":
            print(f"{GREEN}OK{RESET}")
        else:
            print(f"{RED}{result['status']}{RESET}")

    except Exception as e:
        result["status"] = "EXCEPTION"
        result["error"] = str(e)
        print(f"{RED}EXCEPTION: {e}{RESET}")

    return result


def run_scenario(scenario_id: str, dry_run: bool = False, pause: bool = False) -> Dict:
    """Run a complete scenario."""
    scenario = _load_scenario(scenario_id)
    if scenario is None:
        return format_result(scenario_id, "CANNOT_TEST",
                             {"error": f"Scenario YAML not found: {scenario_id}"})

    title = scenario.get("title", "Unknown")
    priority = scenario.get("priority", "P2")
    category = scenario.get("category", "")
    objective = scenario.get("objective", "")

    # Print header
    print(f"\n{'='*70}")
    print(f"{BOLD}{CYAN}  Scenario: {scenario_id} — {title}{RESET}")
    print(f"  Priority: {priority} | Category: {category}")
    print(f"  Objective: {objective}")
    print(f"{'='*70}")

    if dry_run:
        print(f"\n  {YELLOW}[DRY-RUN MODE]{RESET}")
        steps = scenario.get("test_steps", [])
        for step in steps:
            _execute_step(step, scenario_id, dry_run=True)
        assertions = scenario.get("assertions", [])
        print(f"\n  Assertions ({len(assertions)}):")
        for a in assertions:
            print(f"    - {a}")
        cleanup = scenario.get("post_cleanup", [])
        print(f"\n  Post-cleanup ({len(cleanup)}):")
        for c in cleanup:
            print(f"    - {c}")
        return format_result(scenario_id, "DRY_RUN", {"steps": len(steps)}, title=title)

    # Check if already passed (skip logic)
    tracker = load_master_tracker()
    prev = tracker.get("scenarios", {}).get(scenario_id)
    if prev and prev.get("classification") == "PASS":
        print(f"  {GREEN}ALREADY PASSED — skipping (use --force to re-run){RESET}")
        return format_result(scenario_id, "PASS",
                             {"skipped": True, "previous": prev}, title=title)

    # Step 1: Precondition checks
    print(f"\n  Checking preconditions...")
    rc, out, err = _run_tool("invariant_checker.py", ["--quick"])
    if rc != 0:
        print(f"  {YELLOW}WARNING: Quick invariant check failed (may be expected){RESET}")

    # Step 2: Snapshot BEFORE
    before_label = f"before_{scenario_id}"
    _run_tool("state_inspector.py", ["--snapshot", before_label])

    # Step 3: Execute test steps
    step_results = []
    steps = scenario.get("test_steps", [])
    for step in steps:
        sr = _execute_step(step, scenario_id, pause=pause)
        step_results.append(sr)
        if sr["status"] == "EXCEPTION":
            break

    # Step 4: Snapshot AFTER
    after_label = f"after_{scenario_id}"
    _run_tool("state_inspector.py", ["--snapshot", after_label])

    # Step 5: Run full invariant check
    print(f"\n  Running invariant checker (full)...")
    rc, inv_out, inv_err = _run_tool("invariant_checker.py", ["--full"])
    try:
        inv_result = json.loads(inv_out)
    except (json.JSONDecodeError, TypeError):
        inv_result = {"overall": "ERROR", "error": inv_out or inv_err}

    # Step 6: Classify result
    has_errors = any(s["status"] in ("ERROR", "EXCEPTION", "ASSERTION_FAILED")
                     for s in step_results)
    inv_failed = inv_result.get("overall") != "PASS"

    if has_errors:
        classification = "FAIL"
    elif inv_failed:
        classification = "PASS_WITH_RISK"
    else:
        classification = "PASS"

    color = CLASSIFICATION_COLORS.get(classification, RESET)
    print(f"\n  {'='*50}")
    print(f"  Result: {color}{BOLD}{classification}{RESET}")
    print(f"  Steps: {'FAIL' if has_errors else 'PASS'}")
    print(f"  Invariants: {'FAIL' if inv_failed else 'PASS'}")
    print(f"  {'='*50}")

    # Build result
    result = format_result(
        scenario_id, classification,
        {"step_results": step_results},
        title=title,
        invariant_results=inv_result,
    )

    # Save result
    result_path = RESULTS_DIR / f"{scenario_id}.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"  Result saved: {result_path}")

    # Update master tracker
    update_master_tracker(scenario_id, result)

    # Run cleanup if specified
    cleanup_steps = scenario.get("post_cleanup", [])
    if cleanup_steps:
        print(f"\n  Running cleanup...")
        for c in cleanup_steps:
            if isinstance(c, str) and "cleanup.py" in c:
                mode = "--soft" if "soft" in c else "--hard"
                _run_tool("cleanup.py", [mode])

    # P0 FAIL: stop banner
    if classification == "FAIL" and priority == "P0":
        print(f"\n{RED}{'!'*70}")
        print(f"  P0 FAILURE — STOP! Fix before continuing.")
        print(f"  Scenario: {scenario_id} — {title}")
        print(f"{'!'*70}{RESET}")
        if sys.stdin.isatty():
            input("  Press Enter to continue...")

    return result


def main():
    parser = argparse.ArgumentParser(description="Scenario Runner — Crash Test Tool 5")
    parser.add_argument("--scenario", type=str, help="Run a single scenario by ID")
    parser.add_argument("--day", type=int, help="Run all scenarios for a day")
    parser.add_argument("--category", type=str, help="Run all scenarios in category")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    parser.add_argument("--pause", action="store_true", help="Pause between steps")
    parser.add_argument("--force", action="store_true", help="Re-run even if already passed")
    args = parser.parse_args()

    if args.scenario:
        run_scenario(args.scenario, dry_run=args.dry_run, pause=args.pause)

    elif args.day:
        # Find all scenarios for this day
        if yaml is None:
            print("ERROR: PyYAML required for --day mode")
            sys.exit(1)
        scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
        day_scenarios = []
        for sf in scenario_files:
            with open(sf) as f:
                s = yaml.safe_load(f)
            if s and s.get("day") == args.day:
                day_scenarios.append(s["scenario_id"])
        if not day_scenarios:
            print(f"No scenarios found for day {args.day}")
            sys.exit(0)
        print(f"Day {args.day}: {len(day_scenarios)} scenarios")
        for sid in day_scenarios:
            run_scenario(sid, dry_run=args.dry_run, pause=args.pause)

    elif args.category:
        if yaml is None:
            print("ERROR: PyYAML required for --category mode")
            sys.exit(1)
        scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
        cat_scenarios = []
        for sf in scenario_files:
            with open(sf) as f:
                s = yaml.safe_load(f)
            if s and s.get("category") == args.category:
                cat_scenarios.append(s["scenario_id"])
        if not cat_scenarios:
            print(f"No scenarios found for category {args.category}")
            sys.exit(0)
        print(f"Category {args.category}: {len(cat_scenarios)} scenarios")
        for sid in cat_scenarios:
            run_scenario(sid, dry_run=args.dry_run, pause=args.pause)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
