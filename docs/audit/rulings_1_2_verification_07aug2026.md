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

---
---

# §8 · 07-Aug-2026 EVENING — THE ENTRY-THROTTLE MEASUREMENT (Q1–Q5) + THE BROKER GTT CHECK

**⛔ READ-ONLY. NO CODE. NO CONFIG. NO SCHEMA. NO GTT CANCELLED. NO BUILD.**

> **WHY THIS IS HERE AND NOT IN A NEW FILE.** The card offered a choice. This is an **append**,
> because §8 does not introduce a topic — it **closes OPEN-1 and OPEN-2 of this document's own §6**
> and **corrects this document's own H1 row 5 and H7 row 5**. A companion file would fork the record
> that PATHS.md and `SYSTEM_MAP.md` already point at by section. The anti-duplication rule binds here
> exactly as it bound Ruling 1 in §4.
>
> **PROVENANCE.** Code read at working-tree `8affcae` (docs-only since `348c226`; `signals/entry_throttle.py`
> is byte-identical to its only ever commit, `c0554c6`). Production reads: VM
> `/home/ubuntu/systems/trading-system/logs/system_2026-07-27.log` (JSON), `journalctl -u trading-system.service`,
> `data_store/trading_system.db` `mode=ro`, and **the Zerodha GTT/orders/holdings/positions API**, ~17:0x–17:3x IST.

---

## §8.1 — Q1 · FROM WHAT EVENT DOES `per_symbol_cooldown_sec` MEASURE?

### 🏷️ **(b) CONFIRMED DESIGN — it measures PREVIOUS ENTRY → NEW ENTRY. There is no exit hook anywhere in the class.** **(S)** + **(P)**

**The code that RECORDS the timestamp** — `signals/entry_throttle.py:109-113`, inside `admit()`, on the
admit path only:

```python
            # admit — record the placement
            self._last_entry = now
            self._recent.append(now)
            if symbol:
                self._per_symbol_last[symbol] = now
```

**The code that COMPARES against it** — `signals/entry_throttle.py:97-107`:

```python
            # 3. per-symbol cooldown
            if self._per_symbol > 0 and symbol:
                last = self._per_symbol_last.get(symbol)
                if last is not None:
                    elapsed = now - last
                    if elapsed < self._per_symbol:
```

> ### ⭐ **`_per_symbol_last[symbol]` IS WRITTEN AT EXACTLY ONE PLACE IN THE REPOSITORY — LINE 113, INSIDE `admit()`.**
> *(Width: repo-wide grep for `entry_throttle|EntryThrottle|per_symbol_cooldown|\.admit\(` across every
> `*.py`. Every hit outside `tests/` is a **constructor** argument (`main.py:3150`, `core/config_loader.py:404`,
> `signals/signal_processor.py:151/230-234`), a **metrics read** (`:602`), or one of the three `admit()`
> calls. **No exit path, no fill path, no `trade_closed` path, and no reconciler touches this class.**)*
>
> ⇒ **The interval is PREVIOUS ENTRY → NEW ENTRY.** The class has no way to learn that a position exited.

**And `admit()` is reached at entry DISPATCH, not at fill.** All **three** production `_placer.place(`
call sites — `signal_processor.py:1263` (main), `:2028` (gate path), `:2305` (retest path) — are each
immediately preceded by `_tr = self._entry_throttle.admit(symbol)` at `:1249`, `:2017`, `:2296`.
**(S)** The chokepoint is complete: there is no fourth production `place()` site.

---

## §8.2 — Q2 · WHY DID IT NOT BLOCK SENCO ON 27-JUL? — **ALL FOUR NAMED CANDIDATES, SCORED**

### 🏷️ **(b) CONFIRMED DESIGN. Candidate 1 is the whole explanation. Candidates 2, 3 and 4 are each REFUTED BY MEASUREMENT — not merely unsupported.** **(P)** + **(S)**

