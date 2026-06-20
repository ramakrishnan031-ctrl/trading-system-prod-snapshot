#!/usr/bin/env python3
"""
scripts/request_copy.py -- VM Security Manager, Phase 2 (copy protection).

`request-copy <reason>`  — deliberately UNLOCK VM->PC copying for 15 minutes.

This is the ONLY blessed way to copy off the VM: it issues a short-lived token
that the copy-guard wrapper (scripts/copy_gate.py --enforce) checks. Issuing a
token is logged (audit + Telegram) so every off-box copy leaves a deliberate,
attributable trail.

Hard rules enforced here (mirrors copy_gate priority):
  * TIME-LOCK 18:00-08:00 IST: NO token can be issued (top priority, absolute).
  * Master switch OFF: copies are already unrestricted; no token needed.
  * Session cap: too many concurrent SSH sessions -> refuse.

Usage:
  request-copy <reason...>        issue a 15-min token
  request-copy --status           show the current gate decision/token
  request-copy --revoke           revoke (delete) the active token now
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.copy_gate import (  # noqa: E402
    CopyConfig,
    CopyGate,
    CopyToken,
    active_ssh_session_count,
    in_time_lock,
    now_ist,
    send_alert,
    _print_status,
)

_log = logging.getLogger("request_copy")


def request(reason: str, cfg: CopyConfig) -> int:
    gate = CopyGate(cfg)
    now = now_ist()
    reason = (reason or "").strip() or "(no reason given)"

    # RULE 1 (ABSOLUTE): TIME-LOCK — refuse to even mint a token.
    if in_time_lock(now, cfg.time_lock_start, cfg.time_lock_end):
        gate.audit("COPY_DENIED_TIME_LOCK", reason=reason, context="request_copy")
        send_alert(cfg, "WARNING", "Copy token DENIED (time-lock)",
                   f"request-copy '{reason}' at {now:%H:%M} IST blocked by the "
                   f"{cfg.time_lock_start:02d}:00-{cfg.time_lock_end:02d}:00 hard lock.")
        print(f"DENIED: Copying blocked {cfg.time_lock_start:02d}:00-"
              f"{cfg.time_lock_end:02d}:00 IST. No token can be issued.")
        return 1

    # RULE 2: master switch OFF -> copies already unrestricted.
    if not cfg.enabled:
        print("Note: copy protection is OFF — copies are already unrestricted "
              "(no token needed).")
        return 0

    # RULE 3: session cap.
    sessions = active_ssh_session_count()
    if sessions > cfg.max_sessions_for_copy:
        gate.audit("COPY_DENIED_SESSION_LIMIT", reason=reason, sessions=sessions)
        send_alert(cfg, "WARNING", "Copy token DENIED (sessions)",
                   f"request-copy '{reason}' refused: {sessions} active SSH "
                   f"sessions > limit {cfg.max_sessions_for_copy}.")
        print(f"DENIED: too many active SSH sessions ({sessions} > "
              f"{cfg.max_sessions_for_copy}).")
        return 1

    # Issue a token (TTL minutes).
    tid = uuid.uuid4().hex[:8]
    issuer = os.environ.get("USER") or os.environ.get("LOGNAME") or "?"
    expires = now + timedelta(minutes=cfg.token_ttl_minutes)
    token = CopyToken(token_id=tid, reason=reason, issuer=issuer,
                      issued_at=now.isoformat(), expires_at=expires.isoformat())
    gate.write_token(token)
    gate.audit("COPY_TOKEN_ISSUED", token_id=tid, reason=reason,
               ttl_minutes=cfg.token_ttl_minutes, expires_at=token.expires_at)
    send_alert(cfg, "INFO", "Copy token issued",
               f"VM->PC copy unlocked: '{reason}' — valid "
               f"{cfg.token_ttl_minutes} min until {expires:%H:%M:%S} IST "
               f"(by {issuer}).")
    print(f"Copy token granted (id {tid}). Valid {cfg.token_ttl_minutes} min, "
          f"until {expires:%H:%M:%S} IST.")
    print(f"Reason: {reason}")
    print("You may now copy VM->PC until the token expires.")
    return 0


def revoke(cfg: CopyConfig) -> int:
    gate = CopyGate(cfg)
    tok = gate.read_token()
    if tok is None:
        print("No active token to revoke.")
        return 0
    gate.clear_token()
    gate.audit("COPY_TOKEN_REVOKED", token_id=tok.token_id, reason=tok.reason)
    send_alert(cfg, "INFO", "Copy token revoked",
               f"Copy token {tok.token_id} ('{tok.reason}') revoked manually.")
    print(f"Revoked copy token {tok.token_id}.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Request a time-limited VM->PC copy token (Phase 2).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="show current gate status")
    g.add_argument("--revoke", action="store_true", help="revoke the active token")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("reason", nargs="*", help="why you need to copy (audited)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = CopyConfig.load(args.config) if args.config else CopyConfig.load()

    if args.status:
        return _print_status(cfg)
    if args.revoke:
        return revoke(cfg)
    return request(" ".join(args.reason), cfg)


if __name__ == "__main__":
    sys.exit(main())
