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
import concurrent.futures
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as _time
from pathlib import Path
from typing import Callable, Optional

import core.time_authority as time_authority
from alerts.telegram_notifier import TelegramNotifier
from broker.clock_skew_probe import BrokerClockSkewProbe
from broker.token_monitor import TokenMonitor
from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from broker.order_state_machine import OrderStateMachine
from broker.product_resolver import ProductResolver
from broker.rate_limiter import RateLimiter
from broker.zerodha_adapter import ZerodhaAdapter
from capital.drift_handler import CapitalDriftHandler
from capital.fund_manager import FundManager, resolve_bucket_allocation
from capital.kill_switch import KillSwitch
from capital.position_sizer import PositionSizer
from capital.risk_engine import RiskEngine
from core.account_registry import AccountRegistry
from core.config_loader import SERVICE_WINDOW_END_MAX, load_all
from core.config_auditor import audit as audit_config
from core.config_snapshotter import snapshot_config
from core.config_validator import config_validator
from core import effect_telemetry
from core.events import EventBus, CapitalDriftDetected, KillSwitchActivated
from core.exceptions import CapitalStateInconsistent, TradingSystemError
from core.instrument_cache import InstrumentCache
from core.logger import get_logger, setup_logging
from core.market_windows import MarketWindows
from core.mis_blocklist import MisLearnedBlocklist
from core.state_store import StateStore
from data.candle_store import CandleStore
from data.live_feed import LiveFeedManager
from orders.eod_squareoff import EodSquareoff
from orders.shadow_tracker import ShadowTracker
from orders.cnc_gtt import CncGttPlacer
from orders.cnc_gtt_monitor import CncGttMonitor
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import LimitTripleProtocol
from orders.order_reconciler import OrderReconciler
from orders.tgt_retry_manager import TGTRetryManager
from orders.smart_tgt_manager import SmartTgtManager
from screening.entry_gate import EntryGate, WatchEntry
from screening.quality_scorer import QualityScorer
from screening.secondary_screener import SecondaryScreener
from screening.step_executor import StepExecutor
from signals.signal_processor import SignalProcessor
from scripts.healthcheck_server import start_healthcheck_server
from signals.webhook_receiver import WebhookReceiver
from strategies.loader import StrategyLoader, scan_strategy_errors
from utils.holiday_guard import is_trading_day, next_trading_day, get_holiday_name
from utils.instance_lock import acquire_instance_lock, check_port_available, release_instance_lock
from utils.startup_checks import (
    StartupCheckFailed,
    StartupScenario,
    check_config_hash,
    check_kill_switch_present,
    check_paper_capital_consistency,
    check_webhook_endpoint,
    detect_startup_scenario,
    run_all_startup_checks,
)
from scripts.zerodha_login import is_token_valid, load_token

VERSION = "2.0.0"

# DUP-1 (2026-04-26 audit): _IST removed; never read locally.

# Set once in Phase 0a; used in top-level exception handler (MAIN3)
_log: logging.Logger = logging.getLogger("main")

# Set by signal handlers; main thread blocks on this
_shutdown_event = threading.Event()

# FIX-062: Flag to track BrokerAuthError for token invalidation
_broker_auth_failed = False


# ─────────────────────────────────────────────────────────────────────────────
# FIX-062: Token invalidation on auth error
# ─────────────────────────────────────────────────────────────────────────────

# H-11: canonical broker-token path. MUST match the loader — main.py live startup
# is_token_valid()/load_token() (:1800/:1806), _load() (:314/:399/:1637), the token
# refresh (scripts/auto_refresh_token.py) and PATHS.md all use
# data_store/session/zerodha_token.json. Before H-11 _invalidate_token used
# Path("zerodha_token.json") (process CWD), so on a BrokerAuthError the dead token
# in data_store/session/ was NEVER renamed — _invalidate_token logged "not found,
# skipping" and the FIX-062 auth-restart-loop guard was inert (the service could
# loop on a revoked token). Pointing at the real path re-arms the guard.
_TOKEN_PATH = Path("data_store/session/zerodha_token.json")
_TOKEN_INVALID_PATH = _TOKEN_PATH.with_suffix(".invalid")


def _invalidate_token() -> None:
    """
    FIX-062 / H-11: Rename the REAL broker token
    (data_store/session/zerodha_token.json -> .invalid) so the dead token is gone.

    Called when BrokerAuthError occurs to prevent an infinite restart loop: with
    the token renamed, the next non-interactive live start fails fast at the
    is_token_valid() gate (main.py: "Token missing or expired" -> return 6) instead
    of restarting straight back onto the same revoked token.
    os.rename() is atomic on Linux.
    """
    token_path = _TOKEN_PATH
    invalid_path = _TOKEN_INVALID_PATH

    if not token_path.exists():
        _log.warning(
            "_invalidate_token: %s not found, skipping rename", token_path
        )
        return

    try:
        os.rename(token_path, invalid_path)
        _log.critical(
            "BrokerAuthError: token invalidated, renamed to zerodha_token.invalid -- "
            "manual token refresh required"
        )
    except Exception as exc:
        _log.error("_invalidate_token failed: %s", exc, exc_info=True)


def _mark_auth_failed() -> None:
    """FIX-062: Set flag to trigger token invalidation at shutdown."""
    global _broker_auth_failed
    _broker_auth_failed = True
    _log.critical("BrokerAuthError detected -- token will be invalidated at shutdown")


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

def required_startup_secrets(primary_account_id: str) -> list[str]:
    """Env-var names that MUST be set before startup, in BOTH paper and live.

    C-2 (02-Jul-2026): WEBHOOK_SECRET is required in EVERY mode -- the webhook must
    never be unauthenticated (paper previously omitted it, a parity/security gap:
    an unset secret made the paper webhook accept unsigned requests). Consumed by
    run_all_startup_checks, which fails fast if any name is unset.
    """
    return [
        f"ZERODHA_API_KEY_{primary_account_id}",
        f"ZERODHA_API_SECRET_{primary_account_id}",
        "TELEGRAM_BOT_TOKEN",
        "WEBHOOK_SECRET",
    ]


def _http_fetch(url: str, timeout_sec: float = 5.0):
    """Minimal stdlib HTTP fetcher for startup checks.

    FIX-184: send a non-default User-Agent. urllib's default "Python-urllib/x.y"
    UA is blocked by Chartink's Cloudflare edge with 403, which urlopen RAISES as
    HTTPError -> previously swallowed into status=None, so every scanner logged
    "returned status None (unreachable)" at startup even though the URLs are fine
    (any non-urllib UA returns 200). Also surface real HTTP error codes (4xx/5xx)
    instead of masking them as None, so a genuinely bad URL (404) is
    distinguishable from a true connection failure (None).
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": "TradingSystem/2.0 startup-check"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read(256).decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        # Real HTTP response carrying an error status — report the code, not None.
        try:
            body = exc.read(256).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return exc.code, body
    except Exception as exc:
        return None, str(exc)


def _send_holiday_notification(message: str) -> None:
    """Best-effort Telegram notification for holiday/weekend shutdown.

    Uses direct HTTP POST before logger setup. Silent on failure.
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHANNEL_PRIMARY")
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0):
            pass
    except Exception:
        pass


def _alert_invalid_strategy_configs(
    bad_files: list[tuple[str, str]],
    config_dir: Path,
) -> None:
    """Fire ONE loud Telegram + email alert naming every strategy YAML that failed to
    load/validate — a missing/invalid ``direction`` or ANY other schema failure — so a
    forgotten ``direction:`` can never silently fail to trade (18-Jul-2026).

    Called from the startup-checks abort path (``invalid_strategy_configs`` blocking
    failure) BEFORE the boot returns. The boot STILL fails — a broken strategy set must
    not run — but this tells Rama LOUDLY and specifically WHY the system won't start,
    naming each file + reason instead of burying it in one log line.

    Reuses the SAME Telegram + critical-email path as the strategy-registry / cron
    officers (``TelegramNotifier.from_env`` + ``write_critical_sentinel``) — no parallel
    notifier. The Telegram send uses ``write_sentinel=False`` so this method owns the
    single email sentinel itself (no duplicate email), and the email still fires even if
    the Telegram token is unset. FAIL-SAFE: every send is wrapped — an import or delivery
    failure is logged and swallowed, so the alert can never crash or mask the abort it is
    describing. Mode-agnostic (paper == live): a boot-blocking config error alerts either
    way, exactly like the cron officers.
    """
    if not bad_files:
        return

    lines = [
        "STRATEGY CONFIG INVALID — the system will NOT start until this is fixed.",
        "",
        "%d strategy YAML file(s) failed to load/validate:" % len(bad_files),
    ]
    for fname, reason in bad_files:
        lines.append("  • %s — %s" % (fname, reason))
    lines.append("")
    lines.append(
        "Fix the file(s) above and restart. A missing or invalid `direction:` is the "
        "usual cause — every strategy must declare direction: LONG or SHORT."
    )
    body = "\n".join(lines)
    title = "[BOOT ABORT] %d invalid strategy YAML(s)" % len(bad_files)

    # Telegram (best-effort). CRITICAL tier; write_sentinel=False because we write the
    # one email sentinel ourselves below (avoids a duplicate email).
    try:
        notifier = TelegramNotifier.from_env(logger=_log, config_dir=config_dir)
        if notifier is not None:
            notifier.send(
                severity="CRITICAL",
                title=title,
                body=body,
                source_module="main.strategy_config",
                write_sentinel=False,
            )
    except Exception as exc:  # noqa: BLE001 — a Telegram failure must never crash the boot
        _log.error("strategy_config_alert.telegram_failed: %s", exc)

    # Email via the critical sentinel (the alert-watcher delivers it), mirroring the
    # registry / cron officers. Guarantees an inbox record even if Telegram is unset.
    try:
        from alerts.critical import write_critical_sentinel
        write_critical_sentinel(
            title=title,
            body=body,
            source_module="main.strategy_config",
            subject="[CRITICAL] %s" % title,
            content_type="text/plain",
            plain_fallback=body,
            sentinel_dir=Path("data_store"),
        )
    except Exception as exc:  # noqa: BLE001 — email is best-effort; the boot abort continues
        _log.error("strategy_config_alert.email_failed: %s", exc)


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
    from requests.adapters import HTTPAdapter
    timeout = app_config.broker_limits.timeouts.read_sec
    kite = KiteConnect(api_key=os.environ["ZERODHA_API_KEY"], timeout=timeout)
    kite.set_access_token(os.environ["ZERODHA_ACCESS_TOKEN"])
    adapter = HTTPAdapter(pool_connections=1, pool_maxsize=50)
    kite.reqsession.mount("https://", adapter)
    return kite


def _build_market_data_kite(is_paper: bool, kite_client):
    """
    SNR-DETECTOR-V1: return a kite handle usable for READ-ONLY historical data.

    Live: reuse kite_client (== adapter._kite). Paper: kite_client is None
    (orders are simulated), so build a read-only KiteConnect from the saved
    token file — the SAME source _make_paper_quote_provider uses for real paper
    quotes; api_key comes from the token file, NOT a hardcoded key. Orders stay
    paper-simulated; this handle is for market data only (parity). None if no
    token (the fetch closure then fails safe → fetch_failed rows).
    """
    if not is_paper:
        return kite_client
    try:
        import json
        from pathlib import Path
        from kiteconnect import KiteConnect  # type: ignore[import]
        token_path = Path("data_store/session/zerodha_token.json")
        if not token_path.exists():
            return None
        token_data = json.loads(token_path.read_text())
        k = KiteConnect(api_key=token_data["api_key"])
        k.set_access_token(token_data["access_token"])
        return k
    except Exception:
        return None


def _make_sr_fetch_fn(market_kite, rate_limiter):
    """
    SNR-DETECTOR-V1: a rate-limited historical-fetch closure injected into the
    pure sr_detector package. Routes every call through
    rate_limiter.acquire("historical") (spec C pacing); fails safe if no kite
    handle (the OhlcFetcher records a fetch_failed row, never raises).
    """
    def _fetch(instrument_token, from_date, to_date, interval):
        if market_kite is None:
            return []
        rate_limiter.acquire("historical")
        return market_kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
        )
    return _fetch


def _load_holidays(app_config) -> set:
    """Convert NseHolidaysConfig holidays to set[date] (MAIN20)."""
    return {h.date for h in app_config.nse_holidays.holidays}


def _load_special_sessions(app_config) -> dict:
    """
    FIX-094: Convert special_sessions config to dict[date, tuple[time, time, time]].

    Returns:
        dict mapping date -> (market_open, market_close, eod_squareoff)
        Empty dict if no special_sessions configured.
    """
    from datetime import datetime

    special_sessions_cfg = getattr(app_config.system, 'special_sessions', None)
    if not special_sessions_cfg:
        return {}

    result = {}
    for date_str, session in special_sessions_cfg.items():
        # Parse YYYY-MM-DD date string
        session_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        # Parse HH:MM time strings
        market_open = _parse_hhmm(session['market_open'])
        market_close = _parse_hhmm(session['market_close'])
        eod_squareoff = _parse_hhmm(session['eod_squareoff_time'])
        result[session_date] = (market_open, market_close, eod_squareoff)

    return result


def _make_paper_quote_provider():
    """
    Return a quote_provider for paper mode that fetches REAL quotes from Kite.

    Reads credentials from data_store/session/zerodha_token.json (saved by
    interactive startup) and calls Kite quote API for live OHLC/VWAP data.
    Returns empty dict when API fails -- callers (LTP gating, EOD) already
    handle missing symbols gracefully. Never returns fake non-zero prices.

    Thread-safe LTP cache (3s TTL) prevents 30+ LTP-gating threads from
    each making individual API calls and exhausting the connection pool.
    """
    from broker.zerodha_adapter import Quote
    from kiteconnect import KiteConnect
    import json
    import logging
    import threading
    import time as _time_mod
    from pathlib import Path
    from requests.adapters import HTTPAdapter

    _log = logging.getLogger("paper_quote_provider")
    token_path = Path("data_store/session/zerodha_token.json")
    kite_client = None
    if token_path.exists():
        try:
            with open(token_path) as f:
                token_data = json.load(f)
            kite_client = KiteConnect(api_key=token_data["api_key"])
            kite_client.set_access_token(token_data["access_token"])
            adapter = HTTPAdapter(pool_connections=1, pool_maxsize=50)
            kite_client.reqsession.mount("https://", adapter)
        except Exception:
            pass

    # Load symbol aliases for Chartink -> Zerodha mapping
    _aliases: dict[str, str] = {}
    alias_path = Path("config/symbol_aliases.yaml")
    if alias_path.exists():
        try:
            import yaml
            with open(alias_path) as f:
                _aliases = yaml.safe_load(f) or {}
            _log.info("paper_quote_provider: loaded %d symbol aliases", len(_aliases))
        except Exception as exc:
            _log.warning("paper_quote_provider: failed to load symbol_aliases.yaml: %s", exc)

    _cache: dict[str, Quote] = {}
    _cache_ts: float = 0.0
    _cache_lock = threading.Lock()
    _api_lock = threading.Lock()
    _CACHE_TTL_SEC = 3.0
    _MIN_CALL_INTERVAL_SEC = 0.35

    _last_call_mono: float = 0.0

    def _raw_fetch(symbols):
        nonlocal _last_call_mono
        from core.time_authority import now_ist
        ts = now_ist()
        if kite_client is None:
            _log.warning("paper_quote_provider: no kite_client, returning empty")
            return {}

        # Translate symbols via alias map
        translated = [_aliases.get(s, s) for s in symbols]

        with _api_lock:
            wait = _MIN_CALL_INTERVAL_SEC - (_time_mod.monotonic() - _last_call_mono)
            if wait > 0:
                _time_mod.sleep(wait)
            instrument_keys = [f"NSE:{s}" for s in translated]
            try:
                raw = kite_client.quote(*instrument_keys)
                received = len(raw or {})
                if received < len(translated):
                    missing_translated = set(translated) - {k.split(":", 1)[-1] for k in (raw or {})}
                    missing_original = [symbols[translated.index(t)] for t in missing_translated if t in translated]
                    _log.warning(
                        "paper_quote_provider: partial response — requested %d, got %d. Missing: %s",
                        len(translated), received, sorted(missing_original)
                    )
            except Exception as exc:
                _log.warning("paper_quote_provider: Kite API failed: %s", exc)
                return {}
            finally:
                _last_call_mono = _time_mod.monotonic()

        # Build quotes dict, reverse-translating symbols to original names
        quotes: dict[str, Quote] = {}
        reverse_map = {v: k for k, v in _aliases.items()}
        for key, data in (raw or {}).items():
            zerodha_symbol = key.split(":", 1)[-1]
            original_symbol = reverse_map.get(zerodha_symbol, zerodha_symbol)
            depth = data.get("depth", {})
            bid = 0.0
            ask = 0.0
            if depth:
                bids = depth.get("buy", [])
                asks = depth.get("sell", [])
                bid = float(bids[0]["price"]) if bids else 0.0
                ask = float(asks[0]["price"]) if asks else 0.0
            ohlc = data.get("ohlc", {})
            quotes[original_symbol] = Quote(
                symbol=original_symbol,
                last_price=float(data.get("last_price", 0.0)),
                bid=bid,
                ask=ask,
                volume=int(data.get("volume", 0)),
                ts=ts,
                vwap=float(data["average_price"]) if data.get("average_price") else None,
                open_price=float(ohlc["open"]) if ohlc.get("open") else None,
                day_high=float(ohlc["high"]) if ohlc.get("high") else None,
                day_low=float(ohlc["low"]) if ohlc.get("low") else None,
                upper_circuit=float(data["upper_circuit_limit"]) if data.get("upper_circuit_limit") else None,
                lower_circuit=float(data["lower_circuit_limit"]) if data.get("lower_circuit_limit") else None,
            )
        return quotes

    def _provider(symbols):
        nonlocal _cache, _cache_ts
        now_mono = _time_mod.monotonic()

        with _cache_lock:
            if now_mono - _cache_ts < _CACHE_TTL_SEC:
                hit = {s: _cache[s] for s in symbols if s in _cache}
                if len(hit) == len(symbols):
                    return hit

        fresh = _raw_fetch(symbols)

        with _cache_lock:
            _cache.update(fresh)
            _cache_ts = _time_mod.monotonic()

        return {s: fresh[s] for s in symbols if s in fresh}

    return _provider


def _parse_hhmm(s: str) -> _time:
    """Parse 'HH:MM' (validated upstream by TradingHoursConfig) into a time."""
    h, m = s.split(":")
    return _time(int(h), int(m))


