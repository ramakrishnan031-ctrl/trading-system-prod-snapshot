"""
main.py -- Trading System v2 Entry Point

Single orchestration script.  Wires all subsystems, runs startup phases,
manages the runtime loop, and performs clean shutdown.

Locked Decisions: MAIN1-MAIN25, SU1-SU20.
Invocation: python main.py [--mode paper|live] [--resume] [--config PATH]
                           [--status] [--dry-run] [--version] [--interactive]

Exit codes:
    0  clean shutdown OR holiday/weekend (system not started)
    1  TradingSystemError
    2  unexpected exception
    3  startup check failure (blocking)
    4  HALT scenario without --resume
    5  invalid args / config / missing env var
    6  token missing or expired (non-interactive live)
    7  live mode confirmation cancelled
    8  account selection cancelled
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import core.time_authority as time_authority
from alerts.telegram_notifier import TelegramNotifier
from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from broker.order_state_machine import OrderStateMachine
from broker.product_resolver import ProductResolver
from broker.rate_limiter import RateLimiter
from broker.zerodha_adapter import ZerodhaAdapter
from capital.fund_manager import FundManager
from capital.kill_switch import KillSwitch
from capital.position_sizer import PositionSizer
from capital.risk_engine import RiskEngine
from core.account_registry import AccountRegistry
from core.config_loader import load_all
from core.events import EventBus, CapitalDriftDetected, KillSwitchActivated
from core.exceptions import TradingSystemError
from core.instrument_cache import InstrumentCache
from core.logger import get_logger, setup_logging
from core.market_windows import MarketWindows
from core.state_store import StateStore
from data.candle_store import CandleStore
from data.live_feed import LiveFeedManager
from orders.eod_squareoff import EodSquareoff
from orders.shadow_tracker import ShadowTracker
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import LimitTripleProtocol
from orders.order_reconciler import OrderReconciler
from orders.smart_tgt_manager import SmartTgtManager
from screening.entry_gate import EntryGate, WatchEntry
from screening.quality_scorer import QualityScorer
from screening.secondary_screener import SecondaryScreener
from screening.step_executor import StepExecutor
from signals.signal_processor import SignalProcessor
from signals.webhook_receiver import WebhookReceiver
from strategies.loader import StrategyLoader
from utils.holiday_guard import is_trading_day, next_trading_day
from utils.startup_checks import (
    StartupScenario,
    check_config_hash,
    check_webhook_endpoint,
    detect_startup_scenario,
    run_all_startup_checks,
)
from scripts.zerodha_login import is_token_valid, load_token

VERSION = "2.0.0"

_IST = timezone(timedelta(hours=5, minutes=30))

# Set once in Phase 0a; used in top-level exception handler (MAIN3)
_log: logging.Logger = logging.getLogger("main")

# Set by signal handlers; main thread blocks on this
_shutdown_event = threading.Event()


# ─────────────────────────────────────────────────────────────────────────────
# CLI (MAIN2)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Trading System v2",
    )
    parser.add_argument(
        "--mode", choices=["paper", "live"], default="paper",
        help="Trading mode (default: paper)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Clear HARD_KILL/SOFT_KILL and allow startup from HALT scenario",
    )
    parser.add_argument(
        "--config", metavar="PATH", default=None,
        help="Override config/ directory path",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print subsystem status and exit 0",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Run startup checks only; no trading",
    )
    parser.add_argument(
        "--version", action="store_true",
        help="Print version and exit 0",
    )
    parser.add_argument(
        "--interactive", action="store_true",
        help="Interactive startup: account selector, login, mode picker (SU4)",
    )
    return parser.parse_args(argv)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _http_fetch(url: str, timeout_sec: float = 5.0):
    """Minimal stdlib HTTP fetcher for startup checks."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as resp:
            body = resp.read(256).decode("utf-8", errors="replace")
            return resp.status, body
    except Exception as exc:
        return None, str(exc)


def _init_time_authority(app_config, kill_switch) -> None:
    """Configure time_authority thresholds and critical-skew callback (MAIN21)."""
    clock_cfg = app_config.system.clock

    def _on_critical_skew(skew: float, reason: str) -> None:
        _log.critical("Clock skew critical: %.1fs -- %s", skew, reason)
        kill_switch.soft_kill(
            reason=f"clock_skew_critical: {reason}", triggered_by="time_authority"
        )

    time_authority.configure(
        thresholds={
            "warn_sec":    clock_cfg.warn_skew_sec,
            "alert_sec":   clock_cfg.alert_skew_sec,
            "halt_sec":    clock_cfg.halt_skew_sec,
            "startup_max_sec": clock_cfg.startup_max_skew_sec,
        },
        on_critical_skew=_on_critical_skew,
    )


def _build_kite_client(app_config):
    """Construct KiteConnect with env-var credentials and timeout (MAIN19)."""
    from kiteconnect import KiteConnect  # type: ignore[import]
    timeout = app_config.broker_limits.timeouts.read_sec
    kite = KiteConnect(api_key=os.environ["ZERODHA_API_KEY"], timeout=timeout)
    kite.set_access_token(os.environ["ZERODHA_ACCESS_TOKEN"])
    return kite


