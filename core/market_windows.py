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

# Cap for next_trading_day walk; protects against pathological holiday lists.
# 30 days handles 11 consecutive NSE holidays (longest known streak) with buffer.
_NEXT_DAY_LOOKAHEAD_CAP = 30


class MarketWindows:
    """Stateless market-window authority.

    All `now` arguments must be timezone-aware datetimes (IST). The class
    itself does not validate timezone; that is the caller's contract via
    time_authority.
    """

    def __init__(
        self,
        entry_start: time = DEFAULT_ENTRY_START,
        entry_end: time = DEFAULT_ENTRY_END,
        market_open: time = DEFAULT_MARKET_OPEN,
        market_close: time = DEFAULT_MARKET_CLOSE,
        eod_squareoff: time = DEFAULT_EOD_SQUAREOFF,
        holidays: set[date] | None = None,
    ) -> None:
        self.entry_start = entry_start
        self.entry_end = entry_end
        self.market_open = market_open
        self.market_close = market_close
        self.eod_squareoff_t = eod_squareoff
        self.holidays: set[date] = set(holidays) if holidays else set()

    # -- weekend / holiday -------------------------------------------------

    def is_trading_holiday(self, now: datetime) -> bool:
        """True if `now` falls on a weekend or a configured holiday."""
        d = now.date()
        # Monday=0 .. Sunday=6
        if d.weekday() >= 5:
            return True
        return d in self.holidays

    def next_trading_day(self, now: datetime) -> date:
        """Return the next date strictly after `now.date()` that is a
        trading day (not weekend, not holiday). Walks at most
        _NEXT_DAY_LOOKAHEAD_CAP days; raises ValueError if exceeded.
        """
        candidate = now.date() + timedelta(days=1)
        for _ in range(_NEXT_DAY_LOOKAHEAD_CAP):
            if candidate.weekday() < 5 and candidate not in self.holidays:
                return candidate
            candidate += timedelta(days=1)
        raise ValueError(
            f"next_trading_day exceeded {_NEXT_DAY_LOOKAHEAD_CAP}-day "
            f"lookahead from {now.date().isoformat()}; check holiday config."
        )

    # -- intraday windows --------------------------------------------------

    def is_market_open(self, now: datetime) -> bool:
        """True if `now` is within market hours on a trading day."""
        if self.is_trading_holiday(now):
            return False
        t = now.time()
        return self.market_open <= t < self.market_close

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

    # -- EOD square-off ----------------------------------------------------

    def is_eod_squareoff_due(self, now: datetime) -> bool:
        """True if `now.time()` >= the configured EOD square-off time
        (P1 default 15:17 IST; configurable via system_config.yaml's
        trading_hours.eod_squareoff_time per CFG-1) on a trading day.
        Caller owns the 'already fired today' edge-trigger flag.
        """
        if self.is_trading_holiday(now):
            return False
        return now.time() >= self.eod_squareoff_t

    def eod_squareoff_time(self, now: datetime) -> datetime:
        """Return the EOD square-off datetime for the date of `now`,
        preserving tzinfo.
        """
        return datetime.combine(
            now.date(), self.eod_squareoff_t, tzinfo=now.tzinfo
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
        """
        today_open = datetime.combine(
            now.date(), self.market_open, tzinfo=now.tzinfo
        )
        today_close = datetime.combine(
            now.date(), self.market_close, tzinfo=now.tzinfo
        )

        # FIX-053: If market is currently open, return 0 immediately
        if not self.is_trading_holiday(now) and today_open <= now < today_close:
            return 0

        # Before today's open
        if not self.is_trading_holiday(now) and now < today_open:
            return int((today_open - now).total_seconds())

        # After today's close or holiday — roll to next trading day
        next_day = self.next_trading_day(now)
        next_open = datetime.combine(
            next_day, self.market_open, tzinfo=now.tzinfo
        )
        return int((next_open - now).total_seconds())
