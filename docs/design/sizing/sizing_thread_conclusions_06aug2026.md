# SIZING THREAD — THE CONCLUSIONS, AND THE REASONING BETWEEN THE MEASUREMENTS

**06-Aug-2026 · DELIVERY + ORDER-SIZING thread ONLY.** Citations at the deployed SHA `0197923`
unless stated. ⛔ **No value set · no YAML key · no code · no ruling taken.**

> ## ⛔ WHAT THIS FILE IS — **and what it deliberately is NOT**
> **The five MEASUREMENT documents already exist. ⛔ Nothing here duplicates them — POINT AND GO:**
>
> | document | what it holds |
> |---|---|
> | `current_sizing_chain_06aug2026.md` | the chain, rung by rung |
> | `tier_multiplier_measurement_06aug2026.md` | the tier, measured over 483 trades / 72,755 signals |
> | `config_surface_review_06aug2026.md` | the 301→113 corpus, the shared-key list |
> | `delivery_config_surface_06aug2026.md` | the four-way classification, the 1:1 rule, §6's filter, §7's three gates |
> | `dependency_map_06aug2026.md` | the recount, the dependency table, the graph + circularities |
>
> ⭐⭐ **WHAT WAS MISSING IS THE REASONING *BETWEEN* THEM — it existed only in conversation and dies
> with the session.** That is this file. **Cold-read target: a fresh session should be able to
> RESUME the thread from here alone.**

## 🧭 DECISION IMPACT MATRIX — ⛔ **read this instead of seven sections**

> 🏷️ **THE DISTINCTION THAT MATTERS: `OPEN — MEASUREMENT` and `OPEN — GOVERNANCE DEPENDENCY` are
> acted on by DIFFERENT PEOPLE.** ⛔ **More evidence moves the first and does nothing for the second.**

| finding | § | needs MEASUREMENT? | needs a RULING? | blocks IMPLEMENTATION? |
|---|---|---|---|---|
| **the intraday two-leg margin divisor** | §1.1a | 🔴 **YES — passive, takeable on any trading day** | ⛔ no | ⚠️ **yes, for intraday sizing** — and ⭐ **the dangerous direction is the one currently assumed** |
| **delivery divisor = 1** | §1.1 | ✅ settled **(P)** | ⛔ no | ⛔ no |
| **leverage: declare the denominator** | §1.3 | ✅ settled **(P)** | ⚠️ **shape only** — keep deflate-the-requirement | ⛔ no |
| **the ladder taxonomy** | §2 | ✅ settled **(S)**, structurally | ⚠️ adopt-as-written? | ⛔ no — ⭐ **it costs zero refused trades** |
| **`:530`'s `raw_qty × 2` ceiling** | §2.3 | ✅ settled **(S)** | ⛔ no | ⚠️ **latent** — lives the moment a modifier exceeds 1 |
| **the tier is armed, not dead** | §3 | ✅ settled **(P)** | 🔴 **YES — tier ON/OFF for delivery** | ⛔ no |
| **G2's doubling warning stays unconditional** | §3.4 | ✅ settled **(P)** 372/483 | ⛔ **no — ⛔ do NOT re-open** | ⛔ no |
| **the delivery surface ORDER** | §5 | ✅ settled **(P)** | 🔴 **YES — it is item 5's content** | 🔴 **YES** |
| **segment halt = gate, not kill** | §6.1 | ✅ shape settled **(S)** | 🔴 **YES** | 🔴 **YES** |
| **carried positions when delivery halts** | §6.1 | ⛔ no | 🔴 **YES — and it DEPENDS on the line above** | 🔴 **YES** |
| **drift tolerance declined; fix the comparison** | §6.2 | ✅ settled **(S)** | ⚠️ the *comparison* fix is a separate item | ⛔ no |
| **bucket denomination does not isolate** | §6.3 | ✅ settled **(S)** | ⚠️ only if the snapshot shape is pursued | ⚠️ **must land AFTER F6** |
| **isolate the policy, never the purse** | §6.4 | ✅ settled | ✅ **promoted — rules §1.13** | ⛔ no |
| 🔴 **inventory AUTHORITY** | §7.1 | ⛔ **no — evidence changes nothing** | 🔴 **YES** | 🔴🔴 **BLOCKS THE WHOLE SURFACE** |
| 🔴 **pipeline OWNERSHIP (3 gates)** | §7.1 | ⛔ **no — it is STRUCTURAL** | 🔴 **YES** | 🔴🔴 **BLOCKS THE WHOLE SURFACE** |

