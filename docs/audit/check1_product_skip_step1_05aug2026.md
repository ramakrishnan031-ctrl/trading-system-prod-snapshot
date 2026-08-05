# CHECK1 / DELIVERY SKIP — STEP-1 MEASUREMENT · 05-Aug-2026

> ## ⛔ THIS IS STEP 1. **NOTHING IS DESIGNED, PROPOSED OR BUILT HERE.**
> Measurements and classifications only. Every open design question is listed, unanswered, in
> **`NOT DESIGNED HERE`** at the end. No `.py` / `.yaml` / `.sql` / `.sh` / `.service` / `.json`
> was read-modified — this session produced `.md` only.

**Session:** 05-Aug-2026, measured **10:55–11:4x IST** (clock read two ways: `python zoneinfo
Asia/Kolkata` = 10:55:09 IST; `date -u` = 05:25:09 UTC).
**Constraints honoured:** ⛔ no push · ⛔ no code change · ⛔ **no VM contact, no broker contact,
no live-DB read** (market open, service running, two real CNC positions held) · ⛔ intraday path
not investigated (scope lock).
**SHAs measured:** **DEPLOYED `0197923ecb4954fc3ef1b17acac507fc191c9508`** (what the VM booted at
08:15 and what boots THU 06-Aug if nothing is pushed) · **HEAD `0aad93879b057cefc226a9937ceb1c7d6bc00a0d`**.

---

## ⭐⭐ THE THREE ANSWERS THAT GO AT THE TOP

### 1. 🔴 RELEASE GATE — **DOES CHECK1 BEHAVE THE SAME AT `0197923` AS AT HEAD? → YES.**
**And in the strongest available form: not "logic identical, lines moved" but BYTE-IDENTICAL.**

```
git diff 0197923 HEAD --stat -- orders/order_reconciler.py core/state_store.py \
                                broker/product_resolver.py core/constants.py
  -> EMPTY (no change to any of the four)

md5, DEPLOYED vs HEAD:
  order_reconciler   f99258e8910dc9a8db230de178226e69  ==  f99258e8910dc9a8db230de178226e69
  state_store        57f4987eb1e47811a60736a740e18367  ==  57f4987eb1e47811a60736a740e18367
  product_resolver   2d64af04c95d5d6e4be56c5768659623  ==  2d64af04c95d5d6e4be56c5768659623
  constants          a85f3b3484fe2cfd9dd6112ab162540c  ==  a85f3b3484fe2cfd9dd6112ab162540c
  broker/zerodha_adapter.py — git diff --stat EMPTY as well
```

⇒ **EVERY LINE NUMBER IN THIS DOCUMENT IS VALID AT *BOTH* SHAs.** There is no bookkeeping-vs-gate
ambiguity to resolve, because there is no difference of either kind.
⇒ **A push tonight does NOT change what CHECK1 does at Thursday 08:15.**
**Width of that claim, stated so it is falsifiable:** the 8 unpushed commits touch exactly five
files — `docs/04_db_schema_reference.md`, `docs/MASTER_PENDING_01-Aug-2026.md`, `main.py`,
`signals/signal_processor.py`, `tests/unit/test_signal_alert_capital_vocabulary.py`
(`git diff --name-only origin/main..main`). **`main.py` is comment-only** (AST sha256 identical
both sides, md5 moved — proven, §T1). **`signal_processor.py` is a real change on the signal-alert
path and has no call path to the reconciler.** ⇒ **no unpushed commit can reach CHECK1.**
**CLASSIFICATION: (b) CONFIRMED DESIGN** — the release gate is clean, which is the answer the gate
exists to produce.

### 2. ⭐ HOW MANY CANONICAL INTENT→PRODUCT MAPPINGS EXIST?
> **There is EXACTLY ONE canonical intent→product mapping — `ProductResolver`
> (`broker/product_resolver.py`), constructed from `config/system_config.yaml`'s `product_map`
> (`INTRADAY→MIS · DELIVERY→CNC · COVER_ORDER→CO`, `product_resolver.py:11-12`) — and EXACTLY ONE
> canonical product→intent inverse, `core.constants.PRODUCT_TO_INTENT`, which six production
> modules IMPORT rather than redefine.**

The six importers (`git ls-files '*.py' | xargs grep -n PRODUCT_TO_INTENT`, `.git`/`venv` excluded
by `git ls-files` construction): `capital/fund_manager.py:160` · `capital/kill_switch.py:79` ·
`orders/order_placer.py:429` · `orders/order_reconciler.py:98` · `orders/shadow_tracker.py:56`
(+ `core/constants.py` itself). **All six alias the same object; none defines its own.**
⚠️ **ONE STALE COMMENT DISCLOSED, NOT FIXED (bucket (c)):** `orders/order_placer.py:423` says
*"Keep in lockstep with `order_reconciler._PRODUCT_TO_INTENT`"* — an obligation that **no longer
exists**, because `order_reconciler.py:98` now imports the same canonical constant (`FIX-166 F17`,
`order_reconciler.py:94`: *"canonical copy now in core.constants"*). **The duplication it warns
about was already consolidated; the comment describes a maintenance duty against a copy that is
gone.** ⛔ Left alone.

