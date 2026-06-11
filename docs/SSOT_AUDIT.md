# Single Source of Truth Audit

Date: 05-Jun-2026 | Crash Test Day 0 (offline pre-tests)

## 1. CAPITAL STATE

- **Truth source**: fm_ledger (replay all entries to reconstruct)
- **NOT truth**: capital_snapshot table (cache only), FundManager in-memory (lost on crash), Excel report (derived), Telegram message (derived)
- **Verification**: Replay fm_ledger == capital_snapshot == in-memory. If not: invariant A fails.
- **Crash test coverage**: CT044-CT051, CT047 (invariant violation)
- **Verified today**: PARTIAL — CT047 (forced invariant violation) and CT048 (double release) tested. Capital reconstruction after SIGKILL verified via CT096/CT117.

## 2. POSITION

- **Truth source**: Broker (Zerodha) + Reconciliation Layer
- **NOT truth**: trades table alone (may lag), in-memory position dict (lost on crash)
- **Verification**: order_reconciler.reconcile_once() compares system vs broker.
- **Crash test coverage**: CT057, CT058, CT062, CT097
- **Verified today**: NO — requires live broker API (Day 2).

## 3. ORDER

- **Truth source**: Broker Orderbook (Zerodha order status)
- **NOT truth**: orders table alone (may not reflect latest broker state)
- **Verification**: order_monitor polls broker, updates orders table.
- **Crash test coverage**: CT044, CT052-CT061
- **Verified today**: NO — requires live broker API (Day 2).

## 4. SIGNAL

- **Truth source**: signals table in DB
- **NOT truth**: in-memory queue (lost on crash), webhook response (transient)
- **Verification**: Every webhook POST -> row in signals table with terminal status.
- **Crash test coverage**: CT008, CT009, CT027
- **Verified today**: PARTIAL — CT006 (malformed payloads), CT019-CT022 (validation), state_machine_validator confirmed all 363,824 signals have valid statuses.

## 5. KILL SWITCH STATE

- **Truth source**: kill_switch_state table in DB
- **NOT truth**: in-memory kill_switch flag (lost on crash)
- **Verification**: CT077 (survives restart), CT078 (stale clear)
- **Crash test coverage**: CT067-CT082
- **Verified today**: YES — CT007 (edge cases), CT093 (graceful shutdown preserves state), CT096 (survives SIGKILL). Kill switch correctly persists and enforces after restart.

## 6. TRADE LIFECYCLE

- **Truth source**: trades table + orders table (joined)
- **NOT truth**: Telegram notification (may fail), Excel report (derived)
- **Verification**: forensic_reconstructor.py traces full chain.
- **Crash test coverage**: CT156, CT157
- **Verified today**: PARTIAL — CT001 (state_machine_validator: 207 trades, 477 orders, all valid transitions). Full chain verification requires live API.

## 7. SYSTEM CONFIGURATION

- **Truth source**: system_config.yaml + strategy YAMLs (on disk)
- **NOT truth**: in-memory config objects (may be stale after file edit)
- **Verification**: Startup re-reads all configs. No hot-reload (by design).
- **Crash test coverage**: CT133
- **Verified today**: PARTIAL — CT133 (bad YAML) PASS_WITH_RISK on VM (07-Jun-2026). CT132 pending (needs running system).