⭐ **Read the last two rows first.** ⛔ **Everything marked "blocks implementation" is downstream of
them.**

> 🔒 **AND THEN READ `§9` — THE EXIT CRITERION.** ⛔ **This header tells you to read the matrix
> *instead of* the sections; §9 is the one section that instruction must not hide.** It states when
> the thread is **CLOSED** *(A–D)* and — ⭐ **the part that is ours** — **what may REOPEN it (E)**.
> ⛔ *"Someone had a further idea"* **is not on that list.**

---

# §1 · THE CONCRETE SIZING FACTS THAT ARE IN NO MEASUREMENT DOC

## 1.1 · 🔴 THE ÷3 PREMISE IS REFUTED — **and differently per pipeline**

**The model being tested:** divide quantity by 3, because *"entry + SL + TGT each consume capital."*

| pipeline | divisor | evidence |
|---|---|---|
| **DELIVERY (CNC)** | **1** | **(P)** the delivery order set is **ONE row — `leg=ENTRY`, `product=CNC`, `COMPLETE`. Zero SL rows, zero TGT rows.** Protection is a **broker-side GTT**, which **blocks no margin until it triggers** ⇒ ⛔ **÷3 would cut CNC size to a third for nothing** |
| **INTRADAY** | **at worst 2**, and only while both exits rest | ⚠️ `LIMIT_TRIPLE` is **two-phase** — SL+TGT are deferred to fill time. 🏷️ **`OPEN — MEASUREMENT`: whether the broker charges the second leg is UNVERIFIED against this account.** ⛔ Do not close this by reasoning; it needs a margin observation — ⭐ **and it is TAKEABLE on any trading day: see §1.1a** |

### 1.1a · 🏷️ `OPEN — MEASUREMENT` · **the intraday two-leg margin question**

⭐⭐ **This is the ONE piece of sizing work that is neither blocked nor analysis.** It needs a **live
observation**, not a ruling and not another document.

> **THE QUESTION:** with `LIMIT_TRIPLE`'s **SL and TGT both resting after a fill**, **does the broker
> charge margin for the second?**
> ⇒ ⭐⭐ **It decides whether the intraday divisor is 1 or 2 — a FACTOR OF TWO on every intraday
> position.**

**THE OBSERVATION IS PASSIVE — ⛔ NO TRADE IS TO BE PLACED FOR IT.**

> 🔄 **AMENDED 07-Aug-2026 00:3x — THE OPERATIVE PROCEDURE NOW LIVES IN
> `docs/audit/FRIDAY_MORNING_07-Aug-2026.md` §4**, upgraded from *"record three numbers"* to a
> **CALIBRATION TEST**: **FOUR clock-stamped readings** *(used margin before · used margin after both
> exits rest · position value · available funds)* **then FOUR documented fields** *(observed divisor ·
> confidence · broker conditions · is a repeat required)*. ⛔ **Follow §4, not the struck text below.**
> ⭐ **Why it is amended and not rewritten (`G4`):** the three-number form is what the thread actually
> decided on 06-Aug; a reader must be able to see that the procedure was **strengthened**, not that it
> was always this.

~~1. Kite **Funds → `used margin`** *before* the first MIS entry fills;~~
~~2. **again after both exits are resting** *(i.e. the deferred `place_exits` has run)*;~~
~~3. **the position's own value**, for the comparison. Record all three with clock times.~~

⛔ **If no MIS trade fires, the question simply ROLLS — do NOT manufacture one.** ⭐ **A roll is a
legitimate outcome under `§9`'s exit criterion (B), ⛔ not a miss — but it must be RECORDED as one.**

> ### ⚠️ THE ASYMMETRY — **and it is what decides how much this matters**
> **If the divisor is 2 and we assume 1 ⇒ positions are DOUBLE-SIZED against the intended cap.**
> **If it is 1 and we assume 2 ⇒ they are merely HALF-sized.**
> ⇒ 🔴 **THE DANGEROUS DIRECTION IS THE ONE CURRENTLY ASSUMED.**

