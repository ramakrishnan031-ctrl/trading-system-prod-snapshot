# RULINGS 1 & 2 — RECORDED, AND THE VERIFICATION THEY COMMISSIONED

**07-Aug-2026 (Friday) · Opus 5 · ⛔ READ-ONLY MEASUREMENT + DOCUMENTATION. NO CODE. NO SCHEMA. NO GATE.**

> ## 📌 PROVENANCE OF EVERY CODE CITE
> Measured at repo **`612a06b`**. Every commit from `348c226` to that point is **docs-only** —
> `git diff --name-only 348c226..HEAD -- '*.py' '*.yaml' '*.sql'` returns **empty** — so every file
> cited below is **byte-identical to `348c226`**. `capital/risk_engine.py`, `core/state_store.py`,
> `orders/cnc_gtt_monitor.py` and `allocation/portfolio_allocator.py` are additionally unchanged
> since `0197923` (the SHA the prior documents pinned); **`signals/signal_processor.py` is NOT** —
> it changed between `0197923` and here, so its line numbers were **re-measured**, not inherited.
> **(M3: line numbers hold only at their measured SHA.)**
>
> **Production reads:** `data_store/trading_system.db` on the VM, `mode=ro` URI, ~16:0x–16:2x IST.
> **Config reads:** `config/system_config.yaml`, PC and VM compared on the keys that matter — identical.

---

# §0 · OBJECTIVE

Two things, and they are different in kind:

**(A) RECORD** the two governance rulings Rama took on 07-Aug-2026 — decision-ledger rows **1**
(control-inventory authority) and **2** (shared symbol namespace, tightened). Documentation only.

**(B) VERIFY** seven hypotheses (H1–H7) about whether the system can actually answer the question
Ruling 2 rests on: *"does an OPEN position currently exist for this symbol?"* Read-only.

⛔ **NO IMPLEMENTATION IS AUTHORISED BY ANY OF THIS.** No gate changed, no predicate rewritten, no
config key flipped, no schema, no F6 fix, F6's retirement checklist untouched. Gate 2 — Rama's
authorisation in his own words for a build — has not been given.

---

# §1 · DOCUMENTATION CHANGES MADE

| # | commit | what |
|---|---|---|
| 1 | `98e7406` | **Ruling 1 recorded.** Authority header on `ops_dashboard/docs/G2a_capacity_inventory.md`; one-line superseded pointer at `docs/audit/audit_05jul2026.md:527`. The 27 rows **not** edited. Location **not** decided — the file stays put. |
| 2 | `612a06b` | **The 113-key review merged in.** `30 + 14 = 44` rows; five collision classes; the review demoted in place to evidence. |
| 3 | `520d363` | **Ruling 2 recorded verbatim** in `MASTER_PENDING_01-Aug-2026.md` §R.2, with the three consequences scored against measurement; cross-referenced at `delivery_config_surface` §7.1, `module_17_risk_engine.txt`, G2a row 36 + §H-5; dated note only in the F6 design doc. |
| 4 | *(this file)* | the verification. |
| 5 | — | `PATHS.md` + `docs/SYSTEM_MAP.md` refreshed. |

**⛔ ONE DOCUMENTED SITE WAS DELIBERATELY NOT CROSS-REFERENCED.** `docs/report_data_contract.md:189`
names `REJECTED_DUPLICATE_SYMBOL`, but it documents *how the reject code is bucketed in a report*,
not the protection rule. A ruling cross-ref there would be noise in a data contract. **Stated, not
silently skipped.** The 27 other files matching the grep are **dated audit reports** — history, and
the campaign rule against editing dated history applies.

---

# §2 · H1–H7 — VERDICTS

**Buckets:** **(a) CONFIRMED DEFECT · (b) CONFIRMED DESIGN · (c) ASSUMPTION DISPROVED ·
(d) CANNOT DETERMINE.** **Evidence classes:** **(P)** it happened · **(S)** the code says so ·
**(I)** it follows from the code but has never been observed.

## H1 — "exactly THREE product-blind gates enforcing one-trade-per-symbol"
### 🏷️ **(b) CONFIRMED DESIGN — with a correction: the true count is 3, 4 or 5 depending on the definition, and the card's definition yields 3.** **(S)** + **(P)**

**Width of the search:** every call site of `has_active_position`, `get_active_position_direction`
and `count_executed_trades_today_for_symbol_direction` across the whole repo (**each has exactly ONE
production caller**), plus a repo-wide grep for `DUPLICATE_SYMBOL|CONTRARY_POSITION|SYMBOL_DIRECTION_DAILY_LIMIT|one_trade_per_symbol`
across all `*.py`.

