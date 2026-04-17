"""
tests/integration/conftest.py -- Trading System v2

Fixtures for end-to-end smoke tests (Module 37, IT1-IT12).

Wired system (paper mode, real subsystems, SQLite in tmp_path):
  - ZerodhaAdapter: paper_mode=True, SimKiteClient quote_provider
  - All core subsystems: FundManager, PositionSizer, RiskEngine
  - SignalProcessor + WebhookReceiver (Flask test client)
  - now_ist() patched in 4 modules to MOCK_NOW (10:30 IST 2026-04-15 Wed)

Integration scan_webhook_map (IT3): one scanner "vwap_bounce_long".
"""
from __future__ import annotations

import logging
import queue
import types
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import patch

import pytest
import yaml

from broker.cost_calculator import CostCalculator
from broker.order_state_machine import OrderStateMachine
from broker.product_resolver import ProductResolver
from broker.rate_limiter import RateLimiter
from broker.zerodha_adapter import ZerodhaAdapter
from capital.fund_manager import FundManager
from capital.kill_switch import KillSwitch
from capital.position_sizer import PositionSizer
from capital.risk_engine import RiskEngine
from core.config_loader import BrokerCostsConfig, BrokerLimitsConfig, ScoringConfig
from core.events import EventBus
from core.market_windows import MarketWindows
from core.state_store import StateStore
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import LimitTripleProtocol
from screening.quality_scorer import QualityScorer
from screening.secondary_screener import SecondaryScreener
from screening.step_executor import StepExecutor
from signals.signal_processor import SignalProcessor
from signals.webhook_receiver import WebhookReceiver
from strategies.loader import StrategyLoader
from tests.integration.sim_kite import SimKiteClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# Wednesday 2026-04-15 10:30 IST — inside entry window (09:30-13:30), not a holiday
MOCK_NOW = datetime(2026, 4, 15, 10, 30, 0, tzinfo=IST)

# Naive IST string, 10 seconds before MOCK_NOW — passes expiry check (< 60s)
# and step_10 signal_age (≤ 30s → 1.0)
MOCK_TRIGGERED_AT = "2026-04-15 10:29:50"

PAPER_CAPITAL = 500_000.0

# The one scanner used in integration tests
SCANNER_NAME = "vwap_bounce_long"
STRATEGY_NAME = "vwap_bounce_long"