⛔ **Record only. No sizing change follows from it without the full loop.**

## 1.2 · `margin_reserved` = **entry × 1.05 exactly** — ⛔ not ×3

**(S)** the ×1.05 is the **FIX-090 SL-Market buffer** (`capital.slm_margin_buffer_pct`).
⚠️ **And it is applied to a CNC trade that has no SL-Market order at all.** ⭐ Conservative, so **not
a defect** — but it is **the same coupled-by-omission shape** as everything else in this thread: a
number correct for one pipeline, applied to both because nobody declared which it was for.

## 1.3 · ⭐⭐ LEVERAGE — **the code DEFLATES the requirement; the proposed model INFLATES the base**

**They are equivalent for a single position. They are NOT equivalent for any cap written as a
percentage.**
**(P) only 1 of the 5 rungs uses purchasing power (`qty_by_capital`).** The other four are
percentages of *capital*.
⇒ ⛔ **Adopting inflate-the-base would silently multiply four caps by 5×.**
✅ **RULING SHAPE: KEEP deflate-the-requirement, and make every rung DECLARE ITS DENOMINATOR.**

## 1.4 · ⭐ The sentence the thread turns on *(Rama's)*

> **"₹35k is PURCHASING POWER, not capital."**
> ⇒ **A percentage is meaningless until it names what it is a percentage OF.**

---

# §2 · ⭐⭐ THE LADDER TAXONOMY — **the thread's main structural output**

| kind | may it… | ordering |
|---|---|---|
| **CAP** | reduce, or **REJECT**. ⛔ **never increase** | **COMMUTATIVE** — it is a `min()`; order is irrelevant |
| **MODIFIER** | scale what the CAP layer permitted. ⛔ **never exceed it** | **ORDER-DEPENDENT — must be declared** |
| **FLOOR / RESCUE** | ⭐ **the ONLY rung that may INCREASE**, and only to undo a MODIFIER's rounding to zero. ⛔ **NEVER a CAP's refusal** | **TERMINAL — exactly one, always last** |

## 2.1 · Why FIX-133's floor has never been governed

⭐ **It sits outside both existing categories, which is exactly why no rule ever covered it:**
it **increases** (`0 → 1`) so it is **not a cap**; and modifiers only *shape what caps permitted*,
and **0 permits nothing.** ⇒ **It is a third kind, and naming it is what makes it governable.**

## 2.2 · ✅ **THE TAXONOMY COSTS NOTHING — proven STRUCTURALLY, not empirically**

**(S) `position_sizer.py:449`** — `if raw_qty <= 0: return SizingResult(success=False, qty=0, …)` —
**returns BEFORE the tier block at `:466`.**
⇒ ⭐⭐ **A CAP-INDUCED ZERO CAN NEVER REACH THE FLOOR.** Every FIX-133 rescue is therefore
**modifier-induced by construction** ⇒ **the taxonomy refuses ZERO trades that are taken today.**
⭐ **This is a structural proof, not a sample** — ⛔ it cannot be falsified by a future data set.

**And zero expectancy cost, measured:** `cost_R` is **flat**, and **`qty=1` is the BEST cohort**
(**−0.0587 R** vs −0.2553 and −0.092).

## 2.3 · 🔴 A LATENT VIOLATION ALREADY IN THE CODE

**(S) `position_sizer.py:527-530`:**
```python
tiered_qty = int(math.floor(raw_qty * effective_mult))
# FIX-133 Item 21: cap at 2x base_qty, floor at 1 …
tiered_qty = max(1, min(tiered_qty, raw_qty * 2))
```
⇒ **`min(tiered_qty, raw_qty * 2)` ENCODES PERMISSION FOR A MODIFIER TO REACH TWICE THE CAP LAYER.**
⭐ **Dead today** — `effective_mult ≤ 1.0` in production (`tier_mult ≤ 1.0`, `perf_weight = 1.0` on
483/483). ⛔ **Live the moment any modifier exceeds 1** — which is precisely what a tier
re-calibration or a `min_weight` change would do. 🏷️ **A taxonomy violation waiting for a config.**

