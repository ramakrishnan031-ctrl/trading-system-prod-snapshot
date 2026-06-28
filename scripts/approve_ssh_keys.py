"""
scripts/approve_ssh_keys.py — deliberate, audited SSH-key re-baseline (Part B).

After a LEGITIMATE SSH key rotation, run this to make security_monitor accept the
current ~/.ssh/authorized_keys WITHOUT editing the git-tracked config/security.yaml
(which a deploy's `checkout -f` would clobber — the root cause of the repeated
false-positive CRITICALs). It writes an OPERATOR OVERRIDE
(data_store/security/ssh_key_baseline.json, NOT git-tracked) that
`security_monitor.apply_operator_ssh_baseline()` overlays — so the re-baseline is
durable across deploys. It ALWAYS sends a Telegram so a re-baseline can NEVER
happen silently (preserves the security value; kills the false positives).

OPERATOR-RUN ONLY — never invoked by the monitor itself.

  python scripts/approve_ssh_keys.py            # show live keys + diff vs baseline (NO change, NO Telegram)
  python scripts/approve_ssh_keys.py --apply     # write the new baseline + re-seed state + Telegram
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.security_monitor import (  # noqa: E402
    SecConfig,
    apply_operator_ssh_baseline,
    authorized_keys_fingerprints,
    _DEFAULT_CONFIG,
    _DEFAULT_OPERATOR_SSH_BASELINE,
    _DEFAULT_STATE,
)


def _effective_baseline(cfg: "SecConfig") -> set:
    allowed = set(cfg.expected_key_fingerprints)
    if cfg.expected_key_fingerprint:
        allowed.add(cfg.expected_key_fingerprint)
    return allowed


def _now_ist():
    try:
        from core.time_authority import now_ist
        return now_ist()
    except Exception:
        from datetime import datetime
        return datetime.now()


def _telegram(title: str, body: str) -> str:
    """Best-effort Telegram (WARNING — not a CRITICAL sentinel/email)."""
    try:
        from alerts.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.from_env()
        if n is None:
            return "NO notifier (TelegramNotifier.from_env returned None)"
        n.send(severity="WARNING", title=title, body=body, source_module="approve_ssh_keys")
        return "sent"
    except Exception as exc:  # never let alerting failure mask the re-baseline
        return f"send FAILED: {exc}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deliberate SSH-key re-baseline (operator-run ONLY).")
    ap.add_argument("--apply", action="store_true",
                    help="write the new baseline override + re-seed state + Telegram")
    ap.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    ap.add_argument("--authorized-keys", default=None,
                    help="authorized_keys path (default: cfg.authorized_keys_path)")
    args = ap.parse_args(argv)

    cfg = SecConfig.load(args.config)
    apply_operator_ssh_baseline(cfg)   # diff is vs the EFFECTIVE current baseline
    akpath = args.authorized_keys or cfg.authorized_keys_path

    live = authorized_keys_fingerprints(akpath)
    baseline = _effective_baseline(cfg)
    added = [f for f in live if f not in baseline]
    removed = [f for f in baseline if f not in live]

    print(f"=== approve_ssh_keys @ {_now_ist():%Y-%m-%d %H:%M:%S %Z} ===")
    print(f"authorized_keys     : {akpath}")
    print(f"LIVE keys ({len(live)})        : {live or '(none)'}")
    print(f"CURRENT baseline ({len(baseline)}) : {sorted(baseline) or '(none)'}")
    print(f"  + would ADD       : {added or '(none)'}")
    print(f"  - would DROP       : {removed or '(none)'}")

    if not live:
        print("\nREFUSING: authorized_keys has 0 readable keys — will NOT baseline an empty "
              "set (that would disable the unexpected-key check). No change made.")
        return 2

    if not args.apply:
        if not added and not removed:
            print("\nBaseline already matches the live keys. (re-run with --apply to re-affirm + Telegram)")
        else:
            print("\nDRY-RUN — re-run with --apply to APPROVE the live keys as the new baseline.")
        return 0

    # ── --apply: write the DURABLE operator override (outside git) ───────────
    rec = {
        "fingerprints": live,
        "count": len(live),
        "approved_at": _now_ist().isoformat(),
        "approved_by": "operator (scripts/approve_ssh_keys.py --apply)",
        "note": "deliberate SSH re-baseline after a legitimate key rotation",
    }
    _DEFAULT_OPERATOR_SSH_BASELINE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DEFAULT_OPERATOR_SSH_BASELINE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, _DEFAULT_OPERATOR_SSH_BASELINE)
    print(f"\nWROTE override baseline : {_DEFAULT_OPERATOR_SSH_BASELINE} ({len(live)} key(s))")

    # Re-seed the monitor STATE so the 'NEW SSH KEY DETECTED' (sha-changed) flag clears now.
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "security_monitor.py"),
             "--baseline", "--config", str(args.config), "--state", str(_DEFAULT_STATE)],
            capture_output=True, text=True, timeout=60,
        )
        print("re-seed state (--baseline):", "ok" if r.returncode == 0 else f"rc={r.returncode} {r.stderr[-200:]}")
    except Exception as exc:
        print(f"re-seed state: WARNING {exc}")

    # ALWAYS Telegram — a re-baseline must never be silent.
    body = (
        f"SSH key baseline RE-BASELINED (deliberate operator approval).\n"
        f"{len(live)} key(s) now authorized:\n" + "\n".join(live) + "\n"
        f"Added: {added or 'none'}\nDropped: {removed or 'none'}\n"
        f"At {rec['approved_at']} via `approve_ssh_keys --apply`."
    )
    print("telegram:", _telegram("🔑 SSH baseline re-approved", body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