⚠️ **BUT THE *BUCKET* ROUTING IS DEFINED TWICE — and 2.7 asks for exactly this distinction:**
`_INTRADAY_INTENTS = frozenset({"INTRADAY","COVER_ORDER","BRACKET_ORDER"})` appears at **BOTH**
`capital/position_sizer.py:52-54` **and** `capital/fund_manager.py:100`.
**THEY AGREE TODAY — byte-identical membership.** ⇒ ⭐ **LATENT ARCHITECTURAL DEBT, NOT A LIVE
DEFECT** (two that agree is latent; two that disagree is live).
**CLASSIFICATION: (a) CONFIRMED DEFECT — latent.** Recorded, ⛔ not fixed, ⛔ **and absolutely not
resolved by adding a third definition anywhere.**

### 3. ⭐⭐ IS `product` REACHABLE AT THE CHECK1 CALL SITE? → **IT IS ALREADY BEING READ THERE.**
Not "available on the row" — **CHECK1 itself reads it.**
`orders/order_reconciler.py:1271-1272` (valid at both SHAs):
```python
product = trade["product"]
intent  = _PRODUCT_TO_INTENT.get(product or "", "")
```
⛔⛔ **CORRECTED 05-Aug ~13:0x — MY OWN CITATION ABOVE WAS WRONG ON THE TABLE, AND THE CORRECTION
MATTERS BECAUSE IT BROKE THREE OPERATOR COMMANDS.** The first version of this section said
*"`trades.product` is `TEXT NOT NULL` (`core/schema.sql:326`)"*. **`:326` is the right LINE and the
WRONG TABLE — it is inside `CREATE TABLE orders`, declared at `:313`.**
⭐ **MEASURED: `product` does NOT exist on `trades` at all** — `awk 'NR>=117 && NR<=255 && /product/'`
over the `trades` CREATE TABLE (declared `:117`) returns **ZERO hits**, and no migration adds it
(width: `grep -rn "ALTER TABLE trades" --include=*.py --include=*.sql`, `venv` excluded → **3 hits,
all in `tests/`, all `DROP COLUMN`**).
**HOW THE CODE ACTUALLY GETS IT — `core/state_store.py`, `get_all_open_trades()`:**
```sql
SELECT t.trade_id, … , t.reservation_id,
       o.product,                       -- <-- from ORDERS, not TRADES
       o.order_id AS entry_broker_order_id
FROM trades t
LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = 'ENTRY'
WHERE t.status IN ('OPEN', 'PARTIAL')
```
`get_stuck_exiting_trades()` states *"Same JOIN/columns as get_all_open_trades"* ⇒ **the second call
path's rows carry `product` the same way.**
⇒ ⭐ **THE CONCLUSION IS UNCHANGED AND THE MECHANISM IS NOT: `product` IS in hand at
`_check1_manual_close:1271` — but it arrives by a `LEFT JOIN` on `orders.leg='ENTRY'`, not as a
column of `trades`.** Any future remedy consults `core.constants.PRODUCT_TO_INTENT` and **that
join**, and needs no new source of truth.
🔴 **AND THE CONSEQUENCE THE JOIN CARRIES, WHICH A COLUMN WOULD NOT: IT IS A *LEFT* JOIN.** A trade
whose `ENTRY` order row is missing yields **`product IS NULL`** ⇒ **invisible to any
`WHERE product='CNC'` filter, and `_PRODUCT_TO_INTENT.get(product or "", "")` at `:1272` maps it to
the empty intent.** ⇒ **a second, independent route to a false "no delivery trades" — one that no
status filter can catch.**
**CLASSIFICATION: (c) ASSUMPTION DISPROVED** *(my own, from this document's first version)* — and the
`NULL`-product route is **(a) CONFIRMED DEFECT — latent**, disclosed here, ⛔ not fixed, ⛔ not a new
row.

---

## §T1 — CLAIMS A–F, EACH TESTED AGAINST SOURCE

### CLAIM A — *CHECK1 calls `get_active_gtt_states()`, which is `SELECT * FROM gtt_state WHERE status = 'ACTIVE'`*
**MEASURED.** `orders/order_reconciler.py:855` → `self._store.get_active_gtt_states()`.
`core/state_store.py:2247-2250`:
```python
def get_active_gtt_states(self) -> List[sqlite3.Row]:
    """All ACTIVE gtt_state rows. ..."""
    return self.fetch_all("SELECT * FROM gtt_state WHERE status = 'ACTIVE'")
```
⭐ **The read is wrapped in a bare `except Exception: _delivery_rows = []` (`:856-857`)** — a
`gtt_state` read failure yields an **EMPTY** delivery set, i.e. **fails toward NOT skipping**.
**CLASSIFICATION: (b) CONFIRMED DESIGN** for the query; the fail-open direction is noted at §T4-N2.

### CLAIM B — *it builds a set of `trade_id` values from those ACTIVE rows*
**MEASURED.** `order_reconciler.py:858-859`:
```python
delivery_trade_ids = {r["trade_id"] for r in _delivery_rows}
delivery_symbols   = {r["symbol"]   for r in _delivery_rows}
```
**Both are built. `trade_id` gates CHECK1; `symbol` gates CHECK2.**
**CLASSIFICATION: (b) CONFIRMED DESIGN.**

### CLAIM C — *it skips ONLY when the trade's own `trade_id` is in the set; keyed on `trade_id`, not symbol, not broker; an ACTIVE row with a different/NULL `trade_id` also fails to skip*
**CONFIRMED IN FULL.** `order_reconciler.py:870-878`:
```python
for trade in local_trades:
    if trade["trade_id"] in delivery_trade_ids:
        continue                      # SLICE2.5-P2: delivery trade -> CncGttMonitor owns it
    symbol = trade["symbol"]
    bp = broker_pos.get(symbol)
    if bp is None:
        actions.append(self._check1_manual_close(trade))     # CHECK 1: MANUAL_CLOSE (RC5a)
```
- **SKIPPED when:** the open trade's `trade_id` is a member of `delivery_trade_ids`.
- **NOT SKIPPED when:** it is not — and then `bp is None` (no broker position for that symbol)
  routes it into `_check1_manual_close`.
- **ACTIVE row with `trade_id` NULL:** the set contains `None`; a real trade's `trade_id` is never
  `None`, so **no match → NOT skipped.**
- **ACTIVE row whose `trade_id` belongs to a DIFFERENT trade:** membership fails → **NOT skipped.**
- **Does any part consult the broker, the symbol, or the product?** **No.** The skip predicate is
  set-membership on `trade_id` alone. *(The broker is consulted separately, at `:874`, to decide
  `bp is None` — that is the trigger, not the skip.)*
⇒ ⭐⭐ **"Is there an ACTIVE row?" IS NOT A SUFFICIENT CHECK. The row must be keyed to THAT trade.**
**CLASSIFICATION: (b) CONFIRMED DESIGN** — the predicate is precise and intentional; the hazard is
that a human check phrased as *"the GTT passed"* does not test it.

### CLAIM D — *the not-skipped branch fires MANUAL_CLOSE, which cancels broker orders and releases capital*
**CONFIRMED.** Full chain at §T3. Summary of the ordered effects inside
`_check1_manual_close` (`:1183`+):
1. **§D deferral gate** `:1199-1217` — **inert at the default `0.0`** (`_bound > 0.0` guard at
   `:1209`). ⭐ Asserted by tripwire tests, not assumed (`:1202-1205`).
2. **`mark_trade_manually_closed(trade_id)`** `:1220` — the **idempotent claim**, deliberately
   first; it is the double-release guard (`:1251-1252`).
3. If already terminal → returns `tier="COSMETIC"`, **no release** (`:1230-1244`).
4. **`_cancel_orphaned_orders_for_trade(...)`** `:1264` — **broker cancellation. ⛔ NOT
   idempotent, and it DESTROYS the mid-fill evidence** (`:1253`, `:1260`).
5. Reads `product` / derives `intent` `:1271-1272`; resolves a real exit price from broker trades
   `:1287`; classifies the closure from evidence `:1315`.
6. **Capital release**, keyed by the derived `intent` (§T3 link 5).
**CLAIM D IS NOT REFUTED.** ⛔ **The cancel is the irreversible step and it is ordered AFTER the
idempotent claim — which is correct, and is why the claim cannot be read as "harmless".**
**CLASSIFICATION: (a) CONFIRMED DEFECT *as a hazard on a delivery trade*** — the mechanism is
correct for its intended (intraday) subject and destructive when applied to a held CNC position.

### CLAIM E — *on T+1 a CNC holding leaves `positions()` and appears in `holdings()`; CHECK1 reads `positions()`*
**CONFIRMED.** `broker/zerodha_adapter.py:928` —
`def get_holdings(self) -> list[Holding]:` docstring: ***"Carried (T+1+) CNC delivery holdings.
qty = settled + t1 …"***. `get_positions()` at `:1182`.
**Which does CHECK1 read?** `positions()` only — `order_reconciler.py:839`
(`raw_positions = self._adapter.get_positions()`), which builds `broker_pos` at `:866`.
**`get_holdings()` call count in `orders/order_reconciler.py`: ZERO** (width: `grep -n
"get_positions()\|get_holdings()" orders/order_reconciler.py` → 7 hits, all `get_positions`, at
`:742 :745 :839 :2913 :2923 :2966 :2971`).
⇒ **On T+1 the held CNC symbol is absent from `broker_pos` ⇒ `bp is None` ⇒ the CHECK1 branch.**
**CLASSIFICATION: (b) CONFIRMED DESIGN** for the adapter split (it mirrors Kite); **(a) CONFIRMED
DEFECT** for the reconciler being blind to the second half of it.

### CLAIM F — *`gtt_state` held ZERO rows before today ⇒ this protection has NEVER executed*
⛔ **CANNOT BE CONFIRMED FROM HERE — and the honest answer is bounded.**
**What I CAN establish (code + history):** the writers are exactly three, all in production code —
`orders/cnc_gtt.py:210` · `orders/cnc_gtt.py:217` · `orders/cnc_gtt_monitor.py:232` — all calling
`core/state_store.py:2182 insert_gtt_state` (`INSERT INTO gtt_state` at `:2202`). Width:
`git ls-files '*.py' | xargs grep -n "insert_gtt_state\|INSERT INTO gtt_state"` → **12 hits, of
which 3 are production call sites, 1 the definition, and 8 are under `tests/`.**
⇒ **A row could only ever have been written by the CNC GTT placement path**, which is gated by the
delivery lock that was `false` until 04-Aug 19:08.
⛔ **What I CANNOT determine without a live read:** whether `gtt_state` is empty *now*, and whether
today's fills wrote rows. **The local PC copy of `data_store/trading_system.db` is mtime
2026-08-03 16:08 — it predates both the flip push and today's boot and CANNOT contain a row from
today.** It was not queried.
**CLASSIFICATION: (d) CANNOT DETERMINE.** **Access that would settle it:** a read-only
`SELECT gtt_id, trade_id, symbol, status, created_at FROM gtt_state ORDER BY created_at` on the
**live VM DB**, tonight. **Listed as an evening operator step.**

---

## §T2 — ⭐⭐ THE FINDING THIS SESSION ADDS: THE DELIVERY EXCLUSION IS APPLIED AT 3 OF 4 PATHS

**`_check1_manual_close` has FOUR entry points, not one.** Width: `grep -n "_check1_manual_close"
orders/order_reconciler.py` → definition `:1183`, call sites `:878`, `:1531`, `:2389`.

| # | call site | reached from | delivery-guarded? | guard |
|---|---|---|---|---|
| 1 | **`:878`** | the CHECK1/3/4/5 loop over `get_all_open_trades()` | ✅ **YES** | `:871-872` `trade_id in delivery_trade_ids` |
| 2 | 🔴 **`:1531`** | **`_check_stuck_exiting(broker_pos)`**, called at **`:944`** | ⛔ **NO** | *(none — see below)* |
| 3 | **`:2389`** | the M-O2 partial-close handler, when `broker_qty <= 0` | ⛔ **no guard of its own** | reached only from the guarded loop |

**The other two places the exclusion IS applied**, for completeness: `:916` CHECK2, keyed on
**`delivery_symbols`** (symbol, not trade_id); `:967-970` the CHECK9/G5b working set, keyed on
`trade_id`.

### 🔴 SITE 2 IS STRUCTURALLY UNGUARDED — MEASURED, CERTAIN
`_check_stuck_exiting` is invoked at `:944`, **inside** the `raw_positions is not None` guard but
**outside every delivery filter**. It receives only `broker_pos`, then **re-queries its own working
set** at `:1516` — `self._store.get_stuck_exiting_trades(cutoff)` — with **no delivery exclusion at
all**, and at `:1526-1531`:
```python
bp = broker_pos.get(symbol)
broker_qty = abs(bp.qty) if bp is not None else 0
if broker_qty == 0:
    actions.append(self._check1_manual_close(trade))
```
⇒ **A CNC trade in `EXITING` status, stuck beyond `stuck_exiting_timeout_minutes` (default 30,
`:1513`), whose symbol is absent from `positions()` — which is exactly what T+1 produces — reaches
`_check1_manual_close` REGARDLESS of whether it has an ACTIVE `gtt_state` row.**

**Why this matters relative to the module's own stated intent** — `:849-853`, verbatim:
> *"delivery (CNC) trades with an ACTIVE overnight GTT are managed by CncGttMonitor (holdings + GTT
> aware), NOT by the position/SL/exit checks below — a carried CNC holding lives in holdings() not
> positions(), so CHECK1 would wrongly mark it CLOSED_MANUAL…"*