## 2.4 · ⛔ `abs()` REMOVAL DOES **NOT** FIX F6 — *(carried here because it is a sizing-ladder fact)*

The signed sum gives `held = −1`, which is **still `!= 0`**, so it **still re-protects**, and it
hands `_reprotect` a **negative quantity.** ⇒ **Both the naive fix and the current code are wrong;
the predicate needs a different shape, not a different sign.**

---

# §3 · THE TIER — **an ALGORITHM that is correct, and a CONFIGURATION that is constant**

## 3.1 · `HIGH` is UNREACHABLE BY CONSTRUCTION

**(P)** threshold **80** against a **measured ceiling of 65** across **72,755 signals**.
`MEDIUM` fired **6 times (0.008 %)** and **none became a trade**. ⇒ **`0.5` on 483/483.**

## 3.2 · ⛔ THE ALGORITHM IS CORRECT — the band logic runs on every signal

⭐ **It is the CONFIGURATION that keeps it constant, and that SHARPENS the hazard rather than
softening it:** raising the scorer's ceiling (**G2**) brings the tier alive **with no code change at
all.** ⛔ **Do not record this as "the tier is dead."** It is armed and waiting on a number.

## 3.3 · ⭐⭐ Its ONLY effect distinguishable from halving the concentration cap

**The 111 `qty=1` trades it lets through at one share.** ⛔ **Everything else it does is
arithmetically identical to a cap change** ⇒ *"should we tune the tier or the cap?"* is, for
483−111 = 372 trades, **a question with no observable difference.**

## 3.4 · 🔴 G2's DOUBLING WARNING IS UNCONDITIONAL — **and must NOT be weakened**

**(P) 372/483 (77 %) would change size today, at ₹10,000.**
⛔⛔ **I proposed making that warning conditional. That was WRONG, and it was the DANGEROUS
direction** — a conditional warning on a doubling is a warning that will be absent on the day it is
needed. 🏷️ **Recorded as a failed prediction in §8.**

## 3.5 · 🔴🔴🔴 **(E) HAS FIRED — 07-Aug-2026. THE SCOPE OF §3.3 WAS WRONG, AND A LIVE POSITION PROVED IT**

🏷️ **THE FIRST LEGITIMATE REOPEN SINCE CLOSURE.** ⭐⭐ **And it came from the BROKER SCREEN, not from
either reviewer** — production evidence contradicting a registered conclusion, which is exactly the
clause `(E)` names. ⛔ Not a refinement; the nineteen remain declined.

**(P) MEASURED, ⛔ NOT INFERRED** — `trades` row, MANINFRA, entered 07-Aug 10:06:05:

| field | value |
|---|---|
| `qty_by_risk` | 40 |
| `qty_by_capital` | 20 |
| **`qty_by_concentration`** | **8** |
| **`binding_constraint`** | **`concentration`** |
| `tier_multiplier_mode` | **ON** |
| **`tier_weight_applied`** | **0.5** |
| `perf_weight_applied` | 1.0 |
| **`qty_planned` = `qty_filled`** | **4** |
| `entry_actual_price` | 115.23 |

⇒ **`raw_qty = min(40, 20, 8) = 8`** and **`floor(8 × 0.5) = 4`.**
⇒ 🔴🔴 **THE TIER HALVED A LIVE DELIVERY POSITION TODAY — THE FIRST OBSERVABLE INSTANCE IN THE
CAMPAIGN'S HISTORY.** ⭐ **FIX-133's floor never engaged, because the modifier did not round to zero.**

### ⛔ THE CORRECTION IS TO THE **SCOPE**, NOT THE ARITHMETIC

**The chain — concentration → `min()` → tier → floor — is UNCHANGED and was never in doubt.** What
was wrong is the **generality**: §3.3's *"its only effect is the 111 `qty=1` trades"* and §3 §210's
*"at `raw_qty=1` the FIX-133 floor cancels it"* were drawn from a corpus in which **every delivery
position ever measured was `qty 1`** — ATULAUTO ₹587.40 · ASKAUTOLTD ₹656.60 · DIFFNKG ₹446.10,
**all high-priced.**

