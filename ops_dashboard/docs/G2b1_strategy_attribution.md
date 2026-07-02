# G2b-1 — Per-Strategy Signal Attribution (STEP 0 findings)

**Date:** 2026-07-03 IST · **Scope:** can webhook-level funnel numbers be attributed per strategy without fabrication?
**Consumer:** `backend/services/strategy_tower.py` (SIGNALS group) + `/api/strategies`. Facts cited `file:line`; decision rules at the end for Web Claude review.

## 0.1 scan_webhook_map cardinality — **1:1 today; 1:N structurally impossible**

- Map file: `config/scan_webhook_map.yaml`. Format (S14, header comment L8-12): `scanners.<scanner_name>: {strategy: <one name>, chartink_url: ...}` — **each scanner entry names exactly ONE strategy**.
- Validation: `strategies/loader.py::_validate_scan_webhook_map` L93-137 — reads `raw["scanners"]` (L115), takes `entry.get("strategy")` (a single key, L121-124), errors if missing (L126-130) or if the strategy has no YAML (L132-137). **Duplicate scanner names → CRITICAL startup failure** (map header L5-6), so the same scanner cannot appear twice pointing at two strategies.
- Actual map today: **15 scanners → 15 strategies, names identical pairwise** (`gap_fade_long→gap_fade_long`, … all 15). So the live mapping is **exactly 1:1**.
- **N:1 is legal** (two scanner names could target one strategy in the future) — per-strategy attribution stays EXACT under N:1 (sum the strategy's scanner set).
- **1:N (one scanner feeding N strategies) cannot be expressed** in the current format. The only unattributable case is a webhook from a scanner **absent from the map** (retired/renamed scanner, or a map edit lag).

## 0.2 webhook_audit → scanner → strategy

- `webhook_audit` identifies the scanner via **`scanner_name TEXT NOT NULL`** (`core/schema.sql:520`); one row per POST with `signals_accepted`/`signals_rejected` aggregates (L524-525) and a GENERATED `date` column (L527).
- The URL path param IS the scanner (`POST /webhook/<scanner_name>`, `signals/webhook_receiver.py:268-270`), and unknown scanners are 404-rejected against `scan_webhook_map.scanners` (`webhook_receiver.py:434-437`) — audit rows for unknown scanners still get written (audit fires in `finally`, `:391-396`, response_code 404).
- ⇒ **Every webhook_audit row can be attributed to exactly one strategy via the map**, except rows whose `scanner_name` is (a) not in the current map (404s / retired scanners) — those are attributable to no strategy.

## 0.3 signals.status taxonomy (post-insert rows only)

Statuses actually written (verified in code; the CHECK enum `core/schema.sql:42-93` is an open set via GLOB):

| Family | Statuses | Written by |
|---|---|---|
| **accepted / in-flight / success** | `QUEUED` (insert, `webhook_receiver.py:691`), `PROCESSING` (`signal_processor.py:618,1419,1742`), `RESERVED` (`:885,1559,1851`), `PROCESSED` (successful place, `:1067,1649,1906`), `PROCESSED_NO_PLACER`, `ACCEPTED`, `PASSED`, `TRADED`, `GATE_*` (EntryGate parked), `RETEST_*` (SNR-V2 parked) | receiver + processor |
| **expired** | `REJECTED_EXPIRED` — the processor's 60s age gate raises `_PipelineReject("EXPIRED")` (`signal_processor.py:637-643`) → stored as `f"REJECTED_{check}"` (`:1095,1676,1921`). Bare `EXPIRED` is in the enum but the receiver's expiry check returns **pre-insert** (`webhook_receiver.py:639-640` — no row); treat bare `EXPIRED` as legacy-expired if ever seen. | processor |
| **risk-rejected** | `REJECTED_{OPEN_POSITIONS, DAILY_TRADES, DAILY_LOSS, CONSECUTIVE_LOSSES, SECTOR_EXPOSURE, CONTRARY_POSITION, DUPLICATE_SYMBOL, KILL_SWITCH, KILL_SWITCH_LATE, STRATEGY_CIRCUIT_BREAKER, TRADE_TYPE}` (risk-engine check codes, `capital/risk_engine.py:21,329-401`) | processor |
| **capital-rejected** | `REJECTED_{CAPITAL, RESERVE_FAILED, SIZING_VALID}` (+ sizing guards `REJECTED_{LOT_SKEW, INVALID_DERIVED_PRICE, NO_ATR_DATA, SCORE_*}`) | processor |
| **duplicate (post-insert)** | `DUPLICATE` — only the **insert-race** path (`webhook_receiver.py:695-698` IntegrityError). The bulk TTL-dedup drop is **pre-insert** (`:656-659`, no row) and exists only inside `webhook_audit.signals_rejected`. | receiver |
| **other rejects/failures** | remaining `REJECTED*`, `DROPPED_*`, `SKIPPED_*`, `QUEUE_FULL`, `PLACEMENT_FAILED`, `TIMEOUT` (A-2) | receiver + processor |

**Bucket rule implemented in `db_reader.strategy_signal_funnel`** (documented in code): `expired` = `REJECTED_EXPIRED` or `EXPIRED`; `duplicated` = `DUPLICATE`; `rejected` = `REJECTED*`/`DROPPED_*`/`SKIPPED_*`/`QUEUE_FULL`/`PLACEMENT_FAILED`/`TIMEOUT` minus expired; `accepted` = everything else (QUEUED/PROCESSING/RESERVED/PROCESSED*/PASSED/TRADED/ACCEPTED/GATE_*/RETEST_*).

## 0.4 DECISION RULES (for Web Claude review)

- **R1 — attribution.** Scanner→strategy is resolved through `scan_webhook_map.yaml` (parsed read-only, by value). A strategy's webhook-level "received" = Σ `webhook_audit` over **its scanner set** (exact under 1:1 and N:1). Where a `webhook_audit.scanner_name` is **not in the map**, its numbers are shown as a separate row at scanner level labeled **"scanner-level (shared)"** — never split, never guessed. Since 1:N is structurally impossible today (0.1), this label today means "unmapped/unattributable scanner"; if a future map format ever allows 1:N, those scanners fall into the same labeled bucket by the same rule (attribution-cardinality ≠ 1 ⇒ scanner-level row).
- **R2 — two-level SIGNALS display.** Per strategy: webhook level (received = audit Σ over its scanners — includes the pre-insert ~82% dupe drop) + stored level (accepted/rejected/duplicated/expired from `signals` rows, which ARE strategy-tagged). The pre-insert dupes are visible as `received − stored` per strategy; per-signal dupe detail remains impossible (W9), stated on-screen.
- **R3 — expired.** `REJECTED_EXPIRED` (+ legacy `EXPIRED`), i.e. the 60s processor age gate (`signal_expiry_sec` actually = `signal_queue.expiry_sec` 600s at webhook edge; the processor re-checks the same configured expiry — the "60s threshold" in the instruction is the processor pickup-age semantic; we surface whatever the config says, not a hardcoded 60).
- **R4 — ROI denominator (PERFORMANCE.ROI).** `ROI% = net_pnl / Σ margin_reserved` over the strategy's trades **created today** (open + closed; `trades.margin_reserved`, the capital actually reserved per trade). Rationale: it is the strategy's real capital consumption recorded at reservation time; per-day basis matches every other tower number. Zero denominator ⇒ ROI `null` (renders "—").
- **R5 — PROCESSING order buckets.** Orders joined via `trades.strategy` (all legs, today by `placed_at`): `created` = all rows; `submitted` = `status <> 'PENDING'` (reached the broker per OSM); `filled` = `COMPLETE`; `rejected` = `FAILED`; `cancelled` = `CANCELLED`.
- **R6 — HEALTH "last failure".** `last_failure_time = max(latest FAILED order placed_at [via trades.strategy join], latest losing close exit_time [net_pnl<0])` — order rejection or losing close, whichever is latest; each part also shown separately. `last_successful_trade` = latest `exit_time` with `net_pnl > 0`.
