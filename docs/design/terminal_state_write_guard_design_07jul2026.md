# Terminal-State Write Guard — DESIGN REPORT (investigate + STOP)

**Wave-5 · Audit-B Phase-4 rec #5 · 07-Jul-2026 (Tue, off-market)**

> ✅ **BUILT 07-Jul-2026 (local, UNPUSHED, NOT deployed).** The hybrid was accepted and
> built exactly as designed: the trigger keystone (schema.sql, no version bump — stays
> v41) + the `close_trade` atomic CAS (D-1). All 9 raw writers verified to already catch
> broad `Exception` (no except-broadening needed). Tests
> `tests/unit/test_terminal_state_write_guard.py` (47) + 3 updated `test_order_manager.py`
> assertions; **626 green across new+affected+adjacent, 0 regressions.** memory
> `terminal_state_write_guard_design_07jul` (→BUILT); SYSTEM_MAP changelog 2026-07-07.

**Original status (below): DESIGN ONLY — investigate + report.**

Source finding (`docs/audit/audit_05jul2026.md`):
- §4.1 (:339): *"CLOSED→anything: no production caller, and all three finalizers are guarded (order_manager.py:536; state_store.py:1552, 1384) — but `update_trade_status` (order_manager.py:631-637) and `record_entry_fill` (:394-406) are raw UPDATEs with no from-state guard, so the invariant is by convention, not enforcement."*
- §4.4 gap (:365): *"No DB-level transition guard on `trades.status` … CLOSED-reopen is prevented only by caller discipline."*
- §4.8 rec #5 (:391): *"[M] Terminal-state write guard: add `AND status NOT IN ('CLOSED','CLOSED_MANUAL','FAILED','CANCELLED')` to `update_trade_status`/`record_entry_fill` or a DB trigger."*

---

## 1. State-transition MODEL (the legal map)

### 1.1 States (schema.sql:158-161 CHECK)
```
CHECK (status IN (
   'PENDING','PENDING_FILL','OPEN','PARTIAL','EXITING','CLOSED',
   'CLOSED_MANUAL','CANCELLED','FAILED','UNKNOWN_IN_FLIGHT')
   OR status GLOB 'REJECTED*')
```
The CHECK validates the **set** of allowed values — it does **not** constrain transitions. That is the entire gap.

| Class | States |
|---|---|
| **Non-terminal (mutable)** | `PENDING_FILL`, `PENDING`, `UNKNOWN_IN_FLIGHT`, `OPEN`, `PARTIAL`, `EXITING` |
| **Terminal (absorbing)** | `CLOSED`, `CLOSED_MANUAL`, `FAILED`, `CANCELLED`, `REJECTED*` |

`PARTIAL` is schema-legal and read everywhere, but **no production writer sets `status='PARTIAL'`** (confirmed by repo-wide grep — every `'PARTIAL'` hit is a *reader* `WHERE status IN (…)` or the separate `orders`/OSM state machine). It must remain an allowed from/to state for forward-compat; the finalizers already accept it as a from-state.

### 1.2 LEGAL transition map (derived from actual writers)
```
(create) ─────────────► PENDING_FILL
PENDING_FILL ─────────► PENDING
PENDING_FILL,PENDING ─► UNKNOWN_IN_FLIGHT          (A-2 timeout)
PENDING_FILL,PENDING ─► OPEN                        (record_entry_fill)
PENDING_FILL,PENDING,UNKNOWN_IN_FLIGHT ─► OPEN      (adopt_recovery_trade_to_open — A-1/E-1)
PENDING_FILL,PENDING,UNKNOWN_IN_FLIGHT ─► EXITING   (mark_recovery_trade_exiting — HARD_KILL adopt)
PENDING_FILL,PENDING,UNKNOWN_IN_FLIGHT ─► FAILED    (fail_recovery_trade / reconciler / zero-fill)
PENDING_FILL,PENDING ─► FAILED | REJECTED*          (_handle_placement_failure)
PENDING_FILL(unfilled) ─► CANCELLED                 (eod_squareoff / stale-pending cleanup)

OPEN ◄──────────────► PARTIAL                        (schema-legal; no live writer today)
OPEN,PARTIAL ─────────► EXITING                      (kill_switch / structure_exit / emergency exit)
OPEN,PARTIAL ─────────► CLOSED                        (close_trade — SL/TGT/EOD fill; mark_trade_closed_gtt)
OPEN,PARTIAL ─────────► CLOSED_MANUAL                 (mark_trade_manually_closed)

EXITING ──────────────► CLOSED           ★ H-2         (close_trade accepts EXITING — order_manager.py:544)
EXITING ──────────────► CLOSED_MANUAL                  (mark_trade_manually_closed — stuck-EXITING finalize)
EXITING ──────────────► OPEN             ★ Task-4       (revert_exiting_to_open — still-has-position)

CLOSED / CLOSED_MANUAL / FAILED / CANCELLED / REJECTED*  ──► (no legal out-edge — ABSORBING)
```