def _write_session(store: StateStore, session_date: str, mode: str,
                   config_hash: str, now_iso: str) -> None:
    """INSERT OR REPLACE the single session row (id=1)."""
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO session
                (id, session_date, account_id, broker, mode, trade_type,
                 last_config_hash, session_start, last_updated)
            VALUES
                (1, ?, 'default', ?, ?, 'INTRADAY', ?, ?, ?)
            """,
            (session_date, mode if mode == "paper" else "zerodha",
             mode.upper(), config_hash, now_iso, now_iso),
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

def _make_critical_failure_cb(
    kill_switch: KillSwitch,
    notifier: Optional[TelegramNotifier],
    mode: str = "LIVE",
):
    def _on_critical_failure(source: str, reason: str) -> None:
        _log.critical("Critical failure from %s: %s", source, reason)

        # FIX-062: Mark auth failure to invalidate token at shutdown
        if "BrokerAuthError" in reason:
            _mark_auth_failed()

        kill_switch.soft_kill(
            reason=f"{source}: {reason}", triggered_by="auto"
        )
        if notifier is not None:
            try:
                notifier.send(
                    severity="CRITICAL",
                    title=f"[{mode}] 🚨 Critical Failure",
                    body=(
                        f"Source: {source}\n"
                        f"Reason: {reason}"
                    ),
                    source_module="main",
                )
            except Exception as ne:
                _log.error("notifier.send failed in critical callback: %s", ne)
    return _on_critical_failure


def _make_force_close_cb(
    kill_switch: KillSwitch,
    notifier: Optional[TelegramNotifier],
    mode: str = "LIVE",
):
    """FIX-128 Fix C: force-close circuit breaker callback (15:15 IST trigger)."""
    def _on_force_close() -> None:
        _log.critical(
            "circuit_breaker.force_close_triggered: soft_kill, EOD squareoff handles positions"
        )
        kill_switch.soft_kill(
            reason="circuit_breaker_force_close_15:15",
            triggered_by="order_monitor",
        )
        if notifier is not None:
            try:
                notifier.send(
                    severity="WARNING",
                    title=f"[{mode}] CIRCUIT BREAKER — Force Close",
                    # ⛔ Do NOT restore "will close all positions". EOD6
                    # (eod_squareoff.py:23/:34/:1073) does NOT touch DELIVERY
                    # (CNC) positions — a spared delivery leg is CARRIED by
                    # design (ledger #2 / Q4). The old wording was correct only
                    # while delivery was impossible; it goes false at the flip
                    # and would tell the operator, every afternoon, that
                    # positions which deliberately survive are about to be
                    # closed. Pinned by test_kill_alerts_delivery_carveout.py.
                    body=(
                        "15:15 circuit breaker fired: pending entry orders cancelled.\n"
                        "EOD squareoff closes INTRADAY (MIS/CO) positions at 15:17.\n"
                        "Delivery (CNC) is carried by design (EOD6) — not squared off."
                    ),
                    source_module="main",
                )
            except Exception as ne:
                _log.error("notifier.send failed in force_close callback: %s", ne)
    return _on_force_close


def _make_api_failure_hard_kill_cb(
    kill_switch: KillSwitch,
    notifier: Optional[TelegramNotifier],
    mode: str = "LIVE",
):
    """FIX-128 Fix C: circuit breaker hard_kill on 3 consecutive API failures."""
    def _on_api_failure(reason: str) -> None:
        _log.critical("circuit_breaker.api_failure_hard_kill: %s", reason)
        try:
            kill_switch.hard_kill(
                reason=f"circuit_breaker_api_failure: {reason}",
                triggered_by="order_monitor",
            )
        except Exception as exc:
            _log.error("hard_kill failed in api_failure callback: %s", exc)
        if notifier is not None:
            try:
                notifier.send(
                    severity="CRITICAL",
                    title=f"[{mode}] CIRCUIT BREAKER — API Failure Hard Kill",
                    body=f"3 consecutive broker API failures detected.\n{reason}",
                    source_module="main",
                )
            except Exception as ne:
                _log.error("notifier.send failed in api_failure callback: %s", ne)
    return _on_api_failure



def _make_daily_loss_cb(
    kill_switch: KillSwitch,
    notifier: Optional[TelegramNotifier],
    mode: str = "LIVE",
    eod_ref: Optional[dict] = None,  # {"eod": EodSquareoff | None} — filled after EodSquareoff created
):
    """
    FIX-128 (Fix D): Daily loss limit kill sequence.

    Correct sequence:
      1. Telegram alert: DAILY LOSS LIMIT HIT
      2. Fire EodSquareoff.fire_now() — cancels pending entries + market-closes all positions
      3. Trigger SOFT_KILL (not HARD_KILL — positions are being closed)

    eod_ref is a mutable dict because EodSquareoff is created AFTER FundManager in main().
    Main fills it after EodSquareoff construction.
    Paper mode: EodSquareoff.fire_now() simulates closes (paper adapter).
    """
    def _on_daily_loss_breach() -> None:
        _log.critical(
            "daily_loss_limit.breach_sequence_start",
            extra={"mode": mode},
        )

        if notifier is not None:
            try:
                notifier.send(
                    severity="CRITICAL",
                    title=f"[{mode}] DAILY LOSS LIMIT HIT",
                    body=(
                        "Closing all positions and halting new trades.\n"
                        "Cancel pending entries → Market-close all positions → SOFT_KILL"
                    ),
                    source_module="main",
                )
            except Exception as ne:
                _log.error("notifier.send failed in daily_loss callback: %s", ne)

        eod_instance = (eod_ref or {}).get("eod")
        if eod_instance is not None:
            try:
                eod_instance.fire_now(
                    reason="daily_loss_limit_breached",
                    triggered_by="fund_manager",
                )
                _log.critical("daily_loss_limit.eod_fire_now_complete")
            except Exception as exc:
                _log.error(
                    "daily_loss_limit.eod_fire_now_failed: %s — proceeding to soft_kill", exc
                )
        else:
            _log.warning(
                "daily_loss_limit.eod_not_wired — skipping position close; soft_kill only"
            )

        kill_switch.soft_kill(
            reason="daily_loss_limit_breached",
            triggered_by="fund_manager",
        )
        _log.critical("daily_loss_limit.sequence_complete: soft_kill triggered")

    return _on_daily_loss_breach


def _make_orphan_cb(
    kill_switch: KillSwitch,
    notifier: Optional[TelegramNotifier],
    mode: str = "LIVE",
):
    def _on_orphan(order_id: str, reason: str) -> None:
        _log.critical("Orphan order %s: %s", order_id, reason)
        kill_switch.soft_kill(
            reason=f"orphan_order:{order_id}:{reason}", triggered_by="auto"
        )
        if notifier is not None:
            try:
                notifier.send(
                    severity="CRITICAL",
                    title=f"[{mode}] 🚨 Orphan Order Detected",
                    body=(
                        f"Order ID: {order_id} | Reason: {reason}\n"
                        "Action: Soft kill triggered."
                    ),
                    source_module="main",
                )
            except Exception as ne:
                _log.error("notifier.send failed in orphan callback: %s", ne)
    return _on_orphan


def _build_strategy_governor(store, app_config, notifier, mode: str):
    """FIX-130 Item 6: build StrategyGovernor if circuit breaker config is present."""
    from capital.strategy_governor import StrategyGovernor
    cfg = getattr(app_config.system, "strategy_circuit_breaker", None)
    if cfg is None:
        return None
    # effect-telemetry (B2): registered only on the constructed path — if the
    # circuit-breaker config is ever removed, the assertion flags it and the
    # registry edit rides that change (C2 discipline).
    effect_telemetry.register_constructed("strategy_governor")
    return StrategyGovernor(
        store=store,
        config=cfg,
        notifier=notifier,
        logger=get_logger("strategy_governor"),
        mode=mode,
    )


_gate_release_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="gate-release",
)


def _make_gate_release_cb(signal_processor: SignalProcessor):
    def _gate_release_worker(entry: WatchEntry, release_ltp) -> None:
        try:
            signal_processor.continue_from_gate(entry, release_ltp=release_ltp)
        except Exception as exc:
            _log.error(
                "continue_from_gate failed for %s: %s", entry.signal_id, exc,
            )

    def _on_gate_release(entry: WatchEntry, reason: str) -> None:
        if reason == "PRICE_HIT":
            release_ltp = entry.extras.get("release_ltp")
            _log.info(
                "Gate PRICE_HIT for %s signal_id=%s -- submitting to pool",
                entry.symbol, entry.signal_id,
            )
            # FIX-169 F25: run on dedicated pool, not gate worker thread
            _gate_release_pool.submit(_gate_release_worker, entry, release_ltp)
        else:
            _log.info(
                "Gate release %s for %s signal_id=%s -- no action",
                reason, entry.symbol, entry.signal_id,
            )
    return _on_gate_release


# ─────────────────────────────────────────────────────────────────────────────
# EOD pre-alert at 14:45 (FIX-130 Item 7)
# ─────────────────────────────────────────────────────────────────────────────

def _fire_eod_pre_alert(store: "StateStore", notifier, mode: str, log) -> None:
    """Query open trades and send Telegram warning ~30 min before EOD squareoff."""
    try:
        rows = store.fetch_all(
            "SELECT symbol, direction, qty_filled, entry_actual_price "
            "FROM trades WHERE status = 'OPEN' AND qty_filled > 0 "
            "ORDER BY symbol",
        )
        if not rows:
            log.info("eod_pre_alert: no open positions; skipping alert")
            return
        n = len(rows)
        lines = [f"EOD WARNING: {n} position{'s' if n != 1 else ''} force-squared in ~30 min:"]
        for r in rows:
            direction = r["direction"]
            qty = r["qty_filled"] or 0
            price = r["entry_actual_price"] or 0.0
            lines.append(f"  {r['symbol']}: {direction} {qty} @ {price:.2f}")
        body = "\n".join(lines)
        if notifier is not None:
            notifier.send(
                severity="WARNING",
                title=f"[{mode}] EOD SQUAREOFF IN ~30 MIN",
                body=body,
                source_module="main",
            )
        log.info("eod_pre_alert: sent for %d open position(s)", n)
    except Exception as exc:
        log.error("eod_pre_alert: failed: %s", exc)


def _start_eod_pre_alert_thread(
    store: "StateStore",
    notifier,
    mode: str,
    log,
    market_windows,
    shutdown_event: "threading.Event",
) -> None:
    """Background thread: sleep until 14:45 IST then fire the EOD pre-alert (once/day)."""
    import time as _time_mod
    from core.time_authority import now_ist as _now_ist

    _PRE_ALERT_TIME = _time(14, 45)

    def _run() -> None:
        while not shutdown_event.is_set():
            now = _now_ist()
            today = now.date()
            # Only fire on trading days
            if market_windows is None or market_windows.is_trading_holiday(now):
                break
            alert_dt = now.replace(
                hour=_PRE_ALERT_TIME.hour, minute=_PRE_ALERT_TIME.minute,
                second=0, microsecond=0,
            )
            wait_sec = (alert_dt - now).total_seconds()
            if wait_sec <= 0:
                # Already past 14:45 today — don't fire again today
                break
            # Sleep in small increments so shutdown_event is checked
            end = _now_ist().timestamp() + wait_sec
            while not shutdown_event.is_set() and _now_ist().timestamp() < end:
                _time_mod.sleep(min(30.0, end - _now_ist().timestamp()))
            if shutdown_event.is_set():
                return
            _fire_eod_pre_alert(store, notifier, mode, log)
            break  # fire once per day; thread exits

    t = threading.Thread(target=_run, name="eod-pre-alert", daemon=True)
    t.start()


def _start_market_open_margin_sync_thread(
    broker_adapter: "ZerodhaAdapter",
    fund_manager: "FundManager",
    notifier,
    mode: str,
    log,
    shutdown_event: "threading.Event",
    market_windows,
) -> None:
    """FIX-164: Re-sync capital from broker at 09:15 IST (market open).

    Fixes the case where the system starts pre-market with stale Rs 0 margins
    and the user deposits funds after startup but before 09:15. Only fires if
    the system started before 09:15; if started after market open, the startup
    fetch already captured the current balance so this is a no-op.
    Paper mode: broker_adapter.get_margins() returns static paper_capital —
    sync is a no-op but follows the identical code path (paper/live parity).
    """
    import time as _time_mod
    from core.time_authority import now_ist as _now_ist

    _MARKET_OPEN = _time(9, 15)

    def _run() -> None:
        now = _now_ist()
        if market_windows is not None and market_windows.is_trading_holiday(now):
            return
        target_dt = now.replace(
            hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute,
            second=0, microsecond=0,
        )
        wait_sec = (target_dt - now).total_seconds()
        if wait_sec <= 0:
            log.info("market_open_margin_sync: started after 09:15, skipping re-sync")
            return
        end = _now_ist().timestamp() + wait_sec
        while not shutdown_event.is_set() and _now_ist().timestamp() < end:
            _time_mod.sleep(min(30.0, end - _now_ist().timestamp()))
        if shutdown_event.is_set():
            return
        try:
            new_capital = broker_adapter.get_margins().net
            old_capital = fund_manager.get_snapshot().total
            fund_manager.sync_from_broker(new_capital)
            delta = new_capital - old_capital
            log.info(
                "market_open_margin_sync: capital re-synced at 09:15",
                extra={"old": old_capital, "new": new_capital, "delta": delta},
            )
            if abs(delta) > 1.0:
                try:
                    notifier.send(
                        severity="INFO",
                        title=f"[{mode}] Capital Updated at Market Open",
                        body=(
                            f"09:15 margin re-sync: ₹{old_capital:,.0f} → "
                            f"₹{new_capital:,.0f} (Δ ₹{delta:+,.0f})"
                        ),
                        source_module="main",
                    )
                except Exception as exc:
                    log.warning("market_open_margin_sync: notification failed: %s", exc)
        except Exception as exc:
            log.error("market_open_margin_sync: failed: %s", exc, exc_info=True)

    t = threading.Thread(target=_run, name="market-open-margin-sync", daemon=True)
    t.start()


# ─────────────────────────────────────────────────────────────────────────────
# FIX-189 (P1-A completion): EOD window-end self-exit. The market-window guard
# only blocks *starting* overnight; a service started in-window otherwise runs
# all night (no EOD self-exit existed — the runtime loop just waits on the
# shutdown event, and EOD squareoff only trips a scheduled SOFT_KILL). This
# watchdog sets the shutdown event once we are past the service-window end
# (the CONFIGURED trading_hours.service_window_end — 17:35 IST as deployed;
# 25-Jul-2026, was a hardcoded 16:00) AND positions are flat, so main() exits 0 →
# systemd does not restart (Restart=on-failure) and token-watcher's
# "exit-0-today" path skips a restart → clean overnight + clean morning start.
# Safety: it NEVER exits while any position has live exposure (OPEN/PARTIAL/
# PENDING_FILL) — it stays up to keep managing residual positions and exits only
# once flat. Parity: StateStore.count_active_positions() is mode-agnostic, so
# paper and live behave identically.
#
# M-C8: it also never exits while a HARD_KILL flatten is in progress — see
# _eod_self_exit_due. count_active_positions() is EXITING-blind and cannot see one.
# ─────────────────────────────────────────────────────────────────────────────

# M-C8: sentinel for the `active` slot when a flatten is in progress. Distinct from
# -1 ("not due / unknown") so the caller can log the two apart: -1 is silence, this
# is "we are deliberately holding the process open for the flatten".
_ACTIVE_FLATTEN_IN_PROGRESS = -2

# M-C8: bounded grace for draining the flatten worker on an EXTERNAL stop
# (systemd/operator SIGTERM|SIGINT). deploy/systemd/trading-system.service sets
# TimeoutStopSec=30, after which systemd SIGKILLs us no matter what we want — so a
# flatten that needs its full 2h CANNOT be honoured here, and pretending otherwise
# would just get us killed mid-write. 15s leaves the other half of the budget for
# the rest of _shutdown (WAL checkpoint + close). A flatten that outlives the grace
# gets a CRITICAL: positions may remain open, and that is the operator's call.
# The INTERNAL eod-self-exit path never hits this: it waits (unbounded) via the
# flatten gate above and only shuts down once the flatten is COMPLETE.
_FLATTEN_DRAIN_GRACE_SEC = 15.0

def _eod_self_exit_due(
    store,
    now: datetime,
    window_end: _time,
    flatten_in_progress_fn: "Optional[Callable[[], bool]]" = None,
) -> "tuple[bool, int]":
    """Decide whether the service should self-exit for the day.

    Returns (due, active_positions). `due` is True iff `now` is at/after
    `window_end` AND no HARD_KILL flatten is in progress AND there are zero active
    positions (OPEN/PARTIAL/PENDING_FILL). Before `window_end` → (False, -1)
    without querying. On a count error → (False, -1) so the service stays up
    (fail-safe). While a flatten is in progress → (False, _ACTIVE_FLATTEN_IN_PROGRESS).

    M-C8 — WHY THE FLATTEN GATE EXISTS (this closes a race that is latent TODAY,
    not one the async worker introduced): count_active_positions() counts only
    OPEN/PARTIAL/PENDING_FILL. The HARD_KILL flatten marks trades EXITING *early* —
    before the retry loop has confirmed the exit filled. EXITING is in none of those
    three, so a flatten that is still retrying reads here as "0 active positions",
    i.e. as FLAT, and this function would say "due" and exit the process out from
    under trades that are still being flattened. Asking the kill switch directly is
    the only honest answer; the position count structurally cannot give one.

    fail-safe: if flatten_in_progress_fn raises we assume a flatten IS in progress
    and stay up. Never exit on an unknown.
    """
    if now.time() < window_end:
        return (False, -1)
    if flatten_in_progress_fn is not None:
        try:
            if flatten_in_progress_fn():
                return (False, _ACTIVE_FLATTEN_IN_PROGRESS)
        except Exception:
            return (False, _ACTIVE_FLATTEN_IN_PROGRESS)  # cannot confirm → stay up
    try:
        active = int(store.count_active_positions())
    except Exception:
        return (False, -1)
    return (active == 0, active)


def _start_eod_self_exit_thread(
    store: "StateStore",
    notifier,
    mode: str,
    log,
    market_windows,
    shutdown_event: "threading.Event",
    window_end: "_time",
    poll_interval_sec: int = 60,
    flatten_in_progress_fn: "Optional[Callable[[], bool]]" = None,
) -> None:
    """Daemon thread: after the service-window end, exit cleanly once flat.

    M-C8: `flatten_in_progress_fn` (KillSwitch.is_flatten_in_progress) blocks the
    self-exit while a HARD_KILL flatten is still running — the position count alone
    cannot detect one (it is EXITING-blind).

    25-Jul-2026: `window_end` is REQUIRED — it used to default to None and resolve to
    the module constant SERVICE_WINDOW_END. That constant has since been split into a
    START cutoff (SERVICE_START_CUTOFF) and a CONFIGURED stop time, so the old default
    would now silently resolve to the wrong role. No caller relied on it (main.py
    passes it; every test passes it explicitly), so it is gone rather than repointed.
    """
    import time as _time_mod
    from core.time_authority import now_ist as _now_ist

    def _run() -> None:
        # Wait until window_end on the start date (the trading day we came up).
        start_now = _now_ist()
        target_dt = start_now.replace(
            hour=window_end.hour, minute=window_end.minute,
            second=0, microsecond=0,
        )
        while not shutdown_event.is_set() and _now_ist() < target_dt:
            remaining = (target_dt - _now_ist()).total_seconds()
            _time_mod.sleep(min(30.0, max(1.0, remaining)))
        if shutdown_event.is_set():
            return

        warned_not_flat = False
        warned_flatten = False
        while not shutdown_event.is_set():
            due, active = _eod_self_exit_due(
                store, _now_ist(), window_end, flatten_in_progress_fn
            )
            if active == _ACTIVE_FLATTEN_IN_PROGRESS and not warned_flatten:
                warned_flatten = True
                log.critical(
                    "eod_self_exit: past %s IST but a HARD_KILL flatten is IN "
                    "PROGRESS — holding the process open until it finishes (the "
                    "position count cannot see an in-flight flatten; exiting now "
                    "would abandon trades mid-exit).",
                    window_end.strftime("%H:%M"),
                )
            if due:
                log.info(
                    "eod_self_exit: past %s IST and flat (0 active positions) — "
                    "clean shutdown for the day; auto-restarts tomorrow after "
                    "the morning token refresh.", window_end.strftime("%H:%M"),
                )
                try:
                    if notifier is not None:
                        notifier.send(
                            severity="INFO",
                            title=f"[{mode}] EOD Clean Shutdown",
                            body=(
                                f"Past {window_end.strftime('%H:%M')} IST and flat "
                                "— service exiting cleanly for the day. "
                                "Auto-restarts tomorrow after the morning token refresh."
                            ),
                            source_module="main",
                        )
                except Exception:
                    pass
                shutdown_event.set()
                return
            if active > 0 and not warned_not_flat:
                warned_not_flat = True
                log.warning(
                    "eod_self_exit: past %s IST but %d active position(s) remain "
                    "— staying up to manage them; will exit once flat.",
                    window_end.strftime("%H:%M"), active,
                )
                try:
                    if notifier is not None:
                        notifier.send(
                            severity="WARNING",
                            title=f"[{mode}] EOD shutdown deferred",
                            body=(
                                f"{active} position(s) still open past "
                                f"{window_end.strftime('%H:%M')} IST — service staying "
                                "up until flat (not abandoning positions)."
                            ),
                            source_module="main",
                        )
                except Exception:
                    pass
            # Re-check after poll_interval (in shutdown-aware increments).
            end_ts = _now_ist().timestamp() + poll_interval_sec
            while not shutdown_event.is_set() and _now_ist().timestamp() < end_ts:
                _time_mod.sleep(min(30.0, max(1.0, end_ts - _now_ist().timestamp())))

    t = threading.Thread(target=_run, name="eod-self-exit", daemon=True)
    t.start()


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
    webhook_receiver: Optional[WebhookReceiver] = None,
    clock_skew_probe: Optional[BrokerClockSkewProbe] = None,
    token_monitor: Optional[TokenMonitor] = None,  # FIX-128 Fix E
    tgt_retry_manager: Optional[TGTRetryManager] = None,  # Task: TGT retry
    sr_detector=None,  # SNR-DETECTOR-V1: async S&R observer (None when disabled)
    zone_warmer=None,  # SNR-V2: ZoneWarmer daemon (None when disabled)
    retest_monitor=None,  # SNR-V2: RetestMonitor daemon (None when disabled)
    structure_exit_manager=None,  # SNR-V2 Phase B: StructureExitManager (None when disabled)
    market_regime_runner=None,  # V3 03.02: Market Regime shadow runner (None when disabled)
    portfolio_allocator=None,  # V3 03.05: ranked-admission worker (None when off)
    v3_chain=None,  # V3 Step 10: decision-chain shadow enrichment worker (None when off)
    pb01_entry_stage=None,  # V3 Step 10b: PB-01 next-morning entry-stage daemon (None when off)
    pb01_capture_worker=None,  # V3 Step 10b: PB-01 EOD watchlist-capture worker (None when off)
    kill_switch=None,  # M-C8: drain the async HARD_KILL flatten worker (None when absent)
    flatten_drain_timeout_sec: float = _FLATTEN_DRAIN_GRACE_SEC,
    mode: str = "LIVE",
) -> None:
    """Reverse-order shutdown (MAIN15)."""
    # FIX-060: Set shutdown event FIRST so rate limiter aborts immediately
    _shutdown_event.set()
    _log.info("Shutdown initiated")

    # M-C8: drain the HARD_KILL flatten worker FIRST, before tearing anything down.
    # Ordering is the whole point: the worker places exit orders through the adapter
    # and writes through `store`, and store.close() is at the bottom of this
    # function. Draining last would pull the DB out from under a live flatten.
    # Nothing below is needed BY the flatten, so nothing is lost by waiting here.
    #
    # This is the EXTERNAL-stop path (systemd/operator signal). The internal
    # eod-self-exit never arrives here mid-flatten — its gate already waited. So a
    # flatten still running at this point means someone asked us to stop NOW, and
    # systemd will SIGKILL at TimeoutStopSec regardless: bounded grace, then a
    # CRITICAL saying plainly that positions may be left open.
    if kill_switch is not None:
        try:
            if not kill_switch.drain_flatten(timeout=flatten_drain_timeout_sec):
                _log.critical(
                    "SHUTDOWN WITH FLATTEN STILL RUNNING: the HARD_KILL flatten did "
                    "not finish within the %.0fs stop grace. POSITIONS MAY REMAIN "
                    "OPEN AT THE BROKER — verify at the broker and flatten manually "
                    "(8-step broker-truth runbook; never flatten from DB state).",
                    flatten_drain_timeout_sec,
                )
            else:
                _log.info("flatten worker drained (or none was running)")
        except Exception as exc:
            _log.critical("flatten drain failed: %s — continuing shutdown", exc)

    # effect-telemetry (ledger #1, B4): the EOD census — emitted at the single
    # clean-stop convergence point, AFTER the flatten drain (so drain-time acts
    # are counted) and BEFORE any manager teardown. emit_census() can never
    # raise; a crash day produces no census by design (B5 heartbeat deferred).
    try:
        from core.time_authority import now_ist as _census_now_ist
        _census_day = _census_now_ist().date().isoformat()
        effect_telemetry.emit_census(
            logger=get_logger("effect_census"),
            day=_census_day,
            webhook_day_count=(
                lambda d=_census_day: (store.fetch_one(
                    "SELECT COUNT(*) FROM webhook_audit WHERE date = ?", (d,)
                ) or [0])[0]
            ),
        )
    except Exception as exc:  # noqa: BLE001 — belt over emit's own braces
        _log.error("effect_census: emit wrapper failed: %s", exc)

    # FIX-062: Invalidate token if auth error occurred
    if _broker_auth_failed:
        _invalidate_token()

    # H-16: stop webhook_receiver FIRST so new webhooks return 503. In-flight
    # requests drain naturally; signal_processor stop below still works on
    # queued items.
    if webhook_receiver is not None:
        try:
            webhook_receiver.stop()
        except Exception as exc:
            _log.error("webhook_receiver.stop error: %s", exc)
    try:
        signal_proc.stop()
    except Exception as exc:
        _log.error("signal_processor.stop error: %s", exc)
    # SNR-DETECTOR-V1: stop the observer worker (after signal_processor so no new
    # candidates are enqueued). Daemon thread; best-effort.
    if sr_detector is not None:
        try:
            sr_detector.stop()
        except Exception as exc:
            _log.error("sr_detector.stop error: %s", exc)
    # V3 03.02: stop the Market Regime shadow runner (daemon; best-effort).
    if market_regime_runner is not None:
        try:
            market_regime_runner.stop()
        except Exception as exc:
            _log.error("market_regime_runner.stop error: %s", exc)
    # V3 03.05: stop the Portfolio Allocator window worker (daemon; best-effort).
    if portfolio_allocator is not None:
        try:
            portfolio_allocator.stop()
        except Exception as exc:
            _log.error("portfolio_allocator.stop error: %s", exc)
    # V3 Step 10: stop the decision-chain shadow enrichment worker (daemon; best-effort).
    if v3_chain is not None:
        try:
            v3_chain.stop()
        except Exception as exc:
            _log.error("v3_chain.stop error: %s", exc)
    # V3 Step 10b: stop the PB-01 entry stage + EOD capture worker (daemons; best-effort).
    # After webhook_receiver.stop() above, so no new EOD alert can be captured.
    if pb01_entry_stage is not None:
        try:
            pb01_entry_stage.stop()
        except Exception as exc:
            _log.error("pb01_entry_stage.stop error: %s", exc)
    if pb01_capture_worker is not None:
        try:
            pb01_capture_worker.stop()
        except Exception as exc:
            _log.error("pb01_capture_worker.stop error: %s", exc)
    # SNR-V2: stop the retest monitor + zone warmer daemons (best-effort).
    if retest_monitor is not None:
        try:
            retest_monitor.stop()
        except Exception as exc:
            _log.error("retest_monitor.stop error: %s", exc)
    # SNR-V2 Phase B: unsubscribe structure-exit from the candle feed (best-effort).
    if structure_exit_manager is not None:
        try:
            structure_exit_manager.stop()
        except Exception as exc:
            _log.error("structure_exit_manager.stop error: %s", exc)
    if zone_warmer is not None:
        try:
            zone_warmer.stop()
        except Exception as exc:
            _log.error("zone_warmer.stop error: %s", exc)
    try:
        entry_gate.stop()
    except Exception as exc:
        _log.error("entry_gate.stop error: %s", exc)
    # Flask has no clean stop (daemon thread dies on process exit)
    try:
        smart_tgt.stop()
    except Exception as exc:
        _log.error("smart_tgt.stop error: %s", exc)
    if tgt_retry_manager is not None:
        try:
            tgt_retry_manager.stop()
        except Exception as exc:
            _log.error("tgt_retry_manager.stop error: %s", exc)
    try:
        order_reconciler.stop()
    except Exception as exc:
        _log.error("order_reconciler.stop error: %s", exc)
    # FIX-129 (Item 45): Cancel pending ENTRY orders before stopping the monitor.
    # SL/TGT/EOD legs are preserved — exit protection remains active at the broker.
    try:
        n_cancelled = order_monitor.cancel_all_entry_orders()
        if n_cancelled:
            _log.info("Shutdown: cancelled %d pending ENTRY order(s)", n_cancelled)
    except Exception as exc:
        _log.error("order_monitor.cancel_all_entry_orders error: %s", exc)
    try:
        order_monitor.stop()
    except Exception as exc:
        _log.error("order_monitor.stop error: %s", exc)
    if clock_skew_probe is not None:
        try:
            clock_skew_probe.stop()
        except Exception as exc:
            _log.error("clock_skew_probe.stop error: %s", exc)
    if token_monitor is not None:  # FIX-128 Fix E
        try:
            token_monitor.stop()
        except Exception as exc:
            _log.error("token_monitor.stop error: %s", exc)
    try:
        live_feed.disconnect()
    except Exception as exc:
        _log.error("live_feed.disconnect error: %s", exc)
    try:
        candle_store.stop()
    except Exception as exc:
        _log.error("candle_store.stop error: %s", exc)
    try:
        _hhmm = time_authority.now_ist().strftime("%H:%M")
        notifier.send(
            severity="INFO",
            title=f"[{mode}] 🛑 System Stopping",
            body=f"Version: {VERSION} | {_hhmm} IST",
            source_module="main",
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

    # FIX-057 / FIX-131 Item 23: WAL checkpoint after all threads joined, before close/exit
    try:
        stats = store.checkpoint_wal()  # PASSIVE: safe with any remaining readers
        _log.info(
            "WAL checkpoint_wal on shutdown: busy=%d log=%d checkpointed=%d",
            stats.get("busy", -1),
            stats.get("log", -1),
            stats.get("checkpointed", -1),
        )
    except Exception as chk_exc:
        # Checkpoint failure must not block shutdown
        _log.warning("WAL checkpoint on shutdown failed: %s", chk_exc)

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


def _interactive_confirm(account, mode, input_fn=input):
    """
    Unified confirm screen for both paper and live modes (SU12).
    Shows account + mode summary; y/n to proceed. Exits 7 on 'n'.
    """
    print()
    print("  ─" * 30)
    print("  Confirm startup:")
    print()
    print(f"  Account : {account.account_id}  ({account.label})")
    print(f"  Mode    : {mode.upper()}")
    if mode == "live":
        print()
        print("  WARNING: Real money will be traded.")
    print()
    raw = input_fn("  Proceed? [y/n]: ").strip().lower()
    if raw != "y":
        print("  Startup cancelled.")
        sys.exit(7)


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
# FIX-189 (P1-A): broad service-window guard — the trading service must NOT run
# overnight. A leftover/evening-refreshed token previously let token-watcher
# start the service late at night (observed 18-Jun 23:22); it then ran until the
# token expired (~04:30) and emitted false CRITICAL alerts (capital drift @ 04:24
# off an overnight broker net=0.0; KiteTicker max-reconnect exhausted @ 07:07).
# Outside this broad window main() exits cleanly (0) so systemd/token-watcher do
# not keep a long-lived process alive off-hours. The normal path stays inside the
# window: 08:15 token-refresh cron -> 08:30 premarket start; intraday crash
# recovery is also in-window. EOD self-exit (post square-off) is unchanged.
#
# ⭐ 25-Jul-2026 — THIS CONSTANT USED TO HAVE TWO ROLES. `SERVICE_WINDOW_END` was both
# the latest the service may START (here) and the moment it STOPS (the eod-self-exit
# thread). Making only the stop time configurable opens a silent trap: a crash-restart
# between the two values hits this guard, returns exit 0 — which `Restart=on-failure`
# does not retry — so the process stays dead for the evening, and the liveness probe
# (cron `*/5 09-15`, last run 15:55) is not running to notice.
#
# The two roles are now separate names, and locked together so the gap cannot exist:
#   * SERVICE_START_CUTOFF        — latest the service may START (this guard)
#   * trading_hours.service_window_end (CONFIG) — when it STOPS
# The cutoff binds to the schema's exclusive upper bound for the configured stop, so
# EVERY legal stop time is strictly inside the start window — including after a revert
# to 16:00. The trap is unrepresentable, not merely untested.
#
# This guard runs at main() BEFORE load_all(), so it cannot read config; binding to the
# bound rather than the value is what makes that safe. Accepted cost: the anti-overnight
# start window widens 16:00 -> 18:15. A stray start inside it arms the self-exit thread
# whose target time is already past, so it goes flat-check-then-exit within one poll
# (<=60 s). The FIX-189 incidents (23:22 start, 04:24 alerts) are still refused.
# See docs/audit/service_window_configurable_25jul2026.md §3.
# ─────────────────────────────────────────────────────────────────────────────
SERVICE_WINDOW_START = _time(8, 0)   # IST — before the 08:30 premarket start
SERVICE_START_CUTOFF = SERVICE_WINDOW_END_MAX  # IST — latest the service may START


def _within_service_window(now: datetime) -> bool:
    """True if `now` (IST, tz-aware) is inside the broad START window
    [SERVICE_WINDOW_START, SERVICE_START_CUTOFF) = [08:00, 18:15).

    This is the START guard only — it answers "may the service come up now?", NOT
    "should it still be running?". The stop time is the configured
    trading_hours.service_window_end, enforced by the eod-self-exit thread.

    Pure time-of-day check (holiday/weekend is handled by the separate holiday
    guard), so it is host-timezone independent.
    """
    return SERVICE_WINDOW_START <= now.time() < SERVICE_START_CUTOFF


def _config_error_detail(exc: BaseException) -> str:
    """Render the REASON a config load failed, for the boot log.

    25-Jul-2026: `ConfigSchemaError`'s message only names the FILE ("Schema validation
    failed for system_config.yaml"); the reason lives in the structured context kwargs,
    which TradingSystemError documents as "intended for structured logging". main() was
    logging only `%s`, so a rejected config VALUE would die at the 08:15 boot without
    saying which key was wrong — the operator would pay for that diagnosis on a trading
    morning. Found while proving the new service_window_end range validator actually
    rejects (a validator whose reason nobody can read is half a validator).

    Never raises: diagnostics must not mask the failure they are describing.
    """
    try:
        out = ""
        for err in (getattr(exc, "context", {}) or {}).get("errors") or []:
            loc = ".".join(str(p) for p in err.get("loc", ()))
            out += f" | {loc or '<root>'}: {err.get('msg')}"
        return out
    except Exception:  # noqa: BLE001 — a broken diagnostic must never break the boot log
        return ""


def _service_window_banner(start: _time, cutoff: _time, stop: _time) -> str:
    """The boot-log line naming BOTH windows (§3.6 of the build instruction).

    After a week of finding things that look alive and produce nothing, "what window
    is this process actually running?" must be answerable from the log rather than by
    reading the deployed source.
    """
    return (
        f"service window: may START in [{start:%H:%M}, {cutoff:%H:%M}) IST; "
        f"EOD self-exit at {stop:%H:%M} IST (configured: "
        f"trading_hours.service_window_end)"
    )


# M-C1 (2026-07-07): the broker's net margin on a mid-day warm restart ALREADY
# includes today's realized PnL, but rehydrate Phase 2 re-applies that same PnL
# (fm_ledger RELEASE_USED carryover) -> the live seed would double-count it
# (inflated reservable capital + a phantom -today_pnl drift on the next
# sync_from_broker). Subtract today's realized-PnL carryover from the seed so
# seed + Phase 2 == broker.net BY CONSTRUCTION. The Sigma is over the EXACT same
# fm_ledger rows Phase 2 walks (shared helper), so the cancellation is exact incl.
# sign (loss day -> Sigma<0 -> seed rises). Cold boot: 0 closed trades -> Sigma=0
# -> seed = broker.net (unchanged). PAPER is UNTOUCHED: its static paper_capital
# seed correctly excludes PnL.
#
# Extracted from main() 21-Jul-2026 (B1) to make the live-seed arithmetic invocable
# by a test, and parameterised with the day-floor (B5, midnight day-floor fix):
# main() derives start_of_today_iso ONCE and passes the SAME value here and to
# rehydrate_from_open_trades, so the seed's Sigma and Phase-2's re-addition can
# never span different days. start_of_today_iso=None forwards to
# today_realized_pnl_carryover's own None-fallback (standalone/test callers). The
# expression is otherwise identical to the former inline seed (one extra stack
# frame, no new state, no exception handling added or removed).
def compute_live_seed(broker_adapter, fund_manager, start_of_today_iso=None) -> float:
    return (
        broker_adapter.get_margins().net
        - fund_manager.today_realized_pnl_carryover(start_of_today_iso)
    )


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
            next_day_name = _next.strftime("%A")
            today_fmt = today.strftime("%d-%b-%Y")
            next_fmt = _next.strftime("%d-%b-%Y")

            if today.weekday() >= 5:
                reason = "Weekend"
            else:
                reason = get_holiday_name(today, config_dir) or "Market Holiday"

            terminal_msg = (
                "\n"
                + "-" * 50 + "\n"
                "  AlgoCore System Command Center\n"
                + "-" * 50 + "\n\n"
                "Hey boss!\n"
                "Guess what...\n\n"
                "  --> MARKET IS CLOSED <--\n\n"
                f"  Reason : {reason}\n"
                f"  Today  : {today_fmt}\n\n"
                "So yeah...\n"
                "No trades, no stress - go enjoy your day!!\n\n"
                f"  Let's connect on: {next_fmt} ({next_day_name})\n"
                + "-" * 50 + "\n"
            )
            print(terminal_msg)

            # Anti-loop: only send Telegram once per day. token_watcher may
            # restart the service multiple times on holidays; the sentinel
            # prevents flooding the channel with duplicate messages.
            sentinel = Path("logs") / f".holiday_notified_{today.isoformat()}"
            if not sentinel.exists():
                telegram_msg = (
                    "Hey boss!\n"
                    "Guess what...\n\n"
                    "MARKET IS CLOSED\n\n"
                    f"Reason : {reason}\n"
                    f"Today  : {today_fmt}\n\n"
                    "No trades, no stress - go enjoy your day!\n\n"
                    f"Let's connect on: {next_fmt} ({next_day_name})"
                )
                _send_holiday_notification(telegram_msg)
                try:
                    sentinel.parent.mkdir(parents=True, exist_ok=True)
                    sentinel.write_text(today_fmt)
                except OSError:
                    pass
            return 0
    except FileNotFoundError:
        pass  # missing holiday file: proceed with startup

    # ── FIX-189 (P1-A): service-window guard — never run overnight ───────────
    # Skip for operator/diagnostic invocations (--status/--dry-run/--interactive/
    # --resume) and overridable via TS_IGNORE_MARKET_WINDOW=1 for emergencies.
    _window_bypass = (
        args.status or args.dry_run or args.interactive or args.resume
        or os.environ.get("TS_IGNORE_MARKET_WINDOW") == "1"
    )
    _svc_now = time_authority.now_ist()
    if not _window_bypass and not _within_service_window(_svc_now):
        # Prints the START window, which is what this guard actually enforces — it runs
        # before load_all() and so cannot know the configured stop time. Naming the
        # wrong window here would mislead whoever reads it during an incident.
        print(
            f"Outside service START window "
            f"[{SERVICE_WINDOW_START.strftime('%H:%M')}-"
            f"{SERVICE_START_CUTOFF.strftime('%H:%M')} IST]; current IST "
            f"{_svc_now.strftime('%H:%M')}. Not starting (clean exit 0). "
            "(The daily stop time is separate and configured: "
            "trading_hours.service_window_end.) "
            "Token-watcher starts the service in-window after the morning "
            "token refresh. Override with TS_IGNORE_MARKET_WINDOW=1 or --resume."
        )
        return 0

    setup_logging(Path("logs"))
    _log = get_logger("main")
    _log.info("Trading System v%s starting (mode=%s)", VERSION, args.mode)

    # ── Single-instance lock (prevents stale process conflicts) ─────────────
    lock_ok, lock_reason = acquire_instance_lock()
    if not lock_ok:
        _log.critical("Instance lock failed: %s", lock_reason)
        return 1
    try:
        return _main_locked(args, config_dir)
    finally:
        release_instance_lock()


def _main_locked(args, config_dir: Path) -> int:
    """Main body after instance lock acquired. Lock released by caller."""

    # ── Phase 0b: Config load (MAIN5) ──────────────────────────────────────���
    try:
        app_config = load_all(config_dir)
    except Exception as exc:
        _log.critical("Config load failed: %s%s", exc, _config_error_detail(exc))
        return 5
    _log.info("Config loaded from %s (%d files)", config_dir, len(app_config.file_hashes))

    # CV1: Register all config values for usage tracking (post-paper audit)
    config_validator.register_all_from_app_config(app_config)

    # Service window (25-Jul-2026). The STOP time is configured; the START cutoff is a
    # module constant bound to the same schema bound (see SERVICE_START_CUTOFF). Derived
    # ONCE here and passed to the eod-self-exit thread — one parse, one source of truth.
    # Logged immediately so the running window is answerable from the boot log.
    _service_window_end = _parse_hhmm(app_config.system.trading_hours.service_window_end)
    _log.info(
        _service_window_banner(
            SERVICE_WINDOW_START, SERVICE_START_CUTOFF, _service_window_end
        )
    )

    # ── Phase 0c: StateStore + EventBus + KillSwitch + TimeAuthority (MAIN6) ─
    # P11 (14-Jul): schema migrations run ON OPEN (StateStore.__init__ → _initialize_schema),
    # so ANY process that opens the live DB with newer code would silently migrate it. AC1:
    # THIS boot path is the ONLY sanctioned migrator (allow_migrate=True); every other
    # StateStore opener uses the default False and refuses + fails loud. AC2: even here, refuse
    # while the market is open — a mid-session crash-restart with a pending migration means a
    # schema change was pushed during market hours (a rule violation); fail loud, don't rebuild
    # the live trades table under a running market. See docs/audit/migration_on_open_rule_14jul2026.md.
    from core.time_authority import now_ist as _now_ist_boot
    from datetime import time as _dtime_boot
    _th_boot = app_config.system.trading_hours
    _now_boot = _now_ist_boot()
    _mo_boot = _dtime_boot(*map(int, _th_boot.market_open.split(":")))
    _mc_boot = _dtime_boot(*map(int, _th_boot.market_close.split(":")))
    _market_open_now = (_now_boot.weekday() < 5) and (_mo_boot <= _now_boot.time() <= _mc_boot)
    store = StateStore(
        Path("data_store/trading_system.db"),
        allow_migrate=True,          # AC1: the single sanctioned migration entry point
        market_open=_market_open_now,  # AC2: even the boot path refuses while market is open
    )
    event_bus = EventBus()

    # Session mode label used for all Telegram alert titles: "[PAPER]"/"[LIVE]"
    mode_label = str(args.mode or "live").upper()

    kill_switch = KillSwitch(
        store,
        event_bus,
        get_logger("kill_switch"),
        api_failure_threshold=app_config.system.kill_switch.api_failure_threshold,
        enable_auto_trip=app_config.system.kill_switch.enable_auto_trip,
        mode=mode_label,
        emergency_exit_buffer_pct=app_config.system.capital.emergency_exit_buffer_pct,  # FIX-181
    )

    # TimeAuthority needs kill_switch for the critical-skew callback
    _init_time_authority(app_config, kill_switch)
    today_ist_date: date = time_authority.now_ist().date()

    # FIX-127: auto-clear stale kill switch from previous trading day.
    # A new day starts clean; if the trigger was legitimate, startup
    # reconciliation will re-trigger it within seconds.
    kill_switch.clear_stale_state(today_ist_date)

    # FIX-154: auto-clear scheduled kills (force_close, EOD_SQUAREOFF) even on
    # same-day restart, as long as no open positions. Emergency kills still
    # require manual --resume.
    kill_switch.auto_clear_scheduled_kill()
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
            store.close()  # FIX-169 F32
            return 4
        kill_switch.resume(reason="--resume flag", resumed_by="operator")
        _log.critical("HALT cleared via --resume flag")

    # CL4 config-hash diff check (MAIN6)
    hash_result = check_config_hash(store, app_config, _log)
    config_hash_changed = hash_result.changed

    # ── --status mode (MAIN16) ──────────────────────────────────────────────
    if args.status:
        result = _print_status(store, kill_switch, scenario_result)
        store.close()  # FIX-169 F32
        return result

    # MED #10: Write session row early — before any crash-prone Phase 0d/0e code.
    # If startup crashes mid-way, the next run will still find a session row with
    # today's date, so cold-start detection remains correct.
    # The Phase 0h call below will UPDATE this row with the final config_hash.
    _write_session(
        store=store,
        session_date=today_iso,
        mode=args.mode,
        config_hash=json.dumps(app_config.file_hashes),
        now_iso=time_authority.now_ist_iso(),
    )

    # ── Phase 0d: Startup checks (MAIN7) ────────────────────────────────────
    holidays = _load_holidays(app_config)
    special_sessions = _load_special_sessions(app_config)  # FIX-094
    th = app_config.system.trading_hours
    market_windows = MarketWindows(
        entry_start=_parse_hhmm(th.entry_start),
        entry_end=_parse_hhmm(th.entry_end),
        market_open=_parse_hhmm(th.market_open),
        market_close=_parse_hhmm(th.market_close),
        eod_squareoff=_parse_hhmm(th.eod_squareoff_time),
        eod_entry_cutoff=_parse_hhmm(th.eod_entry_cutoff),
        holidays=holidays,
        special_sessions=special_sessions,  # FIX-094
    )

    # FIX-094: Log special session override if active for today
    today_date = time_authority.now_ist().date()
    if today_date in special_sessions:
        m_open, m_close, eod_sq = special_sessions[today_date]
        _log.info(
            "FIX-094 special session override active",
            extra={
                "date": today_date.isoformat(),
                "market_open": m_open.isoformat(),
                "market_close": m_close.isoformat(),
                "eod_squareoff": eod_sq.isoformat(),
            },
        )

    # Build broker adapter early for clock check (paper mode skips live calls)
    state_machine = OrderStateMachine()
    # FIX-060: Pass shutdown_event so rate limiter aborts gracefully
    rate_limiter = RateLimiter(app_config.broker_limits, shutdown_event=_shutdown_event)
    product_resolver = ProductResolver(app_config.system.product_map)
    cost_calculator = CostCalculator(app_config.broker_costs)

    if args.mode == "paper":
        kite_client = None  # paper adapter uses quote_provider from token file
    else:
        # Pre-load primary account to resolve account-specific API key env var
        # (env has ZERODHA_API_KEY_LFL836, not the generic ZERODHA_API_KEY).
        # Full registry load happens below; this early read is only to alias the key.
        try:
            _pre_registry = AccountRegistry.load(config_dir / "accounts.csv")
            _pre_primary = _pre_registry.primary()
            os.environ["ZERODHA_API_KEY"] = os.environ[_pre_primary.api_key_env]
            _pre_token = load_token(Path("data_store/session/zerodha_token.json"))
            os.environ["ZERODHA_ACCESS_TOKEN"] = _pre_token["access_token"]
        except FileNotFoundError:
            _log.critical(
                "Token file missing. Run: python scripts/zerodha_login.py "
                "and SCP token to VM before starting in live mode."
            )
            store.close()  # FIX-169 F32
            return 6
        except KeyError as exc:
            _log.critical("Missing credential env var for live mode: %s", exc)
            store.close()  # FIX-169 F32
            return 6
        kite_client = _build_kite_client(app_config)

    is_paper = (args.mode == "paper")
    # EF-4: adapter constructed with provisional paper_capital=0.0. The real
    # value is late-bound via broker_adapter.set_paper_capital() after
    # account selection completes (see block below the interactive/
    # non-interactive branch). Do NOT read get_margins() in paper mode
    # before the late-bind call.
    broker_adapter = ZerodhaAdapter(
        kite_client=kite_client,
        rate_limiter=rate_limiter,
        product_resolver=product_resolver,
        cost_calculator=cost_calculator,
        state_machine=state_machine,
        logger=get_logger("zerodha_adapter"),
        paper_mode=is_paper,
        paper_capital=0.0,
        # paper mode needs a quote_provider so get_quote() doesn't raise
        # NotImplementedError. Provider reads token file for real Kite quotes.
        quote_provider=(_make_paper_quote_provider() if is_paper else None),
        # H-20 / ZA16a: paper needs the bus to publish synthesized
        # OrderFilled. Live adapter ignores these kwargs.
        bus=event_bus,
        paper_auto_fill_delay_sec=app_config.system.paper.auto_fill_delay_sec,
        # Audit 6.2: paper LTP-gating settings
        paper_ltp_gating_enabled=app_config.system.paper.ltp_gating_enabled,
        paper_ltp_gating_max_wait_sec=app_config.system.paper.ltp_gating_max_wait_sec,
        paper_ltp_gating_poll_sec=app_config.system.paper.ltp_gating_poll_sec,
        # BL-6: 429 exponential backoff config (lives under broker_limits.yaml)
        rate_limit_backoff=app_config.broker_limits.rate_limit_backoff,
        # SLICE2.5-P1: master delivery lock — refuses real CNC orders/GTTs when false.
        delivery_enabled=app_config.system.delivery_enabled,
        # Option A (10-Jul-2026): product-coercion guard — coerces any non-INTRADAY
        # intent to MIS at the broker chokepoint while the breaker is on (the P0 MIS-only
        # guarantee now that the load-time intent rewrite is removed).
        force_intraday_only=app_config.system.force_intraday_only,
    )

    try:
        account_registry = AccountRegistry.load(config_dir / "accounts.csv")
        _log.info(
            "AccountRegistry loaded: %d accounts (primary: %s)",
            account_registry.count(),
            account_registry.primary().account_id,
        )
    except Exception as exc:
        _log.critical("Failed to load accounts.csv: %s", exc)
        store.close()  # FIX-169 F32
        return 3

    _primary_id = account_registry.primary().account_id
    # C-2 (02-Jul-2026): WEBHOOK_SECRET is required in BOTH paper and live -- the
    # webhook is never unauthenticated in any mode (paper-parity fix; paper was
    # previously exempt, so an unset secret left the paper webhook open). Fail-fast
    # at startup, exactly like live.
    required_secrets = required_startup_secrets(_primary_id)

    # BL-20: pre-load InstrumentCache so run_all_startup_checks can enforce
    # a minimum row count (guards against startup on a stub/stale CSV).
    # A load failure is handled two ways:
    #   - file missing: check_config_files_present will block with
    #     "missing_config_files"
    #   - file present but corrupt: log + pass None → check blocks with
    #     "instrument_cache_too_small"
    instrument_cache: Optional[InstrumentCache] = None
    try:
        instrument_cache = InstrumentCache.load(config_dir / "instruments.csv")
        _log.info(
            "InstrumentCache loaded: %d instruments", instrument_cache.count()
        )
    except Exception as exc:
        _log.error(
            "InstrumentCache pre-load failed: %s "
            "(startup_checks will surface the root cause)", exc
        )

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
        instrument_cache=instrument_cache,  # BL-20
        db_path=str(Path("data_store/trading_system.db")),  # FIX-169 F34
        log_dir=Path("logs"),  # FIX-169 F34
        # F-1 (audit 02-Jul): the field lives on the nested logging config, not on
        # SystemConfig (which is extra="forbid" and has no such attribute) — the old
        # getattr(app_config.system, "min_free_disk_gb", 1.0) therefore ALWAYS fell
        # back to 1.0, silently ignoring the operator-configured 2 GB floor. Read the
        # real path so the configured startup disk floor is applied.
        min_free_disk_gb=app_config.system.logging.min_free_disk_gb,
    )

    if args.dry_run:
        _log.info("--dry-run: startup checks complete")
        print(f"Startup report: ok={report.ok}")
        print(f"  Blocking failures: {report.blocking_failures}")
        print(f"  Warnings         : {report.warnings}")
        store.close()  # FIX-169 F32
        return 0 if report.ok else 3

    if not report.ok:
        _log.critical(
            "Startup checks failed: %s", report.blocking_failures
        )
        # MISSING-DIRECTION ALERT (18-Jul-2026): if the boot is aborting because a
        # strategy YAML is missing/has an invalid `direction` (or otherwise fails to
        # validate), fire ONE loud Telegram+email alert naming the file(s) + reason
        # BEFORE we abort — so a forgotten `direction:` can't silently fail to trade.
        # We KEEP failing the boot (a broken strategy set must not run); the alert is
        # purely additive and fail-safe (scan + send never raise).
        if "invalid_strategy_configs" in report.blocking_failures:
            try:
                _bad_strats = scan_strategy_errors(config_dir / "strategies")
                if _bad_strats:
                    _alert_invalid_strategy_configs(_bad_strats, config_dir)
            except Exception as _sca_exc:  # noqa: BLE001 — alert must never mask the abort
                _log.error("strategy_config_alert failed (non-fatal): %s", _sca_exc)
        store.close()  # FIX-169 F32
        return 3

    # BL-20: startup_checks passed, so instrument_cache is non-None and
    # >= min_instrument_rows. Fallthrough guard for type-checkers.
    assert instrument_cache is not None

    # CFG-6 (2026-04-26 audit): wire paper-mode slippage now that
    # instrument_cache is loaded (P12). Late-bind keeps the existing main
    # ordering -- adapter is constructed earlier than the cache because
    # startup_checks needs both. No-op when is_paper is False.
    if is_paper:
        from broker.slippage_engine import SlippageEngine
        broker_adapter.set_slippage_engine(
            SlippageEngine(app_config.slippage, instrument_cache)
        )

    # FIX-181 (GICRE incident): wire the InstrumentCache into the adapter so
    # place_order snaps every LIMIT/SL price + trigger to a valid tick before
    # it reaches Kite. Both paper and live (parity) — an off-tick price is a
    # calculation bug in either mode and Zerodha rejects it outright.
    broker_adapter.set_instrument_cache(instrument_cache)

    # ── Account selection + token handling (SU4, SU8-SU14) ──────────────────
    _token_path = Path("data_store/session/zerodha_token.json")
    _access_token: Optional[str] = None

    if getattr(args, "interactive", False):
        # Interactive flow: account → mode → confirm → login (SU7-SU13)
        print("=" * 60)
        print("  TRADING SYSTEM v2 -- INTERACTIVE STARTUP")
        print("=" * 60)
        print()
        print(f"  Today: {today.strftime('%d-%b-%Y (%A)')} -- Trading day")
        print()
        selected_account = _interactive_select_account(account_registry)
        # Mode selection overrides --mode if interactive
        args.mode = _interactive_select_mode(selected_account)
        _interactive_confirm(selected_account, args.mode)
        _access_token = _interactive_check_or_login(
            selected_account, _token_path, today
        )
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

    # I.4 (2026-04-25): pin the broker contract. AccountRow.broker is read
    # from accounts.csv but never validated downstream -- if the CSV gets
    # "Zerodha", "ICICI", "Upstox", or any typo, the system silently goes
    # on to instantiate ZerodhaAdapter and dies with a cryptic later error.
    # Fail fast with a helpful message instead.
    _broker = (selected_account.broker or "").strip().lower()
    if _broker != "zerodha":
        _log.critical(
            "main.unsupported_broker",
            extra={"account_id": selected_account.account_id, "broker": selected_account.broker},
        )
        print(
            f"\n[FATAL] account {selected_account.account_id!r} has "
            f"broker={selected_account.broker!r}; only 'zerodha' is "
            f"supported in this build. Edit config/accounts.csv and retry.\n"
        )
        return 9

    # Inject resolved access_token into env so existing broker/feed code can read it
    if _access_token:
        os.environ["ZERODHA_ACCESS_TOKEN"] = _access_token

    # EF-4: recompute is_paper -- args.mode may have flipped during the
    # interactive mode selection above; any prior is_paper reading is now
    # stale. Every downstream read (set_paper_capital, notifier paper_mode,
    # SU19 capital branch) must use this refreshed value.
    is_paper = (args.mode == "paper")
    # effect-telemetry (ledger #1, B2): record the mode once — the assertion's
    # consequence differs by it (paper fail-fast; live CRITICAL + continue).
    effect_telemetry.set_mode(is_paper)

    # EF-4: late-bind adapter paper_capital to the selected account's value.
    # Single source of truth = AccountRow.paper_capital (accounts.csv). No-op
    # in live mode (adapter.get_margins() reads real broker margins there).
    if is_paper:
        broker_adapter.set_paper_capital(selected_account.paper_capital)

    # ── W0: config snapshot (daily-report redesign foundation) ───────────────
    # Persist the FULL resolved runtime config to config_snapshots so the daily
    # report's Config sheet — and any historically-correct report re-run — can
    # read the config AS IT WAS on this date, DB-purely. Fires in BOTH paper and
    # live (config resolution is identical); mode is a column. Placed after the
    # mode is FINAL (post interactive flip) and after --status/--dry-run/failed-
    # startup have already returned. Idempotent per (date, config_hash): a same-
    # config restart is a no-op; a config change on a same-day restart writes a
    # new row. Non-fatal — a snapshot failure must never block trading startup.
    try:
        snapshot_config(
            store,
            app_config,
            snapshot_date=today_iso,
            snapshot_ts=time_authority.now_ist_iso(),
            account_id=selected_account.account_id,
            mode=("PAPER" if is_paper else "LIVE"),
            trade_type=app_config.system.trade_type,
            logger=_log,
        )
    except Exception as exc:  # noqa: BLE001
        _log.error("config_snapshotter: snapshot failed (non-fatal): %s", exc)

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
        send_in_paper_mode=tg_cfg.telegram_alerts_in_paper_mode,
        enabled=tg_cfg.enabled,                                # TASK-10: master ON/OFF switch
        max_retries=tg_cfg.max_retries,                        # FIX-131 Item 18
        retry_backoff_seconds=tg_cfg.retry_backoff_seconds,    # FIX-131 Item 18
        rate_limit_per_minute=tg_cfg.rate_limit_per_minute,    # FIX-131 Item 18
        send_deadline_seconds=tg_cfg.send_deadline_seconds,    # M-A2: bound the caller
        email_fallback_config=alert_cfg.email_fallback,        # FIX-166 F13
    )

    # Refresh mode label — interactive startup may have changed args.mode.
    mode_label = str(args.mode or "live").upper()
    # Wire notifier to kill_switch (built before notifier existed) so soft_kill
    # alerts reach Telegram.
    kill_switch.set_notifier(notifier, mode=mode_label)

    # FIX-166 F22: wire adapter so hard_kill can exit positions via
    # _exit_all_trades_indestructible. Adapter is constructed after
    # kill_switch, so we late-bind it here (same pattern as set_notifier).
    kill_switch.set_adapter(broker_adapter)

    # Alert operator about config hash change now that notifier is ready
    if config_hash_changed:
        store.insert_system_event(
            event_type="CONFIG_DIFF",
            timestamp=time_authority.now_ist_iso(),
            details=json.dumps({"changed_files": hash_result.changed_files}),
        )
        try:
            changed = hash_result.changed_files
            files_str = (
                ", ".join(changed) if isinstance(changed, (list, tuple))
                else str(changed)
            )
            notifier.send(
                severity="WARN",
                title=f"[{mode_label}] ⚠️ Config Changed Since Last Session",
                body=f"Files: {files_str}",
                source_module="main",
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

    # FIX-128 (Fix D): late-binding ref for EodSquareoff in daily_loss_breach callback.
    # Filled after EodSquareoff is instantiated below.
    _eod_ref: dict = {"eod": None}

    # SLICE2.5-PHASE-3 (B): conditional capital allocation. Compute the ACTIVE trade
    # types from the three flags (same predicate as the delivery_lock boot log), then
    # resolve the EFFECTIVE bucket split. Flag default FALSE -> the fixed config split
    # (70/30), byte-for-byte unchanged. While force_intraday_only=true (current state)
    # intraday_active is true (coercion) AND delivery_active false -> 100/0 even if the
    # flag were on. FundManager is UNCHANGED — it only receives the final pcts.
    _ph3_delivery_active = (
        app_config.system.delivery_enabled
        and not app_config.system.force_intraday_only
        and app_config.system.trade_type in ("DELIVERY", "BOTH"))
    _ph3_intraday_active = (
        app_config.system.trade_type in ("INTRADAY", "BOTH")
        or app_config.system.force_intraday_only)
    _ph3_intraday_pct, _ph3_positional_pct = resolve_bucket_allocation(
        conditional_enabled=cap_cfg.conditional_allocation_enabled,
        delivery_active=_ph3_delivery_active,
        intraday_active=_ph3_intraday_active,
        intraday_pct=cap_cfg.intraday_bucket_pct,
        positional_pct=cap_cfg.positional_bucket_pct,
    )
    _log.info(
        "capital.bucket_allocation",
        extra={"conditional_enabled": cap_cfg.conditional_allocation_enabled,
               "delivery_active": _ph3_delivery_active,
               "intraday_active": _ph3_intraday_active,
               "intraday_pct": _ph3_intraday_pct,
               "positional_pct": _ph3_positional_pct},
    )

    fund_manager = FundManager(
        state_store=store,
        bus=event_bus,
        logger=get_logger("fund_manager"),
        intraday_bucket_pct=_ph3_intraday_pct,        # PHASE-3 (B): effective split
        positional_bucket_pct=_ph3_positional_pct,    # PHASE-3 (B): effective split
        # BUILD 1 (#1): single daily-loss source — the post-close realized breach
        # derives its ₹ limit from the SAME pct the pre-trade gate (RiskEngine)
        # uses. There is no longer an absolute capital.daily_loss_limit.
        daily_loss_limit_pct=app_config.system.risk.daily_loss_limit_pct,
        leverage_map=leverage_map,
        on_daily_loss_breach=_make_daily_loss_cb(
            kill_switch=kill_switch,
            notifier=notifier,
            mode=mode_label,
            eod_ref=_eod_ref,  # FIX-128: late-bound; filled after EodSquareoff created
        ),
        kill_switch=kill_switch,  # FM19 / BL-9: invariant violations -> hard_kill
    )
    # M-C1 / midnight day-floor (21-Jul-2026): derive the day-floor ONCE here and
    # pass the SAME value to BOTH the live-seed carryover Sigma (compute_live_seed,
    # live only) and rehydrate Phase 2 (both modes). An off-schedule boot straddling
    # midnight would otherwise let the seed and rehydrate each derive their own
    # now_ist() floor a few ms apart on OPPOSITE sides of 00:00 -> the two Sigma's
    # would span different day-row-sets and the seed<->rehydrate cancellation would
    # leave a residue of -Sigma(D). Both callees keep their own None-fallback for
    # standalone/test callers; the single-floor guarantee at BOOT is by convention
    # at these two call sites, pinned structurally in
    # tests/unit/test_mc1_live_seed_rehydrate.py
    # (test_boot_derives_the_day_floor_once_and_passes_it_to_both).
    # See docs/decisions/DESIGN_midnight_day_floor.md.
    from core.time_authority import now_ist
    _start_of_today_iso = now_ist().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()

    # SU19: paper mode uses configured paper_capital; live uses broker margins
    if args.mode == "paper":
        _startup_capital = selected_account.paper_capital
    else:
        _startup_capital = compute_live_seed(
            broker_adapter, fund_manager, _start_of_today_iso
        )
    fund_manager.initialize(_startup_capital)

    # F.1 / EF-7: paper-mode regression guard. Post-E.7, adapter.get_margins()
    # and fund_manager.total both derive from AccountRow.paper_capital and must
    # match exactly. Diverges only if the setter path has regressed (e.g.,
    # set_paper_capital not called, main.py re-ordered). Fail fast before the
    # event loop starts to avoid a flood of G3 capital_drift alerts.
    try:
        check_paper_capital_consistency(
            fund_manager=fund_manager,
            broker_adapter=broker_adapter,
            is_paper=is_paper,
            logger=_log,
        )
    except StartupCheckFailed as exc:
        _log.critical("ef7_startup_check_failed: %s", exc)
        store.close()
        return 3

    # BL-1 / FM18: replay fm_ledger + trades + orders so in-memory capital
    # state matches persisted state on a warm start. On a cold/clean start
    # there are no open trades and this is a no-op. CapitalStateInconsistent
    # signals that the persisted history itself is internally inconsistent
    # and trading cannot resume safely; we exit with code 3 (startup check
    # failure) so ops can distinguish this from the generic unexpected-
    # exception path (code 2).
    #
    # ⛔⛔ DO NOT RE-KEY THIS ON THE LEDGER. It walks OPEN/PARTIAL *trades*
    # (fund_manager.rehydrate_from_open_trades -> store.get_all_open_trades),
    # and that choice -- trade status as the source of truth, not fm_ledger --
    # is the ONLY reason a corrupt ledger cannot poison startup capital.
    #
    # WHY THIS COMMENT EXISTS AT ALL (04-Aug-2026): fm_ledger is NOT
    # self-consistent. 10 reservations (Rs1,628.13, dated 15-18 Jun 2026) carry
    # NO terminating row and never will -- their cause predates all retained
    # logs, so they are deliberately annotated and NOT reconciled (writing a
    # terminator would assert a termination nobody can prove). All 10 sit on
    # trades that are CANCELLED or CLOSED_MANUAL, i.e. terminal, so
    # get_all_open_trades() never returns them and they have never touched
    # in-memory capital.
    #
    # ⚠️ That safety is CORRECT BY ACCIDENT, and this venue is the dangerous
    # one because the wrong change LOOKS LIKE AN IMPROVEMENT: "rebuild capital
    # from the capital ledger rather than from trade rows" reads as the more
    # principled design, and it would silently inherit all 10 phantom
    # reservations at the next boot. On today's book that is ~16.5% of capital
    # -- wrong by a sixth, and not obviously broken to anyone reading the
    # startup log.
    #
    # The rule, in full, lives at docs/04_db_schema_reference.md ("THE LEDGER
    # IS NOT SELF-CONSISTENT"): capital reconstructed from fm_ledger MUST be
    # reconciled against trade status, or must exclude reservations belonging
    # to terminal trades.
    try:
        _rehydrate_summary = fund_manager.rehydrate_from_open_trades(
            _start_of_today_iso
        )
        _log.info(
            "fund_manager.rehydrated",
            extra=_rehydrate_summary,
        )
    except CapitalStateInconsistent:
        _log.critical("capital_state_inconsistent", exc_info=True)
        store.close()
        return 3

    # FIX-156: after FM rehydrate, paper adapter capital must reflect realized
    # PnL from fm_ledger replay. Without this re-sync, adapter stays at the
    # static starting capital while FM.total includes replayed PnL, causing
    # permanent capital drift alerts every reconciler cycle.
    if is_paper:
        _fm_total = fund_manager.get_snapshot().total
        broker_adapter.set_paper_capital(_fm_total)
        _log.info(
            "paper_capital re-synced post-rehydrate",
            extra={"fm_total": _fm_total},
        )

    ps_cfg = app_config.system.position_sizing
    position_sizer = PositionSizer(
        fund_manager=fund_manager,
        leverage_map=leverage_map,
        risk_per_trade_pct=ps_cfg.risk_per_trade_pct,
        max_concentration_pct=ps_cfg.max_concentration_pct,
        min_qty_threshold=ps_cfg.min_qty_threshold,
        tier_multipliers={
            "HIGH": ps_cfg.tier_multipliers.HIGH,
            "MEDIUM": ps_cfg.tier_multipliers.MEDIUM,
            "LOW": ps_cfg.tier_multipliers.LOW,
        },
        logger=get_logger("position_sizer"),
        instrument_cache=instrument_cache,  # IC7: lot_size from cache
        lot_skew_rejection_threshold=ps_cfg.lot_skew_rejection_threshold,  # FIX-021
        max_position_value_pct=ps_cfg.max_position_value_pct,  # FIX-144 / BUILD 1 (#2)
        enabled=ps_cfg.enabled,                # Diary #4: tier-multiplier ON/OFF switch
        flat_value_rs=ps_cfg.flat_value_rs,    # Diary #4: flat Rs/order when OFF
        # V3 03.06 delivery scaffold (INERT; None → global values, byte-identical).
        delivery_risk_per_trade_pct=ps_cfg.delivery_risk_per_trade_pct,
        delivery_max_position_value_pct=ps_cfg.delivery_max_position_value_pct,
    )
    # Diary #4: surface the active sizing mode at startup (one info-level line).
    if ps_cfg.enabled:
        _tw = ps_cfg.tier_multipliers
        _log.info(
            "position_sizing.mode",
            extra={"mode": "ON",
                   "tier_weights": {"HIGH": _tw.HIGH, "MEDIUM": _tw.MEDIUM, "LOW": _tw.LOW},
                   "dynamic_by_winrate": ps_cfg.dynamic_by_winrate},
        )
        print(f"Tier multiplier: ON (tier weights: {_tw.HIGH}/{_tw.MEDIUM}/{_tw.LOW} x perf_weights)")
    else:
        _log.info(
            "position_sizing.mode",
            extra={"mode": "OFF_FLAT", "flat_value_rs": ps_cfg.flat_value_rs},
        )
        print(f"Tier multiplier: OFF (flat Rs {ps_cfg.flat_value_rs:.0f}/order, safety ceilings active)")

    # Q4(b) capital-safety: LIVE requires a real kill_switch wired into the RiskEngine
    # we are about to build. Boot-time fail-fast (a day not started beats a day run
    # without the emergency brake). RiskEngine's runtime warn+skip is left UNCHANGED.
    try:
        check_kill_switch_present(kill_switch=kill_switch, is_paper=is_paper, logger=_log)
    except StartupCheckFailed as exc:
        _log.critical("q4b_kill_switch_missing_live: %s", exc)
        store.close()
        return 3

    risk_cfg = app_config.system.risk
    # BUILD 1 (#3, 24-Jun): the FIX-190 live_test_mode swap was REMOVED. The
    # live_test_* caps had been set equal to the base caps for parity, so the
    # swap was a no-op that only looked active. The base caps below are now the
    # sole authority in both paper and live.
    risk_engine = RiskEngine(
        fund_manager=fund_manager,
        state_store=store,
        max_open_positions=risk_cfg.max_open_positions,
        max_daily_trades=risk_cfg.max_daily_trades,
        one_trade_per_symbol_direction_per_day=risk_cfg.one_trade_per_symbol_direction_per_day,
        max_sector_exposure_pct=risk_cfg.max_sector_exposure_pct,
        max_consecutive_losses=risk_cfg.max_consecutive_losses,
        daily_loss_limit_pct=risk_cfg.daily_loss_limit_pct,
        # B-1 (02-Jul): enforce unrealized MTM in the daily-loss gate (SHADOW default).
        daily_loss_include_unrealized=risk_cfg.daily_loss_include_unrealized,
        # HIGH #2: sector from instrument_cache (was lambda: "UNKNOWN")
        sector_lookup_fn=lambda sym: instrument_cache.sector(sym),
        # F1 (16-Jul): SECTOR_EXPOSURE gate mode (observe default = log-only; flip to enforce
        # only after an observe soak + Rama's approval). Same lookup source as create_trade.
        sector_cap_mode=risk_cfg.sector_cap_mode,
        logger=get_logger("risk_engine"),
        kill_switch=kill_switch,
        # SLICE2.5-PHASE-3 (A): separate delivery (CNC) count caps (inert while
        # force_intraday_only=true — no CNC entries reach the positional branch).
        max_open_delivery_positions=risk_cfg.max_open_delivery_positions,
        max_daily_delivery_trades=risk_cfg.max_daily_delivery_trades,
    )

    live_feed = LiveFeedManager(
        api_key=os.environ.get(selected_account.api_key_env, ""),
        access_token=os.environ.get("ZERODHA_ACCESS_TOKEN", ""),
        on_critical_failure=lambda reason: _make_critical_failure_cb(
            kill_switch, notifier, mode_label
        )("live_feed", reason),
        logger=get_logger("live_feed"),
        paper_mode=is_paper,
        # B.6 / Audit 12: tick-age watchdog gated on market hours so the
        # alert does not flap pre-open / post-close.
        market_windows=market_windows,
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
        mode=mode_label,
    )

    # Wire instrument_cache for token->symbol resolution (SH5)
    shadow_tracker.set_instrument_cache(instrument_cache)

    # Tick dispatcher routes to candle_store and shadow_tracker (SH5)
    def _tick_dispatcher(ticks) -> None:
        for t in ticks:
            ts = t.get("timestamp") or time_authority.now_ist()
            vol = int(t.get("volume") or 0)
            candle_store.on_tick(t["instrument_token"], t["last_price"], ts, volume=vol)
            shadow_tracker.on_tick(t)

    live_feed.register_callback(_tick_dispatcher)

    smart_tgt = SmartTgtManager(
        adapter=broker_adapter,
        state_store=store,
        candle_store=candle_store,
        logger=get_logger("smart_tgt_manager"),
        quote_fn=broker_adapter.get_quote,
        enabled=True,
        # B.4 / Audit 5.4: production wires async modify so the candle-close
        # consumer thread is not blocked by per-trade broker HTTP roundtrips.
        async_modify=True,
        # D.1 (2026-04-25): trail bursts share the same "order" bucket as
        # fresh placements; pass the same RateLimiter so they pace together.
        rate_limiter=rate_limiter,
        volume_dependent_trails=app_config.system.smart_tgt.volume_dependent_trails,  # FIX-026
        max_modify_failures=app_config.system.smart_tgt.max_modify_failures,        # FIX-142
    )
    smart_tgt.set_instrument_cache(instrument_cache)  # Audit #8: tick rounding on SL trail

    # Reconnect chain (ST13, MAIN8 step 11)
    live_feed.set_on_reconnect_callback(
        lambda ts: (candle_store.mark_reconnect(ts), smart_tgt.on_reconnect(ts))
    )

    om_cfg = app_config.system.order_monitor
    cb_cfg = app_config.system.circuit_breaker
    order_monitor = OrderMonitor(
        adapter=broker_adapter,
        state_machine=state_machine,
        bus=event_bus,
        logger=get_logger("order_monitor"),
        poll_interval_sec=om_cfg.poll_interval_sec,
        fill_timeout_sec=om_cfg.fill_timeout_sec,
        on_orphan_callback=_make_orphan_cb(kill_switch, notifier, mode_label),
        on_critical_failure=_make_api_failure_hard_kill_cb(kill_switch, notifier, mode_label),
        partial_fill_timeout_minutes=cb_cfg.partial_fill_timeout_minutes,  # FIX-128
        max_api_failures=cb_cfg.max_api_failures,                          # FIX-128
        force_close_time=cb_cfg.force_close_time,                          # FIX-128
        on_force_close=_make_force_close_cb(kill_switch, notifier, mode_label),  # FIX-128
        min_pending_rr=app_config.system.entry_gate.min_pending_rr,             # FIX-141
    )

    co_protocol = CoPlusTgtProtocol(
        adapter=broker_adapter,
        logger=get_logger("order_protocol_co"),
    )
    limit_protocol = LimitTripleProtocol(
        adapter=broker_adapter,
        logger=get_logger("order_protocol_limit"),
        sl_limit_offset_pct=app_config.system.capital.sl_limit_offset_pct,  # P0 SL-M->SL
    )
    # SLICE2.5-P1: OCO-GTT placer — overnight protection for DELIVERY (CNC) trades.
    # gtt_tgt offset reuses the small intraday sl_limit_offset_pct; gtt_sl offset is the
    # dedicated deep 3% protective floor. Inert until a DELIVERY intent flows (gated by
    # delivery_enabled + force_intraday_only).
    cnc_gtt_placer = CncGttPlacer(
        broker_adapter,
        gtt_sl_limit_offset_pct=app_config.system.capital.gtt_sl_limit_offset_pct,
        gtt_tgt_limit_offset_pct=app_config.system.capital.sl_limit_offset_pct,
        delivery_enabled=app_config.system.delivery_enabled,
        logger=get_logger("cnc_gtt"),
        quote_fn=broker_adapter.get_quote,
        tick_fn=broker_adapter._resolve_tick,
        store=store,  # SLICE2.5-P2: durable gtt_state persistence + boot hydration
    )
    # SLICE2.5-P2: rebuild the one-GTT-per-trade hot cache from the durable gtt_state
    # so a restart MODIFIES (never duplicates) a surviving GTT. Never blocks startup.
    cnc_gtt_placer.hydrate_from_store()
    full_engine = FullEntryEngine(
        co_protocol=co_protocol,
        limit_protocol=limit_protocol,
        logger=get_logger("full_entry_engine"),
        cnc_gtt_placer=cnc_gtt_placer,
    )
    order_manager = OrderManager(
        state_store=store,
        logger=get_logger("order_manager"),
        bus=event_bus,  # BL-12: subscribes to OrderStatusChanged
    )
    # MIS Learned Blocklist (source-free; Step 0 proved no clean MIS source exists).
    # Shared instance: the OrderPlacer 400-handler RECORDS broker MIS-blocks into it;
    # the SecondaryScreener READS it to pre-drop future MIS signals (within a re-test
    # TTL). Recording is always-on; the screener DROP is gated by mis_filter.enabled.
    mis_blocklist = MisLearnedBlocklist(
        path=Path("data_store/mis_blocklist.json"),
        ttl_days=app_config.system.mis_filter.ttl_days,
        logger=get_logger("mis_blocklist"),
    )

    order_placer = OrderPlacer(
        entry_engine=full_engine,
        order_manager=order_manager,
        fund_manager=fund_manager,
        bus=event_bus,
        logger=get_logger("order_placer"),
        order_monitor=order_monitor,              # BL-7b: enables A.3.c track() calls
        cost_calculator=cost_calculator,          # BL-10a: exit-path cost computation
        kill_switch=kill_switch,
        product_resolver=product_resolver,        # HIGH #7: correct product codes in DB
        smart_tgt_manager=smart_tgt,              # BL-7b: CO_PLUS_TGT trail wiring
        smart_tgt_config=app_config.system.smart_tgt,  # BL-7b: trigger_pct/step_pct
        rate_limit_backoff=app_config.broker_limits.rate_limit_backoff,  # BL-19
        entry_gate_slippage_buffer=app_config.system.entry_gate.slippage_buffer,  # FIX-025
        max_entry_slippage_pct=app_config.system.entry_gate.max_entry_slippage_pct,  # FIX-128
        slippage_control=app_config.system.entry_gate.slippage_control,  # sl_fraction/flat_tiers/pct abort
        slippage_bands=app_config.system.slippage_bands,  # Phase 3a: bands for override resolution
        notifier=notifier,
        mode=mode_label,
        live_feed=live_feed,  # FIX-061: LTP retry for exit validation errors
        broker_adapter=broker_adapter,  # FIX-072: margin cache invalidation on 16388
        market_windows=market_windows,  # FIX-073: EOD entry cutoff check
        min_effective_rr=app_config.system.entry_gate.min_effective_rr,  # FIX-136 Item 54
        emergency_exit_buffer_pct=app_config.system.capital.emergency_exit_buffer_pct,  # FIX-181
        mis_blocklist=mis_blocklist,  # MIS learned blocklist: record broker MIS-blocks
        sector_unknown_alert_pct=risk_cfg.sector_unknown_alert_pct,  # F1 (16-Jul): data-quality alert threshold
    )
    order_placer.set_instrument_cache(instrument_cache)  # IC8: tick rounding

    # Slippage intelligence Phase 1 (v31): record raw execution facts to
    # order_execution_log / trade_slippage_log / market_execution_context via
    # ASYNC event subscribers — fully decoupled; can never block trade execution.
    try:
        from orders.slippage_recorder import SlippageRecorder
        SlippageRecorder(
            store, event_bus, get_logger("slippage_recorder"),
            price_bands=app_config.system.slippage_bands,
            adapter=broker_adapter,
        )
    except Exception as _sr_exc:  # noqa: BLE001 — never break startup
        _log.warning("slippage_recorder init failed (non-fatal): %s", _sr_exc)

    # SLICE2.5-P2: overnight CNC-GTT reconcile — re-verifies / recreates / finalises
    # the OCO GTT that protects a delivery position. Wired into the reconciler
    # (startup [4a] + 15-min in-hours cadence [4b]). delivery_enabled=false in Phase 2
    # (durability + safety only — no activation).
    from core.time_authority import now_ist as _now_ist_mh
    _market_hours_fn = lambda: market_windows.is_market_open(_now_ist_mh())  # noqa: E731
    cnc_gtt_monitor = CncGttMonitor(
        store=store,
        adapter=broker_adapter,
        placer=cnc_gtt_placer,
        fund_manager=fund_manager,
        kill_switch=kill_switch,
        notifier=notifier,
        bus=event_bus,
        logger=get_logger("cnc_gtt_monitor"),
        mode=mode_label,
        market_hours_fn=_market_hours_fn,
        cost_calculator=cost_calculator,   # E4: real costs on GTT closes
    )

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
        mode=mode_label,
        cnc_gtt_monitor=cnc_gtt_monitor,        # SLICE2.5-P2 (4a/4b)
        market_hours_fn=_market_hours_fn,
        cost_calculator=cost_calculator,        # E4: real costs on CHECK1/CHECK4
    )

    # Task (2026-06-19): standalone TGT retry — re-place a TGT left unplaced by
    # FIX-190 Bug C (SL still protects) on an exponential backoff.
    tgt_cfg = app_config.system.tgt_retry
    tgt_retry_manager = TGTRetryManager(
        state_store=store,
        order_placer=order_placer,
        notifier=notifier,
        kill_switch=kill_switch,
        logger=get_logger("tgt_retry_manager"),
        poll_interval_sec=tgt_cfg.poll_interval_sec,
        max_attempts=tgt_cfg.max_attempts,
        backoff_base_sec=tgt_cfg.backoff_base_sec,
        enabled=tgt_cfg.enabled,
        mode=mode_label,
    )

    strategies_dir = config_dir / "strategies"
    loader = StrategyLoader()
    strategies = loader.load_all_strategies(
        strategies_dir,
        force_intraday_only=app_config.system.force_intraday_only,  # P0 MIS-only safety
    )

    # Slice 2: one-line strategy-control summary at boot (same resolver the entry
    # gate + status table use) so the WILL/WON'T TRADE state is visible in the log.
    try:
        from strategies.control import strategy_will_trade as _swt
        _tt = app_config.system.trade_type
        _fio = app_config.system.force_intraday_only
        _will, _wont = [], []
        for _nm, _s in sorted(strategies.items()):
            _v = _swt(_s, trade_type=_tt, force_intraday_only=_fio)
            (_will if _v.will_trade else _wont).append(
                _nm if _v.will_trade else f"{_nm} ({_v.reason})"
            )
        get_logger("main").info(
            "strategy_control.summary",
            extra={
                "trade_type": _tt, "force_intraday_only": _fio,
                "will_trade_count": len(_will), "wont_trade_count": len(_wont),
                "will_trade": _will, "wont_trade": _wont,
            },
        )
    except Exception as _sc_exc:  # never let the summary block startup
        get_logger("main").warning(
            "strategy_control.summary_failed", extra={"error": str(_sc_exc)}
        )

    # SLICE2.5-P1: surface the master delivery lock + the combined requirement at boot.
    _de = app_config.system.delivery_enabled
    _fio_lock = app_config.system.force_intraday_only
    _de_ready = (_de and not _fio_lock
                 and app_config.system.trade_type in ("DELIVERY", "BOTH"))
    get_logger("main").info(
        "delivery_lock.status",
        extra={
            "delivery_enabled": _de, "force_intraday_only": _fio_lock,
            "trade_type": app_config.system.trade_type, "cnc_orders_possible": _de_ready,
            "note": ("real CNC/GTT requires delivery_enabled=true AND "
                     "force_intraday_only=false AND trade_type in {DELIVERY,BOTH}"),
        },
    )
    if not _de:
        get_logger("main").warning(
            "delivery_lock: delivery_enabled=FALSE — all CNC orders/GTTs are REFUSED at "
            "the broker boundary (SLICE2.5-P1 master lock). A DELIVERY-intent strategy "
            "either trades intraday (MIS) under force_intraday_only or not at all."
        )
    elif _de and _fio_lock:
        get_logger("main").warning(
            "delivery_lock: delivery_enabled=TRUE but force_intraday_only=TRUE — the "
            "breaker still rewrites every strategy to INTRADAY, so NO CNC order can be "
            "placed. Set force_intraday_only=false to actually trade delivery."
        )

    # Phase 3a: warn (never reject) if a slippage-tolerance override key looks off
    # — an extreme fraction, or a by_symbol/by_strategy typo that would be SILENTLY
    # IGNORED at resolution time (now that we know the instrument + strategy sets).
    # Best-effort; never blocks startup.
    try:
        from orders.order_placer import validate_slippage_overrides
        _ov = app_config.system.entry_gate.slippage_control.overrides
        _known_syms = {r.symbol for r in instrument_cache.all_rows()}
        _known_strats = set(strategies.keys())
        for _w in validate_slippage_overrides(_ov, _known_syms, _known_strats):
            _log.warning("slippage_overrides: %s", _w)
    except Exception as _ov_exc:  # noqa: BLE001 — validation must never break startup
        _log.warning("slippage override validation skipped (non-fatal): %s", _ov_exc)

    scorer = QualityScorer(
        weights=app_config.scoring,
        logger=get_logger("quality_scorer"),
    )
    step_executor = StepExecutor(
        logger=get_logger("step_executor"),
        market_open=market_windows.market_open,  # Audit #18
    )
    # V3 03.03: pre-scoring Hard-Gate (circuit + proximity + freshness). Consumed
    # by the screener only when scoring.v3_hardgate_mode != off (default off →
    # dormant); built always so shadow/enforce need no re-wiring.
    from screening.hard_gate import HardGate
    hard_gate = HardGate(
        now_fn=time_authority.now_ist,
        logger=get_logger("hard_gate"),
        freshness_max_sec=app_config.scoring.v3_freshness_max_sec,
        circuit_proximity_reject_enabled=app_config.system.entry_gate.circuit_proximity_reject_enabled,
    )
    screener = SecondaryScreener(
        step_executor=step_executor,
        quality_scorer=scorer,
        state_store=store,
        quote_fn=broker_adapter.get_quote,
        logger=get_logger("secondary_screener"),
        # NOCIL fix: pre-fill circuit-proximity reject (fast-disable via YAML).
        circuit_proximity_reject_enabled=app_config.system.entry_gate.circuit_proximity_reject_enabled,
        # MIS learned blocklist: pre-drop MIS-blocked symbols (default OFF -> dormant).
        mis_blocklist=mis_blocklist,
        mis_filter_enabled=app_config.system.mis_filter.enabled,
        mis_filter_shadow=app_config.system.mis_filter.shadow,
        resolve_product=lambda intent: product_resolver.resolve(intent, "zerodha"),
        # V3 03.03/03.04 — Hard-Gate + scorer re-scale (default-OFF via config).
        hard_gate=hard_gate,
        scoring_config=app_config.scoring,
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
        market_close=app_config.system.trading_hours.market_close,
        notifier=notifier,
        mode=mode_label,
        # Audit 3.3 + 5.2: EOD LIMIT_THEN_MARKET protocol controls
        exit_protocol=app_config.system.eod_squareoff.exit_protocol,
        limit_aggressive_pct=app_config.system.eod_squareoff.limit_aggressive_pct,
        limit_grace_sec=app_config.system.eod_squareoff.limit_grace_sec,
    )
    # FIX-128 (Fix D): wire EodSquareoff into the daily loss callback late-binding ref.
    _eod_ref["eod"] = eod

    # FIX-186 (FIX 2): wire the stale-order sweep so EOD finalizes any orphan
    # order rows left non-terminal by a broker-side cancel (the 17-Jun IRFC leak).
    eod.set_stale_order_sweep(order_reconciler.sweep_stale_orders)

    # FIX-128 (Fix E): Token expiry monitor — checks Zerodha token every 30 min.
    # Paper mode: no-op (no real token required). Does NOT start until Phase 0g.
    token_monitor = TokenMonitor(
        profile_fn=broker_adapter._kite.profile if not is_paper else (lambda: None),
        on_expiry=lambda: kill_switch.soft_kill(
            reason="token_expired", triggered_by="token_monitor"
        ),
        logger=get_logger("token_monitor"),
        check_interval_sec=1800,  # 30 minutes
        paper_mode=is_paper,
        market_open=app_config.system.trading_hours.market_open,
        market_close=app_config.system.trading_hours.market_close,
        notifier=notifier,
        mode=mode_label,
    )

    signal_queue: queue.Queue = queue.Queue(
        maxsize=app_config.system.signal_queue.capacity
    )

    # Create webhook_receiver first so we can wire in_flight_release to signal_processor
    webhook_receiver = WebhookReceiver(
        signal_queue=signal_queue,
        state_store=store,
        config=app_config,
        market_windows=market_windows,
        kill_switch=kill_switch,
        logger=get_logger("webhook_receiver"),
        secret_token=os.environ.get("WEBHOOK_SECRET"),
    )

    # SNR-DETECTOR-V1 + V2: shared market-data fetch closure, built ONCE if EITHER
    # the V1 observer or the V2 WAIT_FOR_RETEST feature is enabled. Both default OFF
    # → nothing constructed → zero pipeline change (dormant on deploy). Parity: the
    # same wiring runs in paper + live (market data is real in both).
    _sr_cfg = app_config.system.sr_detector
    _v1_on = _sr_cfg.enabled
    _v2_on = getattr(_sr_cfg, "wait_for_retest_enabled", False)
    # SNR-V2 Phase B: structure-aware exit. Shares the ZoneCache/ZoneWarmer infra
    # (so it builds when EITHER retest or structure-exit is on). Dormant by default.
    _struct_exit_cfg = app_config.system.structure_exit
    _struct_exit_on = getattr(_struct_exit_cfg, "structure_exit_enabled", False)
    # Q4(c) capital-safety construction guard: structure_exit makes StructureExitManager
    # the SINGLE SL owner; a strategy that also trails its SL (trailing_sl_enabled) would
    # race it on one leg. The rule lives in the config auditor (single source, group A);
    # config_loader ran group A WITHOUT strategies, so re-run it here WITH the loaded
    # strategies and fail-fast BEFORE any structure-exit / zone infra is built. `is True`
    # (not truthy): a genuine bool only — a MagicMock/proxy config in unit tests must not
    # trip the guard; byte-identical when structure-exit is off. We act ONLY on the A4
    # finding (not raise_if_blocked) so an unrelated group-A block can never mis-fire here.
    if _struct_exit_on is True:
        _a4 = [f for f in audit_config(
                   app_config.system, strategies=strategies, groups="A").blocks
               if f.code == "A4_structure_exit_trailing_sl"]
        if _a4:
            _log.critical("q4c_structure_exit_trailing_sl_contradiction: %s", _a4[0].message)
            store.close()
            return 3
    # SLICE2.5 #16a (27-Jul-2026) — the delivery capital foot-gun, same shape as Q4(c)
    # above and for the same reason: config_loader runs group A WITHOUT strategies, so
    # A5 (delivery live + fixed bucket split + a DELIVERY-ONLY active book => the
    # intraday bucket's share of capital is stranded) is invisible at config load. It
    # would otherwise surface only in the 08:30 pre-flight EMAIL — a report, not a gate.
    # Re-run group A here WITH the loaded strategies and fail-fast before any capital
    # is reserved.
    #
    # Gated on delivery_enabled, which has been false since the 15-Jun incident that
    # created the lock: on every ordinary boot this is a byte-identical no-op, and it
    # can only fire on a day someone deliberately turned delivery on. `is True` (not
    # truthy) for the same reason as Q4(c) — a MagicMock config in a unit test must not
    # trip it. Exit 3, like Q4(c): RestartPreventExitStatus="3 4", so a config block
    # stops cleanly instead of restart-looping.
    if getattr(app_config.system, "delivery_enabled", False) is True:
        _a5 = [f for f in audit_config(
                   app_config.system, strategies=strategies, groups="A").blocks
               if f.code == "A5_delivery_without_conditional_allocation"]
        if _a5:
            _log.critical("slice25_delivery_capital_footgun: %s", _a5[0].message)
            store.close()
            return 3
    # V3 03.02: index-level Market Regime shadow engine (default-off). Shares the
    # same rate-limited OHLC fetch closure (reused for the index by config token).
    _regime_cfg = getattr(app_config.system, "regime", None)
    _regime_on = bool(getattr(_regime_cfg, "enabled", False))
    # V3 Step 10: decision-chain shadow enrichment (default-off). Also needs the
    # shared rate-limited OHLC fetch closure (for its own truncating fetcher).
    _v3_cfg = getattr(app_config.system, "v3_chain", None)
    _v3_chain_on = bool(_v3_cfg is not None and getattr(_v3_cfg, "v3_chain_mode", "off") != "off")
    # V3 Step 10b: PB-01 overnight watchlist + next-morning entry stage (default-off).
    # Also rides the shared rate-limited OHLC fetch closure (EOD LEVEL compute + the
    # 09:20-11:00 5-min retest poll). enabled=false → nothing constructed → byte-identical.
    _watchlist_cfg = getattr(app_config.system, "watchlist", None)
    _watchlist_on = bool(_watchlist_cfg is not None and getattr(_watchlist_cfg, "enabled", False))
    _sr_fetch_fn = None
    if _v1_on or _v2_on or _struct_exit_on or _regime_on or _v3_chain_on or _watchlist_on:
        _md_kite = _build_market_data_kite(is_paper, kite_client)
        if _md_kite is None:
            _log.warning(
                "sr_detector/retest enabled but no market-data kite handle "
                "(no token?) — fetches will fail safe (fetch_failed rows)"
            )
        _sr_fetch_fn = _make_sr_fetch_fn(_md_kite, rate_limiter)

    # SNR-DETECTOR-V1 shadow observer (post-place; non-gating).
    sr_detector = None
    if _v1_on:
        try:
            from sr_detector import build_sr_detector
            sr_detector = build_sr_detector(
                config=_sr_cfg, fetch_fn=_sr_fetch_fn,
                instrument_cache=instrument_cache, store=store,
                logger=get_logger("sr_detector"), mode=mode_label,
                # V3 03.01 Layer-A anchors: inject NSE session bounds from the
                # system's MarketWindows (never hardcode 09:15). Used only when
                # sr_detector.intraday_anchors_enabled is on (default OFF).
                session_open=market_windows.market_open,
                session_close=market_windows.market_close,
            )
            sr_detector.start()
            _log.info("sr_detector: ENABLED and started (mode=%s)", mode_label)
        except Exception as exc:  # never let the detector break startup
            _log.error("sr_detector wiring failed (continuing without it): %s", exc)
            sr_detector = None

    # V3 03.02: Market Regime shadow runner (index-level; NON-GATING). Dormant by
    # default; when regime.enabled it computes/logs/persists the regime once per
    # cycle. It gates NOTHING (no order/score/size/kill-switch). Fail-safe by
    # construction — a broken regime NEVER halts the book.
    market_regime_runner = None
    if _regime_on:
        try:
            from sr_detector.fetch import OhlcFetcher
            from regime import MarketRegimeShadowRunner, build_market_regime
            _regime_fetcher = OhlcFetcher(
                _sr_fetch_fn, instrument_cache,
                lookback_days=int(getattr(_regime_cfg, "daily_lookback_days", 400)),
                logger=get_logger("market_regime"), now_fn=time_authority.now_ist,
                cache_ttl_sec=float(getattr(_sr_cfg, "cache_ttl_sec", 1800.0)))
            effect_telemetry.register_constructed("ohlc_fetchers")  # B2 (idempotent)
            _regime_engine = build_market_regime(
                config=_regime_cfg, fetcher=_regime_fetcher,
                logger=get_logger("market_regime"), now_fn=time_authority.now_ist,
                market_windows=market_windows,
                exchange_status_fn=None,  # no positive halt feed yet → extreme_flag stays FALSE
            )
            market_regime_runner = MarketRegimeShadowRunner(
                engine=_regime_engine, logger=get_logger("market_regime"),
                now_fn=time_authority.now_ist, market_windows=market_windows,
                interval_sec=float(getattr(_regime_cfg, "compute_interval_sec", 60.0)),
                persist_path=str(Path("data_store/regime/regime_state.json")))
            market_regime_runner.start()
            _log.info("market_regime: ENABLED and started (shadow, mode=%s)", mode_label)
        except Exception as exc:  # never let regime break startup
            _log.error("market_regime wiring failed (continuing without it): %s", exc)
            market_regime_runner = None

    # SNR-V2 Phase A: ZoneCache + ZoneWarmer (built BEFORE SignalProcessor so the
    # warmer can be injected). The RetestMonitor + Diverter are built AFTER sp
    # (they need continue_from_retest). All dormant when wait_for_retest_enabled=false.
    zone_warmer = None
    _v2_zone_cache = None
    retest_monitor = None
    structure_exit_manager = None
    if _v2_on or _struct_exit_on:
        try:
            from sr_detector.zone_cache import ZoneCache
            from sr_detector.zone_warmer import ZoneWarmer
            from sr_detector.fetch import OhlcFetcher
            from sr_detector.zone_builder import build_scoring_params, build_zone_knobs
            _v2_knobs = build_zone_knobs(_sr_cfg)
            _v2_scoring = build_scoring_params(_sr_cfg)
            _v2_zone_cache = ZoneCache(
                ttl_sec=_sr_cfg.zone_cache_ttl_sec, now_fn=time_authority.now_ist)
            _structure_fetcher = OhlcFetcher(
                _sr_fetch_fn, instrument_cache, lookback_days=_sr_cfg.lookback_days,
                logger=get_logger("sr_zone_warmer"), now_fn=time_authority.now_ist,
                cache_ttl_sec=_sr_cfg.cache_ttl_sec)
            effect_telemetry.register_constructed("ohlc_fetchers")  # B2 (idempotent)
            zone_warmer = ZoneWarmer(
                fetcher=_structure_fetcher, cache=_v2_zone_cache, knobs=_v2_knobs,
                scoring=_v2_scoring, logger=get_logger("sr_zone_warmer"),
                now_fn=time_authority.now_ist,
                rewarm_margin_sec=_sr_cfg.zone_rewarm_margin_sec)
        except Exception as exc:
            _log.error("sr_detector V2 ZoneWarmer wiring failed (no retest): %s", exc)
            zone_warmer = None
            _v2_zone_cache = None

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
        tgt_min_pct=sp_cfg.tgt_min_pct,              # BL-16
        # Bug G: entry throttle (global min-gap + burst + per-symbol cooldown)
        min_gap_between_entries_sec=getattr(sp_cfg, "min_gap_between_entries_sec", 0.0),
        entry_burst_window_sec=getattr(sp_cfg, "entry_burst_window_sec", 60.0),
        entry_burst_max=getattr(sp_cfg, "entry_burst_max", 0),
        per_symbol_cooldown_sec=getattr(sp_cfg, "per_symbol_cooldown_sec", 0.0),
        notifier=notifier,
        mode=mode_label,
        # B.5 / Audit 5.1: re-entry guard. Skips a new signal whose symbol
        # has an active simulated inning (real trade closed, shadow inning
        # still running) so real + shadow positions never overlap.
        shadow_tracker=shadow_tracker,
        # SNR-DETECTOR-V1: async non-gating S&R observer (None when disabled).
        sr_detector=sr_detector,
        # SNR-V2 Phase A: ZoneWarmer (enqueue on arrival) + structure-SL buffer.
        zone_warmer=zone_warmer,
        retest_sl_buffer_pct=_sr_cfg.sl_buffer_pct,
        # SP7: wire in_flight release so processed signals don't stay locked
        in_flight_release_fn=webhook_receiver.release_in_flight,
        # Use same expiry as webhook_receiver (config signal_queue.expiry_sec)
        signal_expiry_sec=app_config.system.signal_queue.expiry_sec,
        # FIX-067: quote function for momentum fresh LTP fetch
        quote_fn=broker_adapter.get_quote,
        # FIX-130 Item 6: intraday strategy circuit breaker
        strategy_governor=_build_strategy_governor(store, app_config, notifier, mode_label),
        # Slice 2: strategy-control gate inputs (LAYER 1 master + LAYER 0 breaker).
        trade_type=app_config.system.trade_type,
        force_intraday_only=app_config.system.force_intraday_only,
    )

    # ── V3 03.05 Portfolio Allocator (ranked batch admission) — default-OFF ──
    # off (default): NOT constructed → signal_processor keeps allocator=None → the FCFS
    # admission path is BYTE-IDENTICAL. shadow: a non-blocking regret observer runs
    # alongside live FCFS (never reserves/places). enforce: a single admission worker
    # ranks each candle-window batch and governs admission via the SAME reserve+place
    # primitives (built here, but gated on the flag + N shadow sessions + a cutover).
    portfolio_allocator = None
    _alloc_cfg = getattr(app_config.system, "portfolio_allocator", None)
    if _alloc_cfg is not None and getattr(_alloc_cfg, "allocator_mode", "off") != "off":
        try:
            from allocation import PortfolioAllocator
            portfolio_allocator = PortfolioAllocator(
                config=_alloc_cfg,
                fund_manager=fund_manager,
                max_open=int(getattr(risk_engine, "_max_open", 5)),
                active_count_fn=store.count_active_positions,
                logger=get_logger("portfolio_allocator"),
                now_fn=time_authority.now_ist,
                portfolio_lock=fund_manager.portfolio_lock,
                enforce_admit_fn=signal_processor.admit_prepared,
                enforce_reject_fn=signal_processor.reject_prepared,
                # A2/A4: v3_only scope → only V3-playbook strategies (none exist yet →
                # enforce governs nothing even if flipped). Existing scanners keep FCFS.
                v3_scope_fn=lambda s: bool(getattr(s, "v3_playbook", False)),
            )
            signal_processor.set_allocator(portfolio_allocator)
            portfolio_allocator.start()
            _log.info(
                "portfolio_allocator: ENABLED (mode=%s scope=%s)",
                _alloc_cfg.allocator_mode, _alloc_cfg.enforce_scope,
            )
        except Exception as exc:  # never let the allocator break startup — fall back to FCFS
            _log.error("portfolio_allocator wiring failed (continuing FCFS): %s", exc)
            portfolio_allocator = None
            try:
                signal_processor.set_allocator(None)
            except Exception:
                pass

    # ── V3 Step 10 decision chain (SHADOW enrichment) — default-OFF ──
    # off (default): NOT constructed → signal_processor keeps v3_chain=None → the hot-
    # path hook is a single skipped flag check (BYTE-IDENTICAL). shadow: a guarded fire-
    # and-forget observer feeds a BACKGROUND worker that computes the V3 verdict (generic
    # gates + Context/Execution score, LOG-ONLY — never rejects/delays/alters a live
    # order) and appends a would-be record. Its own truncating OhlcFetcher REUSES the
    # SAME rate-limited closure (no new data path); the regime .latest snapshot is read
    # on the hot path AS OF signal time (NO-LOOKAHEAD).
    v3_chain_runner = None
    if _v3_chain_on:
        try:
            from sr_detector.fetch import OhlcFetcher
            from sr_detector.zone_builder import build_scoring_params, build_zone_knobs
            from v3_chain import V3ChainRunner
            _v3_fetcher = OhlcFetcher(
                _sr_fetch_fn, instrument_cache,
                lookback_days=int(getattr(_v3_cfg, "fetch_lookback_days", 180)),
                logger=get_logger("v3_chain"), now_fn=time_authority.now_ist,
                cache_ttl_sec=float(getattr(_v3_cfg, "fetch_cache_ttl_sec", 1800.0)))
            effect_telemetry.register_constructed("ohlc_fetchers")  # B2 (idempotent)
            v3_chain_runner = V3ChainRunner(
                config=_v3_cfg,
                zone_knobs=build_zone_knobs(_sr_cfg),      # REUSE the sr_detector zone knobs (no duplicate tuning)
                zone_scoring=build_scoring_params(_sr_cfg),
                fetcher=_v3_fetcher,
                logger=get_logger("v3_chain"),
                now_fn=time_authority.now_ist,
                regime_runner=market_regime_runner,        # as-of regime snapshot (None when regime OFF)
            )
            signal_processor.set_v3_chain(v3_chain_runner)
            v3_chain_runner.start()
            _log.info("v3_chain: ENABLED (mode=%s, shadow LOG-ONLY)", _v3_cfg.v3_chain_mode)
        except Exception as exc:   # never let the chain break startup
            _log.error("v3_chain wiring failed (continuing without it): %s", exc)
            v3_chain_runner = None
            try:
                signal_processor.set_v3_chain(None)
            except Exception:
                pass

    # V3 Step 10b — PB-01 OVERNIGHT WATCHLIST + next-morning ENTRY STAGE (default-off).
    # watchlist.enabled=false (default): NOTHING constructed → the EOD route keeps
    # eod_capture=None (a fail-safe miss) + no entry-stage daemon → BYTE-IDENTICAL. When
    # enabled it is SHADOW / ANALYSIS-ONLY: the EOD capture persists ONE pb01_watchlist
    # row (LEVEL computed from OUR daily candles); the next-morning 09:20-11:00 entry stage
    # detects the FIRST 5-min retest-confirmation and hands it to the PLACE-FREE would-be
    # runner (a JSONL record — NO order, NO reservation, G-NO-ORDER by construction).
    # Reuses the SAME rate-limited fetch closure; entirely off the hot path.
    pb01_capture_worker = None
    pb01_entry_stage = None
    if _watchlist_on:
        try:
            from core.config_loader import V3ChainConfig
            from sr_detector.fetch import OhlcFetcher
            from sr_detector.zone_builder import build_scoring_params, build_zone_knobs
            from v3_chain.pb01_entry import Pb01EntryStage
            from v3_chain.pb01_runner import Pb01WouldBeRunner
            from v3_chain.watchlist_capture import WatchlistCaptureWorker
            _wl_v3_cfg = _v3_cfg if _v3_cfg is not None else V3ChainConfig()
            _wl_knobs = build_zone_knobs(_sr_cfg)
            _wl_scoring = build_scoring_params(_sr_cfg)
            # ONE fetcher over the shared closure: lookback covers the structural fetch;
            # cache_ttl=0 keeps the entry stage's 5-min poll FRESH (the daily/30-min inputs
            # are memoized at the VALUE level in the entry stage → fetched once per symbol).
            _wl_fetcher = OhlcFetcher(
                _sr_fetch_fn, instrument_cache,
                lookback_days=int(getattr(_wl_v3_cfg, "fetch_lookback_days", 180)),
                logger=get_logger("pb01_watchlist"), now_fn=time_authority.now_ist,
                cache_ttl_sec=0.0)
            effect_telemetry.register_constructed("ohlc_fetchers")  # B2 (idempotent)
            pb01_capture_worker = WatchlistCaptureWorker(
                config=_watchlist_cfg, store=store, fetcher=_wl_fetcher,
                market_windows=market_windows, logger=get_logger("pb01_watchlist"),
                now_fn=time_authority.now_ist, zone_knobs=_wl_knobs, zone_scoring=_wl_scoring)
            _pb01_would_be = Pb01WouldBeRunner(
                config=_watchlist_cfg, v3_cfg=_wl_v3_cfg, fetcher=_wl_fetcher,
                zone_knobs=_wl_knobs, zone_scoring=_wl_scoring,
                logger=get_logger("pb01_watchlist"), now_fn=time_authority.now_ist,
                regime_runner=market_regime_runner)   # as-of regime snapshot (None when OFF)
            pb01_entry_stage = Pb01EntryStage(
                config=_watchlist_cfg, v3_cfg=_wl_v3_cfg, store=store, fetcher=_wl_fetcher,
                market_windows=market_windows, on_confirm=_pb01_would_be.record,
                logger=get_logger("pb01_watchlist"), now_fn=time_authority.now_ist)
            webhook_receiver.set_eod_capture(pb01_capture_worker)   # EOD alerts now captured
            pb01_capture_worker.start()
            pb01_entry_stage.start()      # the row status IS the durable state (restart-safe)
            _log.info("V3 Step 10b PB-01 watchlist: ENABLED — capture + entry stage started "
                      "(SHADOW / ANALYSIS-ONLY, NO order path)")
        except Exception as exc:   # never let the watchlist break startup
            _log.error("pb01 watchlist wiring failed (continuing without it): %s", exc)
            pb01_capture_worker = None
            pb01_entry_stage = None
    else:
        # C3 (25-Jul-2026): say so. Before this, config-off logged NOTHING, so the
        # ABSENCE of the ENABLED line was ambiguous between "disabled by config" and
        # "boot never reached this point" — and an empty pb01_watchlist next morning
        # is the same either way. Now every boot states which of the three it was:
        # ENABLED / DISABLED / the wiring-failed ERROR above.
        _log.info("V3 Step 10b PB-01 watchlist: DISABLED (watchlist.enabled=false) — "
                  "no EOD capture will be wired; an empty pb01_watchlist is EXPECTED")

    # SNR-V2 Phase A: RetestMonitor + Diverter (need signal_processor.continue_from_
    # retest, so built here). The diverter is late-bound into signal_processor; the
    # monitor is rehydrated from retest_state and started; both daemons stop in
    # _shutdown. eod clears parked candidates at square-off. Dormant when off.
    if _v2_on and zone_warmer is not None and _v2_zone_cache is not None:
        try:
            from sr_detector.fetch import OhlcFetcher
            from sr_detector.retest_confirm import RetestParams
            from screening.retest_monitor import RetestDiverter, RetestMonitor
            _onem_fetcher = OhlcFetcher(
                _sr_fetch_fn, instrument_cache, lookback_days=_sr_cfg.onem_lookback_days,
                logger=get_logger("sr_retest_monitor"), now_fn=time_authority.now_ist,
                cache_ttl_sec=0.0)  # fresh 1m each poll (no cache)
            effect_telemetry.register_constructed("ohlc_fetchers")  # B2 (idempotent)
            _retest_params = RetestParams(
                timeout_sec=_sr_cfg.retest_timeout_sec,
                max_away_pct=_sr_cfg.retest_max_away_pct,
                confirm_strong_close_frac=_sr_cfg.confirm_strong_close_frac,
                breakout_margin_pct=_sr_cfg.breakout_margin_pct)
            retest_monitor = RetestMonitor(
                onem_fetcher=_onem_fetcher, state_store=store, params=_retest_params,
                on_confirm=signal_processor.continue_from_retest,
                logger=get_logger("sr_retest_monitor"), now_fn=time_authority.now_ist,
                poll_interval_sec=_sr_cfg.retest_poll_interval_sec)
            _retest_diverter = RetestDiverter(
                zone_cache=_v2_zone_cache, monitor=retest_monitor, state_store=store,
                config=_sr_cfg, logger=get_logger("sr_retest_diverter"),
                now_fn=time_authority.now_ist, mode=mode_label)
            signal_processor.set_retest_diverter(_retest_diverter)
            eod.set_retest_monitor(retest_monitor)
            restored = retest_monitor.rehydrate()
            zone_warmer.start()
            retest_monitor.start()
            _log.info("sr_detector V2 WAIT_FOR_RETEST: ENABLED + started "
                      "(mode=%s, rehydrated=%d)", mode_label, restored)
        except Exception as exc:
            _log.error("sr_detector V2 RetestMonitor wiring failed: %s", exc)
            retest_monitor = None

    # SNR-V2 Phase B: StructureExitManager (structure-aware exit). Needs the same
    # warmed ZoneCache; push-driven by CandleStore 1m closes. Dormant unless
    # structure_exit_enabled. Single SL owner — do NOT co-enable trailing_sl_enabled.
    if _struct_exit_on:
        if _v2_zone_cache is None or zone_warmer is None:
            _log.error("structure_exit enabled but ZoneCache/ZoneWarmer unavailable "
                       "(market-data kite?) — structure-exit NOT started")
        else:
            try:
                from orders.structure_exit_manager import StructureExitManager
                # idempotent: the retest block already started the warmer when _v2_on;
                # this covers the structure-exit-only (retest off) case.
                zone_warmer.start()
                structure_exit_manager = StructureExitManager(
                    adapter=broker_adapter,
                    state_store=store,
                    zone_cache=_v2_zone_cache,
                    config=_struct_exit_cfg,
                    logger=get_logger("structure_exit_manager"),
                    now_fn=time_authority.now_ist,
                    instrument_cache=instrument_cache,
                    order_monitor=order_monitor,
                    candle_store=candle_store,
                    notifier=notifier,
                    mode=mode_label,
                    sl_limit_offset_pct=getattr(
                        app_config.system.capital, "sl_limit_offset_pct", 0.005),
                )
                structure_exit_manager.start()
                _log.info("sr_detector V2 STRUCTURE_EXIT: ENABLED + started (mode=%s)",
                          mode_label)
            except Exception as exc:
                _log.error("sr_detector V2 StructureExitManager wiring failed: %s", exc)
                structure_exit_manager = None

    entry_gate = EntryGate(
        quote_fn=broker_adapter.get_quote,
        logger=get_logger("entry_gate"),
        state_store=store,
        on_release=_make_gate_release_cb(signal_processor),
    )

    # ── Phase 0e: Event bus subscriptions (MAIN9) ────────────────────────────
    event_bus.subscribe(KillSwitchActivated, _log_kill_switch_event)

    # BL-2: CapitalDriftDetected handler with tiered escalation. Replaces the
    # legacy _log_capital_drift_event WARNING stub. Only fund_manager-sourced
    # events trigger kill_switch escalation; reconciler-sourced events log at
    # INFO (they have their own escalation paths).
    drift_handler = CapitalDriftHandler(
        config=app_config.system.drift_handler,
        kill_switch=kill_switch,
        logger=get_logger("drift_handler"),
    )
    event_bus.subscribe(CapitalDriftDetected, drift_handler.on_drift)
    _log.info("capital drift handler subscribed (BL-2)")
    # Note: fund_manager has no on_order_filled/on_position_closed handlers.
    # The reconciler is daemon-poll only (RC4 retired by 2026-04-26 audit).

    # H-7: finalize EOD init now that bus subscriptions are registered.
    # _check_restart_recovery() runs here (deferred from EodSquareoff.__init__);
    # if it recovery-fires, the EodSquareoffComplete publish reaches subscribers.
    eod.post_wire_init()

    # ── Phase 0f: Startup reconciliation (MAIN10, RC14) ─────────────────────
    # BUILD 1 INVARIANT (#12, 24-Jun): this SYNCHRONOUS reconcile_once() MUST
    # precede signal_processor.start() and the webhook server start (Phase 0g
    # below). rehydrate_from_open_trades() above re-reserves capital for any
    # DB-open trade; if that trade is actually flat at the broker (a phantom),
    # only this reconcile releases the phantom's capital. Because the webhook
    # (the sole inbound-signal source) and the signal_processor workers do not
    # start until AFTER this call, NO new signal can be sized/placed while a
    # phantom still holds capital — capital is correct before any trade. The
    # ~15s figure is the BACKGROUND reconciler poll (the 2nd+ reconcile), not
    # this first one. DO NOT REORDER: a guard below asserts this completed
    # before the pipeline starts.
    recon_actions = order_reconciler.reconcile_once()
    _startup_reconcile_done = True  # BUILD 1 (#12): boot-order invariant flag
    if recon_actions:
        _log.info(
            "Startup reconciliation: %d action(s) taken", len(recon_actions)
        )
    # FIX-186 (FIX 2): backstop sweep for orphan order rows that a broker-side
    # cancel finalized without updating the local DB before yesterday's shutdown
    # (the 17-Jun IRFC leak). Runs after reconcile_once so any trades it just
    # closed are already terminal and their stale exit legs get swept here.
    try:
        order_reconciler.sweep_stale_orders()
    except Exception as exc:  # noqa: BLE001 — sweep must never block startup
        _log.error("startup stale-order sweep failed: %s", exc)
    if kill_switch.is_active("any"):
        _log.critical(
            "Kill switch active after startup reconciliation -- aborting"
        )
        store.close()
        return 1

    # BL-21 / D.2: periodic broker clock skew probe.
    # Thin driver over time_authority.record_broker_skew(); skipped in paper
    # mode (adapter.get_server_time() returns local time in paper, so skew
    # would always be ~0).
    clock_skew_probe: Optional[BrokerClockSkewProbe] = None
    if not is_paper:
        clock_skew_probe = BrokerClockSkewProbe(
            adapter=broker_adapter,
            time_authority=time_authority,
            config=app_config.system.clock.probe,
            logger=get_logger("clock_skew_probe"),
        )
        # effect-telemetry (B2): the ONE mode-conditional registration —
        # live-only ctor BY DESIGN; the registry's `modes:` field exempts it
        # from the paper assertion.
        effect_telemetry.register_constructed("clock_skew_probe")
    else:
        _log.info("clock_skew_probe skipped in paper mode")

    # ── Phase 0g: Start subsystems (MAIN11) ─────────────────────────────────
    # candle_store.start() BEFORE live_feed.connect() (BLOCKER #2 fix)
    candle_store.start()
    # v14: persist candles to DB on each close
    # effect-telemetry (ledger #1, frozen contract A2.3): dormant tripwire —
    # X8: this writer must stay dark even if the tick feed ever wires.
    _fx_candle_persist = effect_telemetry.handle("candle_persist")
    def _persist_candle(candle):
        _fx_candle_persist.inc()
        try:
            store.insert_candle(
                symbol=candle.symbol,
                instrument_token=candle.instrument_token,
                ts=candle.ts.isoformat() if hasattr(candle.ts, 'isoformat') else str(candle.ts),
                interval_sec=candle.interval_sec,
                open_=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                is_synthetic=1 if candle.is_synthetic else 0,
            )
        except Exception as exc:
            _log.debug("candle persist failed: %s", exc)
    candle_store.register_on_candle_close(_persist_candle)
    # BLOCKER #11: wire token map so CandleStore can resolve instrument_token -> symbol
    candle_store.set_token_map(instrument_cache.token_map())
    live_feed.connect()
    if is_paper:
        n_cancelled = store.cancel_stale_paper_orders(today_iso)
        if n_cancelled:
            _log.info("Cancelled %d stale paper orders from previous days", n_cancelled)
    order_monitor.rehydrate_from_store(store)  # Audit #21
    # B.1 (2026-04-25): repopulate OrderPlacer._fill_map for SL/TGT/EOD
    # exit legs so a post-restart exit fill closes the trade in DB.
    order_placer.rehydrate_fill_map(store)
    # FIX-169 F31: install signal handlers BEFORE starting threads so a
    # SIGINT during startup triggers clean shutdown instead of default exit.
    _install_signal_handlers()

    order_monitor.start()
    order_reconciler.start()
    if clock_skew_probe is not None:
        clock_skew_probe.start()
    token_monitor.start()  # FIX-128 Fix E: no-op in paper mode
    # BUILD 1 INVARIANT (#12): the synchronous startup reconcile MUST have run
    # before the signal pipeline goes live, so phantom-position capital is
    # corrected before any trade can be sized/placed. See Phase 0f above.
    assert _startup_reconcile_done, (
        "boot-order invariant violated: signal_processor.start() reached before "
        "the synchronous startup reconcile_once() — phantom capital may be unreleased"
    )
    # signal_processor BEFORE entry_gate (BLOCKER #5 fix): gate may call
    # continue_from_gate() immediately on release; workers must be ready.
    signal_processor.start()
    entry_gate.start()
    smart_tgt.start()
    tgt_retry_manager.start()  # Task: standalone TGT retry

    wh_cfg = app_config.system.webhook
    # FIX-081: Port conflict check removed — acquire_instance_lock() already
    # binds a persistent socket lock. Separate check_port_available() was TOCTOU-prone.

    # Audit 6.5 / B.3: Waitress production WSGI server replaces Flask's
    # dev server. Werkzeug's app.run drops connections under burst load
    # (Chartink can fan out 50+ signals in a sub-second window). Waitress
    # is pure-Python, no C deps, Windows-friendly.
    from waitress import serve as _waitress_serve
    webhook_thread = threading.Thread(
        target=_waitress_serve,
        args=(webhook_receiver.app,),
        kwargs={
            "host": wh_cfg.bind_host,
            "port": wh_cfg.bind_port,
            "threads": 8,
            "connection_limit": 100,
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
        # S4 follow-up, ANSWERED 27-Jul-2026: DEGRADE, do not block the boot.
        #
        # This used to _shutdown_event.set(). On 17-Jul that cost a whole trading day
        # (0 trades) behind an exit 0 that looked like a normal stop, because S4 had
        # just put /health behind the webhook secret and this probe called it
        # anonymously. The 401 was fixed; the DECISION -- should one non-2xx from one
        # local endpoint stop everything -- was left open. It is answered here.
        #
        # THE ARGUMENT IS SAFETY, NOT CONVENIENCE. Halting does not prevent the bad
        # outcome; it ENLARGES it. No Flask means no new signals, so entries stop
        # either way -- but _shutdown_event.set() also takes down exit management, the
        # reconciler and the 15:17 EOD squareoff. It converts "no new entries, all
        # protection still running" into "nothing running at all". On 17-Jul the book
        # happened to be flat. That was luck, not design.
        #
        # THE RULE (27-Jul): fail-fast when the failing condition can only arise from
        # a DELIBERATE ACT; degrade-and-alarm when the ENVIRONMENT can cause it. This
        # one is environmental and fires on an unattended 08:15 boot -- the opposite
        # of the #16a delivery-flag BLOCK above, which needs a human to have flipped
        # a flag first.
        #
        # DEGRADING IS ONLY SAFE IF SOMETHING ALARMS, and liveness_probe is NOT that
        # something: it detects a service that is DOWN, and after this change the
        # service is UP. So the alarm has to be raised here, out-of-band, on the path
        # that is verified to reach a human: sentinel -> alert_watcher -> email
        # (same mechanism, same reason, as state_store's migration-refused CRITICAL).
        # A bare _log.critical() would NOT have reached anyone.
        _log.critical("Webhook endpoint not reachable at %s -- DEGRADED: entries "
                      "cannot arrive, exits/reconciler/EOD squareoff continue",
                      webhook_url)
        try:
            from alerts.critical import write_critical_sentinel
            write_critical_sentinel(
                title="Webhook endpoint unreachable at boot -- DEGRADED, still running",
                body=(f"The post-start self-check could not reach {webhook_url}.\n\n"
                      "NO SIGNALS CAN ARRIVE, so no new entries will be taken. The "
                      "service is deliberately STILL RUNNING so that exit management, "
                      "the order reconciler and the 15:17 EOD squareoff keep "
                      "protecting anything already open.\n\n"
                      "If the book is flat this can wait. If anything is open, it is "
                      "still being managed. Investigate the webhook server; a restart "
                      "is the fix once the cause is known."),
                source_module="main.webhook_selfcheck",
                context={"url": webhook_url, "detail": getattr(wh_result, "detail", None),
                         "degraded": True, "shutdown": False},
            )
        except Exception as exc:  # noqa: BLE001 — best-effort; the log line still stands
            _log.error("webhook self-check: CRITICAL sentinel write failed (%s)", exc)

    # E-4 (audit 02-Jul): expose the core daemon poll threads' liveness on /health.
    # order_monitor / order_reconciler / eod_scheduler gate health (a dead poll
    # thread is unambiguously bad); live_feed connection is reported but non-gating
    # (a feed disconnect auto-heals via reconnect and must not false-alarm monitors).
    def _daemon_liveness() -> dict:
        def _alive(fn) -> bool:
            try:
                return bool(fn())
            except Exception:
                return False
        return {
            "order_monitor": {"ok": _alive(order_monitor.is_alive)},
            "order_reconciler": {"ok": _alive(order_reconciler.is_alive)},
            "eod_scheduler": {"ok": _alive(eod.is_alive)},
            "live_feed": {"ok": True, "connected": _alive(live_feed.is_connected)},
        }

    # FIX-132 Item 15: external health monitor on port 8080
    start_healthcheck_server(
        state_store=store, logger=get_logger("healthcheck"), port=8080,
        metrics_provider=signal_processor.get_runtime_metrics,  # FIX-190 (Bug B)
        # Post-mortem 24-Jun: surface the TGT-retry safety daemon's liveness on
        # /health (→ pre-flight Phase B) so a dead/crash-looping worker can't sit
        # unnoticed for days again.
        tgt_retry_provider=tgt_retry_manager.health_snapshot,
        daemon_liveness_provider=_daemon_liveness,  # E-4
    )

    # Pre-flight on-demand: if this restart landed after the 08:30/09:14 cron slot
    # but that phase's sentinel shows it didn't run, launch it best-effort (detached;
    # never blocks startup). No-op on a normal morning (cron already ran the phase).
    try:
        from scripts.preflight.startup_hook import run_on_demand_if_missed
        run_on_demand_if_missed()
    except Exception as _pf_exc:  # never let the hook break startup
        get_logger("main").warning("preflight_startup_hook_failed", extra={"error": str(_pf_exc)})

    # EOD scheduler daemon thread (MAIN14)
    eod_thread = threading.Thread(
        target=eod.start_polling,
        kwargs={"poll_interval_sec": app_config.system.eod_squareoff.poll_interval_sec},
        name="eod-scheduler",
        daemon=True,
    )
    eod_thread.start()

    # FIX-130 (Item 7): EOD pre-alert at 14:45 IST
    _start_eod_pre_alert_thread(
        store=store,
        notifier=notifier,
        mode=args.mode.upper(),
        log=_log,
        market_windows=market_windows,
        shutdown_event=_shutdown_event,
    )

    # FIX-164: Re-sync capital from broker at market open (09:15 IST)
    _start_market_open_margin_sync_thread(
        broker_adapter=broker_adapter,
        fund_manager=fund_manager,
        notifier=notifier,
        mode=mode_label,
        log=_log,
        shutdown_event=_shutdown_event,
        market_windows=market_windows,
    )

    # FIX-189 (P1-A completion): EOD window-end self-exit — exit 0 once flat after the
    # CONFIGURED trading_hours.service_window_end (17:35 IST as deployed; a hardcoded
    # 16:00 until 25-Jul-2026) so the service never idles overnight. Armed
    # only for normal service starts; skipped for operator/diagnostic starts that
    # deliberately bypassed the window guard (--interactive/--resume/
    # TS_IGNORE_MARKET_WINDOW=1), so an operator override is never auto-stopped.
    _eod_exit_armed = not (
        getattr(args, "interactive", False)
        or getattr(args, "resume", False)
        or os.environ.get("TS_IGNORE_MARKET_WINDOW") == "1"
    )
    if _eod_exit_armed:
        _start_eod_self_exit_thread(
            store=store,
            notifier=notifier,
            mode=mode_label,
            log=_log,
            market_windows=market_windows,
            shutdown_event=_shutdown_event,
            window_end=_service_window_end,
            # M-C8: never self-exit while a HARD_KILL flatten is still running.
            flatten_in_progress_fn=kill_switch.is_flatten_in_progress,
        )
    else:
        _log.info(
            "eod_self_exit: not armed (operator/diagnostic start bypassed the "
            "window guard)"
        )

    # CV3: Validate all config values were accessed (non-strict for gradual rollout)
    config_validator.validate_all(strict=False)

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
        config_hash=json.dumps(app_config.file_hashes),
        now_iso=now_iso,
    )
    try:
        _hhmm = time_authority.now_ist().strftime("%H:%M")
        try:
            _market_state = (
                "OPEN"
                if market_windows.is_market_open(time_authority.now_ist())
                else "CLOSED"
            )
        except Exception:
            _market_state = "UNKNOWN"
        notifier.send(
            severity="INFO",
            title=f"[{mode_label}] 🚀 System Active | {mode_label} Mode",
            body=(
                f"Account: {selected_account.account_id} (ZERODHA)\n"
                f"Capital: ₹{float(_startup_capital):,.0f} | "
                f"Market: {_market_state}\n"
                f"{_hhmm} IST"
            ),
            source_module="main",
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

    # ── effect-telemetry (ledger #1, B2) ─────────────────────────────────────
    # Composition-root registration for units that carry NO counter (infra +
    # covered-existing). Counter-bearing managers registered themselves inside
    # their own __init__ via handle(). Then the startup assertion: registry
    # expectation vs what actually registered — paper FAILS FAST, live emits
    # CRITICAL and continues (never crash a live boot on a registration gap).
    for _infra_name in (
        "state_store",          # ctor main:~1851
        "event_bus",            # ctor main:~1856
        "market_windows",       # ctor main:~1938
        "order_state_machine",  # ctor main:~1964
        "rate_limiter",         # ctor main:~1966
        "product_resolver",     # ctor main:~1967
        "cost_calculator",      # ctor main:~1968
        "broker_adapter",       # ctor main:~2001
        "telegram_notifier",    # ctor main:~2243 (its effect-trail = _audit_send, G5)
        "strategy_loader",      # ctor main:~2736
        "full_entry_engine",    # ctor main:~2618 (pass-through router)
        "order_manager",        # ctor main:~2624 (thin DB layer)
        "step_executor",        # ctor main:~2812
        "token_monitor",        # ctor main:~2873 (paper no-op by design)
        "webhook_receiver",     # ctor main:~2892 — covered-existing (webhook_audit)
    ):
        effect_telemetry.register_constructed(_infra_name)
    if instrument_cache is not None:
        effect_telemetry.register_constructed("instrument_cache")  # ctor main:~2059
    try:
        effect_telemetry.assert_composition(notifier=notifier, logger=_log)
    except effect_telemetry.EffectCompositionError:
        raise  # paper/dev fail-fast — this IS the missing composition assertion

    # ── Runtime loop (MAIN13) ────────────────────────────────────────────────
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
        webhook_receiver=webhook_receiver,
        clock_skew_probe=clock_skew_probe,
        token_monitor=token_monitor,  # FIX-128 Fix E
        tgt_retry_manager=tgt_retry_manager,  # Task: TGT retry
        sr_detector=sr_detector,  # SNR-DETECTOR-V1
        zone_warmer=zone_warmer,  # SNR-V2
        retest_monitor=retest_monitor,  # SNR-V2
        structure_exit_manager=structure_exit_manager,  # SNR-V2 Phase B
        market_regime_runner=market_regime_runner,  # V3 03.02
        portfolio_allocator=portfolio_allocator,  # V3 03.05
        v3_chain=v3_chain_runner,  # V3 Step 10
        pb01_entry_stage=pb01_entry_stage,  # V3 Step 10b
        pb01_capture_worker=pb01_capture_worker,  # V3 Step 10b
        kill_switch=kill_switch,  # M-C8: drain the flatten worker before teardown
        mode=mode_label,
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
