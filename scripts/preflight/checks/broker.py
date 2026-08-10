"""
scripts/preflight/checks/broker.py -- Group 4 (Broker connectivity).

Two kinds of check:
  * file/network (no broker session): token file exists/fresh (+auto-refresh fix),
    instruments fresh (+refresh fix), public-IP unchanged, holiday.
  * live broker calls via a BrokerProbe (profile / margins / funds / orders): the
    orchestrator builds ONE probe from the on-disk token (KiteConnect + access_token,
    the standalone pattern used by fetch_daily_candles) and injects it on ctx.extra.
    PAPER mode or a missing/invalid token => probe is None => those checks SKIP
    (never crash; the token checks already flag a missing/stale token).

All alert-only. Live calls are reads (safe even in --dry-run).
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality, FixResult

EXPECTED_MIN_CASH = 0.0   # LIVENESS sentinel: funds must be > this (cash sync sanity)

# FIX 2 (10-Aug-2026) — the ADEQUACY floor, which EXPECTED_MIN_CASH never was.
# `> 0.0` asks "did the field come back with something in it"; it does not ask
# "is there enough here to begin a trading day". On 10-Aug it saw net ₹209.80,
# passed, and the account could not cover a ₹907.02 book.
#
# DECIDED BY RAMA, 10-Aug-2026, quoted: "minimum broker cash: Rs 2k or your
# opinion, but avoid spending time on it — no much of setting x or y amount when
# other config settings are master."  ⇒ ₹2,000, and deliberately nothing more
# elaborate: this is a startup-safety floor, NOT a sizing limit, NOT an
# allocation, and NOT a replacement for the capital invariant.
#
# Overridable from config/preflight.yaml so it can be changed without a code
# change. That file is deliberately NOT in AppConfig's registry — see its header.
MIN_BROKER_CASH_RS_DEFAULT = 2000.0


def _min_broker_cash_rs(config_dir: Path) -> float:
    """The startup-cash floor, from config/preflight.yaml, defaulted.

    DEGRADE, NEVER BLOCK (failfast-vs-degrade): a missing or unreadable file is
    an ENVIRONMENT condition — a fresh worktree, a partial deploy — not a
    deliberate act, so fall back to the default rather than failing the funds
    check for a reason that has nothing to do with the funds.
    """
    try:
        import yaml
        f = Path(config_dir) / "preflight.yaml"
        if not f.exists():
            return MIN_BROKER_CASH_RS_DEFAULT
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        v = ((data.get("startup_floors") or {}).get("min_broker_cash_rs"))
        return MIN_BROKER_CASH_RS_DEFAULT if v is None else float(v)
    except Exception:  # noqa: BLE001 — a bad threshold file must not break the check
        return MIN_BROKER_CASH_RS_DEFAULT


# ── token / file helpers (monkeypatchable in tests) ─────────────────────────────
def _token_path(ctx: CheckContext) -> Path:
    return ctx.db_path.parent / "session" / "zerodha_token.json"


def _read_access_token(ctx: CheckContext) -> Optional[str]:
    try:
        return json.loads(_token_path(ctx).read_text(encoding="utf-8")).get("access_token")
    except Exception:
        return None


def _file_mdate(path: Path) -> Optional[date]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return None


def _trading_days_behind(mdate: date, as_of: date, config_dir: Path) -> int:
    """Trading days strictly after mdate up to and including as_of (weekend-aware,
    so a Friday file on Monday counts as 1, not 3). Falls back to calendar days if
    the holiday calendar is unavailable."""
    if mdate >= as_of:
        return 0
    try:
        from utils.holiday_guard import is_trading_day
    except Exception:
        return (as_of - mdate).days
    n, cur = 0, mdate
    while cur < as_of and n < 400:
        cur += timedelta(days=1)
        if is_trading_day(cur, config_dir):
            n += 1
    return n


def _trigger_token_refresh() -> bool:
    r = subprocess.run(["python", "scripts/auto_refresh_token.py"],
                       capture_output=True, text=True, timeout=120)
    return r.returncode == 0


def _trigger_instruments_refresh(account: str) -> bool:
    r = subprocess.run(["python", "scripts/refresh_instruments.py", "--account", account],
                       capture_output=True, text=True, timeout=120)
    return r.returncode == 0


def _get_public_ip() -> Optional[str]:
    try:
        from broker.auth_recovery import get_public_ip
        ip = get_public_ip()
        return ip or None
    except Exception:
        return None


def _stored_ip_path(ctx: CheckContext) -> Path:
    return ctx.db_path.parent / "preflight" / "last_known_ip.txt"


# ── BrokerProbe: thin wrapper over a standalone authenticated kite session ───────
class BrokerProbe:
    def __init__(self, kite: Any):
        self._kite = kite

    def profile(self) -> dict:
        return self._kite.profile()

    def margins(self) -> dict:
        return self._kite.margins(segment="equity")

    def orders(self) -> list:
        return self._kite.orders()


def build_broker_probe(ctx: CheckContext) -> Optional[BrokerProbe]:
    """Standalone session from the on-disk token. None in paper mode, or when the
    token / api-key / kiteconnect is unavailable (caller SKIPs the live checks)."""
    if ctx.is_paper:
        return None
    token = _read_access_token(ctx)
    if not token:
        return None
    api_key = os.environ.get(f"ZERODHA_API_KEY_{ctx.account}")
    if not api_key:
        return None
    try:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(token)
        return BrokerProbe(kite)
    except Exception:
        return None


def _probe(ctx: CheckContext) -> Optional[BrokerProbe]:
    return ctx.extra.get("broker_probe")


# ── file/network checks ─────────────────────────────────────────────────────────
class TokenFileExistsCheck(Check):
    name = "kite_token_file_exists"
    group = "Broker"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 5

    def run(self, ctx: CheckContext) -> CheckResult:
        if ctx.is_paper:
            return self._skipped("paper mode — no broker token")
        p = _token_path(ctx)
        return self._passed(str(p)) if p.exists() else self._failed(f"token file missing: {p}")


class TokenFreshCheck(Check):
    name = "kite_token_fresh_today"
    group = "Broker"
    criticality = Criticality.CRITICAL
    auto_fixable = True
    fix_action = "trigger_token_refresh"
    expected_duration_ms = 5

    def run(self, ctx: CheckContext) -> CheckResult:
        if ctx.is_paper:
            return self._skipped("paper mode — no broker token")
        p = _token_path(ctx)
        if not p.exists():
            return self._failed(f"token file missing: {p}")
        mdate = _file_mdate(p)
        if mdate == ctx.as_of_date:
            return self._passed(f"token minted today ({mdate})")
        return self._failed(f"token stale (minted {mdate}, today {ctx.as_of_date})")

    def fix(self, ctx: CheckContext) -> FixResult:
        before = str(_file_mdate(_token_path(ctx)))
        ok = _trigger_token_refresh()
        after = str(_file_mdate(_token_path(ctx)))
        fresh = ok and _file_mdate(_token_path(ctx)) == ctx.as_of_date
        return FixResult(fresh, action=self.fix_action, before_state=f"mdate={before}",
                         after_state=f"mdate={after}",
                         error_msg="" if fresh else "auto_refresh_token did not produce a fresh token")


class InstrumentsFreshCheck(Check):
    name = "kite_instruments_fresh"
    group = "Broker"
    criticality = Criticality.CRITICAL
    auto_fixable = True
    fix_action = "run_refresh_instruments"
    expected_duration_ms = 5

    def _path(self, ctx: CheckContext) -> Path:
        return ctx.config_dir / "instruments.csv"

    def run(self, ctx: CheckContext) -> CheckResult:
        # Graded by TRADING days behind (Fix option c, 21-Jun): td 0 = refreshed
        # today PASS; td 1 = last trading day, today's 09:00 refresh pending -> WARN
        # (no false-CRITICAL at Phase A 08:30 / on Mondays); td >= 2 = a refresh was
        # actually missed -> CRITICAL.
        p = self._path(ctx)
        if not p.exists():
            return self._failed(f"instruments.csv missing: {p}")
        mdate = _file_mdate(p)
        if mdate is None:
            return self._warn("could not read instruments.csv mtime")
        td = _trading_days_behind(mdate, ctx.as_of_date, ctx.config_dir)
        if td <= 0:
            return self._passed(f"instruments refreshed today ({mdate})", trading_days_behind=td)
        if td == 1:
            return self._warn(
                f"instruments from last trading day ({mdate}); today's 09:00 refresh pending",
                trading_days_behind=td)
        return self._failed(
            f"instruments stale: {td} trading days behind (refreshed {mdate})",
            trading_days_behind=td)

    def fix(self, ctx: CheckContext) -> FixResult:
        before = str(_file_mdate(self._path(ctx)))
        ok = _trigger_instruments_refresh(ctx.account)
        fresh = ok and _file_mdate(self._path(ctx)) == ctx.as_of_date
        return FixResult(fresh, action=self.fix_action, before_state=f"mdate={before}",
                         after_state=f"mdate={_file_mdate(self._path(ctx))}",
                         error_msg="" if fresh else "refresh_instruments did not refresh today")


class VmIpUnchangedCheck(Check):
    """Public IP vs the last value pre-flight recorded. A change => Kite's order
    IP-allowlist will 403 until Rama updates the dev console. Fix: NONE (Rama's
    external step) -- pre-flight just records the baseline + alerts on change."""

    name = "vm_ip_unchanged"
    group = "Broker"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 5000

    def run(self, ctx: CheckContext) -> CheckResult:
        ip = _get_public_ip()
        if not ip:
            return self._warn("could not determine public IP")
        store = _stored_ip_path(ctx)
        prev = None
        try:
            prev = store.read_text(encoding="utf-8").strip()
        except OSError:
            prev = None
        if prev and prev != ip:
            return self._failed(
                f"public IP changed {prev} → {ip} — update the Kite dev-console "
                "order IP-allowlist", current_ip=ip, previous_ip=prev)
        if prev == ip:
            return self._passed(f"public IP unchanged ({ip})", current_ip=ip)
        # no baseline yet -> record it (unless dry-run) and pass
        if not ctx.dry_run:
            try:
                store.parent.mkdir(parents=True, exist_ok=True)
                store.write_text(ip, encoding="utf-8")
            except OSError:
                pass
        return self._passed(f"recorded baseline public IP ({ip})", current_ip=ip)


class HolidayTodayCheck(Check):
    name = "holiday_today_check"
    group = "Broker"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 20

    def run(self, ctx: CheckContext) -> CheckResult:
        try:
            from utils.holiday_guard import is_trading_day, get_holiday_name
            if is_trading_day(ctx.as_of_date, ctx.config_dir):
                return self._passed(f"{ctx.as_of_date} is a trading day")
            name = get_holiday_name(ctx.as_of_date, ctx.config_dir) or "weekend/holiday"
            return self._failed(f"{ctx.as_of_date} is NOT a trading day ({name})")
        except Exception as exc:
            return self._warn(f"holiday calendar unavailable: {exc}")


# ── live broker-call checks (via injected BrokerProbe; SKIP when unavailable) ─────
class ProfileCallCheck(Check):
    name = "kite_profile_call_ok"
    group = "Broker"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 1500

    def run(self, ctx: CheckContext) -> CheckResult:
        if ctx.is_paper:
            return self._skipped("paper mode — no live broker")
        probe = _probe(ctx)
        if probe is None:
            return self._skipped("no broker session (token missing/invalid)")
        try:
            prof = probe.profile()
            uid = (prof or {}).get("user_id", "?")
            return self._passed(f"kite.profile() ok (user {uid}) — token valid at API")
        except Exception as exc:
            return self._failed(f"kite.profile() failed: {exc}")


class MarginsCallCheck(Check):
    name = "kite_margin_call_ok"
    group = "Broker"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 1500

    def run(self, ctx: CheckContext) -> CheckResult:
        if ctx.is_paper:
            return self._skipped("paper mode — no live broker")
        probe = _probe(ctx)
        if probe is None:
            return self._skipped("no broker session (token missing/invalid)")
        try:
            probe.margins()
            return self._passed("kite.margins() ok")
        except Exception as exc:
            return self._failed(f"kite.margins() failed: {exc}")


class FundsAvailableCheck(Check):
    """Broker cash: is the field alive, AND is there enough to begin a day.

    Those are two questions and this check only ever asked the first. FIX 2
    (10-Aug-2026) adds the second, at Rama's ₹2,000, without disturbing the
    first: `cash <= 0` still fails with exactly the wording it always had.

    PARITY — LIVE-ONLY BY CONSTRUCTION, not by convention: `ctx.is_paper`
    returns SKIPPED on the first line, before any broker call, so paper can
    never reach either predicate. No paper rehearsal of the floor is possible.

    ALERT-ONLY. `Criticality.CRITICAL` here sets how LOUD a failure is, not
    whether anything stops: `report.py:44-51` uses is_blocking to roll up the
    REPORT, and nothing in the boot chain reads the pre-flight sentinel
    (main.py's only touchpoint launches a missed phase detached and never reads
    a result). So a ₹209.80 morning is reported loudly and still starts.
    """

    name = "kite_funds_available"
    group = "Broker"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 1500

    def run(self, ctx: CheckContext) -> CheckResult:
        if ctx.is_paper:
            return self._skipped("paper mode — no live broker")
        probe = _probe(ctx)
        if probe is None:
            return self._skipped("no broker session (token missing/invalid)")
        try:
            m = probe.margins() or {}
            cash = (((m.get("available") or {}).get("cash")) if isinstance(m, dict) else None)
            if cash is None:
                cash = (m.get("net") if isinstance(m, dict) else None)
            if cash is None:
                return self._warn("margins returned no cash field")
            cash = float(cash)
            floor = _min_broker_cash_rs(ctx.config_dir)
            # LIVENESS — unchanged wording, unchanged predicate.
            if cash <= EXPECTED_MIN_CASH:
                return self._failed(f"no funds available (cash ₹{cash:,.0f})",
                                    cash=cash, min_broker_cash_rs=floor)
            # ADEQUACY — the question this check did not ask.
            if cash < floor:
                return self._failed(
                    f"broker cash ₹{cash:,.0f} is below the ₹{floor:,.0f} startup "
                    f"floor — not a tradeable account this morning",
                    cash=cash, min_broker_cash_rs=floor)
            return self._passed(
                f"funds available: ₹{cash:,.0f} (floor ₹{floor:,.0f})",
                cash=cash, min_broker_cash_rs=floor)
        except Exception as exc:
            return self._failed(f"funds check failed: {exc}")


class OrdersEndpointCheck(Check):
    name = "kite_orders_endpoint"
    group = "Broker"
    criticality = Criticality.WARN
    expected_duration_ms = 1500

    def run(self, ctx: CheckContext) -> CheckResult:
        if ctx.is_paper:
            return self._skipped("paper mode — no live broker")
        probe = _probe(ctx)
        if probe is None:
            return self._skipped("no broker session (token missing/invalid)")
        try:
            orders = probe.orders()
            return self._passed(f"kite.orders() ok ({len(orders)} today)")
        except Exception as exc:
            return self._warn(f"kite.orders() failed: {exc}")


CHECKS = [
    TokenFileExistsCheck(),
    TokenFreshCheck(),
    InstrumentsFreshCheck(),
    VmIpUnchangedCheck(),
    HolidayTodayCheck(),
    ProfileCallCheck(),
    MarginsCallCheck(),
    FundsAvailableCheck(),
    OrdersEndpointCheck(),
]
