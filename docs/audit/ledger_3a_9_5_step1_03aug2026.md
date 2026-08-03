# Ledger #3a · #9 · #5 — STEP 1 MEASUREMENTS — 03-Aug-2026 (late)

**Status: `<MEASURED — NOT BUILT, NOT AUTHORISED>` for all three.**
Measured against deployed `297b587` (VM) / working tree `33f819b` (PC). Read-only throughout:
`mode=ro` SQLite on the live DB, `crontab -l`, log greps, source reads. **No `.py`, no config, no
DB row, no service touched by this record.**

⛔ **NO NEW REGISTER ROWS. 231 stands.** Each section registers against an existing §B.1
debt-ledger row (#3 / #9 / #5). This record carries measurements and one owed decision; it does
**not** carry authorisation.

> **Why the three were measured together:** they were the night's queue after the flip constraint
> closed implementation (anything committed from here first executes **Wed 08:15, flip morning**).
> Measurement records execute nothing, so they cost the flip push zero variables.

---

# §1 — LEDGER #3a: `orders.qty_filled` ZEROED ON EVERY COMPLETION

Registers against **§B.1 row #3** (fill/cancel seam truth), sub-item **#3a**, which the 03-Aug
design registration split out to ship alone.

## 1.1 The writer, and why `filled_at` survives while the other two do not

One statement zeroes it — `orders/order_manager.py:738-752`:

```sql
UPDATE orders SET status = ?, qty_filled = ?, avg_fill_price = ?,
                  rejection_reason = COALESCE(?, rejection_reason),
                  filled_at        = COALESCE(?, filled_at),
                  updated_at = ? WHERE order_id = ?
```

The value arrives from `event.qty_filled`, which `_safe_transition` reads off the **un-updated
watch entry** (`broker/order_monitor.py:1323-1326`). `_handle_complete` computes `final_qty` /
`final_price` locally (`:989-990`) and hands them **only** to `OrderFilled` (`:1006-1007`); it
never writes them back to `entry` before the transition publishes at `:992`.

⭐ **`filled_at` is correct for a reason that has nothing to do with the payload.** The subscriber
synthesises it itself — `filled_at = now_ist() if event.status == "COMPLETE"`
(`order_manager.py:155`) — and the SQL writes it with `COALESCE`. So of the four columns this
statement touches, **two are COALESCE-protected and two are unconditional overwrites**, and the
two unconditional ones are exactly the two that ride the stale payload.

⇒ **Two independent defects are stacked here**, and the register described only the first:

1. **The payload is stale** (`order_monitor`), and
2. **the writer overwrites unconditionally** (`order_manager`) where its sibling columns do not.

`entry.filled_qty` is assigned in exactly ONE place repo-wide — `_handle_partial`
(`order_monitor.py:895-897`). No PARTIAL has ever been observed live (P5 census: *0 partials
EVER*), so the field is 0 at every COMPLETE transition, on every path.

## 1.2 Fresh measurement — the register's 405/805 restates as **413/824**

VM `data_store/trading_system.db`, `mode=ro`, 03-Aug:

| measure | value |
|---|---|
| `orders` rows, all time | **824** |
| by status | COMPLETE **413** · CANCELLED **411** · *(nothing else has ever existed)* |
| COMPLETE → `qty_filled > 0` | **0 / 413** |
| COMPLETE → `avg_fill_price NOT NULL` | **0 / 413** |
| COMPLETE → `filled_at NOT NULL` | **413 / 413** |
| COMPLETE by leg | ENTRY 209 · SL 108 · TGT 71 · EOD 25 |

⭐ **Wider than the register's framing:** `SELECT … WHERE qty_filled > 0` over the **whole table,
any status** returns **zero rows**. Not one non-zero value has ever been written to this column,
by any path, in the system's life. The 411 CANCELLED rows are correctly zero; the 413 COMPLETE
rows are wrongly zero; and there is no third population.

⚠️ Corollary, already in HOT memory and re-confirmed here: `orders.status` is only ever
`COMPLETE` or `CANCELLED` — **never `REJECTED`, `FAILED`, `PARTIAL`, or `PENDING` in a surviving
row.** Count rejections from `trades.status`, never from `orders`.

## 1.3 `avg_fill_price` — the same defect, not a second one

Same payload, same statement, **one fix repairs both**. It carries one extra step: `:1324-1326`
maps `entry.avg_fill_price if > 0.0 else None`, so the stale `0.0` becomes SQL `NULL`. The NULL
is a symptom of the staleness, not an independent cause. ⛔ Do not scope it as a separate item.

## 1.4 ⛔⛔ WHO READS IT — **THE REGISTER'S PREMISE IS REFUTED**

**IA-P5-01 states:** *"no runtime consumer reads the column (P4 sweep), so the damage is to
audits/tooling/forensics."*

**That is true of CONTROL FLOW and false of RENDERING.** Nothing in the system branches, sizes,
places, reconciles, or kills on this column — that half of the claim holds and is the reason
there is no capital-path exposure. But **two live production surfaces read and display it**:

### (a) `reports/daily_trade_review.py:508-509` — cron `7 16 * * 1-5`, every trading day

```python
"filled_sl": _f((sl_fill_o or {}).get("avg_fill_price")) or _f(sp.get("sl_fill_price")),
"filled_tgt": _f((tgt_fill_o or {}).get("avg_fill_price")) or _f(sp.get("tgt_fill_price")),
```

`orders.avg_fill_price` is the **DECLARED PRIMARY SOURCE** for the D-block's `filled_sl` /
`filled_tgt`. The contract is written out explicitly in `docs/report_data_contract.md:103`:

> `orders.avg_fill_price` (COMPLETE SL/TGT leg) ∥ `trade_slippage_log.sl_fill_price`/`.tgt_fill_price`

The primary is dead **179/179** (108 COMPLETE SL legs + 71 COMPLETE TGT legs). ⭐ `_pick_order(…,
filled=True)` filters on `status == "COMPLETE"` (`:350-357`), **not** on `qty_filled`, so the row
IS found — it is found and then yields `None`. Every row falls through to the fallback, silently,
every day.

⚠️ **The fallback is not the same quantity.** `slippage_recorder.py:205-206`:

```python
sl_fill  = exit_px if exit_reason == "SL_HIT"  else None
tgt_fill = exit_px if exit_reason == "TGT_HIT" else None
```

It is the **trade-level exit price**, gated on `exit_reason` — the free-text field this project has
already ruled must not be branched on (`closure_source` is the structured authority). So the
surviving path for a per-leg broker fill price runs through a classifier the codebase elsewhere
forbids.

**The measured damage** — rows where a COMPLETE SL/TGT leg existed (so a real broker fill price
was available) but **both** sources are empty:

| leg | trades with a COMPLETE leg | blank in the report |
|---|---|---|
| SL | 108 | **9** |
| TGT | 71 | **5** |

**14 cells, all time. Every one is `exit_reason = 'MANUAL'`** — i.e. the `RMS/MANUAL CLOSE`
mislabel population, which is exactly the set an operator most wants a real exit fill price for.

⭐ **The other ~92% are correct BY FALLBACK.** This is the agree-by-luck shape: a declared primary
source has been dead for the system's entire life and nothing noticed, because a secondary quietly
covers the common case. Neither surface reports that the primary returned nothing.

### (b) `ops_dashboard/frontend/templates/orders.html:38-39` — `gui-dashboard.service` **active + enabled**, `127.0.0.1:8500`

```html
<td class="cap-num" x-text="o.qty_filled + '/' + o.qty_requested"></td>
<td class="cap-num" x-text="dash(o.avg_fill_price)"></td>
```

Fed by `ops_dashboard/backend/readers/db_reader.py:687` (`list_orders`) and `:1442` (trade detail).
The Orders screen therefore renders **`0/N`** in the Filled column and **`—`** in the fill-price
column for **every filled order the system has ever placed**.

⚠️ `ops_dashboard/tests/test_api_contract.py:70` asserts the API returns these keys — a contract
test that is green on the zeroed values and cannot go red on this defect.
⚠️ `ops_dashboard/docs/G5_REDESIGN_PHASE_A.md:156` plans a *planned-vs-actual order price* panel
on this same column: **a future feature specified against a dead source.**

*(Checked and cleared — these read `trades.qty_filled`, a different and correctly-populated
column: `main.py:888`, `sl_breach_monitor`, `cnc_gtt_monitor:543`, `eod_squareoff:1155`,
`shadow_tracker`, `order_reconciler`, `daily_report`, `kill_switch:1529`, `fund_manager:1917`,
`structure_exit_manager:428`, `positions.html:44`, `pnl.html:42`. And these read `orders` but not
this column: `state_store.get_orders_for_trade` callers — `order_reconciler:3366/4309`,
`order_placer:2697/4243/4486` — which use `leg`, `status`, `variety`, `qty_requested` only.)*

**⇒ The precise statement, replacing IA-P5-01's:** *the column has no logic consumer and two
display consumers, one of them a daily cron report where it is the contracted primary source.*

## 1.5 The fix's real shape — ⛔ M4 APPLIES; "~2 lines" is not the whole thing

Mechanically it is two assignments before `order_monitor.py:992`. Three things the estimate hides:

**(i) A DECISION, not an edit — and it is the honesty decision.**
`final_qty = filled_qty if filled_qty > 0 else entry.qty` and
`final_price = avg_price if avg_price > 0 else entry.expected_price`.

- **Option A — persist broker truth** (`filled_qty` / `avg_price`): the column becomes true, and
  stays 0/NULL in the degenerate case where the broker reported nothing on a COMPLETE.
- **Option B — persist the inferred final** (`final_qty` / `final_price`): never zero, but silently
  substitutes the *planned* qty and the *expected* price for a fill that was never reported —
  producing a plausible fabrication indistinguishable from a real fill afterwards.

⛔ **Option B commits, at the writer, the exact error R-4 forbids at the backfill.** For an audit
column the honest choice is A. **This is a design call, not a mechanical edit, and the audit's
"~2 lines" does not surface it.**

**(ii) An ordering constraint on a field that gates a CAPITAL-AFFECTING event.**
The assignment must precede `_safe_transition` (that call is what publishes). But if the
transition returns `False`, `_handle_complete` returns at `:993-994` **without untracking** —
leaving a mutated `entry.filled_qty > 0` in `_watched`. `_handle_terminal:1033` branches on
exactly that field to emit `OrderPartiallyTerminated`, which `order_placer` handles by placing an
SL and booking a partial. Narrow and hard to reach, but it is the capital path, and it is created
*by the fix*, not present before it. ⇒ the fix owes an idempotency/untrack review of the
non-transitioning return.

**(iii) The test layer is structurally blind here — and one test proves it.**
`tests/unit/test_order_placer.py:1613-1621` asserts the PARTIAL path persists `qty_filled == 4`
and `avg_fill_price == 2505.0`, and it **passes** — because `_handle_partial` updates `entry`. The
COMPLETE-path tests at `:1623-1670` publish `OrderStatusChanged` **directly**, with correct values,
bypassing `order_monitor` entirely — **vacuous for this defect by construction.** This is a
textbook IA-XTEST-01 instance: the test passes the args production forgot.

✅ A RED-first test is cheap and available: drive `_process_order` with a fake adapter returning a
COMPLETE history and assert the persisted row. It fails today.

## 1.6 R-4 FIX-FORWARD — now EVIDENCED, not merely cautious

⭐ **A backfill source exists and nobody had looked at it.** `order_execution_log` — written by
`slippage_recorder._on_order_filled` from the `OrderFilled` event, which carries the **correct**
`final_qty` / `final_price`:

| measure | value |
|---|---|
| rows | **370** |
| `actual_price NOT NULL` | **370 / 370** |
| `filled_qty NOT NULL` | **370 / 370** |
| coverage window | 2026-07-20 → 2026-08-03 |

The truth was captured all along, in a different table — **another instance of IA-XARCH-03's
"fill truth ×3 stores"**, found from the opposite direction.

⚠️ Its `order_id` is the **internal** id (`ord_…`) while `orders.order_id` is the **broker** id, so
the direct join yields **0 rows**. The viable bridge is `(parent_trade_id, leg) → (trade_id, leg)`:

| bridge outcome | COMPLETE orders rows |
|---|---|
| exactly one match — **recoverable** | **108 / 413** |
| no match (log starts 20-Jul) | **305 / 413** |
| ambiguous (>1 match) | **0** |

⇒ **A backfill would restore 26% and leave a column where `0` means "no fill" on some rows and
"unknown" on others, indistinguishable forever after.** That is precisely the outcome R-4 was
written to prevent.

✅ **R-4's fix-forward default HOLDS, and is now backed by a measurement rather than by caution.**
If a backfill is ever approved it must be a separate activity that writes a **provenance marker**,
not just a value.

## 1.7 Paper CAN exercise it — by construction, with **zero artifact**

Traced at source: `_synth_fill` writes `{"status": "COMPLETE", "filled_qty": qty, "avg_price":
fill_price}` into `_paper_fills` (`zerodha_adapter.py:2231-2235`), and paper's
`get_order_history` returns exactly those (`:1127-1142`). So in paper mode `_handle_complete`
receives **real** values and discards them identically to live.

⇒ ✅ **This is NOT a "paper cannot exercise it" case** — the usual blocker on this class is absent,
and a PC-paper composition boot is a valid rehearsal for the fix.

⚠️ **But there is no paper artifact to reason from, and the width must be stated:**
**0 / 824** VM order rows are paper mode (all 824 are `mode = 'LIVE'`), and the **PC paper DB
has 0 `orders` rows in total.** The rehearsal must be *produced*; it cannot be assumed.

## 1.8 What #3a leaves open (⛔ nothing decided here)

1. **Option A vs Option B** at the writer (§1.5(i)) — the honesty decision.
2. **The untrack/idempotency review** the fix creates (§1.5(ii)).
3. **Whether the writer's COALESCE asymmetry should be levelled** (§1.1). No re-zero path is
   reachable today (one publisher; OSM terminal states block re-transition; `untrack` removes the
   entry), but the asymmetry is why the column could not stay fixed if a second publisher appeared.
   The reconciler's direct writer `_backfill_entry_order_row` (`order_reconciler.py:4326-4329`)
   already passes real values — and has produced **no** surviving non-zero row, consistent with
   never having run its success path.
4. ⛔ **#3a still ships ALONE.** Nothing here touches #3b's `HUMAN_ORDER` endpoint or the CHECK6
   route, and R-3's binding constraint on any CHECK6 redesign is untouched.

---

# §2 — LEDGER #9: ALERT FATIGUE / FALSE-SAFETY CLAIMS

Registers against **§B.1 row #9** (IA-P9-01 · IA-P9-02).

## 2.1 The audit trail — ⚠️ first finding: the table is empty

The send-side trail is the `alert_send` log line (`alerts/telegram_notifier.py:398-437`,
27-Jul). ⚠️ **The `telegram_alerts` DB table has 0 rows** — it is not the trail, and a future
reader querying it would conclude the system sends no alerts. The line first appears **31-Jul**,
so exactly **two full days** of structured evidence exist.

## 2.2 Volume, by class and severity

| | 31-Jul | 03-Aug |
|---|---|---|
| total `alert_send` | **49** | **84** |
| INFO | 31 | 75 |
| WARNING | 18 | 8 |
| `WARN` *(a third spelling)* | — | **1** |
| outcome `delivered` | 49/49 | 84/84 |
| **sentinel written** | **0** | **0** |

By source, 03-Aug: `signal_processor` 56 · `order_placer` 19 · `main` 5 · `strategy_governor` 2 ·
`kill_switch` 1 · `eod_squareoff` 1.

⭐ Per-signal / per-order INFO dominates: **71 of 84 (85%)** on 03-Aug were 56 signal + 11 order-
placed + 4 SL-hit notifications.

⛔ **Not one non-INFO alert on either day wrote a sentinel.** The entire WARNING tier is
Telegram-only, drop-on-failure, no email, no `.delivered` receipt (TG4). Whatever else is decided
about volume, **nothing at WARNING tier is durable.**

## 2.3 Structurally unconditional — six, plus one that cannot go quiet

Present on **both** days regardless of system state:

`🚀 System Active | LIVE Mode` · `SOFT KILL — scheduled` · `CIRCUIT BREAKER — Force Close` ·
`📊 DAILY SUMMARY` · `EOD Clean Shutdown` · `🛑 System Stopping`

**Two of the six are WARNING-tier.** They arrive whether or not anything is wrong.

Plus one that is nominally conditional and empirically permanent:
**`Sector data-quality: high UNKNOWN rate`** (`order_placer.py:735-752`) fires when the UNKNOWN
fraction exceeds threshold — against the measured **96.6% UNKNOWN** input (IA-P3-05,
`instruments.csv` 142/2,228) it fires every day the system trades enough to hit min-sample. Its
body prescribes *"check instruments.csv / the NSE index-member reference data"* — correct, and
un-actionable at 10:12 on a trading day. **A daily WARNING with a prescription the operator
cannot execute is the purest form of the fatigue this row names.**

Genuinely conditional (verified at source, not inferred): `EOD SQUAREOFF IN ~30 MIN` (only with
open positions at 14:45, `main.py:895-910`) · `⚠️ Config Changed Since Last Session` (only on a
config diff) · `SLIPPAGE GUARD` · `STRATEGY PAUSED` · all per-trade alerts.

## 2.4 ⭐ WHICH PRESCRIBE AN ACTION — **four do, and three of them are wrong**

This is the #8c class: a noisy-but-accurate alert wastes attention; a **confident-and-wrong** one
causes an intervention.

### (1) `Naked untracked position — <SYM>` — WARNING, fired ×5 on 31-Jul — ⛔ **FALSE 5/5**

`orders/order_reconciler.py:1844-1855`:

> *"Untracked/operator position {symbol} qty={qty} avg={price} — no local trade and **NO
> protective stop at the broker**. Not auto-managed (operator policy); surfaced once today.
> **If unexpected, check for a manual order or a system anomaly.**"*

Per **IA-P5-06** the naked detector is **GTT-blind**, and the five that fired on 31-Jul were
**T2's own CNC basket — held deliberately, each with a verified GTT at the broker.** So:

- the factual claim *"NO protective stop at the broker"* is **false for all five**, and
- the prescription sends the operator hunting a manual order **that does not exist.**

⚠️ **This is not a hypothetical — it has already fired, and `expected_alarms.md` does not list
it** (§2.6). It is the same shape as #8c, in a different surface.

### (2) `CIRCUIT BREAKER — Force Close` — WARNING, **daily** — `main.py:703`

> *"15:15 circuit breaker fired: pending entry orders cancelled.
> EOD squareoff will close **all** positions at 15:17."*

### (3) `SOFT KILL — scheduled (…)` — WARNING, **daily** — `capital/kill_switch.py:609-615`

> *"Reason: {reason}
> New signals: BLOCKED | Open positions: managed to SL/TGT/**EOD**"*

⛔ **Verified at source — `orders/eod_squareoff.py:23 / :34 / :1073`: "EOD6 — DELIVERY (CNC)
positions NOT touched. Only INTRADAY (MIS)…"** ⇒ **both bodies become false the moment delivery
is enabled**, and they arrive **together, every day, at 15:15**. See **§4** — this is the decision
this record raises.

### (4) `STRATEGY PAUSED -- <name>` — WARNING, ×2 on 03-Aug — ⛔ **contradicted by its own code**

`capital/strategy_governor.py:154-157`, body: *"Strategy paused **for the rest of today**."*
`capital/strategy_governor.py:42`:

```python
self._paused_today: set[str] = set()  # in-memory; clears on restart/new day
```

A restart resumes the strategy. **The alert text and the code comment two files apart state
opposite facts.** Fired live today: `gap_go_long` 11:09, `vwap_bounce_long` 11:56.

### Carried unchanged, for completeness

**11 `✅ ORDER PLACED` alerts on 03-Aug each claimed `Smart TGT monitoring: ACTIVE (FIXED mode)`**
(`order_placer.py:859-862`, gated only on config presence) against an engine measured starved
(`smart_tgt_state` = 0 rows ever). A false **claim** rather than a prescription — same family,
already registered as the [HIGH] divergence.

## 2.5 ⚠️ NEW — THE SEVERITY DISPATCHER **FAILS QUIET**

`alerts/telegram_notifier.py:386-393`:

```python
if severity == "CRITICAL":   result = self._handle_critical(...)
elif severity == "ERROR":    result = self._handle_error(...)
else:                        # INFO or WARN: attempt, drop on failure (TG4)
    result = self._handle_info_warn(...)
```

**Anything not spelled exactly `CRITICAL` or `ERROR` lands in the quietest tier** — no sentinel,
no email, dropped on failure. There is no validation and no unknown-severity branch.

Two production sites already ship a non-canonical string:
`main.py:2311` and `scripts/gemini_log_review.py:284` use `severity="WARN"`.

**Harmless today** — `WARN` and `WARNING` route identically — but the classifier that decides
loudness is an unvalidated free-text compare whose failure direction is **silence**. A future
`"Critical"`, `"CRIT"`, or a typo would be delivered as INFO with no trace. This is the
"never classify by free text" rule applied to the severity axis itself.

⚠️ **And the two ends of the same path default in OPPOSITE directions:**
`alert_watcher._build_email` **infers `CRITICAL`** when `context.severity` is absent (already
pinned by `test_scheduled_report_severity.py`). One end fails loud, the other fails quiet.

*(Checked and cleared — NOT defects: `ops/control_tower/disk.py:53` and `freshness.py:75` use
`severity="MEDIUM"/"HIGH"` on the **Control Tower Finding** vocabulary, which never reaches
`TelegramNotifier.send`; `scripts/check_cron_drift.py:145`'s `severity = "OK"` is remapped to
`CRITICAL`/`WARNING` before `send_alert` at `:207`. Both premises verified before reporting.)*

## 2.6 Cross-check against `docs/expected_alarms.md` — what it does not cover

The doc (written 03-Aug, closing IA-XDOCS-05) covers the **structural** classes well: the 15:15
chorus (§1), the 18:45 `resume.sh` line (§1a, #8c), the post-schema-push refuse-window (§2),
delivery/T2 artefacts (§3), the exit-code matrix and both silent windows (§4), and the alarms this
push adds (§5).

**What it does not cover is the WARNING traffic that actually arrives:**

| unlisted alarm | tier | measured |
|---|---|---|
| `Naked untracked position` | WARNING | ×5 on 31-Jul — **false 5/5** |
| `SLIPPAGE GUARD — <SYM>` | WARNING | ×4 · ×3 — the **most frequent** WARNING |
| `Sector data-quality: high UNKNOWN rate` | WARNING | daily, unactionable |
| `STRATEGY PAUSED -- <name>` | WARNING | ×2 on 03-Aug |
| `⚠️ Config Changed Since Last Session` | `WARN` | every push |
| `EOD SQUAREOFF IN ~30 MIN` | WARNING | whenever positions are open at 14:45 |

That is **6 of the 9 non-INFO alerts that fired on 03-Aug**, and **5 of 6 WARNING titles across
both audited days.**

⛔⛔ **Set against the doc's own closing rule — *"An unlisted alarm is an incident until proven
otherwise"* — the document as written instructs the operator to treat the majority of the real
daily WARNING stream as an incident.** That inverts its purpose, and it is a **docs-only** fix.

✅ **CLOSED SAME NIGHT:** `expected_alarms.md` §6 added (docs-only, this batch). ⛔ The three
wrong prescriptions in §2.4 are **CODE** and remain **RECORDED, NOT FIXED**.

---

# §3 — LEDGER #5: BROKER-TRUTH CAPITAL ESCALATION

Registers against **§B.1 row #5** (IA-P6-01 · IA-P6-02).

## 3.1 The path, traced end to end

`fund_manager.sync_from_broker` (FM9) publishes `CapitalDriftDetected` when `abs(delta) > 1.0`
(`capital/fund_manager.py:1438-1445`) → `CapitalDriftHandler.on_drift` → tier by absolute rupees
→ `soft_kill` / `hard_kill`.

Thresholds (`config/system_config.yaml:624-628`): log-only **₹250** · soft **₹1,000** · hard
**₹2,500** · escalate after **3** consecutive log-only cycles.

✅ **The handler is fully wired and correct.** Constructed with a **real** kill_switch at
`main.py:3364-3369` and subscribed at `:3369`. Tiers are clean, DH4's counter is source-scoped,
DH6's swallow is deliberate and documented. **There is nothing broken inside `drift_handler.py`.**

## 3.2 ⛔⛔ THE ROW'S MECHANISM IS A **MISDIAGNOSIS** — same shape as #6

**§B.1 row #5 reads:** *"The kill ladder is **structurally deaf** to real cash divergence."*

**The ladder is not deaf. It is never spoken to.**

`sync_from_broker` has **exactly one production call site** — `main.py:1000`. *(Width: 41 repo-wide
occurrences — the `def`, its FM9 docstring, comments in `fund_manager`/`drift_handler`/
`config_loader`, and 28 in tests. One call.)* Its docstring states what it is actually for:

> **`main.py:966-971`** — *"FIX-164: Re-sync capital from broker at 09:15 IST (market open). Fixes
> the case where the system starts pre-market with stale Rs 0 margins and **the user deposits
> funds after startup but before 09:15**."*

⭐ **It is a DEPOSIT-CATCHER.** It is scheduled at 09:15 precisely because that is *after* a
possible manual deposit and *before* the market opens — i.e. **at the one moment in the day when a
trading-induced divergence cannot yet exist.** The 08:15 boot has already seeded `_total` from
`broker.net`; the first entry cannot occur before 10:00. The drift publish is a side-effect bolted
onto a top-up sync, and **the mechanism the ledger assigns to this row was never built for that
job.**

⚠️ **A second narrowing, at `main.py:989-991`:** `if wait_sec <= 0: "started after 09:15, skipping
re-sync"` — on any day the service starts after 09:15 (warm restart, late boot), **FM9 does not run
at all that day.**

## 3.3 The measurement — 33 days, zero events, **by construction not by luck**

`fm_ledger`, all retained history:

| SYNC rows | rows exceeding the `abs(delta) > 1.0` publish gate | max abs delta |
|---|---|---|
| **33** | **0** | **0.00** |

Every row is identical to the paisa — `9910.40 → 9910.40` (03-Aug), `9360.00 → 9360.00` (31-Jul),
`9359.80 → 9359.80` (30-Jul), and so on for 33 consecutive syncs.

⇒ **The only escalating broker-truth publisher has emitted ZERO events in the system's life.**

## 3.4 Where the −₹637.6 actually went — the row's own evidence, relocated

The ledger cites *"−₹637.6 crossing 3 sessions silently."* Measured, that money moved here:

`fm_ledger` INIT rows: **29-Jul `9,997.40` → 30-Jul `9,359.80`.**

It arrived on the **08:15 boot seed**, which is an `INIT` — **not a drift event, with no
comparison of any kind.** The 09:15 SYNC that same day compared `9,359.80` to `9,359.80` and
published nothing, correctly, because by then the seed had already absorbed it.

✅ **Sweep confirms the gap** (width: `capital/`, `core/`, `main.py`, `orders/`, `scripts/`,
`reports/`): **no day-over-day seed check exists anywhere.** Every `INIT` consumer reads a single
day — `WHERE date = ?` at `state_store.py:2610`, `preflight/checks/engine.py:120`,
`daily_trade_review.py:1031` and `:2158`. **Nothing in the system has ever compared today's seed to
yesterday's.**

## 3.5 Has it ever fired? — **no rung, ever** — and ⛔ **DH1 is NOT a defect**

The tier function has run on exactly **two** production events, both on **06-Jul**:

```json
{"logger":"drift_handler","msg":"drift event from non-escalating source",
 "tier":"HARD","delta":10000.0,"expected":0.0,"actual":10000.0,
 "source":"order_reconciler"}
```

Both computed **`tier: HARD`**. Both were logged at **INFO** and dropped, because
`order_reconciler` is not in `_ESCALATING_SOURCES` (DH1, `drift_handler.py:66-70, 149-162`).

⭐⭐ **Read this the right way round: DH1 is the guard that stopped two false HARD_KILLs on a
₹10,000 phantom** (`expected = 0.0` because the FundManager was not yet seeded — the startup
artifact IA-P6-01 already identified). **DH1 working is the reason a spurious hard-kill did not
fire on 06-Jul.**

⛔ **BINDING ON ANY FUTURE #5 WORK: a redesign that loosens DH1 to let reconciler-sourced events
escalate must carry this measurement**, because the only two events that source has ever produced
would both have hard-killed the system incorrectly.

Supporting census: `reconciliation_log` `CAPITAL_DRIFT` = **4,183 rows, tier `UNRECOVERABLE`,
15-Jun → 06-Jul, and silent since** — G3 itself has produced nothing for four weeks.

**⇒ Complete firing history: 0 events from the source the ladder trusts; 2 from the source it
distrusts, both wrong. No rung has ever fired.**

## 3.6 ⇒ RE-SCOPE #5 BEFORE ANY IMPLEMENTATION

The ladder is **family-β structural starvation** (IA-XARCH-01): built, wired, correct, unfed.
Building against the current row's framing would produce work on a component that is not broken.

**#5's real target is the ABSENT DAY-OVER-DAY SEED CHECK (IA-P6-02)** — the cash sibling of the
30-Jul B2 inventory gap. That is a different mechanism in a different place, and it is where the
−₹637.6 was actually lost.

⛔ **Not designed here.** Step 1 measures; the re-scoped item needs its own Step 1.

---

# §4 🔴 THE DECISION THIS RECORD RAISES — OWED TO RAMA, BEFORE WEDNESDAY

**Two alerts arrive together at 15:15 every day, and both bodies become false the moment delivery
is enabled — i.e. on the flip.**

| alert | body | why it goes false |
|---|---|---|
| `CIRCUIT BREAKER — Force Close` (`main.py:703`) | *"EOD squareoff will close **all** positions at 15:17."* | **EOD6 does not touch CNC** |
| `SOFT KILL — scheduled` (`capital/kill_switch.py:609-615`) | *"Open positions: managed to SL/TGT/**EOD**"* | a spared delivery leg is carried, not squared |

Verified at source: `orders/eod_squareoff.py:23 / :34 / :1073`.

**They are pure string literals** — no logic, no new code path, no import change, provable by diff.

⭐ **RECOMMENDATION (mine, ⛔ NOT a decision): let the string fix ride the flip push.** It costs the
flip essentially zero variable, and the alternative is running **flip day itself** — the day the
change first matters — with two alerts telling the operator that positions which deliberately
survive will be closed. That is exactly the truth-telling failure this campaign exists to remove,
arriving on the worst possible day.

⛔ **Rama's call.** If ruled against, the two are recorded in `expected_alarms.md` as
**KNOWN-FALSE-AFTER-FLIP** so the operator is warned by the doc instead.
**Either way it must not be silent** — an entry now stands in `expected_alarms.md` §6 marked
`⏳ DECISION PENDING`, correct under both outcomes, to be resolved when the ruling lands.

---

# §5 WHAT THIS BATCH DID **NOT** DO

- ⛔ **No `.py` changed.** No fix applied for #3a, #9 or #5. All three remain `<MEASURED — NOT BUILT>`.
- ⛔ **No new register rows.** **231 stands.** #3a stays inside row #3; the alert findings stay
  inside row #9; the seed-check re-scope stays inside row #5.
- ⛔ **No backfill.** R-4's fix-forward ruling is reinforced by §1.6, not overturned.
- ⛔ **Nothing decided about DH1, the CHECK6 route, #3b, or the `--cleanup-*` flags.**
- ⛔ **The three wrong prescriptions in §2.4 are NOT fixed** — they are code, and code committed
  tonight first executes on flip morning.
- ✅ **Docs-only changes this batch:** this record, and `expected_alarms.md` §6.

**Related:** `docs/audit/ledger3_design_registration_03aug2026.md` (the #3a/#3b split, R-3, R-4) ·
`docs/audit/ledger6_eod_verify_measurement_03aug2026.md` (the #6 misdiagnosis this record's §3
mirrors) · `docs/audit/integrity_audit_2026.md` (IA-P5-01/-02, IA-P6-01/-02, IA-P9-01/-02) ·
`docs/expected_alarms.md` · `docs/report_data_contract.md:103`.