`_check_stuck_exiting` **is one of "the checks below"** and does not honour that exclusion.

### ⚖️ REACHABILITY — **AND I AM NOT GOING TO OVERSTATE IT**
For site 2 to fire on a delivery trade, that trade must first be in `EXITING`. The writers are
three, and **none of them filters by product** (all key on `trade_id` alone):
`capital/kill_switch.py:1478` (`_mark_trade_exiting`, called `:1751 :1811 :1835 :2001 :2064`) ·
`orders/order_placer.py:3816` (called `:3893`, `:3959` — the emergency-exit path) ·
`orders/structure_exit_manager.py:349-355` (`_mark_exiting`).
⭐ **A structural point in the OPPOSITE direction, recorded because it is the strongest evidence
against alarm:** `core/constants.py:42` — `EMERGENCY_FLATTEN_PRODUCTS = frozenset({"MIS","CO"})`,
with `:40-41` stating CNC *"is flattened LOUDLY (CRITICAL) by the emergency sites"* only as the
unrecognised-product case. And `get_stuck_exiting_trades`'s own docstring says **"EXITING is set by
a HARD_KILL / emergency flatten (Bug A, FIX-190)"** — a path Q4 establishes spares delivery.
⇒ **I could not close the question of whether any live path marks a CNC trade `EXITING`, and
tracing `structure_exit_manager` further would cross the intraday scope lock.**
**CLASSIFICATION: (d) CANNOT DETERMINE.** **What would settle it — two things, both cheap:**
**(i)** a code trace of `structure_exit_manager`'s product scope (does it manage CNC trades at
all?); **(ii)** a read-only `SELECT trade_id, product, status FROM trades WHERE product='CNC'` on
the live DB tonight — **if neither CNC trade is `EXITING`, site 2 is unreachable TODAY and the
finding is latent.**
⛔ **The COVERAGE ASYMMETRY itself is not in doubt and does not depend on (i) or (ii).** It is a
measured structural fact and should be recorded as one.

