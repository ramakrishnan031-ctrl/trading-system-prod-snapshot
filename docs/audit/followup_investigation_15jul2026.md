# 15-Jul-2026 Follow-up Investigation Bundle — READ-ONLY

**Scope:** the follow-ups from the (already-settled) clean 15-Jul supervised session — 18:15
forward-shadow cron, the two EOD-cron failures, the P1 shadow-mismatch, the M-S5 deploy-watch
recalibration, and the PB-01 Chartink wiring spec. **This task FIXES NOTHING** — no `.py`, config,
schema, or DB row changed; DB read exclusively via `sqlite3 -readonly`; code read from the local
tree (== deployed `2dc69d5`, only docs commits since). Deploy unchanged, no rollback (settled).
Prepared 15-Jul evening, off-market.

---

## §0. RAW EVIDENCE (captured verbatim BEFORE interpretation — ChatGPT Q6 mandate)

### 0.1 eod_cleanup — the failing run (`logs/cron-eod-cleanup.log`, run 15:50 IST)
```
eod_cleanup.unexpected_error: FOREIGN KEY constraint failed
Traceback (most recent call last):
  File ".../scripts/eod_cleanup.py", line 222, in main
    run_eod_cleanup(
  File ".../scripts/eod_cleanup.py", line 73, in run_eod_cleanup
    pruned_fp = _cleanup_old_fingerprints(
  File ".../scripts/eod_cleanup.py", line 201, in _cleanup_old_fingerprints
    cur.execute(f"DELETE FROM signals WHERE {where}", (cutoff,))
sqlite3.IntegrityError: FOREIGN KEY constraint failed
```
- **Exact failing SQL** (`_cleanup_old_fingerprints`, line 188-201): 
  `DELETE FROM signals WHERE (status IN ('EXPIRED', 'DUPLICATE') OR status GLOB 'REJECTED*') AND fingerprint_date < ?`
  with bind `cutoff = today − 7 days = '2026-07-08'`.