### 1.3 THE INVARIANT (what the guard must enforce — and only this)
> **A status-changing UPDATE is illegal iff the row's CURRENT (`OLD`) status is terminal.**

Deliberately a **terminal-LOCK**, *not* a full transition matrix. This is the crux from the brief ("an over-strict guard would break normal close/exit/EXITING/recovery flows"): a full matrix risks aborting a legal or future-legal edge (EXITING→CLOSED, recovery flows, OPEN↔PARTIAL). The terminal-lock is (a) sufficient to close the real vulnerability (terminal reopen) and (b) permissive enough that **every** legal edge above still passes, because none of them originate from a terminal state. It is exactly what the six guarded DAO methods already encode in their `WHERE status IN (…)` clauses.

---

## 2. Every raw-UPDATE path on `trades` (status writers)

### 2.1 GUARDED — from-state in the SQL `WHERE` (the model to follow; safe today)
| Method / site | Transition | Guard |
|---|---|---|
| `state_store.revert_exiting_to_open` (1266) | →OPEN | `WHERE status='EXITING'` |
| `state_store.adopt_recovery_trade_to_open` (1290) | →OPEN | `WHERE status IN ('UNKNOWN_IN_FLIGHT','PENDING','PENDING_FILL')` |
| `state_store.mark_recovery_trade_exiting` (1339) | →EXITING | `WHERE status IN ('UNKNOWN_IN_FLIGHT','PENDING','PENDING_FILL')` |
| `state_store.fail_recovery_trade` (1372) | →FAILED | `WHERE status IN ('UNKNOWN_IN_FLIGHT','PENDING')` |
| `state_store.mark_trade_manually_closed` (1528) | →CLOSED_MANUAL | `WHERE status IN ('OPEN','PARTIAL','EXITING')` |
| `state_store.mark_trade_closed_gtt` (2148) | →CLOSED | `WHERE status IN ('OPEN','PARTIAL')` |
| `sl_breach_monitor` (254) | exit_reason/price (no status) | `WHERE status IN ('OPEN','PARTIAL')` |
| `scripts/check_vm_state.py` (112) | →CANCELLED | `WHERE status='PENDING_FILL'` (manual diag; out of hot path) |

### 2.2 UNGUARDED — no from-state guard → **ILLEGAL-CAPABLE** (the fix targets)
| # | Site | Writes | From-state guard? | Notes |
|---|---|---|---|---|
| U1 | `order_manager.update_trade_status` (639-646) | **any status** | **NONE** | Fully generic setter. Highest-value single fix. 4 callers ↓ |
| U1a | ↳ `order_placer.py:959` | →PENDING | — | from PENDING_FILL |
| U1b | ↳ `order_placer.py:1319` | →UNKNOWN_IN_FLIGHT | — | timeout |
| U1c | ↳ `order_placer.py:1875` | →FAILED | — | zero-fill release |
| U1d | ↳ `order_placer.py:4301` | →`final_status` (FAILED/REJECTED*) | — | `_handle_placement_failure` |
| U2 | `order_manager.record_entry_fill` (394-406) | →OPEN | **NONE** | rec #5 names this one |
| U3 | `order_manager.close_trade` (595-624) | →CLOSED | **Python pre-check only** (544) — **non-atomic TOCTOU**; SQL `WHERE trade_id=?` unguarded | D-1 locus |
| U4 | `order_placer.py:3685` | →EXITING | **NONE** | emergency/kill exit |
| U5 | `order_reconciler.py:1961` | →FAILED | **NONE** | recovery absence |
| U6 | `eod_squareoff.py:830` | →CANCELLED | **NONE** | EOD cancel of unfilled entry |
| U7 | `structure_exit_manager.py:354` | →EXITING | **NONE** | structure-break exit |
| U8 | `kill_switch.py:962` (`_mark_trade_exiting`) | →EXITING | **NONE** | "best-effort; broker truth > DB" |
| U9 | `kill_switch.py:1275` | →EXITING | **NONE** | second flatten path |