---

## §T3 — ⭐⭐ THE DEPENDENCY CHAIN (deliverable in its own right — a designer reads THIS, not the prose)

All lines valid at **both** `0197923` and HEAD (§ answer 1).

| # | link | function / line | reversible? | real money or record? | who else reads this link |
|---|---|---|---|---|---|
| 1 | **`gtt_state` read** | `order_reconciler.py:855` → `state_store.py:2250` `SELECT * FROM gtt_state WHERE status='ACTIVE'` | ✅ read-only | record | `cnc_gtt.py:174` · `cnc_gtt_monitor.py:126` · `order_reconciler.py:855` · `scripts/t2_cnc_gtt_realtest.py:498,520` (**width: 9 files, 4 production**) |
| 2 | **the `trade_id` set** | `order_reconciler.py:858` (`delivery_trade_ids`), `:859` (`delivery_symbols`) | ✅ in-memory | record | consumed `:871` (CHECK1/3/4/5), `:916` (CHECK2, symbol-keyed), `:969` (CHECK9/G5b) |
| 3 | **skip / no-skip** | `:871-872` `continue` · trigger `:874-878` `bp is None` | ✅ decision only | — | **site 2 `:1531` bypasses this link entirely** (§T2) |
| 4 | **terminal DB claim** | `mark_trade_manually_closed(trade_id)` `:1220` | ⚠️ **a status write; idempotent but not undone** | **record** — deliberately FIRST as the double-release guard (`:1251-1252`) | the terminal-state guard; `get_all_open_trades` stops returning the row |
| 5 | **broker order cancellation** | `_cancel_orphaned_orders_for_trade(trade_id, symbol, log)` `:1264` | ⛔⛔ **NO — and it DESTROYS THE MID-FILL EVIDENCE** (`:1253`, `:1260`) | **REAL — live broker orders** | the OCO sibling logic; `orphan.mid_fill` feeds the classifier `:1308` |
| 6 | **product / intent derivation** | `:1271-1272` `product = trade["product"]`; `_PRODUCT_TO_INTENT.get(...)` | ✅ | record | the canonical constant, 6 importers (§ answer 2) |
| 7 | **exit-price resolution** | `_resolve_exit_price(...)` `:1287`; source labelled `broker_trades` or `entry_proxy` `:1290` | ✅ read-only | **reads broker trades()** | — |
| 8 | **closure classification** | `classify(Evidence(...))` `:1315`, from `orders/closure_classifier.py` (imported `:100`) | ✅ | record | the `closure_source` column (the `RMS/MANUAL` mislabel work) |
| 9 | **capital release** | keyed by the derived `intent` from link 6 | ⛔ **releases reserved capital** | **REAL — capital state** | `FundManager`; the daily-loss base |
| 10 | **disposition + alert** | `ReconciliationAction(check_name="MANUAL_CLOSE", …)` `:1236-1244` (`tier="COSMETIC"` on the already-closed path); CRITICAL Telegram per the docstring `:1190` | ⛔ alert is sent | record + operator | the reconciliation summary; `expected_alarms` |