- **Exception / timestamp:** `sqlite3.IntegrityError: FOREIGN KEY constraint failed` — cron exit 1, Telegram `15:50:07 cron:eod_cleanup FAILED — exit 1`.
- **FK edge** (parent → child, `core/schema.sql`): parent `signals(signal_id)`; **six** child tables carry `FOREIGN KEY (signal_id) REFERENCES signals(signal_id)` — `trades` (L241), `screener_results` (L661), `gate_state` (L765), `shadow_trades` (L945), `sr_detector_results` (L1258), `retest_state` (L1301).
- **Connection / pragma:** `StateStore` applies `PRAGMA foreign_keys = ON` on **every** connection (`state_store.py:110`, `_CONNECTION_PRAGMAS`). eod_cleanup opens `StateStore(db_path=…)` (line 213) → FK enforcement is ON.
- **Execution order** (`run_eod_cleanup`, each step its OWN `store.transaction()`): (1) `_cleanup_stale_signals` UPDATE signals→EXPIRED · (2) `_cleanup_stale_orders` UPDATE orders→CANCELLED · (3) `_cleanup_orphaned_smart_tgt` DELETE smart_tgt_state · **(4) `_cleanup_old_fingerprints` DELETE FROM signals ← FAILS.** Steps 1-3 committed; only step 4 rolled back.
- **Row counts (read-only, `2026-07-15`):** `signals` total = **146,000** · prune-target set (the DELETE's WHERE) = **108,243** · of those referenced by `trades` = **0** · referenced by `screener_results` = **94,436** · total `REJECTED_*` signals ever = **143,044** · fingerprint_date span = `2026-06-12 … 2026-07-15`.
- **Triggering rows (example):** `sig_9f34…|REJECTED_CIRCUIT_PROXIMITY|2026-06-23|trade_refs=0|screener_refs=1` (×8 shown). Old REJECTED_* signals whose `screener_results` child still references them.

### 0.2 generate_screened_csv — the failing run (`logs/cron-screened-stocks.log`)
```
ERROR: Database query failed: StateStore.transaction() got an unexpected keyword argument 'readonly'
Generating screened stocks CSV for 2026-07-15...
  Database: .../data_store/trading_system.db
  Creating empty CSV with headers only...
  WARNING: No data (database error)
```
- **Exact failing line:** `scripts/generate_screened_stocks_csv.py:130` (and `:156`): `with store.transaction(readonly=True) as cur:`. `StateStore.transaction()` (`state_store.py:506`) signature is `def transaction(self) -> Iterator[sqlite3.Cursor]` — **no `readonly` param** → `TypeError`.
- **DB path used today:** `data_store/trading_system.db` (the **correct** path; the M-SC2 fix at line 250 is present). Older-date lines in the append-log show `data/state.db` (pre-M-SC2-fix runs).

### 0.3 shadow-mismatch — the persisted rows (read-only)
`eod_broker_reconciliation` (last 4 sessions): every row `positions/orders/pnl/ledger = VERIFIED`, `overall = VERIFIED`, **`eod_verify_status = PENDING`**, **`mismatch = 1`**, `authoritative = 0` (SHADOW).
`pnl_reconciliation`: `broker_pnl = 0.0` every day; `variance = |system_pnl|` → 15-Jul **0.99**, 14-Jul 14.06, 13-Jul 14.54, 10-Jul **8.14**.

---

## §1. 18:15 forward-shadow cron — **FIRED ✅**

- Marker `data_store/cron_marks/forward_shadow_record.done` = **`0 2026-07-15T18:16:29+05:30`** (rc 0), mtime 18:16:29.
- Log `logs/cron-forward-shadow.log`: `forward_shadow: date=2026-07-15 wrote=2535 simulated=2405 -> data_store/v3/forward_shadow_fs-v1.jsonl` (JSONL mtime 18:16:28, 4.4 MB).
- **Deployment evidence closed** — the D2/D3 out-of-sample path ran and appended 15-Jul.
- ⚠️ **Minor watch (not a failure):** the same run also logged `date=2026-07-14 wrote=1644` — it reprocessed 14-Jul as well. Confirm on the next run that this is idempotent (MD5 de-dup) and not duplicate-appending prior days into the JSONL. Not blocking.

---

## §2. eod_cleanup FK failure — ROOT CAUSE (stated finding)

**FINDING:** The 15-Jul deploy commit **P10 `2e61fad`** broadened the fingerprint-prune predicate from
`status IN ('EXPIRED','DUPLICATE','REJECTED')` to `(status IN ('EXPIRED','DUPLICATE') OR status GLOB 'REJECTED*')`.
Pre-P10, bare `'REJECTED'` matched **zero** rows (the pipeline persists `REJECTED_<check>`, never bare
`REJECTED`) → the DELETE only ever removed EXPIRED/DUPLICATE noise (which have no child rows) → it always
succeeded. Post-P10, the DELETE now targets the **REJECTED_\*** family — and **94,436** of those old
signals are still referenced by a **`screener_results`** child row (`screener_results.signal_id → signals.signal_id`).
Because `StateStore` runs **`PRAGMA foreign_keys = ON`** on every connection, the DELETE hits the first
child-referenced row and the **whole step-4 statement rolls back with `FOREIGN KEY constraint failed`.**

- **The three leads, resolved by evidence:**
  - **M-K3 ("cron writes run FK-enforcement OFF") — REFUTED for this path.** eod_cleanup opens a full
    `StateStore`, which forces `foreign_keys = ON` (`state_store.py:110`). It is *not* FK-off. **The later
    fix design must not assume FK is off here.** (The FK-OFF code is only the migration table-rebuild,
    `migrations.py:284`, explicitly and locally.)
  - **P10 `2e61fad` — CONFIRMED the trigger** (git diff: the `IN (…,'REJECTED')` → `GLOB 'REJECTED*'` change).
  - **M-S2 `735a6c3` — not the cause.** The conflicting child is `screener_results` (94,436), the scoring
    row every screened signal gets — not a dedup/fingerprint row. Via `trades`: 0 conflicts.
- **ACCUMULATION CHECK (Q5) — this is NOT a one-session issue:** `signals` = **146,000 rows**, **143,044
  (98%) are REJECTED_\***, **108,243** are prune-eligible-but-stuck, span back to **2026-06-12** (~5 weeks),
  growing **~4.3k rows/session**. Pre-P10 the REJECTED_* family "leaked forever" (P10's own commit msg);
  P10 *intended* to start bounding it but **fails atomically, so the prune has never once succeeded** →
  unbounded growth continues. (Secondary regression: because step-4 now rolls back entirely, even the
  previously-pruned EXPIRED/DUPLICATE are no longer removed — a small fraction of the mass.)
- **Impact classification:** **non-safety, non-trading.** Dedup still works (indexed `fingerprint`/`fingerprint_date`
  lookup); DB is 357 MB. The costs are (a) unbounded slow-burn table growth, (b) a daily FAILED cron alert.
- **CANDIDATE FIX DIRECTION (NOT applied — for the design→ChatGPT→implement cycle):** the prune must
  reconcile the retention policy with the `screener_results` FK. Options to weigh (do not pre-pick):
  (i) exclude child-referenced signals from the DELETE (leaves ~94k unpruned — barely helps);
  (ii) cascade-prune the old child rows in the same txn (retention-policy decision — discards scoring history);
  (iii) schema `ON DELETE CASCADE` on the noise-child FKs (schema change + migration);
  (iv) decouple the dedup fingerprint into its own small table so `signals`/`screener_results` prune independently.
  This is a retention-policy + possibly-schema decision → **requires design + ChatGPT review; parity (paper+live).**
- **FIX-PRIORITY RECOMMENDATION:** **pull forward into the NEXT fix cycle** (it is a real regression that
  renders a shipped fix inert and grows a table unbounded + spams a daily alert) — but it is **slow-burn,
  NOT an emergency** (no safety/trading/data-integrity impact today). A normal cycle (days) is appropriate.

---

## §3. generate_screened_csv failure — ROOT CAUSE + M-SC2 re-grade (INDEPENDENT of §2)

**FINDING (independent — a `TypeError`, unrelated to §2's FK):** `generate_screened_stocks_csv.py` calls
`store.transaction(readonly=True)` at lines **130 and 156**, but `StateStore.transaction()` accepts no
`readonly` kwarg → `TypeError` on every query → the script catches it, writes a header-only CSV, and
reports "No data (database error)". Fails **identically on every trading day** in the append-log (2026-06-19 … 2026-07-15).

- **git provenance (proves independence + the re-grade):**
  - The `readonly=True` kwarg was introduced **`1b7225c` (14-May, FIX-039 "DB-based rewrite")** — it has
    been broken for ~2 months.
  - **M-SC2 fix `522da32`** only corrected the **DB path** (`data/state.db` → `data_store/trading_system.db`)
    and un-faked the SUCCESS exit. It did **not** touch the `readonly` bug.
- **RUNTIME PROOF:** today's 15-Jul run used the **correct** DB path (`data_store/trading_system.db`, per the
  522da32 fix) and **still failed** with the `readonly` TypeError → empty CSV. Correct path, still broken.
- **M-SC2 RE-GRADE VERDICT: CLOSED → PARTIAL.** 522da32 genuinely fixed the defect M-SC2 *named*
  (non-existent DB path + faked success) — the commit is **not** mis-credited. But the report's **purpose**
  (a populated screened-stocks CSV) is still unmet, blocked by a **separate, older** `readonly`-kwarg
  TypeError (FIX-039, 14-May). Runtime evidence beats commit-existence (the **M-S4 lesson**) → **M-SC2 is
  PARTIAL**, with the residual logged as a new sub-item (call it **M-SC2b**: screened-csv `transaction(readonly=)`
  TypeError). This is **not** a deploy regression — it predates everything in the 14-Jul bundle.
- **CANDIDATE FIX DIRECTION (NOT applied):** drop the unsupported `readonly=True` from both call sites (the
  script is read-only; a plain read cursor / `fetch_all` suffices), OR add a real `readonly` param to
  `StateStore.transaction()`. Trivial + report-path only, but still design→review→implement per policy.

---

## §4. "shadow mismatch vs eod_verify" — CHARACTERISED (P1 soak evidence, no fix)

- **What it is (code):** `mismatch = int(p1_clean != ev_clean)` (`eod_broker_reconcile.py:294`), where
  `p1_clean = (overall_status == VERIFIED)` and `ev_clean = (eod_verify_status == 'VERIFIED')`. It is a
  **verdict-label** disagreement, **not** a data disagreement.
- **AFFECTED FIELD:** the overall label only. P1-reconcile says **VERIFIED** (positions/orders/pnl/ledger
  all VERIFIED); eod_verify says **PENDING** — solely because eod_verify abstains on P&L ("P&L NOT CHECKED,
  no broker feeder yet — pending P1"). **Positions and orders agree (both VERIFIED); there is no
  position/order discrepancy.**
- **MAGNITUDE:** `pnl_reconciliation` shows **`broker_pnl = 0.0` every session** (feeder not wired), so
  `variance = |system_pnl|`. Today = **Rs 0.99** (= today's system realized net P&L across 7 CLOSED trades).
- **FREQUENCY:** **every session** — `mismatch = 1` on 15/14/13/10-Jul; eod_verify has been PENDING since 08-Jul.
- **VERDICT vs the known nuance:** this **IS** the same known `broker_pnl = 0.0` variance pattern (the
  reference **Rs 8.14** on 10-Jul). Today's Rs 0.99 is **smaller** — **nothing new or larger**. The soak is
  doing its job; **no fix** — P1 stays SHADOW until the broker-P&L feeder is wired (P0-3 promotion work).
- **Adjacent minor bug (record only):** `_local_capital_snapshot` runs `SELECT balance_after FROM fm_ledger
  ORDER BY id DESC` but `fm_ledger`'s PK is **`ledger_id`** (no `id` column) → `no such column: id`, caught,
  returns `0.0`. Cosmetic in shadow; a candidate cleanup for the fix cycle. Not the mismatch, not safety.

---

## §5. M-S5 DEPLOY-WATCH RECALIBRATION (doc/memory only — NO code change)

The deploy watch **"`SHADOW_INNING_ACTIVE` > a couple → STOP"** tripped at **323** on 15-Jul, but the
**M-S5 guard is functioning CORRECTLY** and the threshold was **mis-calibrated** against the pre-existing
`shadow_tracker` mechanism.

- **Today's 323 = 3 symbols, ALL with a genuine active inning, ZERO mis-fires:** NUVOCO 247, LANDMARK 74,
  WANBURY 2 — each had a *real trade CLOSE* today spawning a simulated inning (e.g. NUVOCO `innings` row
  258 `is_real=1` TGT → 259 `is_real=0` sim to EOD). Volume = Chartink re-firing scanners 300–374×/session.
- **Per-session baseline (SHADOW_INNING_ACTIVE rejects):**

  | Session | 15-Jul | 14-Jul | 13-Jul | 10-Jul | 09-Jul | 08-Jul | 07-Jul | 06-Jul |
  |---|---|---|---|---|---|---|---|---|
  | Count | **323** | 303 | 823 | 840 | 505 | 480 | 828 | 122 |

  Today is on the **low end** of a long-standing **122–840** range.
- **ACTION (recorded): recalibrate the WATCH THRESHOLD only. Do NOT modify M-S5 code (it is correct).** The
  deploy-stop is cleared as a false trigger. The §A2 watch item in
  `deploy_behaviour_delta_prediction_14jul2026.md` is annotated as mis-calibrated with this baseline. Keep
  observing future sessions via forward-shadow (this is an engineering conclusion pending continued
  observation, not an absolute).

---

## §6. PB-01 Chartink alert spec — for Rama to wire (read-only; NOT wired here)

PB-01 (`pb01_breakout_retest`) is `enabled: false` + `v3_playbook: true` (fail-closed) and captured nothing
on 15-Jul because **no upstream Chartink alert exists** (the scanner never appears in `webhook_audit`). To
activate the SHADOW capture, Rama configures a Chartink **EOD/daily** alert with:

- **Webhook URL:** `http://161.118.187.249:5000/webhook/pb01_breakout_retest?token=<WEBHOOK_SECRET>`
  - port **5000** (`webhook.bind_port`); path segment **must equal** the scanner key `pb01_breakout_retest`
    (`scan_webhook_map.yaml`); `<WEBHOOK_SECRET>` = the **same shared token** as the other 15 scanners.
- **Auth:** `?token=` query param (`require_hmac: false` — Chartink cannot sign payloads).
- **Payload (Chartink standard JSON):** `stocks` (comma-separated symbols), `trigger_prices`, `triggered_at`,
  `scan_name` (WR4). The system computes the breakout LEVEL itself (Max(20-session High)); no level field needed.
- **Routing:** registered `scanner_type: eod` → handled by `_handle_eod` (`webhook_receiver.py:634`, dispatched
  at :454 **before** the entry-window gate at :470-473) → routed to the **watchlist only**, never the order
  path. A raw firing can never place/modify/cancel (strategy `enabled:false` = fail-closed, proven G-NO-ORDER).
- **⏰ CRITICAL TIMING CONSTRAINT:** the capture worker lives **inside `main`, which is up 08:15–16:00 IST
  only.** The Chartink EOD alert **must fire before 16:00** to be captured — schedule it in the **~15:30–15:59**
  window (after square-off, before the 16:00 self-exit). An alert firing after 16:00 hits a stopped receiver
  → fail-safe miss (no capture, no error). The next-morning entry stage then consumes the captured row in its
  **09:20–11:00** window.

---

## §7. ONE-LINE STATUS + what the NEXT (fix) cycle must cover

**STATUS:** 18:15 cron FIRED (evidence closed); both EOD-cron failures root-caused independently
(eod_cleanup = P10-unmasked FK-vs-`screener_results`, prune stuck, 108k rows, non-safety; screened-csv =
pre-existing `readonly`-kwarg TypeError → **M-SC2 re-graded CLOSED→PARTIAL**); shadow-mismatch = the known
`broker_pnl=0.0` label-gap (Rs 0.99 today, nothing new); M-S5 watch recalibrated (323 in-baseline, guard
correct); PB-01 Chartink spec ready for Rama. **No fix applied.**

**Ordered backlog for the fix cycle (design → ChatGPT review → implement, permanent + paper/live parity):**
1. **eod_cleanup FK prune** (pull forward; non-emergency). Decide the retention policy vs the `screener_results`
   FK; do not assume FK-off (StateStore forces FK-on). 108k stuck, growing ~4.3k/session.
2. **M-SC2b: screened-csv `transaction(readonly=)` TypeError** (trivial; report-path). Re-grade M-SC2 → PARTIAL
   in the pending register.
3. **(cheap sweep) `fm_ledger` capital-snapshot column** (`id` → `ledger_id`) in eod_broker_reconcile.
4. **P1 broker-P&L feeder** (the standing P0-3 work) — until wired, the shadow-mismatch label-gap persists by
   design (no action needed now; it is soak evidence, not a defect).
5. **(watch, not fix)** forward-shadow cron idempotency — confirm it does not re-append prior days.

**Not touched (settled / out of scope):** M-S5 code (correct), P1 promotion, PB-01 wiring (Rama's action),
rollback (none). Read-only throughout.