| # | gate | file:line **@`612a06b`** | reject code emitted | config key | **the EXACT predicate, quoted** |
|---|---|---|---|---|---|
| **1** | symbol + **direction**, per day | `signals/signal_processor.py:706-720` | `SYMBOL_DIRECTION_DAILY_LIMIT` <sub>(raised as `_PipelineReject`)</sub> | **`risk.one_trade_per_symbol_direction_per_day` = `true`** (`:224`) | `n = count_executed_trades_today_for_symbol_direction(symbol, direction, today)` → `if n >= 1: raise` — SQL at `core/state_store.py:723-730`: `WHERE symbol = ? AND direction = ? AND SUBSTR(created_at,1,10) = ? AND status IN (PENDING_FILL, OPEN, PARTIAL, EXITING, CLOSED, CLOSED_MANUAL)` |
| **2** | symbol, **opposite** direction | `capital/risk_engine.py:665-686` <sub>(check **9**)</sub> | `CONTRARY_POSITION` | ⛔ **none — unconditional** | `if active_direction is not None:` … `is_contrary = (incoming=="LONG" and active=="SHORT") or (incoming=="SHORT" and active=="LONG")`. Fed at `:289` by `get_active_position_direction` — `core/state_store.py:866-874`: `SELECT direction FROM trades WHERE symbol = ? AND status IN ('PENDING_FILL','OPEN','PARTIAL') LIMIT 1` |
| **3** | symbol, **any** direction | `capital/risk_engine.py:688-694` <sub>(check **10**)</sub> | `DUPLICATE_SYMBOL` | ⛔ **none — unconditional** | `if has_dup:` … Fed at `:286` by `has_active_position` — `core/state_store.py:845-852`: `SELECT COUNT(*) FROM trades WHERE symbol = ? AND status IN ('PENDING_FILL','OPEN','PARTIAL')` |

**The card's count of three is right.** Two further sites exist and are named because a later reader
who greps will find them:

| # | site | why it is **not** one of the three | status |
|---|---|---|---|
| **4** | `allocation/portfolio_allocator.py:182-183` — `if c.symbol in admitted_symbols: return "DUPLICATE_SYMBOL"` | it dedupes **within one admission batch**, not against the book | 🔴 **INERT** — `allocator_mode: 'shadow'`; shadow **never reserves or places** ⇒ live admission byte-identical. ⚠️ **It emits the identical label `DUPLICATE_SYMBOL`** — a classification-leakage hazard for anyone counting by string |
| **5** | `signals/entry_throttle.py:97-107` — `per_symbol_cooldown_sec = 300` | it is a **spacing** rule, not a **uniqueness** rule | ✅ **LIVE** — and see §4 OPEN-2: *"immediately becomes eligible again"* and a 5-minute cooldown are in direct tension |

> ⭐ **THE STRUCTURAL FINDING H1 EXISTS TO SURFACE: gates 2 and 3 ARE ONE PREDICATE.**
> `has_active_position(symbol)` and `get_active_position_direction(symbol) is not None` are the same
> SQL condition — same table, same status triple — read twice. Gate 2 partitions it by direction and
> runs first; gate 3 catches the remainder. **(P) `CONTRARY_POSITION` has fired ZERO times in
> 109,254 signals** *(width: the complete `signals.status` distribution, every status listed)* —
> because gate 3 is unconditional, so any active position rejects regardless of direction, and gate 2
> can only ever claim the opposite-direction slice of that.

## H2 — "one is date-filtered; `DUPLICATE_SYMBOL` is not, so a carried delivery position blocks intraday indefinitely"
### 🏷️ **(b) CONFIRMED DESIGN, per gate — and (a) CONFIRMED DEFECT in its consequence.** **(S)** + **(P)**

| gate | date-filtered? | evidence | expiry |
|---|---|---|---|
| 1 `SYMBOL_DIRECTION_DAILY_LIMIT` | ✅ **YES** | `AND SUBSTR(created_at,1,10) = ?` with `today = now_ist().date().isoformat()` (`signal_processor.py:712`) | **midnight** |
| 2 `CONTRARY_POSITION` | ⛔ **NO** | the SQL has no date term | 🔴 **never** — only the trade closing |
| 3 `DUPLICATE_SYMBOL` | ⛔ **NO** | the SQL has no date term | 🔴 **never** — only the trade closing |

**The consequence is CONFIRMED and it is LIVE right now.** **(P)** two trades sit `OPEN` with
`exit_time IS NULL`: `DIFFNKG` (`trd_010f8e21…`, since 06-Aug 10:02:16) and `MANINFRA`
(`trd_9e709c50…`, since 07-Aug 10:05:23). Both symbols are therefore blocked to **every** pipeline,
with **no expiry mechanism other than the trade closing** — which is exactly what F6 prevents.

