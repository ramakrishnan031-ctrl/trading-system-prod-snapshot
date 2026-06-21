"""
scripts/preflight/autofix.py -- the safe auto-fix engine.

Contract (locked with Rama):
  * ONLY a fixed whitelist of DB/OS-level fix actions may run. A check whose
    ``fix_action`` is not in the whitelist is ALERT-ONLY -- its fix() is never
    called (in-memory app state lives here; the running app self-heals it).
  * Never mutate in --dry-run.
  * Every fix is idempotent + has a timeout (default 30s).
  * After a fix, the SAME check is re-run; the CRITICAL only clears if the
    re-check returns PASS or WARN.
  * Every attempt is audited (JSON-lines file now; the v33 preflight_autofix_log
    DB table is written by the orchestrator once schema v33 lands).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scripts.preflight.base import Check, CheckContext, CheckResult, FixResult, Status

try:  # logging is best-effort; never let a logger import break the engine
    from core.logger import get_logger
    _log = get_logger("preflight.autofix")
except Exception:  # pragma: no cover
    import logging
    _log = logging.getLogger("preflight.autofix")

# ── the SAFE whitelist (DB/OS only). Everything else = alert-only. ──────────────
SAFE_FIX_ACTIONS = frozenset({
    "systemctl_start_service",
    "set_journal_mode_wal",
    "wal_checkpoint_passive",
    "trigger_token_refresh",
    "run_refresh_instruments",
    "cancel_stale_orphan_order",
    "ensure_dir_perms",
    "kill_switch_prior_day_clear",
})

DEFAULT_FIX_TIMEOUT_SEC = 30
_AUDIT_DIR = Path("logs")


@dataclass
class AutofixOutcome:
    attempted: bool
    final_status: Status
    fix_result: Optional[FixResult] = None
    recheck: Optional[CheckResult] = None
    refused_reason: str = ""


def _now_iso() -> str:
    # IST when available, else UTC -- timestamps are for audit only.
    try:
        from core.time_authority import now_ist
        return now_ist().isoformat()
    except Exception:  # pragma: no cover
        return datetime.now(timezone.utc).isoformat()


def _audit(run_id: str, check: Check, fr: FixResult, *, audit_dir: Path = _AUDIT_DIR) -> None:
    """Append one JSON line describing the fix attempt. Best-effort."""
    row = {
        "ts": _now_iso(),
        "run_id": run_id,
        "check_name": check.name,
        "fix_action": getattr(check, "fix_action", ""),
        "before_state": fr.before_state,
        "after_state": fr.after_state,
        "result": "SUCCESS" if fr.success else "FAILED",
        "error_msg": fr.error_msg,
    }
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        day = _now_iso()[:10]
        with (audit_dir / f"preflight_autofix_{day}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # pragma: no cover - audit must never break the run
        pass
    _log.info("preflight.autofix.attempt", extra={"fix": row})


def _run_with_timeout(check: Check, ctx: CheckContext, timeout_sec: int) -> FixResult:
    """Run check.fix(ctx) in a worker thread; if it overruns, report a timeout.

    We do NOT kill the worker (you can't safely kill a Python thread); fixes are
    idempotent, so a stray slow fix finishing late is harmless -- we just stop
    waiting and report FAILED(timeout) so the CRITICAL is not falsely cleared.
    """
    box: dict = {}

    def worker() -> None:
        try:
            box["fr"] = check.fix(ctx)
        except Exception as exc:  # fix() must never crash the engine
            box["err"] = exc

    t = threading.Thread(target=worker, name=f"fix-{check.name}", daemon=True)
    t.start()
    t.join(timeout_sec)
    if t.is_alive():
        return FixResult(False, action=getattr(check, "fix_action", ""),
                         error_msg=f"fix timed out after {timeout_sec}s")
    if "err" in box:
        exc = box["err"]
        return FixResult(False, action=getattr(check, "fix_action", ""),
                         error_msg=f"{type(exc).__name__}: {exc}")
    fr = box.get("fr")
    if not isinstance(fr, FixResult):
        return FixResult(False, action=getattr(check, "fix_action", ""),
                         error_msg="fix() did not return a FixResult")
    return fr


def attempt(
    check: Check,
    ctx: CheckContext,
    *,
    run_id: str = "",
    timeout_sec: int = DEFAULT_FIX_TIMEOUT_SEC,
    audit_dir: Path = _AUDIT_DIR,
) -> AutofixOutcome:
    """
    Try to auto-fix a failed check, safely. Returns an AutofixOutcome whose
    final_status is:
        AUTOFIXED -- fix ran AND the re-check passed/warned
        FAIL      -- not attempted, or fix failed, or re-check still failed
    """
    action = getattr(check, "fix_action", "")

    if ctx.dry_run:
        return AutofixOutcome(False, Status.FAIL, refused_reason="dry-run (no mutation)")
    if not check.auto_fixable:
        return AutofixOutcome(False, Status.FAIL, refused_reason="not auto_fixable")
    if action not in SAFE_FIX_ACTIONS:
        _log.warning("preflight.autofix.refused",
                     extra={"check": check.name, "action": action})
        return AutofixOutcome(False, Status.FAIL,
                              refused_reason=f"action '{action}' not on safe whitelist")

    fr = _run_with_timeout(check, ctx, timeout_sec)
    _audit(run_id, check, fr, audit_dir=audit_dir)

    recheck = check.run(ctx)
    cleared = fr.success and recheck.status in (Status.PASS, Status.WARN)
    final = Status.AUTOFIXED if cleared else Status.FAIL
    return AutofixOutcome(True, final, fix_result=fr, recheck=recheck)
