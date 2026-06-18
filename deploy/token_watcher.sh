#!/bin/bash
# deploy/token_watcher.sh -- Trading System v2 headless starter  (FIX-188)
#
# Poll loop: every N seconds decide whether to (re)start trading-system.service
# based on its current state + LAST EXIT CODE (systemd ExecMainStatus) and the
# exit DATE (ExecMainExitTimestamp). Exit-code contract (see the unit file):
#   0      = clean EOD / holiday -> restart ONLY if the clean exit was a PRIOR day
#            (today's EOD is done; don't hammer post-EOD).
#   4      = HALT (kill switch active). If it HALTed TODAY -> do NOT restart
#            (manual --resume needed); alert once/day. If a PRIOR day -> one clean
#            start attempt (main.py clear_stale_state auto-clears prior-day kills).
#   3      = startup-check failure -> same prior-day/today treatment as exit 4.
#   1/2    = crash -> restart with backoff (max 3/hour), so mid-day crashes recover.
# All start attempts also require a fresh token for today.
# Idempotent + best-effort; never exits the loop. Runs under systemd as root.

set -u

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/systems/trading-system}"
TOKEN_FILE="${PROJECT_DIR}/data_store/session/zerodha_token.json"
LOG_FILE="${PROJECT_DIR}/logs/token_watcher.log"
SLEEP_SEC="${SLEEP_SEC:-30}"
LONG_SLEEP="${LONG_SLEEP:-300}"          # back-off sleep for HALT/startup-fail/backoff
SERVICE="trading-system.service"
STATE_DIR="${STATE_DIR:-/tmp/ts_watcher_state}"
CRASH_FILE="${STATE_DIR}/crash_restarts"
MAX_CRASH_PER_HOUR="${MAX_CRASH_PER_HOUR:-3}"
VENV_PY="${VENV_PY:-/home/ubuntu/systems/venv/bin/python}"

mkdir -p "$(dirname "$LOG_FILE")" "$STATE_DIR"

log() {
    printf '[%s] %s\n' \
        "$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S IST')" \
        "$1" >> "$LOG_FILE"
}

today_ist() { TZ=Asia/Kolkata date '+%Y-%m-%d'; }