**Graceful-degradation note (matters for the trigger option):** every unguarded site U4–U9 and U1a-d already wraps its UPDATE in `try/except` that logs and continues. The **one** exception is **U3 `close_trade`**, whose caller catches only `ValueError` (order_placer.py:2249) — see §3.4.

Non-status raw UPDATEs (out of scope — do not touch status): `order_manager` exits_verified (269) / latency (474) / entry-fill fields; `order_placer` sl_trail_count (2369); `order_reconciler` qty_filled (1805); `eod_squareoff` exit_reason (1649); the three financial-backfill writers (`record_manual_close_financials` 1584, `record_gtt_close_financials` 2178, `close_trade` read-back). **These write CLOSED/CLOSED_MANUAL rows legally and MUST stay allowed** — the guard must key on the *status column changing*, never on "any UPDATE to a terminal row."

---

## 3. Design recommendation — trigger vs guarded-method + SCHEMA VERDICT

### 3.1 SCHEMA-VERSION VERDICT (the Wave-5 fit question) — **decisive**
How schema is applied (state_store.py `_initialize_schema` 307-376):
1. `run_migrations(...)` runs **only if** `old_version < EXPECTED_SCHEMA_VERSION` (348-361) — rebuilds tables whose CHECK/FK/columns changed (the "12-step" rebuild exists *because* `CREATE TABLE IF NOT EXISTS` cannot alter an existing table).
2. **`conn.executescript(schema_sql)` runs UNCONDITIONALLY on EVERY boot (367)** — idempotent, "always bumps the schema_version row to the latest."
3. Verify `version == EXPECTED_SCHEMA_VERSION` (371).

**A `CREATE TRIGGER IF NOT EXISTS` in schema.sql is a behavior-only pure-add that needs NO version bump and NO migration rebuild.** Reasoning:
- A trigger is a **standalone schema object**, not a table constraint. The "IF NOT EXISTS can't alter an existing table" limitation that forces `MIGRATION_TABLES` rebuilds **does not apply** — `CREATE TRIGGER IF NOT EXISTS` simply *creates* the trigger on the next boot's unconditional `executescript`, on the live v41 DB, in place. Same mechanism as the existing `CREATE INDEX IF NOT EXISTS` objects.
- Leave `EXPECTED_SCHEMA_VERSION=41` and the trailing INSERT at `'41'`: the trigger is still created (executescript), and the version check `41==41` passes. **No bump required, no `MIGRATION_TABLES[42]` entry, no rebuild.** → genuinely no-schema-change, **fits Wave-5.**
- **Rebuild-survival (verified safe):** a *future* `trades` 12-step rebuild DROPs the table (and its triggers), but because `executescript` re-applies schema.sql — including the `CREATE TRIGGER IF NOT EXISTS` — **after** `run_migrations` on the same boot (367 > 350), the trigger is auto-recreated. Requirement: the trigger must **live in schema.sql** (not a one-shot script). It does, by design.

