#!/usr/bin/env bash
# =============================================================================
# scripts/ist_now.sh  —  T4: the single approved operator "what time is it (IST)?"
#
# Prints the authoritative current IST by reusing the app's ONE time source,
# core.time_authority.now_ist() (a fixed UTC+05:30 offset — zoneinfo-free, no
# DST in India). It NEVER shells out to `date` with an IANA zone name, because
# Git Bash / MSYS2 ships no zoneinfo DB, so `TZ='Asia/Kolkata' date` silently
# returns UTC (the 29-Jun defect). Robust from any CWD.
#
# Output: a single ISO-8601 line, e.g.  2026-06-29T20:30:00.123456+05:30
# Usage : scripts/ist_now.sh
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Pick a Python: explicit $PYTHON, then the project venv (PC: venv/Scripts, VM:
# venv/bin), then the shared VM venv, then python3/python on PATH.
pick_python() {
  if [[ -n "${PYTHON:-}" ]]; then printf '%s\n' "$PYTHON"; return 0; fi
  local c
  for c in \
    "$REPO_ROOT/venv/Scripts/python.exe" \
    "$REPO_ROOT/venv/bin/python" \
    "/home/ubuntu/systems/venv/bin/python"; do
    if [[ -x "$c" ]]; then printf '%s\n' "$c"; return 0; fi
  done
  command -v python3 2>/dev/null && return 0
  command -v python  2>/dev/null && return 0
  return 1
}

PY="$(pick_python)" || { echo "ist_now.sh: no Python interpreter found" >&2; exit 2; }

# Run from the repo root so `core` is importable ('' / CWD is on sys.path for -c).
cd "$REPO_ROOT" || { echo "ist_now.sh: cannot cd to repo root $REPO_ROOT" >&2; exit 2; }
exec "$PY" -c "from core.time_authority import now_ist; print(now_ist().isoformat())"