> ## ⭐⭐ **"CONCENTRATION BINDS STRICTLY AT 1" IS PRICE-DEPENDENT.**
> **True for high-priced symbols; FALSE for low-priced ones.** A concentration cap that permits **1**
> share at ₹587 permits **8** at ₹115. ⇒ **the tier's cancellation was never the general case — it
> was an artifact of the sample's price range**, and it was registered without that qualifier.

🏷️ **SAME FAILURE SHAPE AS AR9's TWO SCOPE CORRECTIONS: a conclusion true of the observed set, stated
as true of the class.** ⭐ It is also §8's already-recorded failed prediction *"the tier cancellation
being the general case"* — ⛔ **now upgraded from a self-noted error to a MEASURED one, on a live
position.**

### ⭐⭐ G2's DOUBLING WARNING — SCORED AGAINST A LIVE CASE FOR THE FIRST TIME

**§3.4's warning has never had a delivery instance until today.** At `tier_weight_applied = 1.0`,
**MANINFRA would be 8 shares, not 4 — DOUBLE, on a real position, today.**
⇒ ✅ **§3.4 is CONFIRMED by production, and the instinct to make it conditional is refuted a second
time — this time by evidence rather than by argument.**

### 🔀 WHAT THIS CHANGES — ⛔ AND WHAT IT DOES NOT

⛔ **The two blocking rulings are UNTOUCHED** (the control-inventory authority; may both pipelines
hold the same symbol). §3.5 reaches neither.
⭐ **What it does change is PRIORITY.** The delivery surface was ordered **concentration first, tier
second**, on the reasoning that the tier is inert. **That reasoning no longer holds below roughly
₹150** ⇒ **the tier and `G2` RISE in the order.**
⛔ **The surface is NOT redesigned here** — recorded for the ruling-holder to see **before** he rules.

---

# §4 · RAMA'S SIX CONFIG REQUESTS, TRIAGED

| # | request | verdict |
|---|---|---|
| **1** | min score per day | ⚠️ **ALREADY DESIGNED — Q10 Part A.** ⛔ do not design it twice. ⭐ note scores cap at **65** |
| **2** | entry start/stop + cutoff | ✅ **GENUINE — but DIFFERENT SEMANTICS.** See §5 |
| **3** | market open/close | ✅ **correctly skipped** — global exchange fact |
| **4a** | max open positions incl. carry | ⭐ **ALREADY ISOLATED** (`risk_engine.py:468`) — ✅ **and it DOES count carries** *(no date filter)* |
| **4b** | max position VALUE | 🔴 **has a twin AND has NEVER rejected a trade — zero, ever, in the whole `signals` table** |
| **4c** | max trades/day | ⭐ **ALREADY ISOLATED** (`:552`) — ⛔ **this refutes the "4+2 / 2+4" concern that prompted the whole review** |
| **4d** | max qty per position | ⛔ measured **INERT** |
| **4e** | max loss % on the GTT bucket | ✅✅ **GENUINE — the best item on the list.** See §6.3 |
| **5** | separate drift tolerance | 🔴 **DECLINED — and the reason matters.** See §6.2 |
| **6** | tier ON/OFF for delivery | ✅ **GENUINE** — ⚠️ **but at `raw_qty=1` the FIX-133 floor cancels it, so it changes nothing on 111/483** |

---

# §5 · THE DELIVERY SURFACE — **ORDER, AND THE EXCLUSIONS**

⭐ **Ordered by MEASURED IMPACT. ⛔ NEVER by symmetry with intraday.**

| # | item | why here |
|---|---|---|
| **1** | `delivery.max_concentration_pct` | 🔴 **the ONLY cap that has ever decided a quantity (483/483) — and it has NO delivery control at all** |
| **2** | delivery **tier** | binds 372/483 |
| **3** | `delivery.daily_loss_limit_pct` **on the bucket** | see §6.3 — and it is the request with the most substance |
| **4** | **carry lifecycle** | `max_carry_days` + countdown + slot semantics — concepts intraday does not have |
| **5** | **`entry_cutoff` — a CUTOFF, not a WINDOW** | ⭐ the semantics are *"too late to OBSERVE before it carries overnight unattended"*, ⛔ **not** *"too late to exit today"*. **A different concept from `entry_end`, not a retuned one** |
| **6** | Telegram **vocabulary** + the **capital-starvation alert** | ⭐ **nearly free — the breakdown is already computed and then discarded** |
| **7** | everything else | a twin, a declared reason, or **intentionally unavailable** |

