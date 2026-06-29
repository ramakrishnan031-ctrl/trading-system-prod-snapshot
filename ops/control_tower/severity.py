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
