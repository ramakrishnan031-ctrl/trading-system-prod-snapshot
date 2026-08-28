"""
alerts/mis_squareoff_notifier.py — F / D-1b. Human-facing delivery for the MIS
auto-square-off orchestrator's CRITICAL states.

WHAT THIS BUYS, AND WHAT IT DOES NOT
------------------------------------
The orchestrator emits CRITICAL on DEADLINE_BREACH, MIS_REMAINS, CANCEL_FAILED and
BROKER_STATE_UNAVAILABLE. If those are log-only when the unit first executes live,
nobody learns until someone next looks.

F does NOT enable intervention. PASS 2's measured-bound execution is ~2s; nobody
reads an alert and acts inside that. F buys a SAME-DAY response instead of an
unbounded one. That is the whole claim.

F CANNOT guarantee human attention, cannot infer receipt from provider acceptance,
covers nothing BETWEEN self-tests, does nothing if the host is dead.

EXECUTION INDEPENDENCE — the property this file exists to preserve
-----------------------------------------------------------------
F derives its pre-pass trigger from the SAME config as the orchestrator, on its OWN
timer. It never rides the orchestrator's scheduler, never reads orchestrator-owned
state, and never imports `orders.mis_autosquareoff`. If it did, an orchestrator that
failed before computing CHECK_1 would take F's trigger with it -- and the component
meant to report on the other would go silent by the same fault.

Same CLASS (MisSquareoffTiming, from core/), same CONFIG, never the same INSTANCE.

TWO SELF-TESTS, because health at boot is not health at use
-----------------------------------------------------------
  BOOT     (~08:15) proves the transport was wired and working at startup.
  PRE_PASS (CHECK_1 - 2min, ~15:05) proves it near the moment it matters.
A transport healthy at 08:15 can be dead at 15:07: a revoked token, an expired SMTP
session, a network change, a rate limit reached during the day. The pre-pass test
reduces the uncertainty window from ~7 hours to ~2 minutes. It does NOT prove health
AT 15:07.

KNOWN LIMITATION, recorded rather than engineered around:
    The self-tests are POSITIVE SIGNALS ONLY. Their absence is NOT evidence of
    health and NOT evidence of failure -- it is uninformative. Silence must never be
    interpreted as an all-clear.
There is deliberately no watchdog, no dead-man's switch, no heartbeat and no third
channel. The evidence copy after the passes answers "did it fire?" retrospectively.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Dict, List, Optional

# core/, NEVER orders.mis_autosquareoff. A test asserts that module is absent from
# sys.modules at the moment F fires; importing it here would fail that assertion and
# would reintroduce the coupling the extraction removed.
from core.mis_squareoff_timing import MisSquareoffTiming

# Named explicitly rather than discovered by a package scan: if some alias path ever
# loads the same orchestrator code, that is a coupling FINDING, not something to
# work around.
CANONICAL_ORCHESTRATOR_MODULE = "orders.mis_autosquareoff"

# The orchestrator states F exists to carry. Kept as literals rather than imported,
# because importing them would import the orchestrator.
CRITICAL_CONDITIONS = (
    "DEADLINE_BREACH",
    "MIS_REMAINS",
    "CANCEL_FAILED",
    "BROKER_STATE_UNAVAILABLE",
)


class TransportState:
    """Per-channel outcome. ACCEPTED_BY_PROVIDER is the strongest thing a transport
    can tell us -- it is NOT delivery to a human, and must never be renamed as if it
    were."""
    ATTEMPTED = "ATTEMPTED"
    ACCEPTED_BY_PROVIDER = "ACCEPTED_BY_PROVIDER"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    EXCEPTION = "EXCEPTION"
    UNKNOWN = "UNKNOWN"


class Aggregate:
    BOTH_ACCEPTED = "BOTH_ACCEPTED"
    PARTIAL = "PARTIAL"          # NOT success.
    BOTH_FAILED = "BOTH_FAILED"
    UNKNOWN = "UNKNOWN"


class SelfTestKind:
    BOOT = "BOOT"
    PRE_PASS = "PRE_PASS"


@dataclass
class ChannelResult:
    channel: str                 # "email" | "telegram"
    state: str
    latency_ms: int = 0
    detail: str = ""

    @property
    def accepted(self) -> bool:
        return self.state == TransportState.ACCEPTED_BY_PROVIDER


@dataclass
class NotifyRecord:
    """One notification attempt, start to finish, under ONE correlation id.

    The id is what makes `orchestrator event -> F attempt -> email result ->
    telegram result` provably the same incident. Log adjacency is not identity.
    """
    correlation_id: str
    kind: str                    # SelfTestKind.* or a CRITICAL condition
    at: Optional[datetime] = None
    email: Optional[ChannelResult] = None
    telegram: Optional[ChannelResult] = None
    aggregate: str = Aggregate.UNKNOWN
    boot_id: str = ""

    def as_dict(self) -> dict:
        return {
            "correlation_id": self.correlation_id,
            "kind": self.kind,
            "at": self.at.isoformat() if self.at else None,
            "boot_id": self.boot_id,
            "email_state": self.email.state if self.email else None,
            "email_latency_ms": self.email.latency_ms if self.email else None,
            "telegram_state": self.telegram.state if self.telegram else None,
            "telegram_latency_ms": self.telegram.latency_ms if self.telegram else None,
            "aggregate": self.aggregate,
        }


class MisSquareoffNotifier:
    """F. Observability only -- NEVER control authority.

    `send_email` and `send_telegram` are injected callables, deliberately. The
    shipped TelegramNotifier sends email only as a FALLBACK when telegram fails for
    every chat (telegram_notifier.py: `if write_sentinel and not delivered and
    failed`). That is not two independent channels, so F drives both itself and
    records two independent results.
    """

    def __init__(
        self,
        *,
        trading_hours,
        poll_interval_sec: int,
        send_email: Callable[[str, str], None],
        send_telegram: Callable[[str, str], None],
        logger,
        now_fn: Callable[[], datetime],
        boot_id: Optional[str] = None,
        pre_pass_lead_min: int = 2,
        send_timeout_sec: float = 5.0,
    ) -> None:
        # F builds its OWN timing from config. It never receives the orchestrator's
        # instance: that would make one component's lifecycle the other's
        # precondition -- data coupling wearing the costume of good hygiene.
        self._timing: MisSquareoffTiming = MisSquareoffTiming.build(
            cutoff=trading_hours.mis_squareoff_cutoff,
            first_offset=trading_hours.mis_squareoff_first_offset,
            second_offset=trading_hours.mis_squareoff_second_offset,
            margin_sec=trading_hours.mis_squareoff_margin_sec,
            poll_interval_sec=poll_interval_sec,
            entry_end=trading_hours.entry_end,
            eod_squareoff_time=trading_hours.eod_squareoff_time,
        )
        self._send_email = send_email
        self._send_telegram = send_telegram
        self._log = logger
        self._now = now_fn
        self._boot_id = boot_id or uuid.uuid4().hex[:12]
        self._lead = timedelta(minutes=int(pre_pass_lead_min))
        self._timeout = float(send_timeout_sec)

        self._lock = threading.Lock()
        # Timestamped EVENTS, never a `notification_healthy = True` latch: a stale
        # 08:15 PASS must not read as protection at 15:07.
        self._self_tests: List[NotifyRecord] = []
        self._incidents: List[NotifyRecord] = []
        self._pre_pass_fired: Dict[date, bool] = {}
        self._boot_test_done = False

    # ── the trigger, derived from config on F's OWN clock ────────────────────

    @property
    def timing(self) -> MisSquareoffTiming:
        return self._timing

    @property
    def boot_id(self) -> str:
        return self._boot_id

    def pre_pass_at(self, now: datetime) -> datetime:
        """CHECK_1 - lead. Derived from the same config the orchestrator reads, so it
        moves when the cutoff moves -- and computed here, so a broken orchestrator
        cannot take it away."""
        check_1 = now.replace(
            hour=self._timing.check_1.hour, minute=self._timing.check_1.minute,
            second=0, microsecond=0,
        )
        return check_1 - self._lead

    def check_and_fire_pre_pass(self, now: Optional[datetime] = None) -> Optional[NotifyRecord]:
        """F's OWN poll entry point. Never called by the orchestrator."""
        now = now or self._now()
        today = now.date()
        with self._lock:
            if self._pre_pass_fired.get(today, False):
                return None
            # A WINDOW, not a threshold. `now >= pre_pass_at` alone would fire the
            # announcement at ANY time after 15:05 -- so a service restarting at
            # 16:00, or a test running in the evening, would send "MIS pass due in
            # 2 minutes" when the pass is long past. That is false, and a line Rama
            # learns to ignore is worse than no line at all. Fire only inside the
            # lead window, before CHECK_1 itself.
            check_1 = now.replace(
                hour=self._timing.check_1.hour,
                minute=self._timing.check_1.minute, second=0, microsecond=0)
            if not (self.pre_pass_at(now) <= now < check_1):
                return None
            self._pre_pass_fired[today] = True
        return self._self_test(SelfTestKind.PRE_PASS, now)

    def run_boot_self_test(
        self, now: Optional[datetime] = None, *, background: bool = False,
    ) -> Optional[NotifyRecord]:
        """Once per ACTUAL boot, carrying the boot id so a legitimate restart is
        distinguishable from a duplicate-notification bug. A restart's self-test is
        never suppressed to reduce message count.

        `background=True` in production: two bounded sends are up to 2 x
        send_timeout_sec, and the BOOT PATH MUST NOT WAIT FOR A TRANSPORT. A slow
        SMTP at 08:15 would otherwise delay the service's own startup -- the same
        "notification must never block the trading path" rule applied to boot.
        The result is still recorded and readable via self_tests().
        """
        with self._lock:
            if self._boot_test_done:
                return None
            self._boot_test_done = True
        at_ = now or self._now()
        if not background:
            return self._self_test(SelfTestKind.BOOT, at_)
        threading.Thread(
            target=self._self_test, args=(SelfTestKind.BOOT, at_),
            daemon=True, name="f_boot_self_test",
        ).start()
        return None

    def start_polling(self, poll_interval_sec: Optional[int] = None) -> None:
        """F's OWN poll thread -- deliberately not the orchestrator's.

        Mirrors MisAutoSquareoff.start_polling, but is a separate thread driven by
        a trigger F derived itself from config. That separation is the whole point:
        an orchestrator that failed before computing CHECK_1 must not be able to
        take F's announcement with it.
        """
        interval = int(poll_interval_sec or self._timing.poll_interval_sec)

        def _poll_loop() -> None:
            while True:
                try:
                    self.check_and_fire_pre_pass()
                except Exception as exc:  # noqa: BLE001
                    self._log.error("F pre-pass poll error: %s", exc)
                time.sleep(interval)

        t = threading.Thread(
            target=_poll_loop, daemon=True, name="mis_f_prepass_poll")
        with self._lock:
            self._poll_thread = t
        t.start()
        self._log.info(
            "F pre-pass poll started (interval=%ds); PRE_PASS at CHECK_1-%dmin",
            interval, int(self._lead.total_seconds() // 60))

    def is_alive(self) -> bool:
        with self._lock:
            t = getattr(self, "_poll_thread", None)
        return t is not None and t.is_alive()

    # ── the incident path ────────────────────────────────────────────────────

    def notify_critical(
        self, condition: str, detail: str, correlation_id: Optional[str] = None,
    ) -> NotifyRecord:
        """Carry one orchestrator CRITICAL to a human.

        This NEVER changes orchestrator truth. A delivery failure is recorded as a
        delivery failure; the underlying CRITICAL stands untouched. F is an
        observability mechanism, not the source of truth -- so `DEADLINE_BREACH
        occurred` and `F delivery failed` are two separate records and are never
        collapsed into "no alert", "no incident", "warning only" or PASS.
        """
        rec = self._deliver(
            kind=condition,
            subject=f"[MIS AUTO-SQUAREOFF] {condition}",
            body=detail,
            at=self._now(),
            correlation_id=correlation_id,
        )
        with self._lock:
            self._incidents.append(rec)
        return rec

    # ── delivery ─────────────────────────────────────────────────────────────

    def _self_test(self, kind: str, now: datetime) -> NotifyRecord:
        if kind == SelfTestKind.PRE_PASS:
            body = (
                f"MIS pass due at {self._timing.check_1.strftime('%H:%M')}; "
                f"alerting live. Boot {self._boot_id}."
            )
        else:
            body = (
                f"MIS auto-square-off alerting wired at boot. "
                f"CHECK_1 {self._timing.check_1.strftime('%H:%M')}, "
                f"CHECK_2 {self._timing.check_2.strftime('%H:%M')}, "
                f"cutoff {self._timing.cutoff.strftime('%H:%M')}. Boot {self._boot_id}."
            )
        # INFO and unmistakably distinct from the four CRITICAL conditions: a line
        # Rama learns to ignore would be worse than nothing.
        rec = self._deliver(
            kind=kind,
            subject=f"[NOTIFICATION_SELF_TEST/{kind}] MIS alerting",
            body=body,
            at=now,
        )
        with self._lock:
            self._self_tests.append(rec)
        return rec

    def _deliver(
        self, *, kind: str, subject: str, body: str, at: datetime,
        correlation_id: Optional[str] = None,
    ) -> NotifyRecord:
        rec = NotifyRecord(
            correlation_id=correlation_id or uuid.uuid4().hex[:16],
            kind=kind, at=at, boot_id=self._boot_id,
        )
        # Independent channels. One channel's failure must not erase the other's
        # evidence, and must not stop it being attempted.
        rec.email = self._send_bounded("email", self._send_email, subject, body)
        rec.telegram = self._send_bounded("telegram", self._send_telegram, subject, body)

        e_ok, t_ok = rec.email.accepted, rec.telegram.accepted
        if e_ok and t_ok:
            rec.aggregate = Aggregate.BOTH_ACCEPTED
        elif e_ok or t_ok:
            rec.aggregate = Aggregate.PARTIAL      # NOT success.
        else:
            rec.aggregate = Aggregate.BOTH_FAILED

        # Attempt AND result, never collapsed into "sent = yes".
        self._log.info("F_NOTIFY", extra=rec.as_dict())
        if rec.aggregate != Aggregate.BOTH_ACCEPTED:
            self._log.error(
                "F_NOTIFY_DEGRADED kind=%s aggregate=%s email=%s telegram=%s "
                "correlation_id=%s",
                kind, rec.aggregate, rec.email.state, rec.telegram.state,
                rec.correlation_id,
            )
        return rec

    def _send_bounded(
        self, channel: str, fn: Callable[[str, str], None], subject: str, body: str,
    ) -> ChannelResult:
        """Bounded, and bounded by CONSTRUCTION rather than by hope.

        The orchestrator's PASS 2 has a ~2s measured-bound execution against a 120s
        budget. A blocking SMTP retry or telegram timeout here could BY ITSELF push
        the pass past the 15:12 cutoff -- turning the observability layer into the
        cause of a DEADLINE_BREACH. So each send runs on a worker thread and is
        abandoned at the deadline; the caller is never held beyond it.
        """
        outcome: Dict[str, object] = {"state": TransportState.UNKNOWN, "detail": ""}

        def _run() -> None:
            try:
                fn(subject, body)
                outcome["state"] = TransportState.ACCEPTED_BY_PROVIDER
            except TimeoutError as exc:          # noqa: PERF203
                outcome["state"] = TransportState.TIMEOUT
                outcome["detail"] = str(exc)
            except Exception as exc:             # noqa: BLE001
                outcome["state"] = TransportState.EXCEPTION
                outcome["detail"] = f"{type(exc).__name__}: {exc}"

        t0 = time.monotonic()
        worker = threading.Thread(
            target=_run, daemon=True, name=f"f_notify_{channel}")
        worker.start()
        worker.join(self._timeout)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if worker.is_alive():
            # Abandoned, not awaited. The thread is a daemon and cannot hold the
            # process; the caller proceeds.
            return ChannelResult(
                channel=channel, state=TransportState.TIMEOUT, latency_ms=elapsed_ms,
                detail=f"abandoned after {self._timeout}s",
            )
        return ChannelResult(
            channel=channel, state=str(outcome["state"]), latency_ms=elapsed_ms,
            detail=str(outcome["detail"]),
        )

    # ── evidence ─────────────────────────────────────────────────────────────

    def self_tests(self) -> List[NotifyRecord]:
        with self._lock:
            return list(self._self_tests)

    def incidents(self) -> List[NotifyRecord]:
        with self._lock:
            return list(self._incidents)

    def last_self_test(self, kind: Optional[str] = None) -> Optional[NotifyRecord]:
        """A timestamped event, never a health latch. Callers read WHEN it happened
        and decide for themselves whether that is recent enough to mean anything."""
        with self._lock:
            for rec in reversed(self._self_tests):
                if kind is None or rec.kind == kind:
                    return rec
        return None