# scan_webhook_map passed to SignalProcessor (dict-of-dicts format)
_SCAN_MAP: Dict[str, dict] = {
    SCANNER_NAME: {"strategy": STRATEGY_NAME},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _logger(name: str) -> logging.Logger:
    log = logging.getLogger(f"it.{name}")
    if not log.handlers:
        log.addHandler(logging.NullHandler())
    log.setLevel(logging.DEBUG)
    return log


def _load_yaml_config(filename: str, schema_cls):
    """Load a YAML config file and validate it against a Pydantic schema."""
    path = Path("config") / filename
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return schema_cls.model_validate(data)


def _make_webhook_config(scanner_names):
    """Build a duck-typed config object for WebhookReceiver."""
    cfg = types.SimpleNamespace()
    cfg.signal_queue = types.SimpleNamespace(
        capacity=300, backpressure_pct=0.80, expiry_sec=60
    )
    # WebhookReceiver only checks `scanner_name in known_scanners` (key presence)
    cfg.scan_webhook_map = types.SimpleNamespace(
        scanners={name: True for name in scanner_names}
    )
    return cfg


# ---------------------------------------------------------------------------
# SystemContext — holds references to all wired subsystems
# ---------------------------------------------------------------------------

@dataclass
class SystemContext:
    store: StateStore
    sim_kite: SimKiteClient
    receiver: WebhookReceiver
    signal_processor: SignalProcessor
    kill_switch: KillSwitch
    fund_manager: FundManager
    risk_engine: RiskEngine
    screener: SecondaryScreener
    adapter: ZerodhaAdapter
    strategies: dict
    bus: EventBus


# ---------------------------------------------------------------------------
# wired_system fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def wired_system(tmp_path):
    """
    Full paper-mode system wired in a temp SQLite DB.

    Lifecycle: patches applied → signal_processor.start() → yield ctx →
               signal_processor.stop() → patches removed → store closed.
    """
    # ── State store ──────────────────────────────────────────────────────────
    store = StateStore(str(tmp_path / "it_test.db"))

    # ── Quote provider ───────────────────────────────────────────────────────
    sim_kite = SimKiteClient()

    # ── Event bus ────────────────────────────────────────────────────────────
    bus = EventBus()

    # ── Broker layer ─────────────────────────────────────────────────────────
    broker_limits = _load_yaml_config("broker_limits.yaml", BrokerLimitsConfig)
    rate_limiter = RateLimiter(broker_limits)

    product_map = {
        "INTRADAY": "MIS",
        "COVER_ORDER": "CO",
        "DELIVERY": "CNC",
        "BRACKET_ORDER": "",
    }
    product_resolver = ProductResolver({"zerodha": product_map})

    broker_costs = _load_yaml_config("broker_costs.yaml", BrokerCostsConfig)
    cost_calculator = CostCalculator(broker_costs)

    state_machine = OrderStateMachine(bus=bus)

    adapter = ZerodhaAdapter(
        kite_client=None,   # never called in paper mode
        rate_limiter=rate_limiter,
        product_resolver=product_resolver,
        cost_calculator=cost_calculator,
        state_machine=state_machine,
        logger=_logger("adapter"),
        paper_mode=True,
        paper_capital=PAPER_CAPITAL,
        quote_provider=sim_kite.get_quote_fn(),
    )

    # ── Capital layer ────────────────────────────────────────────────────────
    leverage_map = {
        "INTRADAY": 5.0,
        "COVER_ORDER": 6.0,
        "DELIVERY": 1.0,
        "BRACKET_ORDER": 5.0,
    }

    kill_switch = KillSwitch(
        state_store=store,
        bus=bus,
        logger=_logger("ks"),
        api_failure_threshold=3,
        enable_auto_trip=False,  # disabled for test determinism
    )

    fund_manager = FundManager(
        state_store=store,
        bus=bus,
        logger=_logger("fm"),
        intraday_bucket_pct=0.70,
        positional_bucket_pct=0.30,
        daily_loss_limit=10_000.0,
        leverage_map=leverage_map,
    )
    fund_manager.initialize(PAPER_CAPITAL)

    position_sizer = PositionSizer(
        fund_manager=fund_manager,
        leverage_map=leverage_map,
        risk_per_trade_pct=0.01,
        max_concentration_pct=0.10,
        min_qty_threshold=1,
        tier_multipliers={"HIGH": 1.0, "MEDIUM": 0.70, "LOW": 0.50},
        logger=_logger("sizer"),
    )

    risk_engine = RiskEngine(
        fund_manager=fund_manager,
        state_store=store,
        max_open_positions=2,       # low cap — makes scenario 3 easy to trigger
        max_daily_trades=20,
        max_sector_exposure_pct=0.40,
        max_consecutive_losses=4,
        daily_loss_limit_pct=0.05,
        sector_lookup_fn=lambda sym: "UNKNOWN",
        logger=_logger("risk"),
        kill_switch=kill_switch,
    )

    # ── Screening layer ──────────────────────────────────────────────────────
    scoring_cfg = _load_yaml_config("scoring_weights.yaml", ScoringConfig)
    scorer = QualityScorer(weights=scoring_cfg, logger=_logger("scorer"))
    step_executor = StepExecutor(logger=_logger("steps"))
    screener = SecondaryScreener(
        step_executor=step_executor,
        quality_scorer=scorer,
        state_store=store,
        quote_fn=adapter.get_quote,
        logger=_logger("screener"),
    )

    # ── Strategies ───────────────────────────────────────────────────────────
    loader = StrategyLoader()
    strategies = loader.load_all_strategies(Path("config/strategies"))

    # ── Market windows (no holidays for test date) ───────────────────────────
    market_windows = MarketWindows(holidays=set())

    # ── Order placement chain ─────────────────────────────────────────────────
    co_protocol = CoPlusTgtProtocol(adapter=adapter, logger=_logger("co_proto"))
    limit_protocol = LimitTripleProtocol(adapter=adapter, logger=_logger("lim_proto"))
    full_engine = FullEntryEngine(
        co_protocol=co_protocol,
        limit_protocol=limit_protocol,
        logger=_logger("engine"),
    )
    order_manager = OrderManager(state_store=store, logger=_logger("om"))
    order_placer = OrderPlacer(
        entry_engine=full_engine,
        order_manager=order_manager,
        fund_manager=fund_manager,
        bus=bus,
        logger=_logger("placer"),
        kill_switch=kill_switch,
    )

    # ── Signal processing ─────────────────────────────────────────────────────
    signal_queue: queue.Queue = queue.Queue(maxsize=300)

    signal_processor = SignalProcessor(
        signal_queue=signal_queue,
        state_store=store,
        bus=bus,
        fund_manager=fund_manager,
        position_sizer=position_sizer,
        risk_engine=risk_engine,
        kill_switch=kill_switch,
        market_windows=market_windows,
        strategies=strategies,
        scan_webhook_map=_SCAN_MAP,
        secondary_screener=screener,
        quality_scorer=scorer,
        order_placer=order_placer,
        logger=_logger("sp"),
        worker_count=2,
        drain_poll_sec=0.05,
    )

    webhook_cfg = _make_webhook_config(list(_SCAN_MAP.keys()))
    receiver = WebhookReceiver(
        signal_queue=signal_queue,
        state_store=store,
        config=webhook_cfg,
        market_windows=market_windows,
        kill_switch=kill_switch,
        logger=_logger("wh"),
        secret_token=None,
    )

    # ── Time patching ─────────────────────────────────────────────────────────
    # Patch all modules that do `from core.time_authority import now_ist`
    mock_now_fn = lambda: MOCK_NOW
    _patches = [
        patch("signals.webhook_receiver.now_ist", mock_now_fn),
        patch("signals.signal_processor.now_ist", mock_now_fn),
        patch("screening.step_executor.now_ist", mock_now_fn),
        patch("screening.secondary_screener.now_ist", mock_now_fn),
    ]
    for p in _patches:
        p.start()

    # ── Start processor ──────────────────────────────────────────────────────
    signal_processor.start()

    ctx = SystemContext(
        store=store,
        sim_kite=sim_kite,
        receiver=receiver,
        signal_processor=signal_processor,
        kill_switch=kill_switch,
        fund_manager=fund_manager,
        risk_engine=risk_engine,
        screener=screener,
        adapter=adapter,
        strategies=strategies,
        bus=bus,
    )

    yield ctx

    # ── Teardown ─────────────────────────────────────────────────────────────
    signal_processor.stop()
    for p in _patches:
        p.stop()
    store.close()