## 5.1 · ⛔ EXCLUDED — **with the measurement as the reason**

position-value % *(never rejected)* · risk % *("algebraically never")* · the count caps *(already
isolated)* · max qty *(inert)*.

## 5.2 · ⭐⭐ THE FINDING THAT REORDERS EVERYTHING

> **THE TWO KNOBS THAT HAVE DELIVERY TWINS ARE THE TWO THAT NEVER BIND.**
> **THE TWO THAT BIND — CONCENTRATION AND TIER — HAVE NONE.**
⇒ ⛔ **Copying the existing surface would reproduce exactly the WRONG TWO KNOBS.**

## 5.3 · The four-way classification, and why the fourth bucket matters most

`SHARED FOREVER` · `DELIVERY TWIN` · `DELIVERY-ONLY` · ⭐ **`INTENTIONALLY UNAVAILABLE`**.
**The fourth is the LARGEST (42), and it is the one that stops a surface that only ever grows.**
⛔⛔ **A KEY'S EXISTENCE IS AN INVITATION: a delivery EOD-squareoff key invites someone to flatten a
carry.** ⭐ *"Delivery must NOT have this control"* is a design output, not an omission.

## 5.4 · ⭐ THE SEMANTIC-DIFFERENCE RULE

> **A twin that cannot state a SEMANTIC DIFFERENCE from its parent should be `SHARED FOREVER`
> instead, with that as its declared reason.**
⛔ *"Same concept, different number"* is a **value preference**, not an isolation argument.
**(P) applying it: 34 → 9.**

---

# §6 · THE TWO THAT ARE NOT CONFIG DECISIONS *(plus the two that reshape the problem)*

## 6.1 · 🔴 THE SEGMENT HALT IS **A GATE, NOT A KILL**

**Rama's requirement is right:** a max-loss halt should apply only to the affected pipeline.
⛔ **But asking the KILL SWITCH to become per-pipeline changes the safety net's shape**, and the
kill switch is the one component whose portfolio-wide scope is the point.
⭐ **The pattern he wants ALREADY EXISTS: `risk_engine.py:468` / `:552` gate per bucket.**
⇒ **SHAPE: refuse new entries for one pipeline · leave existing positions managed · keep the kill
portfolio-wide.**
🏷️ **`OPEN — GOVERNANCE DEPENDENCY`: what happens to CARRIED positions when delivery halts?**
⛔ Cannot be ruled before the gate-vs-kill question itself.

## 6.2 · 🔴 THE DRIFT TOLERANCE IS **DECLINED**, and the reason is the whole point

The check compares **`snapshot.total` (equity)** against **`margins.net` (free cash)** — ⛔ **two
different quantities**, so **the delta IS the deployed capital.**
⇒ ⭐⭐ **A wider delivery band would make a WRONG COMPARISON QUIETER** — **AR9's refused move,
failing silently.**
✅ **The fix is the COMPARISON, not the tolerance:** `available`-vs-`net`, or
`total`-vs-`(net + holdings)`.
⭐ **Rama's concern is LEGITIMATE and is answered elsewhere — ⛔ it is not declined, only its
proposed mechanism is.** *(This is the `G5` shape: intent stands, mechanism returned.)*

## 6.3 · ⭐⭐ BUCKET DENOMINATION **DOES NOT ISOLATE** — and the leak runs both ways

The delivery bucket is **30 % of `_total`**, and **`_total` falls on an INTRADAY loss**
⇒ ⛔ **an intraday drawdown SHRINKS THE DELIVERY BUDGET.**
⭐ **The leak was spotted running one way; it runs both.**
✅ **The shape that WOULD isolate:** denominate on the **delivery bucket's OWN value, captured at
boot.**
⚠️ **AND IT MUST LAND AFTER F6** — **a snapshot converts a self-correcting error into a durable
one**, and tomorrow's replay carries the **₹587.40 phantom**. *(Must-Land-Before constraint 9.)*

## 6.4 · ⭐⭐⭐ **ISOLATE THE POLICY, NEVER THE PURSE** — the thread's architectural conclusion