> ### ⭐⭐ **AND THE MASKING IS NOW MEASURED, NOT ONLY REASONED**
> `delivery_config_surface` §7.2 argued from source that gate 1 fires first and masks gate 2 on day 1.
> **(P) The production record shows the switchover as a clean edge:**
> `REJECTED_DUPLICATE_SYMBOL` — **107 all-time, LAST fired 31-Jul-2026**.
> `REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT` — **65 all-time, FIRST fired 03-Aug-2026**.
> 31-Jul (Fri) → 03-Aug (Mon) are consecutive trading days. ⛔ **Anyone measuring the symbol block
> under `REJECTED_DUPLICATE_SYMBOL` after 03-Aug reads ZERO and concludes it stopped happening.**
> ⚠️ **NOT ESTABLISHED, and not chased (G3):** the config commit that set the key `true` is
> `300a247`, dated **27-Jul** — four trading days before the first rejection. Whether the gap is a
> deploy lag or simply no qualifying signal is **not determined here**.

## H3 — "NONE of the three evaluates *is a position open right now*"
### 🏷️ **(c) ASSUMPTION DISPROVED — and this is the report's most consequential correction.** **(S)**

| gate | what it actually evaluates | is that "open right now"? |
|---|---|---|
| 1 | *"has this symbol+direction been traded **today**"* — the status set includes **`CLOSED`** and **`CLOSED_MANUAL`** | ⛔ **NO.** This is historical ownership, and Rama's ruling names that exact thing as what it is *not* |
| 2 | *"is there an active position in the **opposite** direction"* — `PENDING_FILL/OPEN/PARTIAL` | ✅ **YES**, restricted to one direction |
| 3 | *"is there an active position"* — `PENDING_FILL/OPEN/PARTIAL`, any direction, any product | ✅ **YES** |

> 🔴🔴 **GATES 2 AND 3 ALREADY IMPLEMENT RULING 2.** Product-blind ✅ · never date-scoped ✅ ·
> release the instant the trade leaves `PENDING_FILL/OPEN/PARTIAL` ✅ · *"evaluated from the current
> account state"* ✅ (to the limit of what `trades` knows — see H4/H5).
> **Gate 1 is the only one the ruling contradicts**, and it is the only one with a config key.
> ⇒ **The loosening half of Ruling 2 is `risk.one_trade_per_symbol_direction_per_day: false`.**
> **(S)** the code's own docstring: *"DEFAULT OFF. When … is false this returns before touching the
> store, so the pre-27-Jul path is **byte-identical**."* (`signal_processor.py:678-679`, and the
> guard at `:706-707` is a bare early `return`.)
> ⛔ **This is a measurement, NOT a recommendation to flip it.** See §4 OPEN-1 and consequence (ii).

## H4 — what source of truth WOULD answer *"is a position open for this symbol right now"*
### 🏷️ **(b) CONFIRMED DESIGN — the enumeration is complete and the answer is: nothing reachable covers both products.** **(S)**

**The gate's call sites, and what they can reach.** `RiskEngine.__init__` (`capital/risk_engine.py:139-190`)
takes `fund_manager`, `state_store`, `kill_switch`, `sector_lookup_fn`, `logger` and scalars —
**no broker adapter, no parameter of any adapter type.** `signals/signal_processor.py:26` states the
rule in the file header: **"SP14 — Layer 5; no direct broker import."**

| # | candidate source | reachable **at the gate**? | covers INTRADAY? | covers DELIVERY? | sign-safe? |
|---|---|---|---|---|---|
| 1 | broker `get_positions()` — `broker/zerodha_adapter.py:1182-1240` | 🔴 **NO** — no adapter on either gate object | ✅ | ⚠️ **T+0 only.** From T+1 the holding moves to `holdings()`; the CNC row that remains is the **SELL** | 🔴 **signed** — `qty=int(row["quantity"])` from Kite's `net`; paper mirrors it deliberately (`:1194-1200`) |
| 2 | broker `get_holdings()` — `:928` | 🔴 **NO** | ⛔ no | ✅ **T+1 onward only** | positive by nature |
| 3 | `trades` via `has_active_position` | ✅ **YES — the only one that is** | ✅ | ✅ | n/a — status-based, no qty |
| 4 | `orders` table | ✅ yes | ✅ | ✅ | it carries **`product`**, but no position state; ⛔ and there is **no `trades.product` column** — product is reachable only via `LEFT JOIN orders … leg='ENTRY'` |
| 5 | `fund_manager` reservation ledger (`get_live_reservations()`, `:1494`; `_Reservation.symbol` exists) | ✅ yes | ✅ in-flight only | ✅ in-flight only | n/a |
| 6 | `cnc_gtt_monitor`'s `held` map (`orders/cnc_gtt_monitor.py:451-465`) | 🔴 **NO** — an exit monitor; **no gate calls it** | ⛔ no | ✅ | 🔴 **NO — `abs(int(qty))` at `:464`. This is F6** |
| 7 | `gtt_state` ACTIVE rows | ✅ yes | ⛔ no | ✅ proxy only | n/a |

> ⭐ **THE ANSWER:** only **candidate 3** is reachable at the gate **and** covers both products —
> and it is a **DB assertion about our own bookkeeping**, not a statement about the account.
> **Candidates 1+2 TOGETHER are the only broker-truth answer, and neither is reachable.**
> ⚠️ **Even combined they are not sufficient without a sign rule** — see H5b.

