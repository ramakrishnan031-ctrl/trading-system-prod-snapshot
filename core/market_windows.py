# =============================================================================
# Script        : core/market_windows.py
# Purpose       : Stateless authority for NSE market time windows (entry,
#                 market hours, EOD square-off, holiday/next trading day).
# Manual Input  : No.
# How it works  : Pure functions over an injected configuration. All time
#                 queries take an explicit `now: datetime` (caller supplies it
#                 from time_authority). No internal clock, no I/O, no globals.
# Inputs        : Constructor args (entry/market/EOD times, holiday set).
#                 Each method: now (timezone-aware datetime, IST expected).
# Outputs       : Booleans, datetimes, ints. No side effects, no logging.
# Entry point   : class MarketWindows
# Layer         : 1 (stdlib only). No dependency on config_loader or any
#                 other project module. Caller injects all values.
# =============================================================================

from __future__ import annotations

from datetime import date, datetime, time, timedelta


# Defaults pinned to P1_market_windows_api spec.
DEFAULT_ENTRY_START = time(9, 30)
DEFAULT_ENTRY_END = time(13, 30)
DEFAULT_MARKET_OPEN = time(9, 15)
DEFAULT_MARKET_CLOSE = time(15, 30)
DEFAULT_EOD_SQUAREOFF = time(15, 17)
# FIX-073: EOD entry cutoff — absolute last moment to place an order.
# Prevents signals delayed in rate limiter from opening positions after
# EOD squareoff has started (broker RMS penalty risk).
DEFAULT_EOD_ENTRY_CUTOFF = time(15, 15)

# Cap for next_trading_day walk; protects against pathological holiday lists.
# 30 days handles 11 consecutive NSE holidays (longest known streak) with buffer.
_NEXT_DAY_LOOKAHEAD_CAP = 30