def _load_holidays(app_config) -> set:
    """Convert NseHolidaysConfig string list to set[date] (MAIN20)."""
    return {date.fromisoformat(d) for d in app_config.nse_holidays.holidays}


def _make_paper_quote_provider():
    """
    Return a simple quote_provider for paper mode (BLOCKER #3 fix).

    In paper mode ZerodhaAdapter.get_quote() delegates to this callable.
    We return a trivial provider that yields a neutral Quote(ltp=100) for
    any symbol so that screener/risk code doesn't raise NotImplementedError.
    In a real paper-trading session the operator should replace this with a
    live-data feed; this stub prevents crashes when no live data is wired.
    """
    from datetime import timedelta, timezone as _tz
    from broker.zerodha_adapter import Quote

    _IST = _tz(timedelta(hours=5, minutes=30), name="IST")

    def _provider(symbols):
        from core.time_authority import now_ist
        ts = now_ist()
        return {
            sym: Quote(
                symbol=sym,
                last_price=100.0,
                bid=99.9,
                ask=100.1,
                volume=100_000,
                ts=ts,
            )
            for sym in symbols
        }

    return _provider


def _write_session(store: StateStore, session_date: str, mode: str,
                   kill_state: str, config_hash: str, now_iso: str) -> None:
    """INSERT OR REPLACE the single session row (id=1)."""
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO session
                (id, session_date, account_id, broker, mode, trade_type,
                 kill_state, last_config_hash, session_start, last_updated)
            VALUES
                (1, ?, 'default', ?, ?, 'INTRADAY', ?, ?, ?, ?)
            """,
            (session_date, mode if mode == "paper" else "zerodha",
             mode.upper(), kill_state, config_hash, now_iso, now_iso),
        )


def _print_status(store: StateStore, kill_switch: KillSwitch,
                  scenario_result) -> int:
    """Print subsystem status and return 0 (MAIN16)."""
    ks = kill_switch.status()
    session = store.get_session_row()
    print(f"Scenario       : {scenario_result.scenario.value}")
    print(f"Kill state     : {ks.get('state', 'UNKNOWN')}")
    print(f"Kill reason    : {ks.get('reason', '')}")
    if session:
        print(f"Session date   : {session['session_date']}")
        print(f"Mode           : {session['mode']}")
    else:
        print("Session        : no session row")
    print(f"Version        : {VERSION}")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Callbacks (MAIN18)
# ─────────────────────────────────────────────────────────────────────────────

def _make_critical_failure_cb(kill_switch: KillSwitch, notifier: Optional[TelegramNotifier]):
    def _on_critical_failure(source: str, reason: str) -> None:
        _log.critical("Critical failure from %s: %s", source, reason)
        kill_switch.soft_kill(
            reason=f"{source}: {reason}", triggered_by="auto"
        )
        if notifier is not None:
            try:
                notifier.send(
                    tier="CRITICAL",
                    title="Critical failure",
                    body=f"{source}: {reason}",
                    source="main",
                )
            except Exception as ne:
                _log.error("notifier.send failed in critical callback: %s", ne)
    return _on_critical_failure


def _make_orphan_cb(kill_switch: KillSwitch, notifier: Optional[TelegramNotifier]):
    def _on_orphan(order_id: str, reason: str) -> None:
        _log.critical("Orphan order %s: %s", order_id, reason)
        kill_switch.soft_kill(
            reason=f"orphan_order:{order_id}:{reason}", triggered_by="auto"
        )
        if notifier is not None:
            try:
                notifier.send(
                    tier="CRITICAL",
                    title="Orphan order detected",
                    body=f"order_id={order_id} reason={reason}",
                    source="main",
                )
            except Exception as ne:
                _log.error("notifier.send failed in orphan callback: %s", ne)
    return _on_orphan


def _make_gate_release_cb(signal_processor: SignalProcessor):
    def _on_gate_release(entry: WatchEntry, reason: str) -> None:
        if reason == "PRICE_HIT":
            _log.info(
                "Gate PRICE_HIT for %s signal_id=%s -- continuing pipeline",
                entry.symbol, entry.signal_id,
            )
            signal_processor.continue_from_gate(entry)
        else:
            _log.info(
                "Gate release %s for %s signal_id=%s -- no action",
                reason, entry.symbol, entry.signal_id,
            )
    return _on_gate_release


# ─────────────────────────────────────────────────────────────────────────────
# Signal handlers (MAIN13)
# ─────────────────────────────────────────────────────────────────────────────

def _install_signal_handlers() -> None:
    def _handler(signum, frame):
        _log.info("Signal %s received -- initiating shutdown", signum)
        _shutdown_event.set()

    signal.signal(signal.SIGINT, _handler)
    try:
        signal.signal(signal.SIGTERM, _handler)
    except (OSError, ValueError):
        pass  # Windows may not support SIGTERM


# ─────────────────────────────────────────────────────────────────────────────
# Event-bus log handlers (MAIN9)
# ─────────────────────────────────────────────────────────────────────────────

def _log_kill_switch_event(event) -> None:
    _log.critical(
        "KillSwitchActivated: %s -> %s reason=%s",
        getattr(event, "previous_state", "?"),
        getattr(event, "new_state", "?"),
        getattr(event, "reason", ""),
    )


def _log_capital_drift_event(event) -> None:
    _log.warning(
        "CapitalDriftDetected: expected=%.2f actual=%.2f delta=%.2f",
        getattr(event, "expected", 0),
        getattr(event, "actual", 0),
        getattr(event, "delta", 0),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shutdown (MAIN15)
# ─────────────────────────────────────────────────────────────────────────────

def _shutdown(
    *,
    signal_proc: SignalProcessor,
    entry_gate: EntryGate,
    smart_tgt: SmartTgtManager,
    order_reconciler: OrderReconciler,
    order_monitor: OrderMonitor,
    live_feed: LiveFeedManager,
    candle_store: CandleStore,
    notifier: TelegramNotifier,
    store: StateStore,
) -> None:
    """Reverse-order shutdown (MAIN15)."""
    _log.info("Shutdown initiated")
    try:
        signal_proc.stop()
    except Exception as exc:
        _log.error("signal_processor.stop error: %s", exc)
    try:
        entry_gate.stop()
    except Exception as exc:
        _log.error("entry_gate.stop error: %s", exc)
    # Flask has no clean stop (daemon thread dies on process exit)
    try:
        smart_tgt.stop()
    except Exception as exc:
        _log.error("smart_tgt.stop error: %s", exc)
    try:
        order_reconciler.stop()
    except Exception as exc:
        _log.error("order_reconciler.stop error: %s", exc)
    try:
        order_monitor.stop()
    except Exception as exc:
        _log.error("order_monitor.stop error: %s", exc)
    try:
        live_feed.disconnect()
    except Exception as exc:
        _log.error("live_feed.disconnect error: %s", exc)
    try:
        candle_store.stop()
    except Exception as exc:
        _log.error("candle_store.stop error: %s", exc)
    try:
        notifier.send(
            tier="INFO",
            title="System stopping",
            body=f"Version {VERSION}",
            source="main",
        )
    except Exception as exc:
        _log.error("notifier.send at shutdown: %s", exc)
    now_iso = time_authority.now_ist_iso()
    try:
        store.insert_system_event(
            event_type="SHUTDOWN",
            timestamp=now_iso,
        )
    except Exception as exc:
        _log.error("SHUTDOWN event write failed: %s", exc)
    try:
        store.close()
    except Exception as exc:
        _log.error("store.close error: %s", exc)
    _log.info("Shutdown complete")


# ─────────────────────────────────────────────────────────────────────────────
# Interactive startup helpers (SU7-SU13)
# ─────────────────────────────────────────────────────────────────────────────

def _interactive_select_account(registry, input_fn=input):
    """
    Display enabled accounts and prompt for selection (SU8).
    Returns selected AccountRow. Exits 8 on 'q'.
    """
    from core.account_registry import AccountRow
    accounts = registry.get_enabled_accounts()
    print()
    print("  Available accounts:")
    print("  #  Account     Broker    Label")
    for i, acct in enumerate(accounts, 1):
        flag = " *" if acct.is_primary else ""
        print(f"  {i}  {acct.account_id:<10}  {acct.broker:<8}  {acct.label}{flag}")
    print()
    while True:
        raw = input_fn("  Select account [1]: ").strip()
        if raw == "":
            raw = "1"
        if raw.lower() == "q":
            print("  Startup cancelled.")
            sys.exit(8)
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(accounts):
                selected = accounts[idx]
                print(f"  Selected: {selected.account_id} ({selected.broker} - {selected.label})")
                return selected
        except ValueError:
            pass
        print(f"  Invalid selection. Enter 1-{len(accounts)} or 'q' to quit.")


def _interactive_check_or_login(account, token_path, today, input_fn=input):
    """
    Check existing token; prompt to reuse or re-login (SU9-SU10).
    Returns access_token string.
    """
    from scripts.zerodha_login import (
        is_token_valid as _is_valid,
        load_token as _load,
        run_login_flow,
    )
    # Check for wrong-account token
    existing = _load(token_path)
    if existing and existing.get("account_id") != account.account_id:
        print(f"  !! Token belongs to {existing.get('account_id')}, not {account.account_id} !!")
        print("  !! Re-login required. !!")

    if _is_valid(account.account_id, token_path):
        saved_at = _load(token_path).get("saved_at", "unknown")
        print(f"  Token found for {account.account_id}, generated at {saved_at}.")
        raw = input_fn("  Use existing token? [Y/n]: ").strip().lower()
        if raw in ("", "y"):
            return _load(token_path)["access_token"]

    # Need fresh login
    api_key = os.environ.get(account.api_key_env)
    api_secret = os.environ.get(account.api_secret_env)
    if not api_key:
        print(f"  Error: env var {account.api_key_env!r} not set.")
        sys.exit(5)
    if not api_secret:
        print(f"  Error: env var {account.api_secret_env!r} not set.")
        sys.exit(5)

    return run_login_flow(
        account_id=account.account_id,
        broker=account.broker,
        api_key=api_key,
        api_secret=api_secret,
        token_path=token_path,
        input_fn=input_fn,
    )


def _interactive_select_mode(account, input_fn=input):
    """Prompt for paper/live mode (SU11). Returns 'paper' or 'live'."""
    paper_cap = f"Rs {account.paper_capital:,.0f}"
    print()
    print("  Select mode:")
    print(f"  1. PAPER  (capital: {paper_cap} from config)")
    print("  2. LIVE   (capital: syncs from broker)")
    raw = input_fn("  Select [1]: ").strip()
    if raw in ("", "1"):
        return "paper"
    if raw == "2":
        return "live"
    return "paper"


def _interactive_confirm_live(account, broker_adapter, input_fn=input):
    """
    Show live confirmation screen and require exact phrase (SU12).
    Returns broker net capital on confirm. Exits 7 on wrong phrase.
    """
    margins = broker_adapter.get_margins()
    capital = margins.net
    print()
    print("  *** LIVE MODE SELECTED ***")
    print()
    print(f"  Account:        {account.account_id} ({account.label})")
    print(f"  Broker capital: Rs {capital:,.2f}")
    print()
    print("  WARNING: Real money will be traded.")
    print()
    phrase = input_fn("  Type 'CONFIRM LIVE' to proceed: ").strip()
    if phrase != "CONFIRM LIVE":
        print("  Live confirmation cancelled.")
        sys.exit(7)
    return capital


def _print_welcome_banner(account, mode, capital, today):
    """Print the welcome banner after interactive flow completes (SU13)."""
    print()
    print("=" * 60)
    print("  TRADING SYSTEM v2 -- STARTING")
    print("=" * 60)
    print()
    print(f"  Welcome, {account.label}!")
    print()
    print(f"  Today:   {today.strftime('%d-%b-%Y (%A)')}")
    print(f"  Broker:  {account.broker}")
    print(f"  Account: {account.account_id} ({account.label})")
    print(f"  Mode:    {mode.upper()}")
    print(f"  Capital: Rs {capital:,.2f}")
    print()
    print("  Starting subsystems...")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# main() (MAIN1-MAIN15)
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[list] = None) -> int:  # noqa: C901
    global _log

    # ── Phase 0a: Logger setup (MAIN4) ──────────────────────────────────────
    try:
        args = _parse_args(argv)
    except SystemExit as se:
        return 5 if se.code != 0 else 0

    if args.version:
        print(f"Trading System v{VERSION}")
        return 0

    # ── Holiday guard (SU6) -- BEFORE setup_logging; zero log on non-trading day
    config_dir = Path(args.config) if args.config else Path("config")
    today = date.today()
    try:
        if not is_trading_day(today, config_dir):
            _next = next_trading_day(today, config_dir)
            print("=" * 60)
            print(f"  Market closed today: {today} ({today.strftime('%A')})")
            print(f"  Next trading day: {_next}")
            print("  System not started. No logs created.")
            print("=" * 60)
            return 0
    except FileNotFoundError:
        pass  # missing holiday file: proceed with startup

    setup_logging(Path("logs"))
    _log = get_logger("main")
    _log.info("Trading System v%s starting (mode=%s)", VERSION, args.mode)

    # ── Phase 0b: Config load (MAIN5) ───────────────────────────────────────
    try:
        app_config = load_all(config_dir)
    except Exception as exc:
        _log.critical("Config load failed: %s", exc)
        return 5
    _log.info("Config loaded from %s (%d files)", config_dir, len(app_config.file_hashes))

    # ── Phase 0c: StateStore + EventBus + KillSwitch + TimeAuthority (MAIN6) ─
    store = StateStore(Path("data_store/trading_system.db"))
    event_bus = EventBus()

    kill_switch = KillSwitch(
        store,
        event_bus,
        get_logger("kill_switch"),
        api_failure_threshold=app_config.system.kill_switch.api_failure_threshold,
        enable_auto_trip=app_config.system.kill_switch.enable_auto_trip,
    )

    # TimeAuthority needs kill_switch for the critical-skew callback
    _init_time_authority(app_config, kill_switch)
    today_ist_date: date = time_authority.now_ist().date()
    today_iso: str = today_ist_date.isoformat()

    scenario_result = detect_startup_scenario(store, kill_switch, today_ist_date, _log)
    scenario = scenario_result.scenario

    if scenario == StartupScenario.COLD:
        _log.info("Startup scenario: COLD (cold start)")
    elif scenario == StartupScenario.WARM:
        _log.info("Startup scenario: WARM (warm restart)")
    elif scenario == StartupScenario.CRASH:
        _log.critical("Startup scenario: CRASH -- crash detected")
        store.insert_system_event(
            event_type="CRASH_DETECTED",
            timestamp=time_authority.now_ist_iso(),
            scenario="CRASH",
            details="No SHUTDOWN marker found for today",
        )
    elif scenario == StartupScenario.HALT:
        if not args.resume:
            _log.critical(
                "Startup scenario: HALT -- kill switch active; "
                "use --resume to clear"
            )
            return 4
        kill_switch.resume(reason="--resume flag", resumed_by="operator")
        _log.critical("HALT cleared via --resume flag")

    # CL4 config-hash diff check (MAIN6)
    hash_result = check_config_hash(store, app_config, _log)
    config_hash_changed = hash_result.changed

    # ── --status mode (MAIN16) ──────────────────────────────────────────────
    if args.status:
        return _print_status(store, kill_switch, scenario_result)

    # MED #10: Write session row early — before any crash-prone Phase 0d/0e code.
    # If startup crashes mid-way, the next run will still find a session row with
    # today's date, so cold-start detection remains correct.
    # The Phase 0h call below will UPDATE this row with final kill_state/config_hash.
    _write_session(
        store=store,
        session_date=today_iso,
        mode=args.mode,
        kill_state=kill_switch.current_state().value,
        config_hash=json.dumps(app_config.file_hashes),
        now_iso=time_authority.now_ist_iso(),
    )

    # ── Phase 0d: Startup checks (MAIN7) ────────────────────────────────────
    holidays = _load_holidays(app_config)
    market_windows = MarketWindows(holidays=holidays)

    # Build broker adapter early for clock check (paper mode skips live calls)
    state_machine = OrderStateMachine(bus=event_bus)
    rate_limiter = RateLimiter(app_config.broker_limits)
    product_resolver = ProductResolver(app_config.system.product_map)
    cost_calculator = CostCalculator(app_config.broker_costs)

    if args.mode == "paper":
        kite_client = None  # paper adapter does not call kite
    else:
        kite_client = _build_kite_client(app_config)

    is_paper = (args.mode == "paper")
    paper_capital = getattr(app_config.system, "paper_capital", 500_000.0)

    broker_adapter = ZerodhaAdapter(
        kite_client=kite_client,
        rate_limiter=rate_limiter,
        product_resolver=product_resolver,
        cost_calculator=cost_calculator,
        state_machine=state_machine,
        logger=get_logger("zerodha_adapter"),
        paper_mode=is_paper,
        paper_capital=paper_capital,
        # paper mode needs a quote_provider so get_quote() doesn't raise
        # NotImplementedError.  A simple passthrough using the Kite HTTP API
        # is sufficient for paper; live mode ignores this kwarg entirely.
        quote_provider=(_make_paper_quote_provider() if is_paper else None),
    )

    required_secrets = ["ZERODHA_API_KEY", "ZERODHA_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN"]

    report = run_all_startup_checks(
        state_store=store,
        kill_switch=kill_switch,
        time_authority=time_authority,
        broker_adapter=broker_adapter,
        market_windows=market_windows,
        app_config=app_config,
        scan_webhook_map=app_config.scan_webhook_map,
        chartink_scanners=app_config.chartink_scanners,
        http_fetcher_fn=_http_fetch,
        webhook_url=None,  # Flask not started yet
        required_secrets=required_secrets,
        config_dir=config_dir,
        logger=_log,
    )

    if args.dry_run:
        _log.info("--dry-run: startup checks complete")
        print(f"Startup report: ok={report.ok}")
        print(f"  Blocking failures: {report.blocking_failures}")
        print(f"  Warnings         : {report.warnings}")
        return 0 if report.ok else 3

    if not report.ok:
        _log.critical(
            "Startup checks failed: %s", report.blocking_failures
        )
        return 3

    # ── Phase 0e-pre: Load instrument + account data (Module 38) ────────────
    try:
        instrument_cache = InstrumentCache.load(config_dir / "instruments.csv")
        _log.info(
            "InstrumentCache loaded: %d instruments", instrument_cache.count()
        )
    except Exception as exc:
        _log.critical("Failed to load instruments.csv: %s", exc)
        return 3

    try:
        account_registry = AccountRegistry.load(config_dir / "accounts.csv")
        _log.info(
            "AccountRegistry loaded: %d accounts (primary: %s)",
            account_registry.count(),
            account_registry.primary().account_id,
        )
    except Exception as exc:
        _log.critical("Failed to load accounts.csv: %s", exc)
        return 3

    # ── Account selection + token handling (SU4, SU8-SU14) ──────────────────
    _token_path = Path("data_store/session/zerodha_token.json")
    _access_token: Optional[str] = None

    if getattr(args, "interactive", False):
        # Interactive flow: SU7 banner, account picker, token, mode, capital
        print("=" * 60)
        print("  TRADING SYSTEM v2 -- INTERACTIVE STARTUP")
        print("=" * 60)
        print()
        print(f"  Today: {today.strftime('%d-%b-%Y (%A)')} -- Trading day")
        print()
        selected_account = _interactive_select_account(account_registry)
        _access_token = _interactive_check_or_login(
            selected_account, _token_path, today
        )
        # Mode selection overrides --mode if interactive
        args.mode = _interactive_select_mode(selected_account)
    else:
        # Non-interactive (SU14): use primary account
        selected_account = account_registry.primary()
        if args.mode == "live":
            if not is_token_valid(selected_account.account_id, _token_path):
                _log.critical(
                    "Token missing or expired for %s. Run: python scripts/zerodha_login.py",
                    selected_account.account_id,
                )
                return 6
            _token_data = load_token(_token_path)
            _access_token = _token_data["access_token"]

    # Inject resolved access_token into env so existing broker/feed code can read it
    if _access_token:
        os.environ["ZERODHA_ACCESS_TOKEN"] = _access_token

    # ── Phase 0e: Construct subsystems (MAIN8) ───────────────────────────────
    alert_cfg = app_config.system.alerts
    tg_cfg = alert_cfg.telegram
    from alerts.telegram_notifier import ChannelConfig as _ChannelConfig
    _tg_channels = [
        _ChannelConfig(
            chat_id_env=ch.chat_id_env,
            label=ch.label,
            enabled=ch.enabled,
        )
        for ch in tg_cfg.channels
    ]
    notifier = TelegramNotifier(
        bot_token=os.environ[tg_cfg.bot_token_env],
        channels=_tg_channels,
        failed_alerts_log_path=alert_cfg.failed_alerts_log_path,
        sentinel_dir=alert_cfg.sentinel_dir,
        logger=get_logger("telegram_notifier"),
        paper_mode=(args.mode == "paper"),
    )

    # Alert operator about config hash change now that notifier is ready
    if config_hash_changed:
        store.insert_system_event(
            event_type="CONFIG_DIFF",
            timestamp=time_authority.now_ist_iso(),
            details=json.dumps({"changed_files": hash_result.changed_files}),
        )
        try:
            notifier.send(
                tier="WARN",
                title="Config files changed since last session",
                body=f"Changed: {hash_result.changed_files}",
                source="main",
            )
        except Exception as exc:
            _log.error("Config diff alert failed: %s", exc)

    cap_cfg = app_config.system.capital
    leverage_map = {
        "INTRADAY":      cap_cfg.leverage_map.INTRADAY,
        "COVER_ORDER":   cap_cfg.leverage_map.COVER_ORDER,
        "DELIVERY":      cap_cfg.leverage_map.DELIVERY,
        "BRACKET_ORDER": cap_cfg.leverage_map.BRACKET_ORDER,
    }

    fund_manager = FundManager(
        state_store=store,
        bus=event_bus,
        logger=get_logger("fund_manager"),
        intraday_bucket_pct=cap_cfg.intraday_bucket_pct,
        positional_bucket_pct=cap_cfg.positional_bucket_pct,
        daily_loss_limit=cap_cfg.daily_loss_limit,
        leverage_map=leverage_map,
        on_daily_loss_breach=lambda: kill_switch.soft_kill(
            reason="daily_loss_limit_breached", triggered_by="fund_manager"
        ),
    )
    # SU19: paper mode uses configured paper_capital; live uses broker margins
    if args.mode == "paper":
        _startup_capital = selected_account.paper_capital
    else:
        _startup_capital = broker_adapter.get_margins().net
    fund_manager.initialize(_startup_capital)

    ps_cfg = app_config.system.position_sizing
    position_sizer = PositionSizer(
        fund_manager=fund_manager,
        leverage_map=leverage_map,
        risk_per_trade_pct=ps_cfg.risk_per_trade_pct,
        max_concentration_pct=ps_cfg.max_concentration_pct,
        min_qty_threshold=ps_cfg.min_qty_threshold,
        tier_multipliers={
            "A": ps_cfg.tier_multipliers.A,
            "B": ps_cfg.tier_multipliers.B,
            "C": ps_cfg.tier_multipliers.C,
        },
        logger=get_logger("position_sizer"),
        instrument_cache=instrument_cache,  # IC7: lot_size from cache
    )

    risk_cfg = app_config.system.risk
    risk_engine = RiskEngine(
        fund_manager=fund_manager,
        state_store=store,
        max_open_positions=risk_cfg.max_open_positions,
        max_daily_trades=risk_cfg.max_daily_trades,
        max_sector_exposure_pct=risk_cfg.max_sector_exposure_pct,
        max_consecutive_losses=risk_cfg.max_consecutive_losses,
        daily_loss_limit_pct=risk_cfg.daily_loss_limit_pct,
        # HIGH #2: sector from instrument_cache (was lambda: "UNKNOWN")
        sector_lookup_fn=lambda sym: instrument_cache.sector(sym),
        logger=get_logger("risk_engine"),
        kill_switch=kill_switch,
    )

    live_feed = LiveFeedManager(
        api_key=os.environ.get("ZERODHA_API_KEY", ""),
        access_token=os.environ.get("ZERODHA_ACCESS_TOKEN", ""),
        on_critical_failure=lambda reason: _make_critical_failure_cb(kill_switch, notifier)(
            "live_feed", reason
        ),
        logger=get_logger("live_feed"),
    )

    candle_store = CandleStore(
        logger=get_logger("candle_store"),
        candle_interval_sec=60,
    )

    sh_cfg = app_config.system.shadow_tracker
    shadow_tracker = ShadowTracker(
        state_store=store,
        bus=event_bus,
        live_feed=live_feed,
        market_windows=market_windows,
        time_authority=time_authority,
        notifier=notifier,
        logger=get_logger("shadow_tracker"),
        max_innings=sh_cfg.max_innings,
        alert_per_inning=sh_cfg.alert_per_inning,
        enabled=sh_cfg.enabled,
    )

    # Wire instrument_cache for token->symbol resolution (SH5)
    shadow_tracker.set_instrument_cache(instrument_cache)

    # Tick dispatcher routes to candle_store and shadow_tracker (SH5)
    def _tick_dispatcher(ticks) -> None:
        for t in ticks:
            ts = t.get("timestamp") or time_authority.now_ist()
            candle_store.on_tick(t["instrument_token"], t["last_price"], ts)
            shadow_tracker.on_tick(t)

    live_feed.register_callback(_tick_dispatcher)

    smart_tgt = SmartTgtManager(
        adapter=broker_adapter,
        state_store=store,
        candle_store=candle_store,
        logger=get_logger("smart_tgt_manager"),
        quote_fn=broker_adapter.get_quote,
        enabled=True,
    )

    # Reconnect chain (ST13, MAIN8 step 11)
    live_feed.set_on_reconnect_callback(
        lambda ts: (candle_store.mark_reconnect(ts), smart_tgt.on_reconnect(ts))
    )

    om_cfg = app_config.system.order_monitor
    order_monitor = OrderMonitor(
        adapter=broker_adapter,
        state_machine=state_machine,
        bus=event_bus,
        logger=get_logger("order_monitor"),
        poll_interval_sec=om_cfg.poll_interval_sec,
        fill_timeout_sec=om_cfg.fill_timeout_sec,
        on_orphan_callback=_make_orphan_cb(kill_switch, notifier),
    )

    co_protocol = CoPlusTgtProtocol(
        adapter=broker_adapter,
        logger=get_logger("order_protocol_co"),
    )
    limit_protocol = LimitTripleProtocol(
        adapter=broker_adapter,
        logger=get_logger("order_protocol_limit"),
    )
    full_engine = FullEntryEngine(
        co_protocol=co_protocol,
        limit_protocol=limit_protocol,
        logger=get_logger("full_entry_engine"),
    )
    order_manager = OrderManager(
        state_store=store,
        logger=get_logger("order_manager"),
    )
    order_placer = OrderPlacer(
        entry_engine=full_engine,
        order_manager=order_manager,
        fund_manager=fund_manager,
        bus=event_bus,
        logger=get_logger("order_placer"),
        kill_switch=kill_switch,
        product_resolver=product_resolver,  # HIGH #7: correct product codes in DB
    )
    order_placer.set_instrument_cache(instrument_cache)  # IC8: tick rounding

    rc_cfg = app_config.system.order_reconciler
    order_reconciler = OrderReconciler(
        state_store=store,
        adapter=broker_adapter,
        fund_manager=fund_manager,
        kill_switch=kill_switch,
        notifier=notifier,
        bus=event_bus,
        logger=get_logger("order_reconciler"),
        cfg=rc_cfg,
        quote_fn=broker_adapter.get_quote,
        broker_orders_fn=broker_adapter.get_open_orders,
    )

    strategies_dir = config_dir / "strategies"
    loader = StrategyLoader()
    strategies = loader.load_all_strategies(strategies_dir)

    scorer = QualityScorer(
        weights=app_config.scoring,
        logger=get_logger("quality_scorer"),
    )
    step_executor = StepExecutor(logger=get_logger("step_executor"))
    screener = SecondaryScreener(
        step_executor=step_executor,
        quality_scorer=scorer,
        state_store=store,
        quote_fn=broker_adapter.get_quote,
        logger=get_logger("secondary_screener"),
    )

    eod = EodSquareoff(
        adapter=broker_adapter,
        state_store=store,
        fund_manager=fund_manager,
        state_machine=state_machine,
        bus=event_bus,
        market_windows=market_windows,
        time_authority=time_authority,
        kill_switch=kill_switch,
        logger=get_logger("eod_squareoff"),
        order_monitor=order_monitor,
        inter_order_delay_ms=app_config.system.eod_squareoff.inter_order_delay_ms,
    )

    signal_queue: queue.Queue = queue.Queue(
        maxsize=app_config.system.signal_queue.capacity
    )

    sp_cfg = app_config.system.signal_processor
    signal_processor = SignalProcessor(
        signal_queue=signal_queue,
        state_store=store,
        bus=event_bus,
        fund_manager=fund_manager,
        position_sizer=position_sizer,
        risk_engine=risk_engine,
        kill_switch=kill_switch,
        market_windows=market_windows,
        strategies=strategies,
        scan_webhook_map=app_config.scan_webhook_map.scanners,
        secondary_screener=screener,
        quality_scorer=scorer,
        order_placer=order_placer,
        logger=get_logger("signal_processor"),
        worker_count=sp_cfg.worker_count,
        drain_poll_sec=sp_cfg.drain_poll_sec,
        instrument_cache=instrument_cache,  # IC: lot_size/sector lookup
        atr_fallback_mode=sp_cfg.atr_fallback_mode,  # MED #12
    )

    entry_gate = EntryGate(
        quote_fn=broker_adapter.get_quote,
        logger=get_logger("entry_gate"),
        state_store=store,
        on_release=_make_gate_release_cb(signal_processor),
    )

    webhook_receiver = WebhookReceiver(
        signal_queue=signal_queue,
        state_store=store,
        config=app_config,
        market_windows=market_windows,
        kill_switch=kill_switch,
        logger=get_logger("webhook_receiver"),
        secret_token=os.environ.get("WEBHOOK_SECRET"),
    )

    # ── Phase 0e: Event bus subscriptions (MAIN9) ────────────────────────────
    event_bus.subscribe(KillSwitchActivated, _log_kill_switch_event)
    event_bus.subscribe(CapitalDriftDetected, _log_capital_drift_event)
    # Note: fund_manager has no on_order_filled/on_position_closed handlers;
    # order_reconciler subscribes OrderStateChanged internally in start() (RC4).

    # ── Phase 0f: Startup reconciliation (MAIN10, RC14) ─────────────────────
    recon_actions = order_reconciler.reconcile_once()
    if recon_actions:
        _log.info(
            "Startup reconciliation: %d action(s) taken", len(recon_actions)
        )
    if kill_switch.is_active("any"):
        _log.critical(
            "Kill switch active after startup reconciliation -- aborting"
        )
        store.close()
        return 1

    # ── Phase 0g: Start subsystems (MAIN11) ─────────────────────────────────
    # candle_store.start() BEFORE live_feed.connect() (BLOCKER #2 fix)
    candle_store.start()
    # BLOCKER #11: wire token map so CandleStore can resolve instrument_token -> symbol
    candle_store.set_token_map(instrument_cache.token_map())
    live_feed.connect()
    order_monitor.start()
    order_reconciler.start()
    # signal_processor BEFORE entry_gate (BLOCKER #5 fix): gate may call
    # continue_from_gate() immediately on release; workers must be ready.
    signal_processor.start()
    entry_gate.start()
    smart_tgt.start()

    wh_cfg = app_config.system.webhook
    webhook_thread = threading.Thread(
        target=webhook_receiver.app.run,
        kwargs={
            "host": wh_cfg.bind_host,
            "port": wh_cfg.bind_port,
            "threaded": True,
            "use_reloader": False,
        },
        name="webhook-server",
        daemon=True,
    )
    webhook_thread.start()

    # Post-start webhook self-check (P17, MAIN11)
    time.sleep(2)
    webhook_url = f"http://127.0.0.1:{wh_cfg.bind_port}/health"
    wh_result = check_webhook_endpoint(webhook_url, _http_fetch, _log)
    if not wh_result.reachable:
        _log.critical("Webhook endpoint not reachable at %s", webhook_url)
        _shutdown_event.set()

    # EOD scheduler daemon thread (MAIN14)
    eod_thread = threading.Thread(
        target=eod.start_polling,
        kwargs={"poll_interval_sec": app_config.system.eod_squareoff.poll_interval_sec},
        name="eod-scheduler",
        daemon=True,
    )
    eod_thread.start()

    # ── Phase 0h: Mark startup complete (MAIN12) ────────────────────────────
    now_iso = time_authority.now_ist_iso()
    store.insert_system_event(
        event_type="STARTUP",
        timestamp=now_iso,
        scenario=scenario.value,
        details=json.dumps({"mode": args.mode, "version": VERSION}),
    )
    _write_session(
        store=store,
        session_date=today_iso,
        mode=args.mode,
        kill_state=kill_switch.current_state().value,
        config_hash=json.dumps(app_config.file_hashes),
        now_iso=now_iso,
    )
    try:
        notifier.send(
            tier="INFO",
            title="System started",
            body=f"Scenario: {scenario.value}. Mode: {args.mode}.",
            source="main",
        )
    except Exception as exc:
        _log.error("Startup notification failed: %s", exc)

    _log.info("All subsystems started -- entering runtime loop")

    # Interactive welcome banner + ready message (SU13)
    if getattr(args, "interactive", False):
        _print_welcome_banner(selected_account, args.mode, _startup_capital, today)
        wh_port = app_config.system.webhook.bind_port
        print("[OK] All subsystems started")
        print(f"[OK] Webhook listening on :{wh_port}")
        print("[OK] System ready for signals")
        print()
        print("Press Ctrl+C to initiate clean shutdown.")

    # ── Runtime loop (MAIN13) ────────────────────────────────────────────────
    _install_signal_handlers()
    _shutdown_event.wait()

    # ── Shutdown (MAIN15) ────────────────────────────────────────────────────
    _shutdown(
        signal_proc=signal_processor,
        entry_gate=entry_gate,
        smart_tgt=smart_tgt,
        order_reconciler=order_reconciler,
        order_monitor=order_monitor,
        live_feed=live_feed,
        candle_store=candle_store,
        notifier=notifier,
        store=store,
    )
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Entry point (MAIN3)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        sys.exit(main())
    except TradingSystemError as e:
        _log.critical("system_error", exc_info=True)
        sys.exit(1)
    except Exception as e:
        _log.critical("unexpected_error", exc_info=True)
        sys.exit(2)