## H5a — is `cnc_gtt_monitor`'s held computation (the `abs()` defect, F6) ON the path that would answer H4?
### 🏷️ **(c) ASSUMPTION DISPROVED — LEXICALLY. And (a) CONFIRMED DEFECT — CAUSALLY.** **(S)**

**Lexically: NO.** `_gather` and `_handle_row` have no caller in `risk_engine` or `signal_processor`;
the grep for `has_active_position` / `get_active_position_direction` returns exactly one production
caller each, both inside `risk_engine`. The gate never evaluates `:464`.

**Causally: YES, and it is decisive.** The gate reads `trades.status`. What writes `CLOSED` to a
delivery trade is `_finalize_gtt_exit` → `mark_trade_closed_gtt`, and **`held == 0` is the sole door
to it** — reached at `cnc_gtt_monitor.py:487` (GTT triggered), `:501-510` (holding flat), and nowhere
else. `:464` makes `held` unable to reach 0 after a completed SELL. ⇒

> ## 🔒 **F6 IS A PREREQUISITE OF RULING 2, NOT A BENEFICIARY OF IT.**
> The `abs()` is not *in* the predicate; it is the reason the predicate's **data source is wrong**.
> A gate can be perfectly implemented on `trades` and still block a genuinely free symbol forever.
> **(P) That is not hypothetical — it happened.** ATULAUTO was sold by its own GTT at ~09:31:56 on
> 06-Aug; the trade row stayed `OPEN`; the symbol stayed blocked; it cleared only at the 07-Aug
> 08:15 boot. **(P)** `DIFFNKG` and `MANINFRA` are in that state **right now**.

## H5b — does ANY candidate in H4 mishandle the SIGN of quantity?
### 🏷️ **(a) CONFIRMED DEFECT — it is a CLASS, at six production sites.** **(S)**

**The source is signed.** `zerodha_adapter.py:1233` `qty=int(row.get("quantity", 0))` from Kite's
`net` book; `:1194-1200` records that paper returns the **signed** net *on purpose*, "NOT `abs()`",
so reverse-aware consumers pick the right flatten direction. **A completed CNC SELL of a T+1 holding
survives the `!= 0` filter as a NEGATIVE row.**

**Every production site that applies `abs()` to a position quantity** *(width: repo-wide grep for
`abs(int(` / `abs(float(` / `abs(qty` / `abs(...quantity` across all `*.py`, excluding `tests/`)*:

| site | context | would a completed SELL read as a HOLD? |
|---|---|---|
| `orders/cnc_gtt_monitor.py:464` | the F6 line | 🔴 **YES — measured** |
| `orders/order_reconciler.py:2968` | CHECK9 FACET-2 oversell guard, `live_held = abs(int(_p.qty))` | 🔴 **YES** — and it takes the **first** matching symbol row and `break`s, so it is also product-blind |
| `orders/order_reconciler.py:2185` · `:2291` · `:3120` | FACET-1 / FACET-2 / CHECK7 | 🔴 yes, same shape |
| `orders/eod_squareoff.py:1079` · `:1487` · `:1525` · `:1613` | flatten sweeps | 🔴 yes, same shape |
| `capital/kill_switch.py:1281` | `if abs(int(getattr(p,"qty",0) or 0)) > 0` | 🔴 yes |
| `orders/structure_exit_manager.py:631` | `abs(int(getattr(p,"qty",0) or 0))` | 🔴 yes |

⚠️ **Most of these are on INTRADAY paths where the net genuinely reaches 0 and the row is filtered
out, so the `abs()` is harmless today.** ⛔ That is exactly what makes it a class rather than a bug:
**the same expression is correct on one path and wrong on the other, and nothing at the call site
distinguishes them.** ⭐ **A broker-truth open-position predicate written in the house style would
reproduce F6 at a new site on its first day.**

### 🎯 H5 — CROSS-CHECK AGAINST F6's SEVEN COSTS

Costs 1–5 are tabulated at `f6_delivery_exit_predicate_design_06aug2026.md` §15; cost 6 at §15.1;
cost 7 (the measurement layer publishing numbers it cannot vouch for) at `MASTER_PENDING` candidate 4.

| F6 cost | does Ruling 2 retire it? |
|---|---|
| 1 · ₹587.42 re-reserved every boot — **capital** | ⛔ **no** — untouched |
| 2 · 1 of 3 delivery slots held — **concurrency** | ⛔ **no** — untouched |
| **3 · `DUPLICATE_SYMBOL` blocks the symbol indefinitely** | 🔴 **NO — and this is the cost the retirement claim was about.** Ruling 2 **ratifies** the block: *"if any open position already exists… every new entry is rejected."* A phantom `OPEN` row **is** such a position under a `trades`-based reading. ⭐ **It would retire only under a BROKER-TRUTH reading — which H4 shows is not reachable at the gate, and H5b shows would need a sign rule the codebase does not have** |
| 4 · three live SELL GTTs on a flat holding | ⛔ no |
| 5 · a fabricated exit price, permanently in the data | ⛔ no |
| 6 · the nightly manual stop; a missed one costs a full trading day | ⛔ no |
| 7 · the day's P&L understated (−7.20 reported vs ≈−19.10 real) | ⛔ no |