class MarketWindows:
    """Stateless market-window authority.

    All `now` arguments must be timezone-aware datetimes (IST). The class
    itself does not validate timezone; that is the caller's contract via
    time_authority.

    FIX-094: Supports special sessions (e.g. Muhurat trading) via
    special_sessions dict mapping date -> SpecialSession(market_open,
    market_close, eod_squareoff_time).
    """

    def __init__(
        self,
        entry_start: time = DEFAULT_ENTRY_START,
        entry_end: time = DEFAULT_ENTRY_END,
        market_open: time = DEFAULT_MARKET_OPEN,
        market_close: time = DEFAULT_MARKET_CLOSE,
        eod_squareoff: time = DEFAULT_EOD_SQUAREOFF,
        eod_entry_cutoff: time = DEFAULT_EOD_ENTRY_CUTOFF,
        holidays: set[date] | None = None,
        special_sessions: dict[date, tuple[time, time, time]] | None = None,
    ) -> None:
        self.entry_start = entry_start
        self.entry_end = entry_end
        self.market_open = market_open
        self.market_close = market_close
        self.eod_squareoff_t = eod_squareoff
        self.eod_entry_cutoff_t = eod_entry_cutoff
        self.holidays: set[date] = set(holidays) if holidays else set()
        # FIX-094: special_sessions maps date -> (market_open, market_close, eod_squareoff)
        self.special_sessions: dict[date, tuple[time, time, time]] = (
            special_sessions if special_sessions else {}
        )

    # -- FIX-094: special session helpers ----------------------------------

    def _get_effective_times(
        self, d: date
    ) -> tuple[time, time, time]:
        """
        FIX-094: Get effective (market_open, market_close, eod_squareoff) for date.

        If `d` has a special session override, returns those times.
        Otherwise returns the default times configured at construction.

        Returns:
            (market_open, market_close, eod_squareoff) as time objects
        """
        if d in self.special_sessions:
            return self.special_sessions[d]
        return (self.market_open, self.market_close, self.eod_squareoff_t)

    # -- weekend / holiday -------------------------------------------------

    def is_trading_holiday(self, now: datetime) -> bool:
        """True if `now` falls on a weekend or a configured holiday.

        FIX-094: Special session dates are NOT considered holidays,
        even if they fall on weekends (e.g. Muhurat trading on Diwali Saturday).
        """
        d = now.date()
        # FIX-094: Special session override exempts from holiday check
        if d in self.special_sessions:
            return False
        # Monday=0 .. Sunday=6
        if d.weekday() >= 5:
            return True
        return d in self.holidays

    def next_trading_day(self, now: datetime) -> date:
        """Return the next date strictly after `now.date()` that is a
        trading day (not weekend, not holiday). Walks at most
        _NEXT_DAY_LOOKAHEAD_CAP days; raises ValueError if exceeded.

        FIX-094: Special session dates count as trading days even if they
        fall on weekends.
        """
        candidate = now.date() + timedelta(days=1)
        for _ in range(_NEXT_DAY_LOOKAHEAD_CAP):
            # FIX-094: Special session dates are always trading days
            if candidate in self.special_sessions:
                return candidate
            # Regular check: weekday and not a holiday
            if candidate.weekday() < 5 and candidate not in self.holidays:
                return candidate
            candidate += timedelta(days=1)
        raise ValueError(
            f"next_trading_day exceeded {_NEXT_DAY_LOOKAHEAD_CAP}-day "
            f"lookahead from {now.date().isoformat()}; check holiday config."
        )

    # -- intraday windows --------------------------------------------------

    def is_market_open(self, now: datetime) -> bool:
        """True if `now` is within market hours on a trading day.

        FIX-094: Uses special session times if configured for this date.
        """
        if self.is_trading_holiday(now):
            return False
        t = now.time()
        market_open, market_close, _ = self._get_effective_times(now.date())
        return market_open <= t < market_close

    def is_entry_allowed(self, now: datetime) -> bool:
        """True if `now` is within the entry-order processing window
        on a trading day.
        """
        if self.is_trading_holiday(now):
            return False
        t = now.time()
        return self.entry_start <= t < self.entry_end

    def is_entry_allowed_for_strategy(
        self, now: datetime, strategy
    ) -> bool:
        """
        True if `now` is within BOTH the global entry window (P1) AND the
        per-strategy entry window declared in the strategy YAML
        (entry_start_time / entry_end_time, S14).

        2026-04-26 audit CFG-5: previously only the global window was
        enforced; per-strategy times were namesake. Strategies like
        gap_fade_long.yaml declare narrower cutoffs (e.g. 11:30) and rely
        on this check. `strategy` is a StrategyConfig with `entry_start_time`
        and `entry_end_time` "HH:MM" strings; defaults are 09:30 / 13:30.
        """
        if not self.is_entry_allowed(now):
            return False
        try:
            sh, sm = (int(x) for x in strategy.entry_start_time.split(":"))
            eh, em = (int(x) for x in strategy.entry_end_time.split(":"))
        except (AttributeError, ValueError):
            # Strategy missing or malformed times — fall back to global.
            return True
        t = now.time()
        return time(sh, sm) <= t < time(eh, em)

    def is_past_eod_entry_cutoff(self, now: datetime) -> bool:
        """
        FIX-073: True if `now.time()` >= the configured EOD entry cutoff.

        This is the absolute last moment to place an order. Prevents signals
        delayed in rate limiter or entry gate from opening positions after
        EOD squareoff time (broker RMS penalty risk).

        Default cutoff is 15:15 IST (2 minutes before EOD squareoff at 15:17).
        Configurable via system_config.yaml trading_hours.eod_entry_cutoff.
        """
        if self.is_trading_holiday(now):
            return True  # Past cutoff on holidays
        return now.time() >= self.eod_entry_cutoff_t

    # -- EOD square-off ----------------------------------------------------

    def is_eod_squareoff_due(self, now: datetime) -> bool:
        """True if `now.time()` >= the configured EOD square-off time
        (P1 default 15:17 IST; configurable via system_config.yaml's
        trading_hours.eod_squareoff_time per CFG-1) on a trading day.
        Caller owns the 'already fired today' edge-trigger flag.

        FIX-094: Uses special session eod_squareoff time if configured for this date.
        """
        if self.is_trading_holiday(now):
            return False
        _, _, eod_squareoff = self._get_effective_times(now.date())
        return now.time() >= eod_squareoff

    def eod_squareoff_time(self, now: datetime) -> datetime:
        """Return the EOD square-off datetime for the date of `now`,
        preserving tzinfo.

        FIX-094: Uses special session eod_squareoff time if configured for this date.
        """
        _, _, eod_squareoff = self._get_effective_times(now.date())
        return datetime.combine(
            now.date(), eod_squareoff, tzinfo=now.tzinfo
        )

    def seconds_to_eod_squareoff(self, now: datetime) -> int:
        """Seconds from `now` until today's EOD square-off. Negative if
        already past. Integer (truncated).
        """
        delta = self.eod_squareoff_time(now) - now
        return int(delta.total_seconds())

    def seconds_to_market_open(self, now: datetime) -> int:
        """Seconds from `now` until the next market open. If `now` is
        before today's open and today is a trading day, returns seconds
        to today's open. If market is currently open, returns 0.
        Otherwise returns seconds to the next trading day's open.
        Integer (truncated), always >= 0.

        FIX-053: If boot happens after 09:15 but before 15:30 (market
        currently open), return 0 immediately instead of rolling over
        to tomorrow's open (24-hour sleep bug).

        FIX-094: Uses special session times if configured for today or next day.
        """
        market_open, market_close, _ = self._get_effective_times(now.date())
        today_open = datetime.combine(
            now.date(), market_open, tzinfo=now.tzinfo
        )
        today_close = datetime.combine(
            now.date(), market_close, tzinfo=now.tzinfo
        )

        # FIX-053: If market is currently open, return 0 immediately
        if not self.is_trading_holiday(now) and today_open <= now < today_close:
            return 0

        # Before today's open
        if not self.is_trading_holiday(now) and now < today_open:
            return int((today_open - now).total_seconds())

        # After today's close or holiday — roll to next trading day
        next_day = self.next_trading_day(now)
        next_market_open, _, _ = self._get_effective_times(next_day)
        next_open = datetime.combine(
            next_day, next_market_open, tzinfo=now.tzinfo
        )
        return int((next_open - now).total_seconds())