🔴 **THE TWO IRREVERSIBLE LINKS ARE 5 AND 9 — a broker cancellation and a capital release.** Both
sit **after** the idempotent claim (link 4) and **before** any product-aware decision (link 6).
⇒ ⭐ **By the time CHECK1 knows the trade is CNC, it has already cancelled the orders.**

### ⚠️ TWO NOTES A DESIGNER MUST NOT MISS
**N1 — the `product` read at link 6 is DOWNSTREAM of the destructive step.** Product is available
on the row from the start (§ answer 3) but is not consulted until `:1271`, thirteen lines after the
cancel at `:1264`.
**N2 — the `gtt_state` read FAILS OPEN.** `:856-857` catches **every** exception and yields
`_delivery_rows = []`, i.e. an empty skip-set. The comment (`:856`) states the reason: *"gtt_state
read must never break reconcile"*. ⇒ **A `gtt_state` read failure makes every delivery trade
eligible for CHECK1.** **CLASSIFICATION: (b) CONFIRMED DESIGN** — deliberate, reasoned, and the
right call for the reconciler's own availability; ⛔ **recorded because its blast radius changed on
05-Aug when the first real CNC position appeared, and it did not change before.**

---

## §T4 — REGRESSION MAPPING (2.9)

**Callers of CHECK1:** three, enumerated in §T2 (`:878`, `:1531`, `:2389`).
**Consumers of its return value:** every site appends a `ReconciliationAction` to the `actions`
list returned by `reconcile_once()` (`:622`); dispositions are `MANUAL_CLOSE` (`:1237`, `:1477`),
`MANUAL_CLOSE_DEFERRED` (`:1168`), `OWN_LEG_CLOSE` (`:1472`).
**Other readers of `get_active_gtt_states()`** — width: `git ls-files '*.py' | xargs grep -ln` → **9
files** (4 production: `core/state_store.py`, `orders/cnc_gtt.py`, `orders/cnc_gtt_monitor.py`,
`orders/order_reconciler.py`; 1 script; 4 tests).

