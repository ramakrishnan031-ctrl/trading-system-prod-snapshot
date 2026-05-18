"""
broker/order_state_machine.py — Trading System v2

Purpose:
    Thread-safe in-memory order state machine. Owns all order state
    transitions for live order lifecycle. Single source of truth.

Locked Design Decisions:
    OSM1  -- States (9): PENDING, SUBMITTED, OPEN, PARTIAL, COMPLETE,
             CANCELLED, FAILED, EXPIRED, UNKNOWN_IN_FLIGHT.
    OSM2  -- Allowed transition table (one-way; terminal states have no exits):
               PENDING          -> SUBMITTED, FAILED, UNKNOWN_IN_FLIGHT
               SUBMITTED        -> OPEN, PARTIAL, COMPLETE, CANCELLED, FAILED, EXPIRED
               OPEN             -> PARTIAL, COMPLETE, CANCELLED, EXPIRED, FAILED
               PARTIAL          -> PARTIAL, COMPLETE, CANCELLED, FAILED
               UNKNOWN_IN_FLIGHT-> OPEN, PARTIAL, COMPLETE, FAILED
               COMPLETE         -> (terminal)
               CANCELLED        -> (terminal)
               FAILED           -> (terminal)
               EXPIRED          -> (terminal)
             Self-loop on PARTIAL allowed (multiple partial fills).
             OPEN/PARTIAL -> FAILED added for OM7 cancel-failure (orphan) path.
             UNKNOWN_IN_FLIGHT added for FIX-068: timeout recovery path.
    OSM3  -- transition(order_id, to_state): reads current state internally,
             validates against OSM2, raises InvalidTransitionError on illegal
             move. Caller only passes target state; machine owns from_state.
    OSM4  -- Terminal states reject ALL further transitions, including
             same-state self-loops. Raises InvalidTransitionError.
    OSM5  -- Thread-safe: single threading.Lock guards the state dict.
             All public methods acquire the lock.
    OSM6  -- InvalidTransitionError (BrokerError, SEVERITY="ERROR").
             context: {order_id, from_state, to_state, allowed_next_states}.
    OSM7  -- RETIRED. Previously published OrderStateChanged on every
             transition. 2026-04-26 audit DEAD-2 removed the publish call;
             the event had no production subscriber after Audit #12.
    OSM8  -- current_state(order_id) -> str. Raises ValueError if unknown.
    OSM9  -- register(order_id) initializes order in PENDING. Raises
             ValueError if already registered.
    OSM10 -- Layer 2 (broker/). Imports: stdlib + core.exceptions.
    OSM11 -- No persistence. In-memory only.
    OSM12 -- InvalidTransitionError context: order_id, from_state, to_state,
             allowed_next_states (list).
    OSM13 -- Module-level helpers: is_terminal(state), allowed_transitions(state),
             all_states().
    OSM14 -- Constants: STATES tuple, TERMINAL_STATES tuple. No string literals
             duplicated.

What This Module Does NOT Do:
    - Does not persist state to database (see OSM11)
    - Does not log (caller logs via log_exception)
    - Does not read config files
    - Does not retry or reconnect to broker
"""
from __future__ import annotations

import threading

from core.exceptions import InvalidTransitionError

# ─────────────────────────────────────────────────────────────────────────────
# Constants (OSM1, OSM14)
# ─────────────────────────────────────────────────────────────────────────────

STATES: tuple[str, ...] = (
    "PENDING",
    "SUBMITTED",
    "OPEN",
    "PARTIAL",
    "COMPLETE",
    "CANCELLED",
    "FAILED",
    "EXPIRED",
    "UNKNOWN_IN_FLIGHT",  # FIX-068: timeout recovery
)

TERMINAL_STATES: tuple[str, ...] = (
    "COMPLETE",
    "CANCELLED",
    "FAILED",
    "EXPIRED",
)

# FIX-108: EARLIER_STATES removed (was defined for chronological guard but never
# implemented). OSM2 transition table already prevents most invalid backwards
# transitions. If full chronological validation is needed, implement via explicit
# ordering check in can_transition_to() rather than a separate constant.