| # | candidate | verdict | evidence, and the width of the check |
|---|---|---|---|
| **1** | **the cooldown measures from the wrong event** | ✅ **SUPPORTED — and sufficient alone** | §8.1 (S) + §8.3's arithmetic (P): the operand the throttle actually held was **719.644 s**, which is **2.399×** the 300 s threshold. It was consulted, it evaluated correctly, and it correctly admitted |
| **2** | the value was different on 27-Jul | 🔴 **REFUTED** | **(P)** `git show d3fa5b8:config/system_config.yaml` — the tree deployed at 10:14 on 27-Jul — reads `per_symbol_cooldown_sec: 300`, identical to today. **Width:** `git log -S "per_symbol_cooldown_sec"` over `system_config.yaml`, `entry_throttle.py`, `signal_processor.py`, `main.py`, `config_loader.py` returns **exactly one commit ever** — `c0554c6`, 19-Jun-2026. **The value has been 300 continuously since the day it was created and has never been edited** |
| **3** | the throttle is not on that code path | 🔴 **REFUTED** | **(S)** all 3 production `place()` sites are throttle-gated (§8.1). **(P)** and it demonstrably ran on that symbol that morning: `SENCO … rejected at ENTRY_THROTTLED` never appears, but the gate itself fired **7 times on 27-Jul**, including its **`per_symbol` category twice** (§8.4) |
| **4** | **the throttle state is in-memory and had been LOST** *(the card flagged this as the more serious outcome)* | 🔴 **REFUTED — decisively** | **(P)** the **complete** `systemd[1]` record for `trading-system.service` on 27-Jul is **THREE LINES**: `Started 08:15:30` · `Deactivated successfully 17:35:04` · the CPU-consumption summary. **Width:** `journalctl -u trading-system.service --since 2026-07-27 00:00 --until 2026-07-28 00:00`, filtered to `systemd[1]` — the emitter that *must* log any start, stop, crash or scheduled restart — and the line count is **3**. ⇒ **ONE continuous process spanning both SENCO entries.** The in-memory map still held SENCO's 10:02 timestamp at 10:14 |

> ## ⭐⭐ **THE RESULT IS THE OPPOSITE OF A FAILURE, AND THAT IS WHY IT MATTERS**
> The throttle did not miss SENCO, was not reset, was not bypassed and was not misconfigured.
> **It held the correct timestamp, computed the correct interval, and correctly admitted** — because
> the interval it is built to measure is not the interval the incident is about.
> ⛔ **There is nothing here to fix.** There is a semantic to be *known before a control is removed.*

### 🔴 **AND A SECOND, INDEPENDENT REASON — WHICH REFRAMES OPEN-1 ENTIRELY**

**(P) Gate 1 did not exist when SENCO re-entered.** The tree deployed at 10:14 on 27-Jul was
`d3fa5b8` (26-Jul 23:19:19). In that tree **both halves are absent** — the config key
`one_trade_per_symbol_direction_per_day` and the code site `SYMBOL_DIRECTION_DAILY_LIMIT` /
`count_executed_trades_today_for_symbol_direction`. They arrive later **the same day**:

| commit | timestamp (IST) | what | Δ after the 10:14:12 re-entry |
|---|---|---|---|
| — | **27-Jul 10:14:12** | **the SENCO re-entry** | — |
| `656b62d` | **27-Jul 13:54:12** | `feat(signals): one completed trade per symbol+direction per day -- DEFAULT OFF` — the code | **+3 h 40 min** |
| `300a247` | **27-Jul 15:24:31** | `feat(config): TURN ON the one-trade-per-symbol+direction rule (Rama, 27-Jul eve)` — `: true` | **+5 h 10 min** |

> ## 🔴🔴 **SENCO IS NOT A TRADE THAT GATE 1 HAPPENS TO COVER. SENCO IS THE INCIDENT THAT CREATED GATE 1 — CODE AND CONFIG BOTH AUTHORED THAT SAME AFTERNOON.**
> ⭐ This also settles **OPEN-7** in passing: the "four trading days" between the config commit
> (27-Jul) and the first rejection (03-Aug) is **not** a deploy lag question about a pre-existing
> rule — the rule was **born** on 27-Jul evening, after the market closed. *(⛔ Whether the gap from
> 28-Jul to 03-Aug is deploy lag or simply no qualifying signal is still **not determined** — that
> half of OPEN-7 stands.)*

---

## §8.3 — Q3 · THE SENCO ARITHMETIC, OPERANDS SHOWN

**Every timestamp below is quoted from the `ts` field of a JSON line in
`logs/system_2026-07-27.log` on the VM. No figure is stated that was not read from that record.**

