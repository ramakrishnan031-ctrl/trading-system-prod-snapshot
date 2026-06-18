#!/bin/bash
# deploy/resume.sh -- Trading System v2  FIX-188b
#
# Systemd-friendly kill-switch resume. Stops the service, clears the kill switch
# in the DB (no instance-lock conflict), then restarts it UNDER systemd. Use this
# instead of a standalone `main.py --resume`, which competes with
# trading-system.service for the instance lock (port 5001) -- the 18-Jun collision.
#
# Usage (on the VM):
#   sudo bash deploy/resume.sh            # clears SOFT_KILL, restarts service
#   sudo bash deploy/resume.sh --force    # also clears HARD_KILL (emergency)
#
# If the kill switch is an EMERGENCY kill (e.g. API/IP 403), fix the root cause
# FIRST (e.g. Kite dev-console IP allowlist) -- otherwise it re-trips.
set -u

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/systems/trading-system}"
VENV_PY="${VENV_PY:-/home/ubuntu/systems/venv/bin/python}"
SVC="trading-system.service"
FORCE="${1:-}"

cd "$PROJECT_DIR" || { echo "cannot cd $PROJECT_DIR"; exit 1; }

echo "1) stopping $SVC (release the instance lock; halt any restart loop)"
sudo systemctl stop "$SVC" 2>&1 || true
sudo systemctl reset-failed "$SVC" 2>&1 || true

echo "2) clearing kill switch in the DB (no instance lock acquired)"
set -a; . ./.env 2>/dev/null || true; set +a
PYTHONPATH=. "$VENV_PY" scripts/clear_kill_switch.py $FORCE
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "   clear refused/failed (rc=$rc) -- NOT starting the service. Resolve, then re-run."
    exit "$rc"
fi

echo "3) starting $SVC under systemd"
sudo systemctl start "$SVC" 2>&1
sleep 3
echo "   state: $(systemctl is-active "$SVC")  (NRestarts=$(systemctl show "$SVC" -p NRestarts --value))"
echo "Done. token-watcher manages subsequent daily starts."
