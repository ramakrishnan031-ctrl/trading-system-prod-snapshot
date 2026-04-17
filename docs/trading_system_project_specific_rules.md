# Trading System Project – Specific Rules (Revised)

> "You are a senior trading systems engineer. Rebuild the core modules (`capital/`, `orders/`, `auth/`) from scratch. **Strictly adhere to the Foundation Engineering Rules v1.0** while implementing the exact logic and fixes specified in the Audit Specification."

---

## 1. Core System Philosophy

- **Deterministic:** Same input → same output
- **Idempotent execution**
- **Single source of truth**
- **Event-driven architecture**
- **Snapshot-based decisions**
- **Broker is ultimate truth**

### Single Source of Truth

| Domain    | Source                       |
|-----------|------------------------------|
| Orders    | Broker                       |
| Positions | Broker + Reconciliation      |
| Capital   | Broker                       |
| Signals   | System                       |
| State     | Central Store                |

---

## 2. State Management

- Centralized state store
- No hidden / local state
- `LOCK → VALIDATE → COMMIT` updates
- Invariant checks enforced

---

## 3. Event & Identity

- `signal_id` → `trade_id` → `order_id`
- Full traceability required
- All logs carry IDs

---

## 4. Order Management

- Strict order state machine
- Idempotent execution
- Broker verification mandatory
- Safe retry on timeout

---

## 5. Risk & Capital

- Single risk engine
- Atomic capital allocation
- Separate capital models
- Daily risk enforcement

---

## 6. Reconciliation

- Continuous broker reconciliation
- Mismatch → fix or stop
- Recovery on restart

---

## 7. Concurrency

- Atomic operations
- No blocking flows
- Thread‑safe design

---

## 8. Time Consistency

- Single time source
- Consistent timezone (IST)
- Deterministic handling

---

## 9. Testing

- Replay mode
- Scenario testing
- Paper + Live modes

---

## 10. Observability

- Full traceability
- Structured logging
- Metrics tracking

---

## 11. Failure Handling

| Type        | Action                  |
|-------------|-------------------------|
| Recoverable | Retry                   |
| Uncertain   | Reconcile               |
| Critical    | Stop system immediately |

---

## 12. Signal Integrity & Queue Governance

| Rule                    | Requirement                                                                                                                                          |
|-------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Signal Deduplication** | Every incoming signal MUST be assigned a `signal_id`. The system MUST reject duplicate `signal_id` values within the same trading day.                |
| **Signal Expiry**        | Signals older than a configurable threshold (e.g., 60 seconds) MUST be rejected before screening to prevent stale execution.                          |
| **Queue Backpressure**   | The webhook receiver MUST return HTTP 503 when the internal queue exceeds 80% capacity. Prevents silent signal loss.                                 |
| **In‑Flight Tracking**   | Symbols currently being processed MUST be tracked to prevent concurrent processing of the same symbol, avoiding duplicate orders.                    |

---

## 13. Kill Switch Enforcement Boundary

| Rule                           | Requirement                                                                                                                                                             |
|--------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Last‑Mile Enforcement**      | The Kill Switch state MUST be re‑checked **immediately before any order dispatch** to the broker (not just at signal entry).                                            |
| **Hard Kill Cancellation**     | When `hard_kill()` is invoked, the system MUST attempt to cancel all known open broker orders **and** verify cancellation success. Uncancelable orders MUST trigger a critical alert. |

---

## 14. Strategy Configuration Validation

| Rule                    | Requirement                                                                                                                                                  |
|-------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Schema Enforcement**   | All strategy YAML files MUST be validated against a Pydantic schema at startup. Any invalid or corrupted file MUST prevent system startup with a clear error message. |
| **No Silent Fallbacks**  | Missing or malformed strategy parameters MUST NOT fall back to hardcoded defaults. They MUST cause immediate rejection of the signal with a logged reason.            |

---

## 15. Paper / Live Execution Parity

| Rule                         | Requirement                                                                                                                                                     |
|------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Unified Execution Path**   | The same `OrderManager` and `RiskEngine` code path MUST be used for both Paper and Live modes. Only the final broker adapter may differ.                         |
| **Cost Inclusion in Paper**  | Paper mode MUST apply the identical transaction cost calculation (brokerage, STT, GST, etc.) as live mode to prevent inflated performance expectations.           |
| **Latency Simulation**       | Paper mode SHOULD introduce configurable artificial latency to simulate real‑world fill delays and slippage.                                                     |

---

## Final Principle

- Deterministic
- Event‑driven
- State‑controlled
- Self‑healing
- Fully auditable