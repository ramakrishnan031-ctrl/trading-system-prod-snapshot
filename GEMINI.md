# GEMINI.md

# AI Engineering Operating Rules

You are an AI coding agent working on a production-grade algorithmic trading system.

You MUST follow these engineering and architectural rules strictly unless explicitly overridden by the user.

---

# 1. Core Engineering Principles

## Deterministic System
- Same input MUST always produce same output.
- No hidden randomness.
- No hidden mutable global state.

## Idempotent Execution
- Re-running scripts must be safe.
- No duplicate side effects.
- No manual cleanup between runs.

## Single Source of Truth
| Domain | Source |
|---|---|
| Orders | Broker |
| Positions | Broker + Reconciliation |
| Capital | Broker |
| Signals | System |
| State | Central Store |

## Resume-Safe Design
- System must recover safely after interruption.
- No corruption on restart.
- Continue from last valid state.

## Configuration-Driven Architecture
- No hardcoded values.
- All behavior controlled through:
  - configs
  - env vars
  - CLI arguments

## Zero Hardcoding
Never hardcode:
- paths
- credentials
- API keys
- symbols
- dates
- capital
- modes
- broker settings

## Boring Code Rule
- Prefer clarity over cleverness.
- Avoid magic abstractions.
- Keep code understandable after one year.

---

# 2. Python Standards

## Naming
- snake_case → functions/variables
- PascalCase → classes
- UPPER_CASE → constants

## File Governance
- Refactor files larger than ~3000 lines.
- Split functions larger than ~60 lines.

## Imports & Runtime Behavior
Forbidden:
- monkey patching
- runtime class modification
- hidden dynamic imports

## Exception Handling
- Never use bare except:
- Always log full traceback.
- Fail loudly and clearly.

## Logging
All modules MUST log:
- startup
- progress heartbeat
- output locations
- completion
- full exceptions

Never silently fail.

---

# 3. Trading System Architecture

## Event-Driven Design
Signal flow:
signal_id → trade_id → order_id

All logs MUST contain identifiers.

## Order Management
- Strict order state machine.
- Broker verification mandatory.
- Idempotent execution.
- Safe retries only.

## Risk Management
- Single centralized risk engine.
- Atomic capital allocation.
- Daily risk enforcement.

## Reconciliation
- Continuous broker reconciliation.
- Mismatch → repair or stop system.
- Recovery-safe restart behavior.

## Concurrency
- Atomic operations only.
- Thread-safe design.
- No race conditions.

---

# 4. State Management Rules

- Centralized state store.
- No hidden local state.
- LOCK → VALIDATE → COMMIT pattern.
- Invariant validation mandatory.

---

# 5. Signal Governance

## Signal Integrity
- Every signal MUST have signal_id.
- Reject duplicate signal_id values.

## Signal Expiry
- Reject stale signals using configurable expiry threshold.

## Queue Protection
- Queue overload MUST trigger backpressure.
- Reject safely instead of silently dropping signals.

## In-Flight Tracking
- Prevent concurrent execution of same symbol.

---

# 6. Kill Switch Rules

## Last-Mile Validation
Kill switch MUST be checked:
- immediately before broker dispatch

## Hard Kill
hard_kill() MUST:
- cancel all open broker orders
- verify cancellation success
- trigger critical alerts on failure

---

# 7. Strategy Configuration Rules

## Schema Validation
- All strategy configs validated at startup.
- Invalid config MUST stop startup.

## No Silent Defaults
- Missing parameters MUST fail explicitly.
- Never silently fallback to hardcoded defaults.

---

# 8. Paper vs Live Rules

## Unified Execution Path
Paper and Live MUST share:
- OrderManager
- RiskEngine
- execution pipeline

Only broker adapter may differ.

## Realistic Paper Trading
Paper mode MUST include:
- brokerage
- taxes
- slippage
- configurable latency simulation

---

# 9. File Safety Rules

## Atomic Writes
Always:
1. write .tmp
2. validate
3. rename atomically

## Immutable Raw Data
Never modify raw data files.

## Validation Before Use
Existing files MUST be:
- opened
- validated
- structurally checked
- logically checked

Never assume existence = correctness.

---

# 10. Operational Discipline

## Virtual Environment
Always use project-local venv.

## Version Discipline
- Pin dependency versions.
- Freeze Python version.

## Cloud Compatibility
Code must run on:
- Windows 11
- Ubuntu cloud systems

No OS-specific hardcoding.

---

# 11. AI Agent Behavioral Rules

When generating code:

- Preserve existing architecture.
- Avoid duplicate implementations.
- Reuse existing modules when appropriate.
- Maintain backward compatibility unless instructed otherwise.
- Prefer explicitness over abstraction.
- Ask for clarification when requirements are ambiguous.
- Do not invent hidden functionality.
- Do not create placeholder logic without marking clearly.
- Maintain deterministic behavior at all times.

---

# 12. Frontend/UI Rules

Frontend should prioritize:
- dense command-center layouts
- operational clarity
- real-time visibility
- low-latency UX
- terminal-inspired readability

Preferred stack:
- React
- Vite
- Tailwind
- WebSockets
- modular component architecture

---

# 13. Preferred Engineering Style

Preferred:
- modular services
- typed interfaces
- explicit state transitions
- structured logs
- pure functions where possible

Avoid:
- giant god classes
- hidden side effects
- duplicate business logic
- uncontrolled async flows

---

# Final Principle

The system must remain:
- deterministic
- idempotent
- event-driven
- fully auditable
- self-validating
- self-healing
- resume-safe
- config-driven
- production-grade