token_is_fresh() {
    [ -f "$TOKEN_FILE" ] || return 1
    local today
    today="$(today_ist)"
    timeout 5 "$VENV_PY" - "$TOKEN_FILE" "$today" <<'PY' 2>/dev/null
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

# Best-effort Telegram (WARNING tier = no sentinel/email; just a ping). Never fails the loop.
send_telegram() {
    local msg="$1"
    (
        cd "$PROJECT_DIR" 2>/dev/null || exit 0
        set -a; . ./.env 2>/dev/null || true; set +a
        PYTHONPATH=. "$VENV_PY" - "$msg" <<'PY' 2>>"$LOG_FILE" || true
import sys
try:
    from alerts.telegram_notifier import TelegramNotifier
    n = TelegramNotifier.from_env()
    if n:
        n.send_alert("[token-watcher] " + sys.argv[1], level="WARNING")
except Exception as e:
    sys.stderr.write("token_watcher telegram failed: %s\n" % e)
PY
    )
}

# Send a given alert key at most once per IST day (date-stamped flag).
alert_once_per_day() {
    local key="$1" msg="$2"
    local flag="${STATE_DIR}/alert_$(today_ist)_${key}.flag"
    if [ ! -f "$flag" ]; then
        : > "$flag"
        send_telegram "$msg"
    fi
}

# Resume detected (service running again): allow future HALT/fail to re-alert.
clear_alert_flags() { rm -f "${STATE_DIR}"/alert_*.flag 2>/dev/null || true; }

crash_count_last_hour() {
    [ -f "$CRASH_FILE" ] || { echo 0; return; }
    local now cutoff
    now="$(date +%s)"; cutoff=$((now - 3600))
    awk -v c="$cutoff" '$1 >= c' "$CRASH_FILE" > "${CRASH_FILE}.tmp" 2>/dev/null && mv "${CRASH_FILE}.tmp" "$CRASH_FILE"
    wc -l < "$CRASH_FILE" 2>/dev/null | tr -d ' '
}
record_crash_restart() { date +%s >> "$CRASH_FILE"; }

start_service() {
    log "$1 Starting $SERVICE."
    if systemctl start "$SERVICE"; then
        log "$SERVICE start command issued."
    else
        log "ERROR: systemctl start $SERVICE failed (rc=$?)"
    fi
}

log "token_watcher started (poll=${SLEEP_SEC}s, backoff=${LONG_SLEEP}s, max_crash=${MAX_CRASH_PER_HOUR}/hr)"

while true; do
    this_sleep="$SLEEP_SEC"

    # FIX-040: skip while a token copy is in progress.
    if [ -f "${TOKEN_FILE}.tmp" ]; then
        sleep "$this_sleep"; continue
    fi

    active_state="$(systemctl show "$SERVICE" -p ActiveState --value 2>/dev/null)"
    exit_status="$(systemctl show "$SERVICE" -p ExecMainStatus --value 2>/dev/null)"
    exit_ts="$(systemctl show "$SERVICE" -p ExecMainExitTimestamp --value 2>/dev/null)"
    exit_date="$(printf '%s' "$exit_ts" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -n1)"
    today="$(today_ist)"
    exited_today=false; [ "$exit_date" = "$today" ] && exited_today=true

    if [ "$active_state" = "active" ] || [ "$active_state" = "activating" ]; then
        clear_alert_flags
        # running -- nothing to do

    elif [ "$active_state" = "failed" ]; then
        case "$exit_status" in
            4)
                if [ "$exited_today" = true ]; then
                    log "HALT detected (exit 4) today -- kill switch active; manual --resume required. NOT restarting."
                    alert_once_per_day halt "trading-system in HALT (exit 4 / kill switch active) today. Fix root cause, then: main.py --resume"
                    this_sleep="$LONG_SLEEP"
                elif token_is_fresh; then
                    start_service "Prior-day HALT (exit 4); attempting clean start (clear_stale_state auto-clears prior-day kills)."
                else
                    this_sleep="$LONG_SLEEP"
                fi
                ;;
            3)
                if [ "$exited_today" = true ]; then
                    log "Startup-check failure (exit 3) today -- config/env issue. NOT restarting."
                    alert_once_per_day startup "trading-system startup checks failed (exit 3) today. Manual fix needed."
                    this_sleep="$LONG_SLEEP"
                elif token_is_fresh; then
                    start_service "Prior-day startup-fail (exit 3); retrying once."
                else
                    this_sleep="$LONG_SLEEP"
                fi
                ;;
            1|2|*)
                n="$(crash_count_last_hour)"; n="${n:-0}"
                if token_is_fresh && [ "$n" -lt "$MAX_CRASH_PER_HOUR" ]; then
                    record_crash_restart
                    start_service "Crash (exit $exit_status) recovery [$((n + 1))/${MAX_CRASH_PER_HOUR} this hour]."
                else
                    log "Crash (exit $exit_status): backoff limit (${MAX_CRASH_PER_HOUR}/hr) reached or no fresh token. Skipping."
                    alert_once_per_day crash "trading-system crashing (exit $exit_status); restart backoff limit reached. Manual check needed."
                    this_sleep="$LONG_SLEEP"
                fi
                ;;
        esac

    else
        # inactive / dead: clean exit 0 (EOD/holiday) or never started this boot.
        if [ "$exit_status" = "0" ] && [ "$exited_today" = true ]; then
            : # today's session already completed cleanly -- do NOT restart post-EOD
        elif token_is_fresh; then
            start_service "Fresh token detected (daily start)."
        fi
    fi

    sleep "$this_sleep"
done