> ## ⛔ **0 of 7 RETIRED. THE DEPENDENCY RUNS THE OTHER WAY, AND THE SEQUENCING REVERSES WITH IT.**
> ⭐ The claim made when option (b) was offered is **false**, and it was false in the direction that
> would have let Ruling 2 be built first.

## H6 — how much same-day re-entry does the tightened rule actually permit?
### 🏷️ **(a) CONFIRMED — measured, and it exceeds the daily cap on the first day it applies.** **(P)**

**Method.** Take every signal with `status='REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT'` (gate 1 is the
only gate whose predicate Ruling 2 contradicts — H3). For each, ask whether a position was open for
that symbol **at that instant**: a trade with `created_at <= t` and `(exit_time IS NULL OR
exit_time > t)`. If yes, Ruling 2 rejects too and nothing changes. If no, Ruling 2 **permits**.

```sql
-- the population
SELECT signal_id, symbol, received_at, substr(received_at,1,10) AS d
  FROM signals WHERE status='REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT' ORDER BY received_at;
-- per row, "was the symbol genuinely free at that instant?"
SELECT count(*) FROM trades
 WHERE symbol = ? AND created_at <= ?
   AND (exit_time IS NULL OR exit_time > ?)
   AND status IN ('PENDING_FILL','OPEN','PARTIAL','EXITING','CLOSED','CLOSED_MANUAL');
```

| measured | value |
|---|---|
| rejections in the population | **65** (03-Aug 10:12:15 → 07-Aug 14:53:15) |
| distinct days · distinct symbols | **5 days · 16 symbols** |
| **WOULD STILL REJECT** (a position was open) | **56** |
| 🔴 **WOULD NOW BE PERMITTED** | **9** |
| **on how many days** | **2** — 05-Aug (**3**) and 06-Aug (**6**) |
| **for how many symbols** | **4** — `ENGINERSIN`(1) · `SUDEEPPHRM`(2) *(05-Aug)* · `DECNGOLD`(3) · `MAYURUNIQ`(3) *(06-Aug)* |

**56 + 9 = 65 ✅.**

> ### 🔴 **AND THE NUMBER THAT MATTERS MOST IS NOT 9 — IT IS 11.**
> **(P)** 05-Aug executed **8** trades; +3 permitted ⇒ **up to 11**. 06-Aug executed **5**; +6 ⇒
> **up to 11**. `risk.max_daily_trades` = **10**.
> ⇒ **On BOTH days the loosening pushes the book past the daily cap.** The extra entries would not
> all have been taken — `max_daily_trades` would have rejected the overflow — but the constraint
> that catches them is a cap that **has not bound since 10-Jul** (H7). ⛔ **The loosening is not
> absorbed by headroom; it consumes all of it and reactivates a dormant cap.**

**⛔ THREE LIMITS ON THIS NUMBER, STATED RATHER THAN BURIED:**
1. **It is a LOWER bound, and F6 pushes it down.** **(P)** 7 of the 56 "would still reject" rows are
   blocked by a trade whose `exit_time IS NULL` — `DIFFNKG` and `MANINFRA`. If F6 were fixed, some
   of those symbols may have been genuinely flat, moving rows from 56 into the permitted set.
   ⭐ **The contamination runs in the same direction as the defect.**
2. **The population is 5 trading days.** Gate 1's reject code first appears 03-Aug (H2). ⛔ Nothing
   here says what a month looks like.
3. **9 rejected SIGNALS ≠ 9 additional TRADES.** The scanner re-fires the same symbol; the permitted
   set contains repeats (`SUDEEPPHRM` ×2, `DECNGOLD` ×3, `MAYURUNIQ` ×3). Upper bound **9**; the
   realistic figure is bounded below by **4** (distinct symbols).

## H7 — what caps bound entries today, and which BINDS first?
### 🏷️ **(c) ASSUMPTION DISPROVED — no DAILY cap has bound in ~20 trading days. The live bound is CONCURRENCY, and a same-day re-entry does not consume it.** **(P)**

