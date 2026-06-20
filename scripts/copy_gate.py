#!/usr/bin/env python3
"""
scripts/copy_gate.py -- VM Security Manager, Phase 2 (copy protection).

The POLICY ENGINE + token store + audit log for VM->PC file copying. Standalone
(like scripts/security_monitor.py): reads its own config/security.yaml, never
imports the trading app's strict config_loader.

What it does (NOT a network blocker — it is the decision brain):
  * check_copy_allowed() -> CopyDecision, evaluated in strict priority order:
        1. TIME-LOCK   18:00-08:00 IST  -> blocked ABSOLUTELY (overrides all,
                                           incl. the OFF switch). Top priority.
        2. SWITCH OFF  copy_protection.enabled=false -> copies unrestricted
                       (the act of DISABLING is what alerts; see security_monitor).
        3. SESSION CAP > max_sessions_for_copy active SSH sessions -> blocked.
        4. TOKEN       a valid, unexpired token must exist -> else blocked.
  * a 15-min token store (data_store/security/copy_token.json) issued by
    scripts/request_copy.py and consumed here.
  * an append-only JSON-lines audit log (data_store/security/copy_audit.log).

Enforcement points that CALL this engine:
  * deploy/security/bin/copy-guard (wrapper symlinked as /usr/local/bin/{scp,
    sftp,rsync}) runs `copy_gate.py --enforce` before exec'ing the real binary,
    so a VM-INITIATED copy is hard-blocked unless a token is live. (A
    PC-INITIATED pull cannot be intercepted here -- security_monitor's auditd
    bypass check detects those.)
  * scripts/request_copy.py reuses the config, token store and audit log.

Modes:
  --enforce  --tool <scp|sftp|rsync> -- <args...>   exit 0 (allow) / 1 (block),
             audits COPY_ALLOWED / COPY_DENIED_<reason>. Used by the wrapper.
  --status   print the current decision (no side effects beyond a read).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_log = logging.getLogger("copy_gate")

_IST = timezone(timedelta(hours=5, minutes=30))
_DEFAULT_CONFIG = _ROOT / "config" / "security.yaml"
_DEFAULT_TOKEN = _ROOT / "data_store" / "security" / "copy_token.json"
_DEFAULT_AUDIT = _ROOT / "data_store" / "security" / "copy_audit.log"


def now_ist() -> datetime:
    return datetime.now(_IST)


# ─────────────────────────────────────────────────────────────────────────────
# Config (the copy_protection: block of config/security.yaml; defensive defaults)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CopyConfig:
    enabled: bool = True
    token_ttl_minutes: int = 15
    time_lock_start: int = 18      # 18:00 — nightly hard block begins
    time_lock_end: int = 8         # 08:00 — nightly hard block ends
    max_sessions_for_copy: int = 2
    token_path: str = str(_DEFAULT_TOKEN)
    audit_log_path: str = str(_DEFAULT_AUDIT)
    sentinel_dir: str = "data_store"

    @staticmethod
    def load(path: Path | str = _DEFAULT_CONFIG) -> "CopyConfig":
        cfg = CopyConfig()
        try:
            import yaml  # PyYAML is already a project dep
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            cp = raw.get("copy_protection", {}) if isinstance(raw, dict) else {}
            if isinstance(cp, dict):
                for f in (
                    "enabled", "token_ttl_minutes", "time_lock_start",
                    "time_lock_end", "max_sessions_for_copy", "token_path",
                    "audit_log_path", "sentinel_dir",
                ):
                    if cp.get(f) is not None:
                        setattr(cfg, f, cp[f])
        except FileNotFoundError:
            _log.warning("security.yaml not found at %s; copy_protection defaults", path)
        except Exception as exc:  # never fail to run on a bad config
            _log.error("copy_protection config load failed (%s); using defaults", exc)
        return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers (unit-tested)
# ─────────────────────────────────────────────────────────────────────────────

def in_time_lock(now: datetime, start_hour: int, end_hour: int) -> bool:
    """True if `now` is inside the nightly copy-lock window [start, end).

    The window wraps midnight when start > end (e.g. 18..8 covers 18:00-23:59
    and 00:00-07:59). start == end means "no lock window" (never locked)."""
    h = now.hour
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= h < end_hour
    return h >= start_hour or h < end_hour  # wrap-around


@dataclass
class CopyToken:
    token_id: str
    reason: str
    issuer: str
    issued_at: str   # ISO-8601 IST
    expires_at: str  # ISO-8601 IST

    def is_expired(self, now: datetime) -> bool:
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except (ValueError, TypeError):
            return True  # unparseable -> treat as expired (fail closed)
        return now >= exp

    @staticmethod
    def from_dict(d: dict) -> "CopyToken":
        return CopyToken(
            token_id=str(d.get("token_id", "")),
            reason=str(d.get("reason", "")),
            issuer=str(d.get("issuer", "")),
            issued_at=str(d.get("issued_at", "")),
            expires_at=str(d.get("expires_at", "")),
        )


@dataclass
class CopyDecision:
    allowed: bool
    reason: str   # TIME_LOCK | PROTECTION_OFF | SESSION_LIMIT | NO_TOKEN | TOKEN_VALID
    message: str
    token: Optional[CopyToken] = None


# ─────────────────────────────────────────────────────────────────────────────
# SSH session helpers (reuse Phase 1; lazy import to avoid an import cycle)
# ─────────────────────────────────────────────────────────────────────────────

def _ssh_peers() -> list[str]:
    try:
        from scripts.security_monitor import established_ssh_peers
        return sorted(set(established_ssh_peers()))
    except Exception:
        return []


def active_ssh_session_count() -> int:
    return len(_ssh_peers())


# ─────────────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────────────

class CopyGate:
    def __init__(self, cfg: CopyConfig) -> None:
        self.cfg = cfg

    # -- token store -------------------------------------------------------
    def read_token(self) -> Optional[CopyToken]:
        try:
            d = json.loads(Path(self.cfg.token_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(d, dict):
            return None
        return CopyToken.from_dict(d)

    def write_token(self, token: CopyToken) -> None:
        p = Path(self.cfg.token_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(token.__dict__, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    def clear_token(self) -> bool:
        try:
            Path(self.cfg.token_path).unlink()
            return True
        except OSError:
            return False

    # -- decision ----------------------------------------------------------
    def check_copy_allowed(
        self,
        now: Optional[datetime] = None,
        active_sessions: Optional[int] = None,
    ) -> CopyDecision:
        now = now or now_ist()
        cfg = self.cfg

        # RULE 1 (ABSOLUTE, top priority): TIME-LOCK. Overrides everything,
        # including the OFF switch and any otherwise-valid token.
        if in_time_lock(now, cfg.time_lock_start, cfg.time_lock_end):
            return CopyDecision(
                False, "TIME_LOCK",
                f"Copying blocked {cfg.time_lock_start:02d}:00-{cfg.time_lock_end:02d}:00 "
                f"IST (hard rule, no override).")

        # RULE 2: master switch OFF -> protection disabled, copies unrestricted.
        # (Disabling itself raises a CRITICAL alert from security_monitor.)
        if not cfg.enabled:
            return CopyDecision(
                True, "PROTECTION_OFF",
                "Copy protection is OFF — copies unrestricted (disabling was alerted).")

        # RULE 3: too many concurrent sessions -> block.
        if active_sessions is None:
            active_sessions = active_ssh_session_count()
        if active_sessions > cfg.max_sessions_for_copy:
            return CopyDecision(
                False, "SESSION_LIMIT",
                f"{active_sessions} active SSH sessions (limit "
                f"{cfg.max_sessions_for_copy}). Copy blocked.")

        # RULE 4: a valid, unexpired token must exist.
        token = self.read_token()
        if token is None or token.is_expired(now):
            return CopyDecision(
                False, "NO_TOKEN",
                "No valid copy token. Run: request-copy <reason>")

        return CopyDecision(
            True, "TOKEN_VALID",
            f"Copy authorized by token (reason: {token.reason}).", token=token)

    # -- audit -------------------------------------------------------------
    def audit(self, event: str, **fields) -> None:
        """Append one JSON line to the copy audit log (best-effort, never raises)."""
        rec = {
            "ts": now_ist().isoformat(),
            "event": event,
            "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "?",
            "ssh_connection": os.environ.get("SSH_CONNECTION", ""),
            "peers": _ssh_peers(),
            "host": socket.gethostname(),
            "pid": os.getpid(),
        }
        rec.update(fields)
        try:
            p = Path(self.cfg.audit_log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            _log.error("copy_gate: audit write failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Alerting (shared by request_copy.py)
# ─────────────────────────────────────────────────────────────────────────────

def send_alert(cfg: CopyConfig, severity: str, title: str, body: str) -> None:
    """Best-effort Telegram (all tiers) + CRITICAL sentinel email. Never raises.

    send() writes the CRITICAL sentinel itself (TG5) and returns its path; we only
    write our own when that did not happen (notifier missing / Telegram disabled /
    send raised) so each CRITICAL = exactly ONE sentinel -> one email."""
    result = None
    try:
        from alerts.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.from_env(logger=_log)
        if n is not None:
            result = n.send(severity=severity, title=f"🔒 {title}", body=body,
                            source_module="copy_gate")
    except Exception as exc:
        _log.error("copy_gate: telegram send failed: %s", exc)
    if severity == "CRITICAL" and not getattr(result, "sentinel_path", None):
        try:
            from alerts.critical import write_critical_sentinel
            write_critical_sentinel(title=title, body=body,
                                    source_module="copy_gate",
                                    sentinel_dir=cfg.sentinel_dir)
        except Exception as exc:
            _log.error("copy_gate: sentinel write failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _enforce(cfg: CopyConfig, tool: str, args: list[str]) -> int:
    """Called by the copy-guard wrapper. exit 0 = allow, 1 = block."""
    gate = CopyGate(cfg)
    dec = gate.check_copy_allowed()
    if dec.allowed:
        gate.audit("COPY_ALLOWED", tool=tool, args=args, decision=dec.reason,
                   token_id=(dec.token.token_id if dec.token else None))
        return 0
    gate.audit(f"COPY_DENIED_{dec.reason}", tool=tool, args=args)
    # Alert on the security-relevant denials (a forgotten token is NOT alerted —
    # that is the normal "run request-copy first" case, just a stderr nudge).
    if dec.reason in ("TIME_LOCK", "SESSION_LIMIT"):
        send_alert(cfg, "WARNING", f"Copy blocked ({dec.reason})",
                   f"A VM->PC {tool} was blocked: {dec.message}\nArgs: {' '.join(args)}")
    sys.stderr.write(f"[copy-guard] DENIED ({dec.reason}): {dec.message}\n")
    return 1


def _print_status(cfg: CopyConfig) -> int:
    gate = CopyGate(cfg)
    now = now_ist()
    dec = gate.check_copy_allowed(now=now)
    tok = gate.read_token()
    print(f"=== Copy gate status @ {now:%Y-%m-%d %H:%M:%S %Z} ===")
    print(f"protection enabled : {cfg.enabled}")
    print(f"time-lock window   : {cfg.time_lock_start:02d}:00-{cfg.time_lock_end:02d}:00 IST "
          f"(now locked: {in_time_lock(now, cfg.time_lock_start, cfg.time_lock_end)})")
    print(f"active SSH sessions: {active_ssh_session_count()} (limit {cfg.max_sessions_for_copy})")
    if tok is None:
        print("token              : none")
    else:
        print(f"token              : {tok.token_id} reason='{tok.reason}' "
              f"expires {tok.expires_at} (expired: {tok.is_expired(now)})")
    print(f"DECISION           : {'ALLOW' if dec.allowed else 'BLOCK'} "
          f"({dec.reason}) — {dec.message}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VM copy gate (Phase 2)")
    ap.add_argument("--enforce", action="store_true",
                    help="decide+audit for the copy-guard wrapper (exit 0 allow / 1 block)")
    ap.add_argument("--status", action="store_true", help="print current decision")
    ap.add_argument("--tool", default="scp", help="tool name (scp/sftp/rsync)")
    ap.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    ap.add_argument("rest", nargs="*", help="copy args (after --)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = CopyConfig.load(args.config)

    if args.enforce:
        return _enforce(cfg, args.tool, args.rest)
    return _print_status(cfg)


if __name__ == "__main__":
    sys.exit(main())
