#!/bin/bash
# deploy/token_watcher.sh -- Trading System v2 headless starter
#
# Poll loop: every N seconds, if a fresh Zerodha token exists for today
# (IST) AND trading-system.service is not active, start the service.
# Idempotent -- safe to run continuously. Stale tokens (yesterday's
# date) are ignored, so the system never starts with an expired token.
#
# Logs every significant event to $PROJECT_DIR/logs/token_watcher.log.
# Run under systemd as root (it calls systemctl start).

set -u

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/systems/trading-system}"
TOKEN_FILE="${PROJECT_DIR}/data_store/session/zerodha_token.json"
LOG_FILE="${PROJECT_DIR}/logs/token_watcher.log"
SLEEP_SEC="${SLEEP_SEC:-30}"
SERVICE="trading-system.service"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    printf '[%s] %s\n' \
        "$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S IST')" \
        "$1" >> "$LOG_FILE"
}

token_is_fresh() {
    [ -f "$TOKEN_FILE" ] || return 1
    local today
    today="$(TZ=Asia/Kolkata date '+%Y-%m-%d')"
    python3 - "$TOKEN_FILE" "$today" <<'PY' 2>/dev/null
import json, sys
path, today = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        tok = json.load(f)
except Exception:
    sys.exit(1)
if tok.get("date") != today:
    sys.exit(1)
if not (tok.get("access_token") or "").strip():
    sys.exit(1)
sys.exit(0)
PY
}

service_is_active() {
    systemctl is-active --quiet "$SERVICE"
}

log "token_watcher started (PROJECT_DIR=$PROJECT_DIR, poll=${SLEEP_SEC}s)"

while true; do
    # FIX-040: Skip poll if .tmp file exists (copy in progress)
    if [ -f "${TOKEN_FILE}.tmp" ]; then
        printf '[%s] DEBUG: %s.tmp exists, skipping poll (copy in progress)\n' \
            "$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S IST')" \
            "$TOKEN_FILE" >> "$LOG_FILE"
        sleep "$SLEEP_SEC"
        continue
    fi

    if service_is_active; then
        : # already running -- nothing to do
    elif token_is_fresh; then
        log "Fresh token detected. Starting $SERVICE."
        if systemctl start "$SERVICE"; then
            log "$SERVICE start command issued."
        else
            rc=$?
            log "ERROR: systemctl start $SERVICE failed (rc=$rc)"
        fi
    fi
    sleep "$SLEEP_SEC"
done
