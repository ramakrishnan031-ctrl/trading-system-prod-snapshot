"""ops/control_tower/severity.py -- Control Tower native->tower severity maps.

THE single reviewable place for how each source's NATIVE severity becomes a
tower severity. These maps DRIVE 1c's push/paging rules, so they live here in
one block — not buried in the adapters.

Tower scale (loudest first): CRITICAL > HIGH > MEDIUM > LOW > INFO.
"""
from __future__ import annotations

TOWER_SCALE = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}

# ── security_monitor: native CRITICAL | WARNING | INFO ────────────────────────
# WARNING -> MEDIUM (NOT HIGH).  Rationale (the 1b §1A decision): security
# WARNINGs are dominated by new-login-IP / sudo / active-session events, and
# Rama's source IPs are DYNAMIC (documented) -> mapping WARNING to HIGH would
# page on every benign login = alert fatigue. Genuine threats (unexpected SSH
# key, copy-bypass) already emit CRITICAL and map straight through. Reviewable
# here; flip WARNING->HIGH in one line if Rama wants louder security.
SECURITY_MAP = {"CRITICAL": "CRITICAL", "WARNING": "MEDIUM", "INFO": "INFO"}

# ── config_auditor: native BLOCK | WARN | INFO | PASS ─────────────────────────
CONFIG_MAP = {"BLOCK": "CRITICAL", "WARN": "HIGH", "INFO": "INFO", "PASS": "INFO"}


def map_severity(mapping: dict, native: str, default: str = "INFO") -> str:
    return mapping.get(str(native or "").upper(), default)


def worst(severities) -> str:
    """Loudest tower severity in the iterable; INFO if empty/unknown."""
    known = [s for s in severities if s in RANK]
    return max(known, key=lambda s: RANK[s]) if known else "INFO"

# ── NI-14 (23-Aug-2026): panel status follows SEVERITY, never mere existence ──
# The defect this replaces: `"warn" if findings else "ok"` made `ok` UNREACHABLE
# for any source that can emit an INFO finding, so a standing INFO condition
# (the root-probe spike) pinned the security panel to `warn` and the field lost
# all discrimination. Keyed off RANK so it follows the ONE vocabulary above --
# note a native security WARNING arrives here as MEDIUM (SECURITY_MAP), so
# keying on the literal "WARNING" would be dead code.
# LOW is deliberately AMBER, not GREEN: LOW is not INFO, and the security
# adapter uses LOW for "last_run.json unreadable" -- a real condition.
# This changes ONLY the status derivation. Findings themselves are untouched:
# still counted, still in open_severities, still push-eligible.
def status_for(severities) -> str:
    """Panel status from finding severities.

    CRITICAL present -> "critical" | anything above INFO -> "warn"
    | INFO-only or no findings -> "ok".
    """
    ranks = [RANK[s] for s in severities if s in RANK]
    if not ranks:
        return "ok"
    top = max(ranks)
    if top >= RANK["CRITICAL"]:
        return "critical"
    return "warn" if top > RANK["INFO"] else "ok"