There is **one broker account and one real balance.**
**Rama's invariant — the sum of reservations across BOTH pipelines can never exceed real capital —
REQUIRES A SHARED QUANTITY.**
⇒ **Full pipeline isolation and that invariant are IN TENSION, and the invariant WINS.**
🏷️ Promoted to `foundation_engineering_rules.md` **§1.13**.

---

# §7 · THE STATE OF THE THREAD, AND WHAT BLOCKS IT

✅ **Investigation COMPLETE.** 🔴 **Architecture: AWAITING RULINGS.** ⛔ **Implementation: BLOCKED,
correctly.**

## 7.1 · TWO GATES FREEZE IT — 🏷️ **both `OPEN — GOVERNANCE DEPENDENCY`**

⛔ **Neither is answerable by measurement. More evidence changes nothing here** — that is what makes
them governance dependencies rather than open questions.

1. **The INVENTORY AUTHORITY ruling** — which control inventory survives, and where it lives.
2. **The PIPELINE-OWNERSHIP ruling** — *may both pipelines hold the same symbol on a day?*
   ⛔ **ALL THREE product-blind gates, not just the one with a config key:**
   `SYMBOL_DIRECTION_DAILY_LIMIT` (`signal_processor.py:717`, expires at midnight) ·
   **`DUPLICATE_SYMBOL`** (`risk_engine.py:688-693`, **never expires**) ·
   `CONTRARY_POSITION` (`risk_engine.py:~680`, **never expires**).
   ⇒ 🔴 **NONE can be made product-aware, because there is NO `trades.product` column** — product
   lives on `orders`, reachable only via `LEFT JOIN … leg='ENTRY'`. **The ruling is STRUCTURAL, not
   a config choice.**

## 7.2 · ⛔ THE BOTTLENECK IS GOVERNANCE, NOT DISCOVERY

⭐ **The one state in which producing more analysis FEELS like progress and is not.**

## 7.3 · ⛔ COMMISSION NO FURTHER SIZING ANALYSIS unless it would change a PENDING RULING

*(Full ruling table: `MASTER_PENDING_01-Aug-2026.md` → THE DECISION LEDGER.)*

---

# §8 · THE METHODOLOGICAL RESULT WORTH KEEPING

> ## ⭐⭐⭐ **EVERY PREDICTION WRITTEN *BEFORE* THE FACT HELD. EVERY EXPLANATION CONSTRUCTED *AFTER* IT FAILED.**
> ⛔ **The split was NOT designed — which is what makes it evidence rather than a slogan.**

| | |
|---|---|
| ✅ **HELD** *(written before)* | the drift silence · the boot seed ≈ 0.940 · the census expectation · `qty_by_flat` null · **the taxonomy costing nothing** · **the cold read going red** |
| ⛔ **FAILED** *(all four MINE, all retrospective)* | the ÷3-era assumptions · the tier cancellation being the general case · **G2's doubling as conditional** · the cost-`R` degradation at `qty=1` |

## 8.1 · ⭐ THREE RULES PASSED THEIR OWN TEST WHILE FAILING THEIR PURPOSE

| rule | passed | but |
|---|---|---|
| **the 1:1 telemetry rule** | as written | it accepted **1 of the 2** known twins |
| **`M12`** | the count verified | **the partition did not** — six phantom members, four omissions |
| **`G7`** | every rule named a parent | **naming a parent does not make a rule BIND** |

⛔⛔ **ALL THREE WERE FOUND BY *APPLICATION*, NONE BY REVIEW.**
⭐ **That is the reusable result: a governance rule is not validated by being written well — it is
validated by being RUN against a real case that could embarrass it.**

---

# §9 · 🔒 THE EXIT CRITERION — ⛔ **IT HAS FIVE PARTS, NOT FOUR**