### ⭐ THE TWO IDENTIFICATIONS THIS CARD ASKED ME TO CONFIRM OR REFUTE
- **"exactly ONE positions-only reader is wrong AND on the capital path (believed CHECK1)"** →
  **CONFIRMED at HEAD.** `:839` `get_positions()` feeds `broker_pos` → `:874` `bp is None` →
  `_check1_manual_close` → links 5 and 9 (broker cancel + capital release). **It is the capital
  path.**
- **"exactly ONE is wrong AND advisory, around `order_reconciler:745`"** → **CONFIRMED at HEAD, and
  the line number HELD.** `:745` is `positions = self._adapter.get_positions()` inside the FIX-008
  startup CNC check, whose own docstring at `:738-740` reads: ***"The check is advisory (WARNING log
  + alert) — it does NOT place exits or call soft_kill, because CNC positions may be intentional
  positions managed outside this system."*** ⇒ **advisory, explicitly, and correct-by-design about
  CNC.** **CLASSIFICATION: (b) CONFIRMED DESIGN.**

### 2.8 — THE Q4 ORDERING CONSTRAINT: **IS THE DOOR OPEN?**
Standing rule: the buy-day product filter must land before anything makes a live component
**holdings-aware**. **PRODUCT-AWARE IS NOT HOLDINGS-AWARE.** Measured for the region a remedy would
touch (`orders/order_reconciler.py`):
- **`get_holdings()` calls that exist there today: 0** (width: 7 `get_positions`/`get_holdings` hits
  in the file, all `get_positions`).
- **raw status string literals that exist there today: 0 added** — the module imports its status
  vocabulary (`_TERMINAL_ORDER_STATUSES` `:103-105`; `EXTERNAL_UNATTRIBUTED`/`OWN_CLOSURE_SOURCES`
  from `core/closure_source` `:95`).
⇒ **Both numbers are 0, and `product` + `PRODUCT_TO_INTENT` are already in scope at `:1271-1272`.**
**Reported as a door-state measurement only — ⛔ no remedy is designed here.**

---

## §T5 — THE TEST LAYER (2.10): **IS IT BLIND BY CONSTRUCTION?**

**Width:** `git ls-files 'tests/*.py' | xargs grep -ln "_check1_manual_close\|check1\|gtt_state"` →
**18 files**, incl. `test_check1_acceptance_rows.py`, `test_check1_classified_close.py`,
`test_check1_deferral.py`, `test_order_reconciler.py`, `test_cnc_gtt_slice25_p1/p2.py`,
`test_cnc_gtt_step4_wiring.py`, `test_paper_carry_blind_spot.py`.

