# P1 — Broker-Authoritative EOD Reconcile: BUILD DESIGN (02-Jul-2026)

**Type:** design-to-build spec for review. **NO implementation** in this pass.
**Inputs (not re-investigated):** `docs/design/p1_eod_broker_sync_assessment_02jul2026.md`, `docs/audit/audit_remediation_status_02jul2026.md`.
**Scope:** a STANDALONE, process-independent, broker-authoritative EOD reconcile of **positions + orders + P&L + capital**; REAL verification replacing `eod_verify`'s false-VERIFY; **broker-unreachable ⇒ UNVERIFIED (never false-pass)**. **DETECT + VERIFY + ALERT ONLY — no corrective automation (that is P3).**

---

## 1. Architecture

**New standalone job `scripts/eod_broker_reconcile.py` — NOT an in-place rewrite of `eod_verify`.** Rationale: `eod_verify` is local-only *by design*; a clean broker-authoritative job is clearer, independently testable, and can run in **shadow** alongside `eod_verify` before it becomes authoritative (mirrors the daily_review→daily_trade_review Phase-C cutover). It **subsumes** `eod_verify`'s verdict and the orphaned `reconcile_pnl`, and **consumes** `reconcile_positions`' output.

**Standalone + process-independent (closes the highest-risk gap).** It reuses `reconcile_positions._resolve_credentials` (AccountRegistry + `accounts.csv` + the token file, `scripts/reconcile_positions.py:80-140`) to build the adapter with its **own** creds and query the broker directly — so it runs **even if `trading-system.service` was DOWN at 15:17** (the case where square-off never fired and today's `eod_verify` false-VERIFIES over a naked broker position).

**What it QUERIES (broker, via the adapter):** `get_positions()` (net), `get_all_orders()` (open orders), `get_margins().net` (funds), and the day's realised P&L (kite `positions().day` realised — the `reconcile_pnl` source, done with *real* creds). **COMPARES to local:** `get_all_open_trades()`, PENDING/PENDING_FILL `orders`, `fm_ledger` realized (`Σ RELEASE_USED.pnl_delta` == `Σ trades.net_pnl`) and balances (`_total`/buckets). **EMITS:**
- **VERIFIED** — every dimension broker-confirmed clean.
- **ISSUES** — any divergence (per-dimension detail).
- **UNVERIFIED** — the broker was unreachable for a required dimension → **never a false VERIFIED**.

**Subsumes the orphaned `reconcile_pnl`:** P1 computes broker day-P&L with `_resolve_credentials` (not `reconcile_pnl`'s generic `ZERODHA_API_KEY`, which "is never set in this deployment" → the creds bug that made it ERROR), compares to system realized, and writes `pnl_reconciliation` using the **correct columns `system_pnl`/`broker_pnl`/`variance`** (the schema is already right at `core/schema.sql:783-785`; the bug was `eod_verify`'s *query* using non-existent `system_net_pnl`/`broker_net_pnl` → swallowed `OperationalError` → variance stuck 0.0). P1 queries the right columns, so the P&L variance actually computes. `reconcile_pnl.py` is then retired.

**Consumes `reconcile_positions`' output (resolves the contradiction):** P1 reads today's `position_reconciliation` rows (written by `reconcile_positions` @15:45). **If any row is `ORPHAN_AT_BROKER`/`MISSING_AT_BROKER`/`QTY_MISMATCH`, P1's overall verdict CANNOT be VERIFIED** — it emits ISSUES. This makes the 15:55/15:58 verdict *unable to contradict* the 15:45 CRITICAL. For robustness (if `reconcile_positions` was skipped/errored), P1 **also does its own** `get_positions()` position comparison — one authoritative pass — and flags any disagreement with the 15:45 rows.

---

## 2. Exact hook points

**Cron slot.** Runs AFTER `reconcile_positions` (15:45) and after settle. During shadow: **add `eod_broker_reconcile` @15:58** while `eod_verify` @15:55 keeps running. At cutover: P1 becomes authoritative and `eod_verify` is retired from cron+registry (git history = rollback), exactly as `daily_review` was deleted in Phase C.
- `config/cron_registry.yaml`: new entry `eod_broker_reconcile` — `schedule: 15:58`, `critical: true`, `command: scripts/eod_broker_reconcile.py`, monitored (writes a `cron_heartbeat`), marker-verified.

**Broker calls** via the adapter built from `_resolve_credentials` (standalone): `get_positions`, `get_all_orders`, `get_margins`, day-P&L. `is_broker_api_available` guard → on unreachable, emit **UNVERIFIED** (not skip-as-pass).

**Tables.**
- READ: `trades` (OPEN/PARTIAL + PENDING), `orders` (PENDING/PENDING_FILL), `fm_ledger` (realized + balances), `position_reconciliation` (the 15:45 output).
- WRITE: `pnl_reconciliation` (correct columns); and a **NEW verdict table `eod_broker_reconciliation`** (PURE-ADD migration) — one row/day:
  ```
  date PK, broker_reachable INT, positions_status TEXT, orders_status TEXT,
  pnl_status TEXT, capital_status TEXT, ledger_status TEXT,
  overall_status TEXT,   -- VERIFIED | ISSUES | UNVERIFIED
  mode TEXT,             -- LIVE | PAPER(self-consistency)
  notes TEXT, verified_at TEXT
  ```
  (A new table, not extending `eod_verification`, so shadow doesn't disturb the existing verdict.)
- `eod_verification`'s status enum stays as-is during shadow; at cutover it's retired with `eod_verify`.

**Verdict surfacing:** (a) an EOD-summary **Telegram** (VERIFIED/ISSUES/UNVERIFIED + the divergent dimensions; ISSUES/UNVERIFIED at CRITICAL once authoritative, INFO in shadow); (b) a **Control Tower** finding + `eod_broker_reconcile` **freshness source** (replacing the freshness-only `eod_squareoff_log` view with a genuine broker verdict); (c) `cron_officer --eod-summary` @18:50 reads the new verdict row.

---

## 3. The 4 detections (compare + emit)

| # | Detection | Compares | Emits |
|---|---|---|---|
| 1 | **Orphan positions** | broker `get_positions` (symbol×qty) vs local OPEN/PARTIAL trades; + reads `position_reconciliation` | broker-has/local-doesn't → `ORPHAN_AT_BROKER`; local-has/broker-doesn't → `MISSING_AT_BROKER`; qty differ → `QTY_MISMATCH`. **This is where a process-down naked position (A-1 territory) is caught at EOD.** Any non-OK → overall ISSUES. |
| 2 | **Capital drift** | broker `get_margins().net` vs local `fm._total` | `\|Δ\| > tolerance` → `CAPITAL_DRIFT` → ISSUES. (EOD, broker-authoritative equivalent of the in-session G3 — but a *verdict*, and it catches drift that accrued after close.) |
| 3 | **Ledger drift** | `fm_ledger` 3-balance invariant (`avail+reserved+used==total`) + `Σ RELEASE_USED.pnl_delta` vs broker day-P&L | post-EOD reserved/used ≈ 0 & avail ≈ total; realized-ledger vs broker-P&L within tolerance; invariant holds. Divergence → `LEDGER_DRIFT` → ISSUES. (Cross-checks the `get_daily_realized` double-cost finding [[get_daily_realized_pnl_double_cost_01jul]].) |
| 4 | **Manual broker-side actions** | broker exit fills / broker-flat-but-local-open vs the local `exit_reason`/closure label | flags a broker close the system didn't originate, AND **the exit-reason label refinement**: classify genuine **TGT_HIT** (broker fill at/near the target) vs **MANUAL_CLOSE** from broker truth, and flag mislabels across the **alert + stored `exit_reason` + stats** (the deferred `order_placer.py:2092` determination) — P1 supplies the broker-truth basis; ISSUES on a mismatch. |

---

## 4. Alerts-before-automation boundary (explicit)

**P1 EMITS the verdict + ALERTS on divergence. It does NOT auto-fix.** No flatten of an orphan-at-broker, no capital correction, no forced relabel-write beyond recording the honest classification, no order actions. **Auto-remediation (guarded EOD flatten, capital correction, relabel) = P3.** P1's job is to make the EOD verdict *true* (never a false "all clear") and to surface divergence loudly — a human/P3 acts.

---

## 5. Shadow-first rollout

- **Phase S (shadow, default):** `eod_broker_reconcile` @15:58 computes the broker-truth verdict, WRITES its `eod_broker_reconciliation` row, and LOGS/alerts at **INFO/shadow severity** — but does **NOT** replace `eod_verify` @15:55 and gates nothing. Observe several EODs: does P1's VERIFIED/ISSUES/UNVERIFIED match reality? The key signal: **cases where `eod_verify` says VERIFIED but P1 says ISSUES = the false-VERIFY caught in the wild.** Also confirm UNVERIFIED fires on real broker-unreachable EODs (weekend/holiday dry-runs).
- **Phase A (authoritative):** flip the flag `eod_reconcile.authoritative: true` → P1 becomes THE EOD verdict (ISSUES/UNVERIFIED alert at CRITICAL; EOD summary + Control Tower + cron_officer read P1); **retire `eod_verify`** (delete @15:55 from cron+registry) and **`reconcile_pnl`** (folded in). `reconcile_positions` @15:45 stays (earlier detection; P1 consumes it).
- **Flag mechanism:** `eod_reconcile.authoritative: false` in `system_config.yaml` (shadow default) read by the job; the cutover is a registry swap (add P1-authoritative alert path, remove `eod_verify`), reversible via the flag + git.

---

## 6. Parity (one code path)

P1 calls the **adapter** (`get_positions`/`get_all_orders`/`get_margins`), which in paper returns the **simulation** (`_paper_positions`/`_paper_fills`/`_paper_capital`). So in PAPER, P1 compares local-DB vs the adapter-sim = a **self-consistency check** (the "broker" is the sim, driven by the same fills that drove local) — **not** independent authority. **No `if paper` branch:** the adapter abstracts the source; P1 only *labels* the verdict `mode=PAPER (self-consistency)` in the record + alert. This is strictly better than `reconcile_positions`' current paper behaviour (it fabricates `status=OK` with no adapter call) — P1 runs the real paper `get_positions`, so paper P1 genuinely catches local-vs-sim drift (and gives paper test coverage).

---

## 7. Test plan (mock broker states; fail-on-old / pass-on-new)

| Scenario | Inject | Expect | Fail-on-old |
|---|---|---|---|
| Clean | broker positions/orders/pnl/margins all match local | **VERIFIED** | — |
| Orphan-at-broker (naked) | broker has a position local doesn't (local shows 0 open) | **ISSUES (POSITIONS)** | today's `eod_verify` (local-only) → **VERIFIED** over a naked position |
| Capital drift | broker margins ≠ local `_total` beyond tolerance | **ISSUES (CAPITAL)** | `eod_verify` never checks capital → VERIFIED |
| Broker unreachable | `get_positions`/`get_margins` raise | **UNVERIFIED** (never VERIFIED) | `eod_verify` still VERIFIED (local-only) |
| P&L variance (column-bug) | broker day-P&L ≠ system realized | `pnl_reconciliation.variance` **computes** → ISSUES | old query used `system_net_pnl` → OperationalError swallowed → variance 0.0 (dead) |
| Contradiction | `position_reconciliation` has `ORPHAN_AT_BROKER` @15:45 | P1 **must NOT VERIFIED** → ISSUES | `eod_verify` ignores that table → VERIFIED contradicting the 15:45 CRITICAL |
| Process-down | trading-system.service down at 15:17; naked position at broker | P1 (standalone creds) still queries broker → **ISSUES** | square-off never ran; `eod_verify` local-only → VERIFIED |
| Parity | paper adapter sim clean | **VERIFIED (paper self-consistency)**, labeled | — |

---

## 8. Deploy / rollback

- **Off-market.** Deploy = (a) `scripts/eod_broker_reconcile.py` (new), (b) cron-registry entry @15:58 (post-receive auto-installs crontab), (c) a **PURE-ADD** schema migration for `eod_broker_reconciliation` (+ a version bump), (d) the shadow flag `eod_reconcile.authoritative: false`. **No** `pnl_reconciliation` schema change (P1 just queries the correct existing columns).
- **Rollback** = flip `authoritative: false` (P1 → shadow, `eod_verify` stays authoritative) or revert the branch. Non-destructive (shadow default + additive table). The cutover (retiring `eod_verify`) is a separate, later registry change — reversible via git.
- **Isolation:** P1 touches `scripts/eod_broker_reconcile.py` (new), `cron_registry.yaml`, `core/schema.sql` (+ migration), `system_config.yaml` (flag), and — at cutover — retires `eod_verify`/`reconcile_pnl`. **Disjoint** from B-1 (`risk_engine`/`fund_manager`/`order_reconciler` MTM) and the A-2/C-1 stack. Own branch off `main`. P1 **reuses the reconciler's broker-access + compare *patterns*** (and the B-1-refreshed MTM is available if a P1 dimension ever needs unrealized), but P1 is a **separate standalone EOD job** — it does not modify the 15s in-session reconciler.

---
**No code changed.** Review this spec → then implement P1 (shadow → authoritative), off-market, isolated. Auto-remediation remains P3.
