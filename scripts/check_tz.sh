#!/usr/bin/env bash
# =============================================================================
# scripts/check_tz.sh  —  T4: loud self-check that `TZ='Asia/Kolkata' date` is
# trustworthy in THIS shell.
#
# Git Bash / MSYS2 ships no IANA zoneinfo DB, so `TZ='Asia/Kolkata' date`
# silently falls back to UTC (offset +0000 instead of +0530) — the 29-Jun
# "13:47 IST when it was 19:22 IST" defect. This guard compares the suspect
# form's UTC offset against the app's authoritative now_ist() (via ist_now.sh).
#
#   AGREE  (suspect offset == +0530)  -> pass, exit 0
#   DISAGREE                          -> FAIL LOUD, exit 1 (never use that form)
#
# On a correctly-configured shell (Linux VM with zoneinfo) it PASSES; on this
# MSYS2 PC it correctly FAILS — that failure IS the proof the guard works.
# Diagnostic/deterrent only: deploy_preflight surfaces it but never blocks on it.
#
# Test hook: CHECK_TZ_FAKE_OFF / CHECK_TZ_FAKE_HM override the suspect values so
# the agree/disagree branches can be exercised deterministically.
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Authoritative IST from the app (ISO, e.g. 2026-06-29T20:30:00.123+05:30).
AUTH_ISO="$("$SCRIPT_DIR/ist_now.sh")" || {
  echo "check_tz.sh: could not obtain authoritative IST via ist_now.sh" >&2
  exit 2
}
auth_hm="$(printf '%s' "$AUTH_ISO" | sed -E 's/.*T([0-9]{2}:[0-9]{2}).*/\1/')"
auth_off="$(printf '%s' "$AUTH_ISO" | grep -oE '[+-][0-9]{2}:?[0-9]{2}$' | tr -d ':')"

# Suspect form — the one that breaks in MSYS2. Overridable for tests.
sus_hm="${CHECK_TZ_FAKE_HM:-$(TZ='Asia/Kolkata' date +%H:%M)}"
sus_off="${CHECK_TZ_FAKE_OFF:-$(TZ='Asia/Kolkata' date +%z)}"

# Decision keys on the UTC OFFSET (the MSYS2 fallback is always +0000 == 5h30 off).
if [[ "$sus_off" == "$auth_off" ]]; then
  echo "OK: TZ='Asia/Kolkata' date is reliable here (suspect ${sus_hm} ${sus_off} == now_ist ${auth_hm} ${auth_off})."
  exit 0
fi

cat >&2 <<EOF
FAIL: TZ='Asia/Kolkata' is UNRELIABLE in this shell.
  TZ='Asia/Kolkata' date  -> ${sus_hm} ${sus_off}   (suspect; MSYS2 has no zoneinfo -> falls back to UTC)
  now_ist()  (authoritative) -> ${auth_hm} ${auth_off}
Never use \`TZ='Asia/Kolkata' date\` in this shell. Use instead:
  scripts/ist_now.sh   |   now_ist()   |   date -u  (UTC)   |   TZ='IST-5:30' date
EOF
exit 1
