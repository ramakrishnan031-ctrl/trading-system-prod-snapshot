"""
core/exceptions.py — Trading System v2

Purpose:
    Single authoritative location for every custom exception in the system.
    Every other module raises from this hierarchy; no custom exceptions are
    defined elsewhere.

Design Refs:
    - E1: TradingSystemError as single root
    - E2: SEVERITY class attribute aligned with G8 alert tiers
    - E3: Five sub-roots (Config, State, Broker, Time, Signal)
    - E4: Concrete exceptions seeded day-one
    - E5: Constructor contract — (message: str, **context), self.context dict
    - E6: No imports — stdlib only. Layer 0.

What This Module Does NOT Do:
    - Does not log (logger imports exceptions, not the other way around)
    - Does not send alerts (alerts layer is Layer 5)
    - Does not import from any other module in this codebase
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Root
# ─────────────────────────────────────────────────────────────────────────────

class TradingSystemError(Exception):
    """
    Root of every custom exception in the trading system.

    Callers can catch `except TradingSystemError` to handle any system
    exception without importing specific sub-roots.

    Attributes:
        context: dict of structured key/value pairs passed at raise time.
                 Intended for structured logging — never parse the message
                 string to extract values; put them in context kwargs instead.
        SEVERITY: class-level str in {"INFO","WARN","ERROR","CRITICAL"}.
                  Inherited by all subclasses; override where appropriate.
    """

    SEVERITY: str = "ERROR"

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context: dict[str, object] = context


# ─────────────────────────────────────────────────────────────────────────────
# Sub-root: ConfigError
# ─────────────────────────────────────────────────────────────────────────────

class ConfigError(TradingSystemError):
    """
    Errors originating in config loading, YAML parsing, or schema validation.
    Raised by: core/config_loader.py, strategies/loader.py, strategies/schema.py
    """
    SEVERITY: str = "ERROR"


class ConfigSchemaError(ConfigError):
    """A config file loaded successfully but failed Pydantic schema validation."""
    SEVERITY: str = "ERROR"


class ConfigMissingError(ConfigError):
    """A required config file or required config key is absent."""
    SEVERITY: str = "ERROR"


class ConfigValidationError(ConfigError):
    """
    Config values loaded but not actually used by modules, or value drift
    detected between loaded config and runtime usage.

    Raised by: core/config_validator.py at startup validation gate.

    Useful context kwargs:
        unaccessed_keys (list[str]): config keys that were never read
        drift_keys (list[str]): keys where loaded != used value
    """
    SEVERITY: str = "ERROR"


# ─────────────────────────────────────────────────────────────────────────────
# Sub-root: StateError
# ─────────────────────────────────────────────────────────────────────────────

class StateError(TradingSystemError):
    """
    Errors originating in state persistence, capital math, or the database.
    Defaults to CRITICAL — any persistence corruption demands immediate action.
    Raised by: core/state_store.py, capital/invariant.py, capital/fund_manager.py
    """
    SEVERITY: str = "CRITICAL"


class CapitalInvariantViolation(StateError):
    """
    The bin card invariant failed:
      margin_used + margin_reserved + margin_available
        ≠ cash_floor + min(0, realized_intraday_pnl)

    Raised inside every capital-mutating transaction (G3 Level 1).
    Caller must roll back the transaction and halt trading.

    Useful context kwargs:
        expected (float): what the invariant sum should equal
        actual (float): what was computed
        delta (float): actual - expected
        operation (str): which operation triggered the check
    """
    SEVERITY: str = "CRITICAL"


class CapitalStateInconsistent(StateError):
    """
    Capital state replay during startup rehydrate completed, but the resulting
    bin-card invariant did not hold (BL-1).

    Raised by FundManager.rehydrate_from_open_trades after replaying open
    trades + today's RELEASE_USED rows. Distinct from CapitalInvariantViolation
    (which fires inside a live mutation): this signals the persisted history
    itself is internally inconsistent and trading cannot resume safely.

    Useful context kwargs:
        expected (float): rhs of the invariant
        actual (float): lhs of the invariant
        delta (float): actual - expected
        anomalies (list[dict]): per-trade anomalies recorded during replay
                                 (missing ledger rows, NULL signal_id, etc.)
    """
    SEVERITY: str = "CRITICAL"


class DedupViolation(StateError):
    """
    A duplicate signal INSERT was attempted against the signals table's
    unique index on (scanner_name, symbol, triggered_at_minute). (P6)

    This is not a bug — Chartink retries are expected. The exception signals
    that the caller should drop this signal, not halt.

    Useful context kwargs:
        fingerprint (str): hash that collided
        signal_id (str): the incoming signal_id that was rejected
    """
    SEVERITY: str = "WARN"


# ─────────────────────────────────────────────────────────────────────────────
# Sub-root: BrokerError
# ─────────────────────────────────────────────────────────────────────────────

class BrokerError(TradingSystemError):
    """
    Errors originating in broker API calls, authentication, or order placement.
    Raised by: broker/zerodha_adapter.py, broker/rate_limiter.py
    """
    SEVERITY: str = "ERROR"


class OrderRejectedError(BrokerError):
    """
    Broker explicitly rejected an order (e.g. insufficient margin, symbol
    not allowed for CO, RMS rejection).

    Useful context kwargs:
        order_id (str): broker order_id if available
        trade_id (str): internal trade_id
        reason (str): broker rejection message
        variety (str): CO / LIMIT / MARKET
    """
    SEVERITY: str = "ERROR"


class SLUnplaceableError(BrokerError):
    """The protective SL leg cannot be placed on the correct side of the entry
    fill — the circuit-band clamp would push the stop to/through the fill
    (instant stop-out). Raised by LimitTripleProtocol.place_exits when the
    placeability gate (orders.price_math.clamp_exit_into_band) returns
    placeable=False for the SL leg, BEFORE any order is sent to the broker.

    The position is live and CANNOT be protected by a resting stop, so
    OrderPlacer escalates via the existing unprotected-position handler:
    emergency market close + hard_kill. (A TGT-unplaceable is benign by
    contrast — the SL still stands — and only holds/retries.)

    Distinct from an LTP-validation reject: order_placer._is_ltp_validation_error()
    returns False for this, so it routes to the emergency-close path, never the
    LTP exit-retry queue.

    Useful context kwargs:
        trade_id (str), symbol (str), sl_price (float), entry_fill (float),
        reason (str): the gate's wrong-side explanation.
    """
    SEVERITY: str = "CRITICAL"


class BrokerAuthError(BrokerError):
    """
    Authentication or token failure — the broker rejected the API token.
    System cannot trade; triggers soft or hard kill depending on context.

    Useful context kwargs:
        account (str): account identifier
        http_status (int): HTTP status code from broker
    """
    SEVERITY: str = "CRITICAL"


class BrokerTimeoutError(BrokerError):
    """
    Broker API call did not respond within the configured timeout window.
    Caller should apply the G7 backoff sequence (1s / 5s / 30s / soft_kill).

    Useful context kwargs:
        endpoint (str): the API endpoint that timed out
        timeout_sec (float): the configured timeout that elapsed
        attempt (int): which retry attempt (1-indexed)
    """
    SEVERITY: str = "ERROR"


class ProductNotSupportedError(BrokerError):
    """
    A semantic product intent is not supported by the requested broker,
    or the product_map entry for that intent is empty/null. (PR7)

    Raised by: broker/product_resolver.py

    Useful context kwargs:
        intent (str): the semantic intent that was requested (e.g. "BRACKET_ORDER")
        broker (str): the broker that does not support it (e.g. "zerodha")
        available_intents (list): intents that DO have a non-empty mapping for this broker
    """
    SEVERITY: str = "ERROR"


class BrokerRateLimitError(BrokerError):
    """
    Client-side token bucket was exhausted and max_wait_sec elapsed before
    enough tokens became available. (RL3, RL9)

    WARN (not ERROR): rate limiting is an expected operational condition under
    heavy load, not a system failure.

    Useful context kwargs:
        category (str): endpoint category ("order", "quote", "historical", "margins")
        requested_tokens (int): number of tokens that were requested
        available_tokens (float): tokens in bucket at timeout
        waited_sec (float): actual time spent waiting
        max_wait_sec (float): the configured wait ceiling that was exceeded
    """
    SEVERITY: str = "WARN"


class BrokerRateLimit429Error(BrokerError):
    """
    Broker returned HTTP 429 Too Many Requests. (BL-6)

    Distinct from BrokerRateLimitError, which signals client-side bucket
    exhaustion (pre-emptive; never reached the broker). This class is raised
    when we DID reach the broker and got a 429 back, meaning our client-side
    estimate was slightly off or the broker tightened limits mid-session.

    The adapter that raises this has ALREADY called rate_limiter.penalize() to
    freeze the bucket for delay_sec; callers that retry will naturally wait
    out the freeze via acquire() on the next attempt. No caller-side sleep
    is required or recommended.

    Useful context kwargs:
        operation (str): the adapter method that was called (e.g. "place_order")
        category (str): rate_limiter category ("order" / "margins" / ...)
        delay_sec (float): the penalize duration applied to the bucket
        attempt (int): 1-indexed 429 attempt count for this category
    """
    SEVERITY: str = "WARN"


class RateLimitAbortedError(TradingSystemError):
    """
    FIX-060: Rate limiter acquire() was aborted because the shutdown event was set.

    Raised when a RateLimiter.acquire() call is waiting for tokens but the
    system shutdown_event fires. This allows graceful shutdown without waiting
    for the full max_wait_sec timeout on every blocked rate limiter.

    INFO (not WARN/ERROR): shutdown is an expected operation, not a failure.

    Useful context kwargs:
        category (str): endpoint category being acquired
        waited_sec (float): time spent waiting before abort
    """
    SEVERITY: str = "INFO"


class InvalidTransitionError(BrokerError):
    """
    An illegal order state transition was attempted. (OSM3, OSM4, OSM6)

    Raised when the requested to_state is not a valid successor of the
    current from_state, OR when any transition is attempted from a terminal
    state (COMPLETE, CANCELLED, FAILED, EXPIRED).

    Useful context kwargs:
        order_id (str): the order whose state was being changed
        from_state (str): current state at time of attempted transition
        to_state (str): the rejected target state
        allowed_next_states (list): valid successors from from_state (empty for terminals)
    """
    SEVERITY: str = "ERROR"


# ─────────────────────────────────────────────────────────────────────────────
# Sub-root: TimeError
# ─────────────────────────────────────────────────────────────────────────────

class TimeError(TradingSystemError):
    """
    Errors originating in time authority or clock validation.
    Raised by: core/time_authority.py
    """
    SEVERITY: str = "ERROR"


class ClockSkewTooLarge(TimeError):
    """
    VM clock is out of sync with broker clock beyond the configured tolerance.

    Raised by assert_clock_at_startup() when startup skew > startup_max_sec (G4).
    Also raised (indirectly via callback) when runtime avg skew > halt_sec.

    Useful context kwargs:
        skew_seconds (float): signed skew — positive means broker is ahead
        threshold_sec (float): the tolerance that was exceeded
        local_time (str): ISO-8601 local time at measurement
        broker_time (str): ISO-8601 broker timestamp
    """
    SEVERITY: str = "CRITICAL"


# ─────────────────────────────────────────────────────────────────────────────
# Sub-root: SignalError
# ─────────────────────────────────────────────────────────────────────────────

class SignalError(TradingSystemError):
    """
    Errors originating in the signal pipeline — webhook ingestion, dedup,
    expiry, screening, or the signal queue.
    Raised by: signals/webhook_receiver.py, signals/signal_validator.py,
               signals/signal_processor.py, screening/
    """
    SEVERITY: str = "WARN"


class SignalExpiredError(SignalError):
    """
    Signal arrived too late — now - signal.triggered_at > 90s. (P9a)
    Normal operational condition during slow market periods; not an error.

    Useful context kwargs:
        signal_id (str): the signal that expired
        age_seconds (float): how old the signal was on arrival
        max_age_seconds (float): configured expiry threshold
    """
    SEVERITY: str = "WARN"


class DuplicateSignalError(SignalError):
    """
    Signal is a duplicate of one already in the pipeline — same fingerprint
    found in the signals table within the dedup window. (P6)

    This is Chartink's 'send duplicate notifications' behaviour.
    Not a bug; caller should silently drop and continue.

    Useful context kwargs:
        signal_id (str): incoming signal_id
        fingerprint (str): the duplicate fingerprint
        original_signal_id (str): ID of the first occurrence
    """
    SEVERITY: str = "WARN"


# ─────────────────────────────────────────────────────────────────────────────
# EventBus error (EV4)
# ─────────────────────────────────────────────────────────────────────────────

class EventDispatchError(TradingSystemError):
    """
    One or more subscribers raised an exception during bus.publish().
    The bus invokes every subscriber before raising this (EV4: collect-all-errors).

    Useful context kwargs:
        event_type (str): name of the event class that was published
        event_id (str): event_id of the published event
        error_count (int): number of subscribers that failed
        errors (str): repr of the collected exception list
    """
    SEVERITY: str = "ERROR"


# ─────────────────────────────────────────────────────────────────────────────
# Instrument / Account errors (IC2)
# ─────────────────────────────────────────────────────────────────────────────

class InstrumentNotFoundError(TradingSystemError):
    """
    Requested symbol or token is not present in the InstrumentCache.

    Raised by: core/instrument_cache.py get_by_symbol() / get_by_token()
    when the caller uses a symbol that was not loaded from instruments.csv.

    Useful context kwargs:
        symbol (str): the symbol that was not found
        token (int): the instrument_token that was not found (alternative lookup)
    """
    SEVERITY: str = "ERROR"