> ## **THE THREAD IS CLOSED WHEN ALL OF (A)–(D) HOLD:**
>
> **(A)** **both governance rulings taken** — the **inventory authority**, and **pipeline
> ownership** *(§7.1)*;
> **(B)** the **intraday margin observation COMPLETED, or explicitly ROLLED** *(§1.1a)*;
> **(C)** implementation **preserves every accepted invariant** — ⭐ **and this is the TESTABLE one:**
> **CAP never enlarges · MODIFIER never bypasses CAP · FLOOR rescues only MODIFIER rounding · the
> shared-capital invariant holds** *(§2, §6.4)*;
> **(D)** **replay confirms no unintended behavioural change.**
>
> ## ⭐⭐⭐ **(E) — AND IT REOPENS IF, AND ONLY IF:**
> · a **taken ruling is REVERSED**;
> · **production evidence CONTRADICTS a registered conclusion**;
> · an **OPEN measurement resolves DIFFERENTLY from its recorded prediction**.
>
> ⛔⛔ **IT DOES NOT REOPEN FOR:** a better rule · a cleaner taxonomy · a governance refinement ·
> **or anyone having a further idea.**

## 9.1 · ⭐⭐ WHY (E) EXISTS — **and it is ours, not ChatGPT's**

**(E) is not a fifth item appended for symmetry.** It exists because this campaign insisted, all
night and against its own output, that **an entry with no reopen condition is ABANDONED, not
closed** — the decision ledger's first useful output was its **EMPTY reopen-trigger column, 8 of 9
rows** *(`MASTER_PENDING_01-Aug-2026.md` → THE DECISION LEDGER)*.

⛔⛔ **A closure definition without a reopen condition would have been THAT EXACT DEFECT, committed
in the one document whose subject is closure.** ⭐ **That is the whole argument for (E): the rule
had to survive being applied to itself.**

🏷️ **⛔ NOT a new governance rule — no `G`/`M` number is claimed here** *(G7)*. It is the **ledger's
own reopen-trigger requirement, applied to this thread**; the parent is the ledger row schema, and
(E) is that schema's `Reopen Trigger` field filled in.

## 9.2 · ⚠️ THE RATIO THAT MAKES (E) NECESSARY — **record it, because it is the evidence**

> **Of THIRTEEN rounds in this thread: ELEVEN were refining each other's rules. TWO produced
> measurements.** *(11 + 2 = 13 — `M12`: the list states its own count and it reconciles.)*

⭐⭐ **That ratio is not a criticism of the eleven** — several produced the taxonomy, the
semantic-difference rule, and the methodological result in §8, all of which are kept. **It is a
statement about what the thread's DEFAULT motion was**, and the default was refinement.

⇒ 🔴 **(E) is the thing that makes that ratio impossible to repeat BY ACCIDENT.** ⛔ Without it,
*"one more governance round"* is always locally justifiable and never has to argue against a written
condition. ⭐ **With it, a proposed reopen must name which of the three triggers it fires — and
"someone had a further idea" is not one of them.**

⚠️ **Read (E) beside §7.2:** the bottleneck is **GOVERNANCE, NOT DISCOVERY** — ⭐ the one state in
which producing more analysis **feels** like progress and is not. **(E) is §7.2 made checkable.**

## 9.3 · 🏁 **(E) HAS BOUND — first application, ~30 minutes after it was written**

**(P) 07-Aug-2026 00:5x.** Eight further governance refinements were proposed. **All eight were
DECLINED under (E)**, which names a governance refinement explicitly as **not** a reopen condition.
⭐⭐ **Five of the eight already existed** *(the dependency map · `M9`'s evidence classes · the
behavioural contract · the ledger's provenance columns · the retirement criterion)* — 🏷️ **the pull
to ADD is stronger than the memory of what exists.**

⇒ 🔴 **DECLINING IS WHAT ADOPTED (E)** *(`G7.1`: a rule is not adopted until applied to a real case
and BOUND or AMENDED)*. ⛔ **Had the eight been accepted, (E) would never have bound — and it would
have joined the three rules in §8.1 that passed their own test while failing their purpose.**
🔓 **The eight are HELD, ⛔ not rejected**, with a written trigger — **a SECOND ledger cold-read
failure whose SHAPE the existing vocabulary cannot express.** *(Disposition in full:
`campaign_practices.md` → GOVERNANCE DEBT → `GD-1`.)*

⚠️ **⛔ THIS SECTION IS NOT A REOPENING.** It records that the criterion was **exercised**; ⭐ §1–§8
are untouched, and no sizing conclusion moved.

---

*⛔ No value set · no YAML key created · no ruling taken · no code changed. Every measurement cited
here lives in one of the five documents named at the top.*