**The proxy, stated rather than assumed:** `admit()` returns at `signal_processor.py:1249`,
microseconds before `place()` emits `order_placer.place_start`. `place_start` is therefore used as the
**admit instant**. Its error is bounded below by the preceding `risk_engine.approve` line, so the true
interval lies in **[718.941 s, 720.298 s]** — and every value in that interval exceeds 300 s, so the
verdict does not depend on the proxy.

| # | event | quoted timestamp (IST) |
|---|---|---|
| ① | trade 1 `risk_engine.approve … approved=True` | `10:02:12.033` |
| ② | trade 1 `trade_created` `trd_c9675b4fae4a…` | `10:02:12.685` |
| ③ | 🔑 **trade 1 `order_placer.place_start` — `admit("SENCO")` recorded here** | **`10:02:12.687`** |
| ④ | trade 1 entry fill @ **418.75** (`avg_fill_price`) | `10:02:17.674` |
| ⑤ | 🔑 **trade 1 `trade_closed` `exit_reason:"TGT_HIT"` @ **425.05** | **`10:13:31.744`** |
| ⑥ | trade 2 `risk_engine.approve … approved=True` | `10:14:11.628` |
| ⑦ | 🔑 **trade 2 `order_placer.place_start` — `admit("SENCO")` consulted here** | **`10:14:12.331`** |
| ⑧ | trade 2 entry fill @ **425.25** (`limit_triple_exits_placed reason:"entry_fill"`) | `10:14:51.157` |

### THE THREE INTERVALS

| interval | operands | seconds | vs 300 s | outcome |
|---|---|---|---|---|
| 🔑 **ENTRY → ENTRY** — *what the throttle actually measured* | ⑦ − ③ = `10:14:12.331 − 10:02:12.687` | **719.644** | **+419.644 (2.399×)** | ✅ **ADMITTED — correctly** |
| **EXIT → re-entry DISPATCH** — *what an exit-keyed cooldown would have measured* | ⑦ − ⑤ = `10:14:12.331 − 10:13:31.744` | **40.587** | **−259.413** | 🔴 **would have BLOCKED** |
| **EXIT → re-entry FILL** — *the figure carried in the incident record* | ⑧ − ⑤ = `10:14:51.157 − 10:13:31.744` | **79.413** | −220.587 | 🔴 would have BLOCKED |

**✅ RECONCILIATION:** the SENCO report's *"Exit → re-entry gap: 10:13:31 → 10:14:51 = 80 seconds"* is
**79.413 s** at full precision. The record is correct; the "80" is a rounding of it.

> ### ⭐ **THE TENSION THE CARD OPENED WITH IS DISSOLVED, NOT EXPLAINED AWAY**
> The card wrote: *"A live 300-second per-symbol cooldown did not stop an 80-second re-entry."*
> **(P) The throttle never saw 80 seconds. It saw 719.644.** The 80 s figure describes an interval
> that **no control in this system measures.** Both stated facts were true; they were about different
> intervals, and nothing in the repo reconciled them because nothing in the repo computes the second.

### 🔑 **AND THE NUMBER THAT PRICES THE DECISION — TRADE 1's HOLDING PERIOD**

⑤ − ③ = `10:13:31.744 − 10:02:12.687` = **679.057 s** (11 min 19 s).

⇒ **When SENCO exited, its cooldown had already been expired for 379.057 s.** Generalised:

> ## 🔴 **THE PER-SYMBOL COOLDOWN'S RESIDUAL PROTECTION AGAINST AN IMMEDIATE RE-ENTRY IS EXACTLY `max(0, 300 − holding_period)`. FOR ANY TRADE HELD LONGER THAN FIVE MINUTES IT IS *ZERO*.**
> **(P) MEASURED over the whole book** — 225 closed trades carrying both `entry_time` and `exit_time`
> (`status IN ('CLOSED','CLOSED_MANUAL')`):
>
> | | n | share |
> |---|---|---|
> | held **< 300 s** — cooldown still alive at exit, offers *some* cover | **40** | **17.8 %** |
> | 🔴 held **≥ 300 s** — cooldown already dead at exit, offers **NO** cover | **185** | **82.2 %** |
>
> **median holding period = 1,596 s (26.6 min) = 5.3× the cooldown.** *(min 2 s · max 166,460 s)*
> ⇒ **On roughly four trades in five, the 300 s cooldown has nothing left to give at the moment the
> re-entry question arises.**
>
> ### ✅ **ROBUSTNESS — the figure is NOT an artifact of delivery carries, and this check could have gone red**
> The 166,460 s maximum is a T+1 carry, and a `GTT_EXIT` trade's `exit_time` is the **boot-time
> finalisation**, not the real exit (F6) — so delivery rows could have inflated the number. **Re-run
> with them excluded:**
>
> | population | n | held ≥ 300 s | median |
> |---|---|---|---|
> | all closed | 225 | **185 = 82.2 %** | 1,596 s |
> | **excluding `exit_reason='GTT_EXIT'`** | 222 | **182 = 82.0 %** | 1,574 s |
> | only `GTT_EXIT` (delivery) | 3 | 3 = 100 % | 2,479 s |
>
> ⇒ **82.2 % → 82.0 %. The conclusion is carried by the intraday book, not by the three delivery
> rows.** *(Exit-reason mix: `SL_HIT` 106 · `TGT_HIT` 72 · `MANUAL` 44 · `GTT_EXIT` 3.)*

---

## §8.4 — ⭐⭐ THE PYRAMID CONTROL — THE SEMANTICS PROVEN ON PRODUCTION DATA, NOT ONLY READ FROM SOURCE

**(P)** The `per_symbol` gate fired **twice on 27-Jul**, on PYRAMID. Because the reject string prints
the operand (`f"per_symbol {symbol} {elapsed:.0f}s < {self._per_symbol:.0f}s"`), the log **states the
interval the throttle measured** — which makes this a direct discriminator between the two candidate
semantics, on live data, on the same day as SENCO.

| # | previous PYRAMID `order_placer.place_start` | throttle reject line | wall-clock Δ | **printed `elapsed`** | match at printed precision |
|---|---|---|---|---|---|
| 1 | `10:06:13.701` | `10:07:14.035` — `per_symbol PYRAMID 60s < 300s` | **60.334 s** | **60** | ✅ |
| 2 | `10:11:13.903` | `10:12:14.835` — `per_symbol PYRAMID 61s < 300s` | **60.932 s** | **61** | ✅ |

> ### 🔴 **AND THE DISCRIMINATOR IS CLEAN BECAUSE PYRAMID NEVER EXITED — IT NEVER EVEN OPENED.**
> **(P)** the full PYRAMID event list for 10:00–10:20 shows the 10:06 entry was **REJECTED BY THE BROKER**:
> `place_order call_start 10:06:14.637` → **`Zerodha rejected order: MIS orders are currently blocked for PYRAMID`** `10:06:14.678`
> → `mis_blocklist: recorded MIS-block` `10:06:15.471` → `Pipeline exception` `10:06:15.474`.
> **There is no PYRAMID position, no fill and no exit anywhere in that window** ⇒ an exit-keyed
> cooldown would have had **no timestamp to measure from** and could not have printed `60s`/`61s` at all.
> **The only events 60.3 s and 60.9 s before the two rejects are the two dispatches.** **Q1 is proven twice over.**

### ⚠️ **A CONSEQUENCE THIS SURFACED THAT WAS IN NO DOCUMENT — REGISTERED, NOT FIXED**

### 🏷️ **(b) CONFIRMED DESIGN, with a consequence worth knowing** **(P)**

**`admit()` records the placement BEFORE `place()` is called** — deliberately, and the docstring gives
the reason: *"a split would leave a TOCTOU window where a burst of concurrent worker threads all pass
check() before any records."* Correct for burst suppression. **But it means a placement the broker
REJECTS still consumes the symbol's full 300 s cooldown.** PYRAMID is the measured instance: an order
that was never accepted locked the symbol for five minutes and blocked **three** later signals
(`10:07:14.035`, `10:07:14.840`, `10:12:14.835`).

⭐ **Same family as the standing finding *"it counts its own rows where it means the broker's reality"*** —
here the throttle counts its own **dispatch** where a reader would assume it counts an **entry**.
⛔ **Not a defect and not proposed for change**: the TOCTOU rationale is sound and this is the price of it.