def is_within_market_hours(now_t: time, open_t: time, close_t: time) -> bool:
    """FIX-169 F18: shared helper replacing duplicate _is_market_hours() in
    token_monitor.py and gemini_watchman.py."""
    return open_t <= now_t <= close_t


# FIX-180 Part 12: broker-API availability guard for cron scripts.
# Friday cutoff after which the broker session/API is no longer useful for the
# week (post-market close; Zerodha tokens + data go stale over the weekend).
_FRIDAY_API_CUTOFF = time(17, 30)


def is_market_day(dt: datetime | None = None) -> bool:
    """True only on weekdays (Mon-Fri); Sat/Sun -> False.

    Calendar weekday check only — does NOT consult the NSE holiday list (that
    needs a configured MarketWindows). Use MarketWindows.is_trading_holiday()
    when full holiday-awareness is required.
    """
    if dt is None:
        from core.time_authority import now_ist  # local import avoids cycle
        dt = now_ist()
    return dt.weekday() < 5


def is_broker_api_available(
    dt: datetime | None = None,
    holidays: set[date] | None = None,
) -> bool:
    """Best-effort guard for cron scripts that call the Zerodha API.

    Returns False on Saturday/Sunday and at/after the end-of-week cutoff (17:30
    IST) once the last trading day of the week is done; True otherwise.

    End-of-week cutoff:
      - No holiday calendar (holidays=None): the last weekday is Friday, so the
        cutoff is Friday >= 17:30 (unchanged legacy behaviour).
      - With a holiday calendar (FIX-181): if Friday is a holiday, the week's
        last trading day is Thursday, so the cutoff PREPONES to Thursday >= 17:30
        (T-1). A Friday holiday itself also returns False (markets closed).

    Scope is deliberately narrow: weekday/time (+ optional Friday-holiday
    preponment). It does NOT walk arbitrarily long holiday runs — cron day
    fields and MarketWindows.is_trading_holiday own full holiday scheduling.
    """
    if dt is None:
        from core.time_authority import now_ist  # local import avoids cycle
        dt = now_ist()
    weekday = dt.weekday()  # Mon=0 .. Sun=6
    if weekday >= 5:  # Saturday / Sunday
        return False

    hols = holidays or set()
    today = dt.date()

    # Friday holiday: markets closed Friday -> API not useful for the week.
    if weekday == 4 and today in hols:
        return False

    # T-1 preponment: if tomorrow (Friday) is a holiday, Thursday is the last
    # trading day, so the end-of-week cutoff moves to Thursday 17:30.
    if weekday == 3:  # Thursday
        next_day = today + timedelta(days=1)
        if next_day in hols and dt.time() >= _FRIDAY_API_CUTOFF:
            return False

    # Normal Friday end-of-week cutoff.
    if weekday == 4 and dt.time() >= _FRIDAY_API_CUTOFF:
        return False

    return True