⭐ **I went looking specifically for a third stub-returns-what-it-was-told instance, as instructed.
I did not find one at the function under test:** `grep -n "monkeypatch.*_check1\|_check1_manual_close
*=\|def _check1_manual_close"` over all of `tests/` returns **ZERO hits** ⇒ **no test replaces
`_check1_manual_close`; the CHECK1 tests drive the real function.**
⚠️ **BUT THE STUBBING HAS MOVED ONE LEVEL DOWN, AND THAT IS THE RELEVANT BLINDNESS:**
`tests/unit/test_cnc_gtt_slice25_p2.py:133` defines `def insert_gtt_state(self, **kw):` on a fake
store — *"…but the write blows up"* — i.e. the `gtt_state` **writer** is substituted. A test whose
`gtt_state` contents are whatever the fake was told to hold **cannot distinguish "an ACTIVE row
exists" from "an ACTIVE row exists whose `trade_id` matches"** unless it deliberately constructs the
mismatch.
**CLASSIFICATION: (d) CANNOT DETERMINE** whether any existing test constructs the trade_id-mismatch
case. **What would settle it:** reading the 18 files' assertions in full — **not done here, because
it is test-layer archaeology beyond a Step-1 measurement, and saying so is more useful than a
half-swept claim.**

### 2.12 — THE ACCEPTANCE CRITERION, CARRIED FORWARD VERBATIM
> **"Any CHECK1 remedy must PROVE, with a test that could have gone red, that INTRADAY (MIS/CO)
> behaviour is BYTE-FOR-BYTE UNCHANGED while DELIVERY (CNC) behaviour is corrected. A remedy that
> improves delivery by altering intraday is not a fix — it is a trade."**

