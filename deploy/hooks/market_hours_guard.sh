#!/bin/bash
# deploy/hooks/market_hours_guard.sh — FIX-065: the market-hours DEPLOY guard.
#
# Decides ALONE whether a push may proceed, from inputs passed as arguments:
#     market_hours_guard.sh <HHMM> <DOW> <COMMIT_MSG>
#       HHMM       : 24h clock in IST, zero-padded ("0915", "1530")
#       DOW        : ISO day-of-week in IST, 1=Mon .. 7=Sun  (`date +%u`)
#       COMMIT_MSG : head commit message (checked for the [force-deploy] bypass)
#     exit 0 -> allow the push        exit 1 -> reject it
#
# WHY A SEPARATE SCRIPT: the caller (pre-receive) supplies the clock, so this file is a
# pure function of its arguments — which is what makes it honestly testable. FIX-065's
# original tests asserted against a mock hook they defined inline, so they passed no
# matter what the real hook did; tests/unit/test_fix065_market_hours_guard.py now runs
# THIS file, the one that actually ships.
#
# WHY pre-receive AND NOT post-receive: git ignores post-receive's exit status — refs are
# already updated by the time it runs — so a guard there cannot reject a push. It could
# only skip the checkout while the bare ref moved, leaving bare != tree: a half-deploy,
# which is worse than no guard at all. pre-receive rejects cleanly and nothing moves.
#
# Holidays are deliberately NOT consulted: this guard must never depend on a YAML calendar
# that could be missing or wrong (that failure mode belongs to the market_day_only work).
# Weekday + clock only. An NSE holiday inside the window is a false reject — take the
# [force-deploy] bypass, which is exactly what it is for.
set -u

HHMM="${1:-}"
DOW="${2:-}"
MSG="${3:-}"

MARKET_OPEN=915    # 09:15 IST
MARKET_CLOSE=1530  # 15:30 IST

# Unparseable clock -> FAIL OPEN (allow). A deploy guard must never wedge deploys because
# `date` misbehaved; the window is an operational convenience, not a security control.
case "$HHMM" in ''|*[!0-9]*) exit 0 ;; esac
case "$DOW"  in ''|*[!0-9]*) exit 0 ;; esac

# 10# forces base-10: "0915" in bash arithmetic would otherwise parse as octal and die.
now=$((10#$HHMM))
dow=$((10#$DOW))

[ "$dow" -ge 6 ] && exit 0                                   # weekend -> always allow
[ "$now" -lt "$MARKET_OPEN" ] && exit 0                      # before the open
[ "$now" -gt "$MARKET_CLOSE" ] && exit 0                     # after the close

case "$MSG" in
  *"[force-deploy]"*)
    echo "market_hours_guard: [force-deploy] present — allowing push during market hours."
    exit 0
    ;;
esac

echo "market_hours_guard: REJECT — cannot deploy during market hours (09:15-15:30 IST)."
echo "  Now: ${HHMM} IST (dow=${DOW}). A push now would swap code under a live session."
echo "  Add [force-deploy] to the HEAD commit message to override."
exit 1