| cap | key | value @07-Aug | enforcement site | **(P) rejections all-time** | last fired |
|---|---|---|---|---|---|
| score gate | `scoring_weights.min_pass_score` | 60 | `quality_scorer` | **61,117** `REJECTED_SCORE_*` (32 distinct statuses, of 109,254 signals) | daily |
| strategy control | — | — | `signal_processor` control gate | **21,430** | daily |
| **concentration (sizing)** | `position_sizing.max_concentration_pct` | **0.10** | `position_sizer.py` | **3,486** | daily |
| strategy circuit breaker | `strategy_circuit_breaker.*` | 2.0× / 12:00 / 10d | `strategy_governor` | **4,433** | daily |
| **entry throttle** | `min_gap` 20s · `burst` 3/60s · `per_symbol` 300s | — | `signals/entry_throttle.py` | **436** | daily — **earliest-firing control on 19 of 21 trading days** |
| **max open positions** | `risk.max_open_positions` | **5** | `risk_engine` check 4 | **500** | **07-Aug** ✅ still live |
| per-strategy concurrency | `strategies/<s>.max_concurrent_positions` | 2 (gap_fade 3) | `signal_processor` H-7 cap | **302** | **07-Aug** ✅ still live |
| **max daily trades** | `risk.max_daily_trades` | **10** | `risk_engine` check 5 | **5,146** | 🔴 **10-Jul — and never since** |
| bucket capital 70/30 | `capital.intraday_bucket_pct` / `positional_bucket_pct` | 0.70 / 0.30 | check 3 + `reserve()` | **24** `REJECTED_SIZING_CAPITAL` | 07-Aug |
| consecutive losses | `risk.max_consecutive_losses` | **4** *(both inventories said 5)* | check 6 | **3** | 03-Aug |
| **daily loss limit** | `risk.daily_loss_limit_pct` | **0.03** | check 7 + post-close breach | 🔴 **0 — no such reject status exists** | never |
| sector exposure | `risk.max_sector_exposure_pct` + `sector_cap_mode` | 0.40, **`observe`** | check 8 | **0** — observe mode | never |
| delivery caps | `risk.max_open_delivery_positions` / `max_daily_delivery_trades` | **3 / 5** | checks 4/5 positional branch | ⛔ **not separately countable** — the delivery branch emits the **same** check name | — |

> ## 🔴 **WHICH BINDS FIRST — TWO ANSWERS, AND THE CARD'S RATIONALE DEPENDS ON THE SECOND**
> **(1) By pipeline order / frequency:** the **entry throttle** fires earliest on **19 of 21** days
> (typically 10:00:2x, on the window-open burst) and `SIZING_CONCENTRATION` on the other 2.
> ⛔ **But a throttle DELAYS; it does not consume the day.**
> **(2) By what actually stops the day:** 🔴 **nothing does.** `max_daily_trades` last bound
> **10-Jul**; peak trades since is **9** (29-Jul) against a cap of **10**. The caps still firing —
> `max_open_positions` (500) and per-strategy concurrency (302) — are **CONCURRENCY** caps, and
> ⭐⭐ **a same-day re-entry after a complete exit does not consume a concurrency slot: the slot was
> released by the exit.** ⇒ **Ruling 2's loosening is bounded by `max_daily_trades = 10` alone, and
> H6 shows both affected days reaching 11.**
> ⚠️ **STATED NOT CHASED (G3):** **(P)** 17-Jun shows **12** executed trades against a cap of 10.
> Outside this card's scope; recorded so it is not later found and mistaken for new.

---

# §3 · P1–P7 SCORED

⭐ **Written by the card's author BEFORE any measurement existed. 5 HELD · 1 FAILED · 1 SPLIT.**

| # | prediction | score | why |
|---|---|---|---|
| **P1** | H1 = exactly 3 *(low confidence, "never measured")* | ✅ **HELD** | 3 uniqueness gates, confirmed at source with all call sites enumerated. ⚠️ The author's own low-confidence flag was warranted for a different reason than expected: the count is right, but **two further symbol-keyed sites exist** (inert allocator, live 300 s cooldown) and neither had ever been written down |
| **P2** | H2 CONFIRMED as stated | ✅ **HELD** | per gate, and the consequence is live on two symbols right now |
| **P3** | H3 CONFIRMED for **all** gates — none consults live openness | 🔴 **FAILED** | **Gates 2 and 3 DO** consult current openness (`PENDING_FILL/OPEN/PARTIAL`, no date term). ⭐⭐ **The most valuable failure in the set:** it converts Ruling 2 from *"build a new predicate"* into *"turn one date-scoped gate off"*, and it is the reason consequence (i) is half-refuted |
| **P4** | broker positions/holdings the only both-product candidate, **and not reachable** at the gate | ➗ **SPLIT — reachability HELD, sufficiency FAILED** | ✅ not reachable: `RiskEngine.__init__` has no adapter; `signal_processor.py:26` *"no direct broker import"*. 🔴 but **neither API alone covers both** — `positions()` is blind to delivery from T+1, `holdings()` blind to intraday; **and `trades` (candidate 3) DOES cover both**, which the prediction did not anticipate |
| **P5a** | REFUTED — `cnc_gtt_monitor` is an exit monitor, not on the gate path *(explicitly the corrected, weaker form of what was said in chat)* | ✅ **HELD** | lexically refuted, exactly as predicted. ⭐ **The correction was right to make**: the chat statement overreached, and the weaker prediction is the one that survived |
| **P5b** | CONFIRMED — sign handling is a class, not a line | ✅ **HELD** | **six** production sites apply `abs()` to a signed broker quantity |
| **P6** | count > 0 *(no figure predicted)* | ✅ **HELD** | **9**, on 2 days, across 4 symbols |
| **P7** | at least max-positions + daily-loss exist; **max-positions binds** | ➗ **HELD on the letter, and the letter understates it** | both exist ✅ and max-positions **is** the binding cap among those still firing ✅ — but **daily loss has never fired at all**, and **the daily-trade cap has not fired since 10-Jul**, so "binding" describes a much emptier field than the prediction implies |

