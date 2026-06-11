#!/bin/bash
# CT156-CT158: Forensics suite — run after 16:30 IST
# CT156: Forensic reconstruction of all today's trades
# CT157: Report integrity cross-check
# CT158: Final state machine validation

set -e
cd ~/systems/trading-system
VENV=~/systems/venv/bin/python3

echo "============================================="
echo "CT156-CT158 FORENSICS SUITE"
echo "Time: $(date '+%H:%M:%S IST')"
echo "============================================="

echo ""
echo "--- CT156: Forensic Reconstruction ---"
$VENV tests/crash_test/forensic_reconstructor.py --all-trades --source db --date today 2>&1 | tail -30
echo ""

echo "--- CT157: State Machine Validation ---"
$VENV tests/crash_test/state_machine_validator.py --all 2>&1 | tail -30
echo ""

echo "--- CT157b: Exactly-Once Verification ---"
$VENV tests/crash_test/exactly_once_verifier.py --date today 2>&1 | tail -20
echo ""

echo "--- CT158: Invariant Check ---"
$VENV tests/crash_test/invariant_checker.py --full 2>&1 | tail -30
echo ""

echo "============================================="
echo "FORENSICS COMPLETE — $(date '+%H:%M:%S IST')"
echo "============================================="