⭐ **IS SUCH A TEST CONSTRUCTIBLE TODAY? — YES, and this answer is more useful than the criterion.**
Three measured reasons:
1. **The discriminator already exists in the data.** ⛔⛔ **CORRECTED 05-Aug ~11:5x — SECOND
   OCCURRENCE OF THE SAME DISPROVED CLAIM, IN THIS SAME DOCUMENT, SURVIVING A FIX APPLIED AT §
   answer-3 TWO HOURS EARLIER.** The original text here read *"`trades.product` is `NOT NULL`
   (`schema.sql:326`)"* — **there is no `trades.product` column**; `:326` is inside `CREATE TABLE
   orders` (declared `:313`). ⭐ **THE CONCLUSION IS UNCHANGED AND THE MECHANISM IS NOT** — the
   discriminator is reachable, but via **`LEFT JOIN orders o ON o.trade_id = t.trade_id AND
   o.leg = 'ENTRY'`**, which is how `get_all_open_trades()` supplies it and how
   `_check1_manual_close:1271` comes to hold it. ⚠️ **AND THE TEST MUST HANDLE `product IS NULL`** —
   a trade with no `ENTRY` order row gets a NULL product, which `:1272` maps to the empty intent; a
   test that only builds MIS-vs-CNC would never exercise that third case.
   **CLASSIFICATION: (c) ASSUMPTION DISPROVED — second occurrence, same root.**
   ⭐⭐ **AND THIS IS LEDGER #8's FINDING ARRIVING ON MY OWN DOCUMENT, WHICH IS WHY IT IS RECORDED
   RATHER THAN QUIETLY EDITED: A FIX AT ONE SITE IS NOT PERMANENT.** Fourth instance in this
   campaign. ✅ **SWEEP RUN, WIDTH STATED, SO THE "DONE" IS FALSIFIABLE:** `git ls-files -- 'docs/*'
   | xargs grep -c "trades\.product"` → **3 hits total** — this one (**the only live error**), the
   §answer-3 correction block quoting the claim in order to disprove it, and the operator card's
   REVISION table describing it as the fault it fixed. ⇒ **the other two are correct usage; there is
   no third live site.**
   A test can build one MIS trade and one CNC trade through the **real** function and assert
   divergent outcomes — the plumbing exists, but it is a join, not a column.
2. **The real function is not stubbed** (0 hits, above), so such a test would exercise the actual
   predicate, not a mock's return value.
3. **The precedent exists in-tree:** `tests/unit/test_reconciler_product_filter.py` and
   `tests/unit/test_kill_switch_product_filter.py` are exactly this shape, and
   `tests/unit/test_h5_killswitch_sweep_product.py` pins a product-awareness regression.
⛔ **ONE CAVEAT THAT MUST RIDE WITH THAT YES (practices §V1): PAPER CANNOT VALIDATE IT.** Paper nets
by **SYMBOL**; live Kite nets per **(symbol, product)** ⇒ **a paper drill of any product-keyed change
is vacuously GREEN.** ⇒ **Validation must be by CONSTRUCTION or by a targeted unit test driving the
REAL helper — ⛔ a paper run is not evidence and must not be offered as any.**
*(`tests/unit/test_paper_carry_blind_spot.py` and `test_paper_product_fidelity.py` exist and appear
to encode exactly this hazard — not read in full here.)*

---

## §T6 — CLASSIFICATION SUMMARY (C12 — four buckets, nothing unclassified)

| # | finding | bucket |
|---|---|---|
| 1 | CHECK1 is **byte-identical** at `0197923` and HEAD; no unpushed commit can reach it | **(b) CONFIRMED DESIGN** |
| 2 | CLAIM A — the ACTIVE-row query is exactly as stated | **(b) CONFIRMED DESIGN** |
| 3 | CLAIM B — both a `trade_id` and a `symbol` set are built | **(b) CONFIRMED DESIGN** |
| 4 | CLAIM C — skip keyed on `trade_id` alone; NULL or mismatched `trade_id` does NOT skip | **(b) CONFIRMED DESIGN** *(the predicate is right; the human phrasing "the GTT passed" is what fails)* |
| 5 | CLAIM D — the not-skipped branch cancels broker orders **and** releases capital, irreversibly | **(a) CONFIRMED DEFECT** *(as a hazard applied to a held CNC position)* |
| 6 | CLAIM E — T+1 moves CNC to `holdings()`; the reconciler calls `get_holdings()` **zero** times | **(a) CONFIRMED DEFECT** *(adapter split itself is (b))* |
| 7 | CLAIM F — has the protection ever run? | **(d) CANNOT DETERMINE** — needs a live `gtt_state` read |
| 8 | 🔴 **`_check_stuck_exiting` (`:944`→`:1531`) reaches CHECK1 with NO delivery guard** | **(a) CONFIRMED DEFECT — coverage asymmetry, structurally certain** |
| 9 | …its **reachability for a CNC trade today** | **(d) CANNOT DETERMINE** — needs (i) `structure_exit_manager` product scope, (ii) live `trades.status` for CNC rows |
| 10 | the `gtt_state` read **fails open** (`:856-857`) — a read failure exposes every delivery trade | **(b) CONFIRMED DESIGN** *(deliberate; blast radius changed 05-Aug)* |
| 11 | `_INTRADAY_INTENTS` defined **twice** (`position_sizer.py:52` · `fund_manager.py:100`), **agreeing** | **(a) CONFIRMED DEFECT — latent** |
| 12 | `order_placer.py:423` "keep in lockstep with `order_reconciler._PRODUCT_TO_INTENT`" — the duplication it guards is already consolidated | **(c) ASSUMPTION DISPROVED** |
| 13 | the AST digest `e19bccce…a171d21` cited for `c5c1926` **does not reproduce** (recomputed `ef355295…`; 4 dump variants tried) — the CLAIM holds, the VALUE does not | **(c) ASSUMPTION DISPROVED** |
| 14 | `:745` is advisory and `:878`/CHECK1 is the capital-path reader — both identifications **held**, line numbers **held** | **(b) CONFIRMED DESIGN** |
| 15 | a CNC position open after 15:17 is **correct** (EOD6 carve-out); the kill's blindness to delivery is **correct** (Q4) | **(b) CONFIRMED DESIGN — ⛔ do not "fix"** |

---

## ⛔ NOT DESIGNED HERE — open questions surfaced, deliberately unanswered

1. **Should the delivery exclusion be applied inside `_check_stuck_exiting`, or hoisted to a single
   place all four entry points pass through?** *(A single choke point is one answer; it is not
   proposed, costed or ruled on here.)*
2. **Should the `product` read move ABOVE the broker cancellation** (links 5→6, §T3-N1), so CHECK1
   knows the trade is CNC before it destroys evidence?
3. **Should the `gtt_state` fail-open (§T3-N2) stay fail-open now that a real CNC position exists?**
   *(Fail-fast vs degrade — and the discriminator rule cuts both ways here.)*
4. **Is the skip's `trade_id` key the right key at all**, given an ACTIVE row with a mismatched
   `trade_id` silently fails to protect?
5. **What closes the two `_INTRADAY_INTENTS` definitions** — and which module owns the survivor?
6. **Does `structure_exit_manager` manage CNC trades?** *(Reachability input for finding 9. Untraced
   here: it would cross the intraday scope lock.)*
7. **Do any of the 18 CHECK1/`gtt_state` tests construct the trade_id-mismatch case**, or is that
   case untested?
8. **Does the "book flat" pre-push gate still apply to a book that is correctly non-flat?** —
   ⛔ **Rama's ruling. Recorded in the deploy ledger, not answered.**

---

**⛔ NOTHING IN THIS DOCUMENT AUTHORISES A CHANGE.** No code was modified, no VM or broker was
contacted, no live DB was read, no register row was created (**231 stands**), and the intraday path
was not investigated.
