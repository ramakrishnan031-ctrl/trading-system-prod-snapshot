"""alerts/delivery.py — THE ALERT DELIVERY CONTRACT (Phase 0, 09-Aug-2026).

⭐ THE INVARIANT, written before any site was touched:

    ① A trading safety action must NEVER depend on a notification succeeding.
    ② A notification failure must ALWAYS remain observable.

🔑 Before this module the codebase satisfied ① by SWALLOWING — `try: notifier.send(…)
except Exception: pass` — which buys ① at the exact cost of ②. Fourteen production
alert sites do that, and one of them (`signal_processor`'s EXPIRED alert, register
`N9-14`) has been calling a method that DOES NOT EXIST since it was written: the
`AttributeError` dies in the swallow, and nothing anywhere records that an alert was
meant to be sent.

⛔ THIS MODULE DOES NOT REMOVE THE SWALLOW. The swallow is what protects the action.
⭐ It makes the swallow RECORD before it passes.

🔑 WHY THE RECORD MUST BE WRITTEN BY THE CALLER, not by the notifier
`TelegramNotifier._audit_send` already writes exactly this vocabulary — but it lives
INSIDE `send()`, so it can only witness a send that was ATTEMPTED. A call that never
REACHES `send()` (a missing method, a bad kwarg, an adapter swapped underneath) never
reaches the audit line either. That is precisely the class this exists to catch, so
the record has to sit one level up, in the caller.

⛔ THE RECORD IS A LOG, NEVER AN ALERT. Raising an alert about a failed alert, on the
channel that just failed, is circular. The log is the only honest sink — and
`logs/system_<date>.log` is where it lands (⛔ `journalctl` carries no INFO).

⛔ THIS MODULE IS NOT A NEW ALERT PATH. It calls the same `notifier.send(...)` every
existing site calls. It adds a record and a guarantee, nothing else.

⚠️ PHASE 0 CONVERTS TWO SITES ONLY — `kill_switch._alert_exit_failed` and
`order_placer`'s emergency-exit alert, the two where silence costs money and requires
Rama to act. The other twelve swallows, the four unguarded sites, `N9-14` and all five
stamp-before-send limiters are DELIBERATELY untouched: turning several never-fired
alerts on at once produces a wall of first-ever alerts on a trading day, and nobody
would know which one to believe.
"""
from __future__ import annotations

from typing import Any, Optional

# ⛔ The SAME three words `TelegramNotifier._audit_send` already writes. A second
# vocabulary would split one trail into two and make the whole thing unqueryable.
ALERT_OUTCOMES = ("delivered", "failed", "suppressed")

# The caller-side counterpart of the notifier's own `alert_send` line. Same family,
# distinguishable suffix: a `grep alert_send` finds BOTH, and the suffix says which
# side of the boundary wrote it.
_EVENT = "alert_send_caller"


def _emit(log: Any, level: str, fields: dict) -> None:
    """Write the record. ⛔ MUST NOT RAISE — §1.3.

    A failure-recorder that throws inside a swallow is the same bug one layer down,
    so every path here is wrapped, including the fallback.
    """
    try:
        fn = getattr(log, level, None)
        if fn is None:
            return
        fn(_EVENT, extra=fields)
    except Exception:  # noqa: BLE001 — the record must never break the caller
        try:
            fn = getattr(log, "error", None)
            if fn is not None:
                fn("%s outcome=%s (structured record failed)",
                   _EVENT, fields.get("outcome"))
        except Exception:  # noqa: BLE001 — last resort: stay silent, stay alive
            pass


def send_alert_recorded(
    notifier: Any,
    log: Any,
    *,
    severity: str,
    title: str,
    body: str,
    source_module: str,
    **send_kwargs: Any,
) -> bool:
    """Send an alert, NEVER raise, and ALWAYS leave a machine-readable record.

    Returns True iff the notifier reported delivery. ⭐ Callers on a safety path are
    expected to IGNORE the return value — that is invariant ① made structural: there
    is no return value a caller could branch on that would let a failed notification
    change what the system does.

    outcome semantics, reusing `_audit_send`'s words exactly:
      · `delivered`  — `send()` returned and reported success
      · `failed`     — `send()` returned failure, OR did not return at all
      · `suppressed` — there was no notifier to call
    """
    if notifier is None:
        _emit(log, "info", {"event_source": "caller", "outcome": "suppressed",
                            "severity": severity, "source_module": source_module,
                            "title": str(title)[:120],
                            "reason": "no notifier wired"})
        return False

    try:
        result = notifier.send(severity=severity, title=title, body=body,
                               source_module=source_module, **send_kwargs)
    except Exception as exc:  # noqa: BLE001 — INVARIANT ①: the action continues
        # ⭐ THE GAP THIS MODULE EXISTS FOR. `_audit_send` cannot see this: the call
        # never got far enough to run it. `N9-14` is exactly this branch.
        _emit(log, "error", {"event_source": "caller", "outcome": "failed",
                             "severity": severity, "source_module": source_module,
                             "title": str(title)[:120],
                             "error": f"{type(exc).__name__}: {exc}"[:200],
                             "reached_send": False})
        return False

    # `send()` returns a SendResult rather than raising on delivery failure. Read it
    # defensively: a caller may have been handed a double, and a missing attribute
    # must not turn an alert into an exception.
    delivered = bool(getattr(result, "success", False))
    _emit(log, "info" if delivered else "error",
          {"event_source": "caller",
           "outcome": "delivered" if delivered else "failed",
           "severity": severity, "source_module": source_module,
           "title": str(title)[:120], "reached_send": True})
    return delivered
