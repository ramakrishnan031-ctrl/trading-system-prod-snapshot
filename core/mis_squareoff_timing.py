"""
core/mis_squareoff_timing.py — the validated MIS square-off TIMING CONTRACT.

EXTRACTED 29-Aug-2026 from orders/mis_autosquareoff.py. A PURE MOVE: the classes,
the parsers and the validation below are byte-for-byte what shipped in effff24.

WHY IT LIVES HERE AND NOT BESIDE THE ORCHESTRATOR
-------------------------------------------------
The notification unit (F) must be able to derive its own pre-pass trigger from the
SAME authoritative config as the orchestrator WITHOUT importing the orchestrator.
If F rode the orchestrator's scheduler -- or merely imported its module -- then an
orchestrator that failed before computing CHECK_1 would take F's trigger with it,
and the one component meant to report on the other would go silent by the same
fault. The test asserts `orders.mis_autosquareoff` is ABSENT from sys.modules at
the moment F fires, so the contract cannot live in that module.

`core/` rather than `orders/` is deliberate and is a DURABILITY choice, not taste:
both packages measure clean today (orders/__init__.py is 0 bytes), but a contract
inside `orders/` would sit in the orchestrator's own package, where an ordinary
future convenience line in orders/__init__.py -- `from .mis_autosquareoff import
MisAutoSquareoff` -- would silently put the orchestrator back on F's import path.
From `core/`, that edit cannot reach F.

SHARE THE CLASS, NEVER THE INSTANCE. Both the orchestrator and F call
MisSquareoffTiming.build(...) themselves, from config. Passing one component's
built instance to the other would reintroduce exactly the lifecycle coupling this
module exists to remove -- so this module owns NO singleton, NO cached instance
and NO process-wide mutable timing state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta


class MisSquareoffConfigError(ValueError):
    """Raised on any invalid timing config. The boot must FAIL CLOSED on this."""


def _parse_hhmm(value: str, key: str) -> dtime:
    """Strict "HH:MM". No coercion, no default, no silent acceptance."""
    if not isinstance(value, str):
        raise MisSquareoffConfigError(
            f"Invalid `{key}`: expected a string \"HH:MM\", got {type(value).__name__}"
        )
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise MisSquareoffConfigError(
            f"Invalid `{key}`: expected \"HH:MM\" in IST, got {value!r}"
        )
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise MisSquareoffConfigError(
            f"Invalid `{key}`: \"HH:MM\" out of range, got {value!r}"
        )
    return dtime(hour=hh, minute=mm)


def _parse_offset_minutes(value: str, key: str) -> int:
    """Strict "<N>m". Positive integer minutes only."""
    if not isinstance(value, str):
        raise MisSquareoffConfigError(
            f"Invalid `{key}`: expected a string like \"5m\", got {type(value).__name__}"
        )
    v = value.strip().lower()
    if not v.endswith("m") or not v[:-1].isdigit():
        raise MisSquareoffConfigError(
            f"Invalid `{key}`: expected \"<minutes>m\" e.g. \"5m\", got {value!r}"
        )
    minutes = int(v[:-1])
    if minutes <= 0:
        raise MisSquareoffConfigError(
            f"Invalid `{key}`: offset must be positive, got {value!r}"
        )
    return minutes


@dataclass(frozen=True)
class MisSquareoffTiming:
    """
    The validated timing contract. Constructing this IS the validation — an
    invalid combination cannot produce an instance, so there is no half-valid
    state for the scheduler to run on.

    ORDERING INVARIANT (all fail-closed):
        entry_end < CHECK_1 < CHECK_2 < cutoff < eod_squareoff_time
        15:00     < 15:07   < 15:10   < 15:12  < 15:17

    `margin_sec` is DERIVED, not picked:
        margin >= poll_interval_sec + PASS_2_measured_bound_execution + buffer
    poll_interval_sec is a real budget component: a 5s poll means PASS 2's
    scheduled instant is EVALUATED anywhere in [15:10:00, 15:10:05]. A margin
    smaller than the poll interval is incoherent — PASS 2 could not be evaluated
    inside it even in principle.
    """
    cutoff: dtime
    check_1: dtime
    check_2: dtime
    margin_sec: int
    poll_interval_sec: int

    @staticmethod
    def build(
        *,
        cutoff: str,
        first_offset: str,
        second_offset: str,
        margin_sec: int,
        poll_interval_sec: int,
        entry_end: str,
        eod_squareoff_time: str,
    ) -> "MisSquareoffTiming":
        cutoff_t = _parse_hhmm(cutoff, "trading_hours.mis_squareoff_cutoff")
        first_m = _parse_offset_minutes(
            first_offset, "trading_hours.mis_squareoff_first_offset")
        second_m = _parse_offset_minutes(
            second_offset, "trading_hours.mis_squareoff_second_offset")
        entry_end_t = _parse_hhmm(entry_end, "trading_hours.entry_end")
        eod_t = _parse_hhmm(eod_squareoff_time, "trading_hours.eod_squareoff_time")

        if not isinstance(margin_sec, int) or isinstance(margin_sec, bool):
            raise MisSquareoffConfigError(
                "Invalid `trading_hours.mis_squareoff_margin_sec`: expected an integer"
            )
        if not isinstance(poll_interval_sec, int) or poll_interval_sec <= 0:
            raise MisSquareoffConfigError(
                "Invalid `mis_squareoff.poll_interval_sec`: expected a positive integer"
            )
        if margin_sec < poll_interval_sec:
            # Prediction #28. A margin below the poll interval lets PASS 1's
            # capped grace run right up to CHECK_2, after which PASS 2 is
            # evaluated late REGARDLESS — the cap would look correct and achieve
            # nothing.
            raise MisSquareoffConfigError(
                f"Invalid `trading_hours.mis_squareoff_margin_sec`: {margin_sec}s is "
                f"below `poll_interval_sec` {poll_interval_sec}s; PASS 2 could not be "
                f"evaluated inside it even in principle"
            )

        if first_m <= second_m:
            raise MisSquareoffConfigError(
                f"Invalid offsets: first_offset ({first_m}m) must exceed "
                f"second_offset ({second_m}m) so CHECK_1 precedes CHECK_2"
            )

        base = datetime(2000, 1, 1, cutoff_t.hour, cutoff_t.minute)
        check_1 = (base - timedelta(minutes=first_m)).time()
        check_2 = (base - timedelta(minutes=second_m)).time()

        if not (entry_end_t < check_1 < check_2 < cutoff_t < eod_t):
            raise MisSquareoffConfigError(
                "Invalid MIS square-off ordering: require "
                "entry_end < CHECK_1 < CHECK_2 < cutoff < eod_squareoff_time; got "
                f"{entry_end_t} < {check_1} < {check_2} < {cutoff_t} < {eod_t}"
            )
        return MisSquareoffTiming(
            cutoff=cutoff_t, check_1=check_1, check_2=check_2,
            margin_sec=margin_sec, poll_interval_sec=poll_interval_sec,
        )