> ⭐⭐ **THE METHODOLOGICAL RESULT HOLDS AGAIN, AND P3 IS THE EVIDENCE.** Every prediction was written
> down first; the one that failed, failed *informatively* and changed the sequencing. ⛔ **A predicted
> outcome that merely gets confirmed teaches nothing about the predictor.**

---

# §4 · §1.4 — **DID THE AUTHORITY RULE BIND?**

## ✅ **YES — it bound twice, and it then required an AMENDMENT. Both, not either.**

**IT BOUND (1) — it stopped the obvious action.** The natural way to "merge 113 keys into a 30-row
inventory" is to write a fresh table containing both key sets. The authority rule forbids creating a
parallel inventory, so the merge had to be **G2a absorbing the review's axes**, with the review
demoted **in place**. ⛔ Without the rule I would have produced a new file, and it would have been a
third inventory wearing a merge's name.

**IT BOUND (2) — it stopped a fabrication.** §1.3 asked for the row arithmetic. The honest arithmetic
is `30 + 14 = 44`, **not** anything ending in 113. The review's **113 is a scope count and was never a
row set** — the document nowhere enumerates 113 rows, and its own bucket total is on record as
reconciling by accident. Producing 113 rows would have required re-deriving the key list from YAML:
**a new measurement wearing a merge's clothes.** The authority rule made that visible as a violation
rather than as diligence.

## ⚠️ **AND IT NEEDED AMENDING — the ruling as taken is necessary and insufficient**

The ruling says the documents are *"keyed on the dotted config key, and the dotted key is the join
identifier."* **That is true, and it did not prevent a single one of the five collisions.** The join
worked perfectly. What failed is that **the two documents mean different things by a *row***:

- G2a's row unit is *a configured limit with a capacity semantic* (a limit ↔ a live counter).
- The review's row unit is *a key or family in the delivery-isolation decision*.
- ⇒ They disagree about whether `webhook.*`, `clock.*`, `signal_queue.*` and `live_feed.*` belong
  **at all** (§H-4), and about how many leaf keys a family has in **10 of 11** shared families (§H-3).

**AMENDMENT ADOPTED** (in the authority header, same day): an authority also declares its **ROW
UNIT**, its **INCLUSION RULE**, and its **VALUE PROVENANCE**. 🏷️ **Parent (G7): it narrows Ruling 1
itself** — the join identifier stays, and two clauses are added without which the ruling permits a
merge that silently changes what the document is.

> ⭐⭐ **AND THE RULE'S FIRST APPLICATION IS WHAT CAUGHT IT — not a review of the rule.**
> That is now the **fourth** governance rule in this campaign found wanting by being *run* rather
> than by being *read*, and the third to be repaired by amendment rather than replacement.
> **(G7.1: AMENDED beats binding unchanged.)**

---

# §5 · COLLISIONS FOUND IN THE 113-KEY MERGE

Full tables with both values and both sources: `ops_dashboard/docs/G2a_capacity_inventory.md` §H.
**Five classes. None resolved silently.**