⚠️ **Two honest caveats:**
- **(convention)** Every prior schema.sql content change *did* bump the version marker as an audit trail (even pure-add tables: v37/v39/v40/v41). Shipping the trigger with **no bump** is a defensible departure (it's a behavior object, not data), but it means `schema_version` no longer 1:1-tracks "schema.sql content." If Web Claude prefers to preserve the convention and bump for traceability, that turns it into a schema change and it **must sequence with P1's pending v42** (trigger→v42, P1→v43, or bundle). My read: **do NOT bump** — keep it behavior-only, which is precisely what makes it Wave-5-eligible.
- **(exception surface)** `RAISE(ABORT)` surfaces in Python as **`sqlite3.IntegrityError`** (a `DatabaseError`, **not** a `ValueError`). U3's caller catches only `ValueError` → see §3.4.

**Guarded-method** = **zero** schema.sql change, no version question at all — the purest Wave-5 fit — **but** only effective if *all* of U1–U9 are routed through it; a *future* raw UPDATE bypasses it.

### 3.2 Option A — DB trigger (data-layer keystone)
```sql
-- schema.sql, behavior-only pure-add (CREATE TRIGGER IF NOT EXISTS; no version bump)
CREATE TRIGGER IF NOT EXISTS trg_trades_terminal_status_guard
BEFORE UPDATE OF status ON trades
FOR EACH ROW
WHEN (OLD.status IN ('CLOSED','CLOSED_MANUAL','FAILED','CANCELLED')
      OR OLD.status GLOB 'REJECTED*')
BEGIN
  SELECT CASE WHEN NEW.status <> OLD.status
    THEN RAISE(ABORT, 'illegal write to terminal trades.status')
  END;
END;
```
- `BEFORE UPDATE **OF status**` → fires only when `status` is in the SET list ⇒ the financial-backfill UPDATEs (no status in SET) **never fire it**. ✓
- `WHEN NEW.status <> OLD.status` → idempotent same-status writes pass; only a *real* terminal→different transition aborts. ✓
- Catches **all** of U1–U9 **and any future writer** — the one property the method can't match.
- **Pros:** one ~7-line DDL; zero hot-path edits; behavior-only (no bump); defense-in-depth vs future raw SQL. **Cons:** `IntegrityError` surface (§3.4); "action-at-a-distance" less visible than a method.

### 3.3 Option B — guarded-method (route all raw sites)
Add to the WHERE of the status-changing writers (or centralize in one `StateStore.transition(trade_id, new_status)`):
```sql
AND status NOT IN ('CLOSED','CLOSED_MANUAL','FAILED','CANCELLED')
AND status NOT GLOB 'REJECTED*'
```
- Highest-value single edit = `update_trade_status` (U1, 4 callers). Then U2–U9 individually (5 files).
- **Pros:** zero schema change; no exception-surface change (rowcount→0, callers already treat `False`/unchanged as "lost the race"). **Cons:** 9 edits on the hot trading path; strength = discipline (a future raw UPDATE re-opens the gap).

### 3.4 RECOMMENDATION — **hybrid: trigger keystone + `close_trade` atomic CAS** (mirrors the house "keystone + layers" pattern, e.g. RAMCOIND L1-L4, cap triple-hardening)
1. **Trigger (Option A)** as the data-layer keystone — catches all 9 sites + every future writer, behavior-only pure-add (no version bump), no hot-path edits. Because U1a–d and U4–U9 already `try/except` their UPDATE, an abort **degrades gracefully** (logged, trade correctly stays terminal).
2. **Convert `close_trade` (U3) to an explicit atomic CAS** — the single site whose caller consumes a return value and the D-1 locus. Either add the terminal-lock to its WHERE and return `None`/sentinel on `rowcount==0`, **or** catch the trigger's `IntegrityError` and translate to the existing "already-closed" `ValueError` path so order_placer.py:2249 keeps working. This removes the TOCTOU that the trigger would otherwise surface as a raw `IntegrityError`.

If Web Claude wants the **strict-minimum-blast-radius Wave-5** cut: **trigger only** (all 9 covered at the data layer; U3's abort handled by broadening its caller's `except` to include `sqlite3.IntegrityError`). If Web Claude prefers **no schema object at all**: **Option B**, starting with `update_trade_status`. My lean is the hybrid, because the trigger is the only option that also stops *future* raw writers and it is confirmed no-bump.

---

## 4. Illegal-transition PROOF (reproducible today)

**Cleanest (generic setter):**
```python
# seed a CLOSED trade, then:
order_manager.update_trade_status(trade_id, "OPEN")
# order_manager.py:644 → UPDATE trades SET status='OPEN', updated_at=? WHERE trade_id=?
# NO from-state guard; schema CHECK passes ('OPEN' is valid).
assert store.get_trade(trade_id)["status"] == "OPEN"   # ← PASSES on HEAD: a terminal trade is REOPENED
```
Capital was already released at close ⇒ the reopened OPEN row is a phantom position the reconciler may re-protect / re-exit ⇒ double management + capital-accounting drift.

**Real race (no raw call needed) — `kill_switch._mark_trade_exiting` (U8) vs a normal exit fill:**
a HARD_KILL flatten loop selects trade T (OPEN); before it writes, T's SL/TGT fill closes it (CLOSED via `close_trade`); the flatten then calls `_mark_trade_exiting(T)` → `UPDATE trades SET status='EXITING' WHERE trade_id=?` ⇒ **CLOSED→EXITING**. T re-enters the live set; the reconciler's `_check_stuck_exiting`→CHECK1 later finalizes it **again** ⇒ second release path. Same shape for U4/U7 (→EXITING) and U6 (→CANCELLED) on an already-terminal row.

---

## 5. Validation strategy (illegal REJECTED + every legal ALLOWED)

New `tests/unit/test_terminal_state_write_guard.py` on a **real** `StateStore`/`OrderManager` (not mocks), parametrized over mode (§7):

**A. Illegal rejected** — for each terminal `T ∈ {CLOSED, CLOSED_MANUAL, FAILED, CANCELLED, REJECTED_PRICE_DRIFT}` × each status-writer {update_trade_status→OPEN, record_entry_fill, close_trade, kill_switch `_mark_trade_exiting`, eod CANCELLED, reconciler FAILED, structure_exit EXITING}: assert the row **remains T** (method: rowcount 0 / returns False / read-back unchanged; trigger: `sqlite3.IntegrityError` raised **and** row unchanged). Includes the RED baseline: on HEAD the generic-setter reopen **succeeds** (proof #4) → must flip to REJECTED.

**B. Every legal edge still ALLOWED** (the anti-over-strict crux — must all stay green):
- `PENDING_FILL→PENDING→OPEN`; `PENDING/PENDING_FILL/UNKNOWN_IN_FLIGHT→OPEN` (adopt); `→UNKNOWN_IN_FLIGHT`; `→FAILED/REJECTED*/CANCELLED`
- `OPEN/PARTIAL→EXITING` (all three exit writers)
- **`EXITING→CLOSED` (H-2)** and **`EXITING→CLOSED_MANUAL`** and **`EXITING→OPEN` (revert)** — explicitly asserted
- `OPEN/PARTIAL→CLOSED` (close_trade + GTT) and `→CLOSED_MANUAL`
- **Financial backfill on a CLOSED / CLOSED_MANUAL row** (`record_manual_close_financials`, `record_gtt_close_financials`, close_trade read-back) **MUST succeed** — proves the guard keys on status-change, not on "any write to a terminal row."
- **Idempotent** same-status write passes.

**C. D-1 race** — two concurrent finalizers on one OPEN trade ⇒ exactly one wins, capital released once (see §6).

**D. Regression** — full suite + `test_state_store`, `test_order_manager` (close_trade guard), `test_order_reconciler`, `test_structure_exit_manager`, EOD, kill_switch suites all green.

---

## 6. D-1 relationship (close_trade double-release)

D-1 = the `close_trade` TOCTOU: Python pre-check (order_manager.py:544 reads status) **then** an unguarded `UPDATE … WHERE trade_id=?` (598) — two concurrent finalizers can both pass the read-check and both write/release. Partial mitigation exists (the OCO race warn-return, order_placer.py:2211-2217).

- The terminal guard **relates to but does not fully subsume** D-1. It closes the **data-corruption half** (a second finalizer can no longer overwrite an already-terminal row — method: no-op; trigger: abort). It converts today's *silent* CLOSED_MANUAL→CLOSED clobber into a loud/no-op.
- D-1's **capital double-release** still needs `close_trade` to be an **atomic CAS** (WHERE terminal-lock + `rowcount` decides the winner) and the caller to release capital **only on a win**. The terminal guard is the **enabling primitive** for that CAS, not a replacement. ⇒ **complementary; the hybrid's step-2 (`close_trade` CAS) is exactly D-1's core fix.** Recommend building them together.

---

## 7. Parity conclusion (single shared transition path)

**Confirmed — one mode-agnostic transition path; no paper-specific branch.** All status writes target the single `trades` table via the shared `order_placer → order_manager → state_store` path and the `state_store` DAO. `mode` (`PAPER|LIVE`) is a **column tag**, not a branch. Paper fills are simulated at the **broker-adapter** layer (`_paper_fills`), but the resulting status transitions flow through the identical DAO/writer path. No writer forks on mode. ⇒ a guard placed in the DAO (method) **or** the table (trigger) covers both modes **by construction**. Tests still parametrize mode to assert identical behavior.

---

### Appendix — build-integration checklist (for when the directive comes; NOT built)
- [ ] If trigger + no bump: add `CREATE TRIGGER IF NOT EXISTS …` to schema.sql; keep `EXPECTED_SCHEMA_VERSION=41`; **do not** add a `MIGRATION_TABLES` entry. `.backup`-test a v41 DB boot → confirm trigger created, version stays 41.
- [ ] `close_trade`: terminal-lock CAS **or** catch `sqlite3.IntegrityError`→translate to the existing `ValueError` "already terminal" path (order_placer.py:2249).
- [ ] If Option B: `update_trade_status` first (4 callers), then U2/U4/U5/U6/U7/U8/U9.
- [ ] Multi-statement-txn note: eod_squareoff (828-837) updates trades+orders in one txn — an abort rolls back both (acceptable: a terminal trade should not have its stale entry-order cancelled either; validate).
- [ ] Confirm the guard does NOT fire on the 3 financial-backfill writers (no `status` in their SET).