⚠️ **And one more, stated because it bears directly on OPEN-2:** PYRAMID's two dispatches are
`10:11:13.903 − 10:06:13.701` = **300.202 s** apart. It re-entered **0.202 s after the cooldown
expired.** With a scanner re-firing on a ~60 s cadence, **the cooldown does not prevent the re-entry —
it schedules it.** *(This is the measured form of H7's "a throttle DELAYS; it does not consume the day.")*

---

## §8.5 — Q4 · DOES THE THROTTLE DISCRIMINATE DIRECTION, OR PRODUCT?

### 🏷️ **(b) CONFIRMED DESIGN — NEITHER. It is direction-blind and product-blind, and its key is the bare symbol string.** **(S)**

- The state is `self._per_symbol_last: dict[str, float]` (`:58`) — **`symbol -> last placement ts`**. One
  scalar per symbol; the type cannot carry a second dimension.
- The signature is `admit(self, symbol: Optional[str] = None)` (`:65`). **Direction, product, intent,
  strategy and side are not parameters** — all three call sites pass `admit(symbol)` and nothing else.

> ⭐ **This is the same shape as the three symbol gates in H1** *(gate 3 `DUPLICATE_SYMBOL` is
> direction-blind and product-blind; gate 2 is the direction-partitioned slice of it)*. ⇒ **Ruling 2 is
> pipeline-independent and so is the throttle** — a CNC delivery entry and a MIS intraday entry on the
> same symbol contend for **one** 300 s cooldown. **Relevant to OPEN-2: whatever Rama decides about
> *"immediately"*, the throttle will apply it across products, because it cannot do otherwise.**

---

## §8.6 — Q5 · WAS THE THROTTLE EXERCISED IN BOTH PAPER AND LIVE?

### 🏷️ **(d) CANNOT DETERMINE BY OBSERVATION — parity is (I), never (P). ⛔ IT WAS EXERCISED IN LIVE ONLY.** **(P)** for the negative; **(S)/(I)** for the parity claim

**MEASURED, not inferred.** Every retained daily log on the VM, checked for its boot-banner mode:

| | |
|---|---|
| retained `logs/system_*.log` files | **24** (`2026-07-07` → `2026-08-07`, the full 30-day window) |
| days whose boot banner reads `mode=live` | **24 of 24** |
| 🔴 days whose boot banner reads `mode=paper` | **0** |
| days on which the `per_symbol` gate fired | **19 of 24** *(all live)* |

⇒ **The per-symbol cooldown has NOT been exercised in paper within any evidence window available to
me.** ⛔ **This does NOT establish "never in paper"** — 30-day retention means the check can only say
*"not in the last 24 trading days."* **(Absence is bounded by the width of the check.)**

**What supports parity is structural, and it is (I):**
`EntryThrottle` holds no adapter, imports nothing from `broker/`, and its outcome depends only on
`time.monotonic()` and the symbol string; `main.py:3150` constructs it identically regardless of mode;
`signal_processor.py:26` declares *"no direct broker import."* The module docstring asserts
*"Parity: pure in-memory rate logic, identical in paper and live"* — **an assertion, not a measurement.**

> ⭐ **AND THE PARITY HAZARD NAMED IN §7 DOES NOT REACH THIS CONTROL — which is worth saying, because
> it is the one place in this campaign where a paper drill would NOT be vacuously green.** The standing
> hazard is **PAPER NETS BY *SYMBOL* / LIVE KITE NETS PER *(SYMBOL, PRODUCT)*** — it bites anything
> reading broker position quantities. **The throttle reads none.** ⇒ a paper drill of the throttle would
> be genuinely informative. ⛔ **It has not been run, and this report does not propose one.**

---

## §8.7 — THE CARD'S PRE-REGISTERED EXPECTATIONS, SCORED

⭐ **Written by the card's author before any measurement existed. 4 HELD · 0 FAILED · 1 correctly ABSTAINED.**

| # | prediction | score | why |
|---|---|---|---|
| **Q1-P** | **ENTRY → ENTRY** *(moderate confidence, reasoned from "atomic check-and-record")* | ✅ **HELD** | and the stated reasoning was the *correct* reasoning — "records at placement ⇒ no exit hook" is exactly what `:113` does. ⭐ The prediction was right for the right reason, which is the stronger form |
| **Q2-P** | the explanation is Q1's answer **alone**; **no** restart or lost-state involvement | ✅ **HELD — and more strongly than predicted** | lost state is not merely *unsupported*, it is **REFUTED**: a 3-line `systemd[1]` record proves one continuous process across both entries. ⭐ The card asked for the serious alternative to be checked rather than assumed away, and that instruction is what turned an absence into a refutation |
| **Q3-P** | entry→re-entry **exceeds** 300 s; exit→re-entry is the recorded 80 s; **no figure predicted for the entry time** | ✅ **HELD** | 719.644 s and 79.413 s. ⭐ **The abstention was correct discipline** — the entry timestamp was genuinely not in the card, and inventing one would have been unfalsifiable |
| **Q4-P** | product-blind **and** direction-blind, same shape as the symbol gates | ✅ **HELD** | `dict[str, float]` keyed on the bare symbol; `admit(symbol)` takes nothing else |
| **Q5-P** | *no prediction offered — "I have no basis"* | ➖ **CORRECTLY ABSTAINED** | there genuinely was no basis in the card, and the measured answer (live-only, 24/24) could not have been reasoned to |

> ### ⚠️ **AND THE HONEST METHODOLOGICAL READ: THIS SET IS WEAKER EVIDENCE THAN THIS MORNING'S.**
> **A clean sweep of confirmations teaches less about the predictor than P3's failure did.** The card
> says so itself and it is right. ⭐ **The one place these predictions earned their keep is Q2-P**: by
> naming lost in-memory state *in advance* as the more serious alternative, the card forced a check
> that would otherwise have been skipped once candidate 1 already explained everything —
> **and a skipped check would have left "the state was probably fine" where there is now a 3-line proof.**

---

## §8.8 — 🔴 WHAT THIS DECIDES — OPEN-1 AND OPEN-2

> ## ⛔ **THE CARD PRE-COMMITTED THAT THE TWO ANSWERS LEAD TO OPPOSITE RECOMMENDATIONS AND FORBADE SOFTENING WHICHEVER WAS FOUND. THE ANSWER FOUND IS THE EXPENSIVE ONE, AND IT IS RECORDED UNSOFTENED.**

**The finding is ENTRY → ENTRY.** Therefore, in the card's own words: *"gate 1 is the ONLY control
standing between the system and an immediate re-entry after a profitable exit, and Rama is being asked
to remove it."* **That reading is CONFIRMED, and measurement makes it sharper than the card put it.**

**Every control enumerated in H7, tested against the SENCO shape** — *a completed, profitable exit
followed by a same-symbol same-direction re-entry seconds later*:

| control | does it block the re-entry? | why not |
|---|---|---|
| **gate 1 `SYMBOL_DIRECTION_DAILY_LIMIT`** | ✅ **YES — the only one** | its status set includes **`CLOSED`/`CLOSED_MANUAL`**, so a completed trade still counts (H3). **This is the loosening half of Ruling 2** |
| gate 2 `CONTRARY_POSITION` | ⛔ no | requires `PENDING_FILL/OPEN/PARTIAL`; a closed trade has left that set. *(And it has fired **0** times in 109,254 signals)* |
| gate 3 `DUPLICATE_SYMBOL` | ⛔ no | same status set — **released by the exit, by design** |
| **`per_symbol_cooldown_sec: 300`** | ⛔ **no — measured: 719.644 s ≥ 300** | entry-keyed; **dead for 82.2 % of trades by the time they exit** (§8.3) |
| throttle `min_gap` 20 s | ⛔ no | global, and 719 s ≫ 20 s |
| throttle `burst` 3/60 s | ⛔ no | one entry in the window |
| `max_open_positions` 5 · per-strategy concurrency | ⛔ no | **the exit released the slot** — H7's ⭐⭐ point |
| `max_daily_trades` 10 | ⚠️ only at the margin | has not bound since **10-Jul**; H6 measures both affected days reaching **11** |

⇒ **Turning gate 1 off does not fall back onto the cooldown. It falls back onto nothing**, on ~82 % of
trades. ⭐ **And §8.2 adds the fact that most changes the character of the decision: gate 1 was written
and switched on within five hours of the SENCO re-entry, in response to it.** OPEN-1 is therefore not
*"should we relax an incidental legacy rule"* — it is *"should we remove the control this incident
caused, given that measurement now shows nothing else would have stopped it."*

⛔ **THIS IS A MEASUREMENT AND A FRAMING. IT IS NOT A RECOMMENDATION, AND THE DECISION IS RAMA'S.**
The counter-argument remains fully alive and is **not** weakened by anything here: the SENCO report's
own §4 measured the re-entry population at **n=2 over five weeks, total stake ≈ ₹5**, and called it
*"two anecdotes… a rule justified on one blocked trade is a rule justified on nothing."* **A control
can be the only one of its kind and still not be worth its cost.** ⭐ What §8 changes is that the price
of removing it is now **known** rather than assumed.

**FOR OPEN-2 — the one sentence Rama was asked for is now better posed.** Rama's text says a symbol
*"immediately becomes eligible again."* **(P)** The cooldown does not contradict that as a matter of
*ownership* — it never asserts ownership, it spaces dispatches, and §8.4 shows it **defers** a re-entry
by seconds rather than preventing it (PYRAMID re-entered at 300.202 s). ⇒ the two can coexist without
amendment. ⛔ **But that is still a decision, not an inference, and §8 does not take it.**

---

## §8.9 — 🔴 THE BROKER GTT SAFETY CHECK — AND THE CARD'S PREMISE IS **REFUTED**

**⛔ ANSWERED FROM THE BROKER API (`kite.get_gtts()` / `.orders()` / `.holdings()` / `.positions()`),
NOT FROM `gtt_state`, exactly as §3.1 required. NOTHING WAS CANCELLED.**

### ✅ **3.1 — ATULAUTO GTT `330657774`: ABSENT FROM THE BROKER. IT IS GONE.**

**(P)** The broker returns **4** GTTs in total. `330657774` is **not among them**, and **no ATULAUTO GTT
of any status exists at the broker.**

### 3.3 — WHAT CLOSED IT, AND WHEN — traced in the log

| timestamp (IST) | line |
|---|---|
| `2026-08-06 10:02:13.710` | `place_gtt call_end … gtt_id:"330657774"` — placed by `cnc_gtt_monitor.recreated`, `why:"GTT missing; holding intact"` **(the F6 respawn)** |
| `2026-08-07 08:15:41.704` | ⚠️ `cnc_gtt_monitor.forensic … detail:"GTT active but holding flat (external close)"` |
| 🔑 `2026-08-07 08:15:41.722` | **`delete_gtt call_end … gtt_id:"330657774", mode:"LIVE"`** ← **the system cancelled it itself, at this morning's 08:15 boot** |
| `2026-08-07 08:15:41.795` | `cnc_gtt_monitor.gtt_exit … exit_price:579.55, pnl:-9.35` → trade `trd_e66ee17b…` **CLOSED**, `exit_reason='GTT_EXIT'` |

⇒ **The "no manual ATULAUTO buy" DO-NOT can be lifted.** ⛔ **I have NOT lifted it — that is Rama's,
as §3.3 requires.**

### 🔴🔴 **BUT THE CARD'S CLOSING PREMISE IS WRONG, AND IT IS WRONG IN THE DIRECTION THAT MATTERS**

The card wrote that ATULAUTO is *"the only item here that involves real money at the broker tonight."*
**(P) It is not — and it is the one item that is now clean. The live one is `DIFFNKG`.**

**MEASURED AT THE BROKER, 07-Aug ~17:2x IST:**

| source | reading |
|---|---|
| `holdings()` DIFFNKG | **`quantity=0`, `t1_quantity=0`, `realised=0`** — the holding is **FLAT** |
| `positions()` net DIFFNKG | **CNC `net=-1`, `buy=0`, `sell=1`** — a completed SELL, surviving as a **NEGATIVE** row |
| `orders()` DIFFNKG today | **ONE** order: `260807170745584` SELL CNC LIMIT qty 1 **`filled=1` `COMPLETE` at 14:50:52** |
| 🔴 `get_gtts()` | **`330944932` · DIFFNKG · `active` · SELL/SELL · CNC/CNC · triggers `[437.2, 459.45]` · created today `15:20:35`** |
| `trades` row `trd_010f8e21…` | **`OPEN`, `exit_time` NULL** — a **PHANTOM** |

**And the respawn is visible as a sequence** — `gtt_state` for DIFFNKG: `330658430` TRIGGERED (→ the
14:50:52 fill) → `330940420` created **15:05:26**, TRIGGERED 15:08:11 → `330944932` created **15:20:35**,
**ACTIVE now**. *(⚠️ **Stated not chased (G3):** the second trigger at 15:08:11 has **no corresponding
order** in the broker's order book — only one DIFFNKG order exists today. Observed, not explained.)*

> ## 🔴 **THERE IS A LIVE, RESTING SELL GTT AT ZERODHA (`330944932`, DIFFNKG, CNC, triggers 437.2 / 459.45) AGAINST A HOLDING THAT IS ALREADY FLAT AND ALREADY SOLD.**
> **This is F6 cost 4 — "three live SELL GTTs on a flat holding" — measured live, tonight, on the
> symbol the card did not name.** The mechanism is exactly the recorded one: the completed CNC SELL
> survives as `net=-1`, `abs()` at `cnc_gtt_monitor.py:464` reads it as `held=1`, the monitor concludes
> the holding is intact and **recreates the GTT every cycle**.
> ⛔ **NOT CANCELLED — §3.2 and §5 forbid it, and they are right to: cancellation is a live broker
> action and it is not authorised here.** ⭐ **Markets are closed, so nothing can fire tonight; the
> exposure is MONDAY.**
> ⭐ **`MANINFRA` is by contrast CORRECT and needs nothing:** `positions()` CNC `net=+4, buy=4, sell=0`
> — a real position — with one matching `active` GTT `330856765`. ⛔ **Do not sweep it with DIFFNKG.**

---

## §8.10 — WHAT §8 REFUTES IN THE CARD *(the card asked for this explicitly)*

| # | the card said | verdict |
|---|---|---|
| 1 | *"Your own **H1 note** says the throttle state is not persisted"* | ⚠️ **MIS-ATTRIBUTED.** H1 row 5 says only that the cooldown is a *spacing* rule and **LIVE**; it makes **no** persistence claim. The in-memory fact is real but its sources are the **code** (`dict`/`deque` instance attrs, no store) and the 19-Jul throttle report — **not H1.** ⭐ Immaterial to the answer; corrected so a later reader does not go looking for it in H1 |
| 2 | *"A live 300-second cooldown did not stop an 80-second re-entry"* — framed as an unreconciled tension | ⚠️ **DISSOLVED, not resolved.** The throttle never evaluated 80 s; it evaluated **719.644 s**. The two facts were never in tension — they describe **different intervals**, and the 80 s one is measured by **no control in the system** |
| 3 | 🔴 *"ATULAUTO … is the only item here that involves real money at the broker tonight"* | 🔴 **REFUTED.** ATULAUTO is **clean** — the system cancelled its GTT at 08:15:41 today. The real-money item is **DIFFNKG `330944932`, resting and active** (§8.9), which the card did not know about |
| 4 | *(implicit)* SENCO passed gate 1 | 🔴 **REFUTED — gate 1 did not exist yet.** Both halves were authored **3 h 40 min and 5 h 10 min AFTER** the re-entry, the same day (§8.2) |

⭐ **Nothing here refutes the card's core instruction, which was correct and load-bearing:** Q1 was
*"the whole question,"* and it was. **And Q2's demand that lost in-memory state be checked rather than
assumed away is what produced the strongest single piece of evidence in this section.**

---

## §8.11 — WHAT §8 DOES **NOT** ESTABLISH

- ⛔ **Nothing about paper.** The throttle has **not** been exercised in paper in 24 retained trading
  days; parity remains **(I)**. ⛔ "Never in paper" is **not** established — retention is 30 days.
- ⛔ **No recommendation on OPEN-1 or OPEN-2.** §8.8 prices the decision; it does not take it.
- ⛔ **Nothing about whether the 300 s value is right.** Only what it measures **from**.
- ⛔ **No claim that the throttle is defective.** It is not. It did exactly what it is built to do.
- ⛔ **Nothing done about `330944932`.** Reported only. Cancellation, and any F6 work, need their own
  card and Rama's authorisation in his own words.
- ⛔ **The 15:08:11 DIFFNKG trigger with no matching broker order is UNEXPLAINED** and was not chased.