| # | class | the collision | resolution |
|---|---|---|---|
| **H-1** | **VALUE** | `risk.max_consecutive_losses`: G2a row 5 says **5** · `audit_05jul2026.md:537` says **5** · **`system_config.yaml:226` says 4** | measured value governs; row 5 corrected in place with its provenance |
| **H-2** | 🔴 **SCOPE — a false premise in both inventories** | the delivery caps are tagged **"INERT (`force_intraday_only=true`)"** in both. **Measured: `force_intraday_only: false` (`:89`), `delivery_enabled: true` (`:102`), `trade_type: BOTH` (`:96`)** — and two CNC positions are open | the tag is **superseded** in the authority. ⛔ An operator reading "INERT" about a live delivery cap is the exact failure the authority exists to stop |
| **H-3** | **FAMILY SIZE** | `alerts.*` — G2a: absent · review: **9** · **measured 31**. `entry_gate.*` — G2a: 4 · review: **9** · **measured 17**. Also `smart_tgt` 3/5/**5** · `strategy_circuit_breaker` 3/4/**4** · `circuit_breaker` 3/2/**3** · `clock` 3/—/**6** · `order_reconciler` 3/3/**7** · `signal_queue` 2/—/**4** · `webhook` 2/—/**7** · `live_feed` 1/—/**3** | ⭐⭐ **exactly ONE of eleven — `drift_handler.*` (4) — matches.** Same defect shape as the 113 accident: **a family counted as if it were a key.** ⇒ the ROW-UNIT clause |
| **H-4** | **INCLUSION RULE** | 4 of G2a's 30 rows sit inside the review's **~90-key excluded infra families** | the authority's inclusion rule governs; the rows stay; the review's exclusion is recorded as *that review's scope*, never a deletion |
| **H-5** | **AN UNRECORDED SITE** | `allocation/portfolio_allocator.py:182-183` emits the label `DUPLICATE_SYMBOL` for a batch rule; it is in **no** inventory, 27-row or 30-row | added as row **41**; cross-referenced to Ruling 2 |

**ARITHMETIC, operands shown:** `BEFORE 30` (§A 8 + §B 2 + §C 7 + §D 5 + §E 8) `+ ADDED 14`
(§G 31–44, each checked against all 30 by dotted prefix) `= AFTER 44`. Re-added independently by
section: `8+2+7+5+8+14 = 44` ✅, and the highest row number is **44** ✅ — two checks that could have
disagreed. **Leaf-key coverage measured, not hand-counted: 169 of 301**; the 132 uncovered decompose
`98` signal-generation + `34` infra `= 132` ✅, **independently reproducing the review's own "98
excluded" figure by a different route.**

⚠️ **NOT RESOLVED:** the review's **301 vs 302** corpus ambiguity. Today's read reproduces **301 leaf
keys / 42 sections by YAML parse** — the *same method* as the review, so it corroborates one arm and
settles nothing. The 302 came from an indentation walk, which was **not re-run**.

---

# §6 · OPEN ITEMS — ⛔ owed to Rama, nothing started

| # | item | why it is open |
|---|---|---|
| **OPEN-1** | 🔴 **Ruling 2's loosening half is a CONFIG FLIP, and it is NOT authorised.** `risk.one_trade_per_symbol_direction_per_day: true → false` is the whole of it (H3). ⛔ **Not flipped, not staged, not proposed as a next step.** It re-admits the SENCO trade class by design, and **H6 shows both affected days reaching 11 against a cap of 10** | Gate 2 has not been given. **And the sequencing now says F6 first regardless** (H5) |
| **OPEN-2** | ⚠️ **`per_symbol_cooldown_sec: 300` contradicts the ruling's wording.** Rama's text says the symbol *"immediately becomes eligible again"*; the throttle blocks re-entry for 5 minutes. It is a **spacing** control, not an ownership one, so it may be intended to survive — **but that is a decision, not an inference** | needs one sentence from Rama: does *"immediately"* govern throttles too? |
| **OPEN-3** | 🔒 **Sequencing REVERSED: F6 must land before Ruling 2 is implemented.** Not before it is *recorded* — that is done | H5: 0 of 7 F6 costs retired; F6 is what makes the predicate's data source wrong |
| **OPEN-4** | **Gates 2 and 3 are one predicate read twice, and gate 3 makes gate 2 unreachable** — `CONTRARY_POSITION` has fired **0** times in 109,254 signals. Whether it is retired, or kept as a documented no-op, is a decision | ⛔ **not proposed.** Recorded because a gate that cannot fire is indistinguishable from one that has not yet needed to |
| **OPEN-5** | **The `abs()`-on-signed-quantity class (6 sites, H5b) has no owner.** F6 fixes one line of it | belongs with the F6 design, not with this card |
| **OPEN-6** | **The 27-row and 30-row inventories are DIFFERENT PARTITIONS** — one keyed on *control*, one on *config key*. Recorded in G2a's footer so `27 → 30` is never read as `+3` | ⛔ no reconciliation attempted; the 27 rows are frozen history |
| **OPEN-7** | ⚠️ **A gate-1 deploy-lag question, stated not chased (G3):** config `true` committed **27-Jul** (`300a247`); first rejection **03-Aug**; four trading days between | not determined here |

---

# §7 · WHAT THIS REPORT DOES NOT ESTABLISH

- ⛔ **Nothing about a month of behaviour.** H6's population is **5 trading days**, H7's is 21.
- ⛔ **Nothing about paper/live parity of the gates themselves.** Both gate call sites are DB-only and
  therefore mode-agnostic *(that is (I) — it follows from `no direct broker import`, and no paper
  drill of these gates was run)*. ⚠️ **But the parity hazard is real one layer down:** any
  broker-truth implementation inherits **PAPER NETS BY SYMBOL / LIVE KITE NETS PER (SYMBOL, PRODUCT)**,
  and a paper drill of it would be **vacuously green**.
- ⛔ **No claim that flipping the config key is safe.** H6 measures what it permits; it says nothing
  about whether those nine entries would have been profitable.
- ⛔ **No test was run and none was needed** — this card changed no code, so there was nothing a
  regression could have gone red on. **(P)** `git diff --name-only 348c226..HEAD -- '*.py' '*.yaml'
  '*.sql'` = empty.