# ─────────────────────────────────────────────────────────────────────────────
# Transition table (OSM2)
# Only non-terminal states appear as keys; terminal states map to empty tuples.
# ─────────────────────────────────────────────────────────────────────────────

_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "PENDING":          ("SUBMITTED", "FAILED", "UNKNOWN_IN_FLIGHT"),  # FIX-068: timeout on place
    "SUBMITTED":        ("OPEN", "PARTIAL", "COMPLETE", "CANCELLED", "FAILED", "EXPIRED"),
    "OPEN":             ("PARTIAL", "COMPLETE", "CANCELLED", "EXPIRED", "FAILED"),   # FAILED added: OM7 cancel-failure path
    "PARTIAL":          ("PARTIAL", "COMPLETE", "CANCELLED", "FAILED"),              # FAILED added: infrastructure failure
    "UNKNOWN_IN_FLIGHT":("OPEN", "PARTIAL", "COMPLETE", "FAILED"),                   # FIX-068: reconciler resolves
    "COMPLETE":         (),
    "CANCELLED":        (),
    "FAILED":           (),
    "EXPIRED":          (),
}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers (OSM13)
# ─────────────────────────────────────────────────────────────────────────────

def is_terminal(state: str) -> bool:
    """Return True if state is a terminal state (no further transitions allowed)."""
    return state in TERMINAL_STATES


def allowed_transitions(state: str) -> list[str]:
    """
    Return the list of valid next states from the given state.

    Returns an empty list for terminal states and for any unrecognised state.
    """
    return list(_TRANSITIONS.get(state, ()))


def all_states() -> list[str]:
    """Return all 8 valid state names as a list."""
    return list(STATES)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

class OrderStateMachine:
    """
    Thread-safe in-memory order state machine.

    Constructed once at startup and shared across threads.

    Usage::
        osm = OrderStateMachine()
        osm.register("ord_abc123")
        osm.transition("ord_abc123", "SUBMITTED")
        state = osm.current_state("ord_abc123")   # -> "SUBMITTED"
    """

    def __init__(self) -> None:
        self._states: dict[str, str] = {}
        self._lock = threading.Lock()

    # ── public methods ────────────────────────────────────────────────────────

    def register(self, order_id: str) -> None:
        """
        Register a new order in PENDING state (OSM9).

        Args:
            order_id: unique order identifier (ord_ prefix by convention).

        Raises:
            ValueError: order_id is already registered.
        """
        with self._lock:
            if order_id in self._states:
                raise ValueError(
                    f"Order {order_id!r} is already registered "
                    f"(current state: {self._states[order_id]!r})"
                )
            self._states[order_id] = "PENDING"

    def transition(self, order_id: str, to_state: str) -> None:
        """
        Move order_id to to_state if the transition is valid (OSM3).

        Reads the current state internally — caller never passes from_state.

        Args:
            order_id: registered order identifier.
            to_state: target state string (must be in STATES).

        Raises:
            ValueError:             order_id is not registered (OSM8).
            InvalidTransitionError: the requested transition is illegal
                                    per OSM2, or from_state is terminal (OSM4).
        """
        with self._lock:
            if order_id not in self._states:
                raise ValueError(
                    f"Order {order_id!r} is not registered in the state machine"
                )

            from_state = self._states[order_id]
            allowed = _TRANSITIONS.get(from_state, ())

            if to_state not in allowed:
                raise InvalidTransitionError(
                    f"Cannot transition order {order_id!r} from "
                    f"{from_state!r} to {to_state!r}",
                    order_id=order_id,
                    from_state=from_state,
                    to_state=to_state,
                    allowed_next_states=list(allowed),
                )

            self._states[order_id] = to_state

    def current_state(self, order_id: str) -> str:
        """
        Return the current state of an order (OSM8).

        Args:
            order_id: registered order identifier.

        Returns:
            One of the 8 state strings.

        Raises:
            ValueError: order_id is not registered.
        """
        with self._lock:
            if order_id not in self._states:
                raise ValueError(
                    f"Order {order_id!r} is not registered in the state machine"
                )
            return self._states[order_id]
