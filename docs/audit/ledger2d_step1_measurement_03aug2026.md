# Ledger #2d — STEP 1 MEASUREMENT — 03-Aug-2026

**Status: `<MEASURED — NOT IMPLEMENTED, NOT AUTHORISED>`. Halted at the card's gate.**
Measured against deployed code `d6c298d` / `8519289`. Read-only; no `.py` changed by this record.

---

## 0. ⛔ FIRST FINDING: THERE IS NO #2d CARD

The instruction was *"run Step 1 only from the #2d card."* **No such card exists**, and this
was already known but never acted on — the #3 design registration recorded it verbatim:
*"`#2d` still has NO register row."* There is also **no `docs/audit/` design doc** for it.

Its entire definition is one clause in `MASTER_PENDING_01-Aug-2026.md:579`:

> *"Carries two riders: the §R7 `kill_switch` three-site CO defect (**needs its own card**)…"*

pointing at `docs/audit/reconciler_product_filter_build_02aug2026.md` **§R7**. This record
therefore works from §R7 plus the four questions supplied with the instruction. **A future
reader must not mistake this record for the missing card** — a card carries authorisation and
a decision; this carries measurements only.

## 1. THE THREE SITES — §R7's LINE NUMBERS STILL HOLD

Re-verified at `d6c298d` (they were measured 02-Aug and a 65-commit push has landed since):

| Site | Line | Context | `intent` source |
|---|---|---|---|
| **A** | `capital/kill_switch.py:1629` | local pass over open trades | `:1599` `_PRODUCT_TO_INTENT.get(raw_product, "INTRADAY")` |
| **B** | `capital/kill_switch.py:1708` | broker-position sweep | `:1698` `_PRODUCT_TO_INTENT.get(sweep_product, "INTRADAY")` |
| **C** | `capital/kill_switch.py:1789` | retry loop | enclosing scope |

✅ **`variety` appears NOWHERE in `capital/kill_switch.py` — grep count 0**, so every one of the
three defaults to `variety="regular"`.

## 2. Q1 — A CO POSITION UNDER THE BREAKER, ON AND OFF

**Both settings are wrong. Only the failure mode differs.**

- **Coercion ON (today):** `CO` is coerced to `MIS` → the broker **accepts** the order, but it
  **does not net the CO position** → **naked MIS short**. Silent, and it costs money.
- **Coercion OFF:** sends `product="CO", variety="regular"` → **the broker rejects it**
  (Audit 3.1). Loud, and the position stays open.

⇒ There is no configuration of the existing flags that closes a CO position correctly from
these three sites. This is a **missing capability**, not a mis-set flag.

## 3. Q2 — IS `entry_broker_order_id` REACHABLE PER SITE? ⭐ **NOT UNIFORMLY — THE KEY FINDING**

| Site | Reachable? | Why |
|---|---|---|
| **A** `:1629` | ✅ **Yes — one column away.** | The open-trades query (`:1528-1536`) already runs a correlated subquery over `orders o WHERE o.trade_id = t.trade_id AND o.leg IN ('ENTRY','CO')` to fetch `product`. Adding `o.order_id` is one more column in a scan that already happens. `orders.order_id` **is** the broker id (`:1461-1462`, `schema.sql:271`). |
| **C** `:1789` | ✅ Yes | Operates on retry entries that carry `trade_id`. |
| **B** `:1708` | ⛔ **Structurally NO** | It iterates **broker** positions. `:1543-1546` states the reason it exists: *"a broker position can exist with no local OPEN/PARTIAL/PENDING_FILL trade."* For exactly that case there is **no local row to join**, so no parent id. |

⛔⛔ **THE DESIGN CONSEQUENCE: "mirror `eod_squareoff` at all three sites" IS NOT ACHIEVABLE AS
STATED.** Sites A and C can adopt the reference implementation; **Site B cannot** for the
orphan-at-broker case and needs a *different* answer. Any design that assumes one uniform fix
across the three is wrong by construction. This is the same class of Step-1 finding that moved
#2c, #2c-R and #2e.

## 4. Q3 — CAN EOD'S CO PATH BE EXTRACTED WITHOUT CHANGING `eod_squareoff`?

**Only a narrow seam, and the boundary matters.**

- ✅ **Extractable:** the `is_co` determination (`:1165`), the broker call
  `cancel_order(entry_broker_id, variety="co")` (`:1188-1190`), and the success/reason verdict
  (`:1191-1198`).
- ⛔ **NOT extractable:** the bookkeeping around it is EOD-specific — it calls
  `self._mark_exit_failed(trade_id)`, increments EOD counters, and writes an
  `INSERT OR IGNORE INTO orders … leg='EOD'` marker row (`:1210-1219`). Dragging that into the
  kill path would file a **kill** exit as an **EOD** exit.

⇒ A shared helper must stop at **"cancel the CO bracket, report success/reason"**, leaving each
caller its own bookkeeping. Under that boundary `eod_squareoff`'s behaviour is unchanged.

## 5. Q4 — CANCEL-DURING-KILL FAILURE MODES

`_cancel_trade_resting_exits` (`:1465`) calls `self._adapter.cancel_order(oid)` — **no
`variety`**, so `"regular"`. For a CO bracket that is the wrong variety.

The failure path (`:1466-1471`) logs a **WARNING** and `continue`s; the flatten then proceeds to
`place_order`. ⇒ **a failed cancel does not stop the reverse order.** Combined with §2 that
gives: resting exit possibly still live **and** a reverse order placed. The degrade is
deliberate (`:1451-1453`: *"must never crash the flatten"*), so this is a **severity** question,
not a crash — but it is not visible above WARNING.

## 6. ⚠️ A NEAR-MISS, RECORDED BECAUSE IT WOULD HAVE BEEN A FALSE ALARM

`entry_broker_order_id` is **absent from `core/schema.sql`** and the **live VM DB rejects it**
(`no such column: entry_broker_order_id`), while `eod_squareoff.py:1157` reads exactly that key.
That looks like §R7's "reference implementation" being dead in the same way `kill_switch`'s old
`broker_order_id` was (`:1461-1464`).

✅ **It is not.** It is an **ALIAS**: `core/state_store.py:1106` — `o.order_id AS
entry_broker_order_id` (also `:1326`, `:1366`), via `get_open_intraday_positions()`.
**§R7 corollary 2 HOLDS: `eod_squareoff` is clean.**

⭐ Recorded because the check is the point: a column-existence grep would have "proved" a
load-bearing claim false. **Verify the premise before reporting the finding.**

## 7. WHAT IS STILL OPEN (⛔ none of it decided here)

1. **Site B's answer** — the only genuinely unsolved piece. A broker position with no local
   trade row has no parent id; options are not enumerated here.
2. **Whether the coercion should change at all** — §2 shows both settings are wrong for CO, so
   this is not a flag flip.
3. **`_cancel_trade_resting_exits`'s variety** — whether it should learn `variety="co"`, and
   whether a failed CO cancel should still permit the reverse order (§5).
4. **Reachability today** — CO is doubly dormant (§R8's unpark trigger is unmet), so this is
   **LATENT**, not live. It is a **hard gate on enabling CO**, not on the flip.

⛔ **HALT. No implementation. The extraction shape needs review before any code, and §3 changes
what that shape can be.**

---

# ADDENDUM — STEP 1b: THE EXTRACTION SHAPE, SETTLED — 04-Aug-2026, 00:3x–01:1x IST

**Status: `<MEASURED — STILL NOT IMPLEMENTED, STILL NOT AUTHORISED>`.** Read-only; **0 `.py`
changed.** Authorised as read-only prep only (ChatGPT, 04-Aug 00:35, §G2), the #2d card's own
`08:15`-boot precondition being unmet at the time of writing.

**Measured at `b791b457935962f8a1597579b42ee45d219a15e2` (`b791b45`).**
⛔ Everything above this line was measured at `d6c298d` and is **kept verbatim per §G4** — it is
not rewritten, and §1's line numbers are superseded by §A1 below rather than corrected in place.

## A1. ⛔ EVERY `kill_switch.py` LINE NUMBER ABOVE IS STALE BY EXACTLY +12

`4149263` (ledger #9's 15:15 string pair) landed `+13/-1` at `@@ -612,0 +613,11 @@` — **entirely
above all three sites.** Re-measured at `b791b45`:

| | measured @ `d6c298d` (§1, §3, §5) | **live @ `b791b45`** |
|---|---|---|
| Site A — `place_order`, local pass | `:1629` | **`:1641`** |
| Site B — `place_order`, broker sweep | `:1708` | **`:1720`** |
| Site C — `place_order`, retry loop | `:1789` | **`:1801`** |
| intent map, A / B | `:1599` / `:1698` | **`:1611` / `:1710`** |
| open-trades query (D1's premise) | `:1528-1536` | **`:1540-1548`** |
| FIX-181 LAYER A rationale (Site B) | `:1543-1546` | **`:1555-1558`** |
| `_cancel_trade_resting_exits`'s cancel | `:1465` | **`:1477`** |

✅ **`orders/eod_squareoff.py` is UNCHANGED since `d6c298d`** (`git log d6c298d..HEAD --` = empty)
⇒ §4's citations (`:1157`, `:1165`, `:1188-1190`, `:1191-1198`, `:1210-1219`) **all still hold.**
The drift is one file, one uniform offset — ⛔ **which is the benign case; a partial or
multi-file drift would not announce itself, so the rule is RE-MEASURE, never "add the offset."**
Rule recorded as **§M3 second entry** in `docs/campaign_practices.md`.

✅ **D1's load-bearing premise SURVIVES the drift:** the correlated subquery at **`:1542-1544`**
does still scan `orders o … WHERE o.trade_id = t.trade_id AND o.leg IN ('ENTRY','CO')`. The
ruling stands; only the citations were wrong.

## A2. ⚠️ A SECOND NEAR-MISS — `leg="CO"` IS AN EXCEPTION FIELD, NOT A DB WRITE

`core/schema.sql:318-320` permits `leg='CO'` and comments it *"CO = CO-bracket entry leg
(order_protocol_co)"*; `orders/order_protocol_co.py:141` then appears to write exactly that.
If true, **EOD's reference implementation would be dead** — `state_store.py:1109-1111` joins
`ON o.leg = 'ENTRY'` and would never match a CO trade.

⛔ **It is false.** `order_protocol_co.py:141`'s `leg="CO"` is a **field on an
`OrderRejectedError`** raised when the broker returns an empty id — it never reaches the DB. The
real writer is `order_placer._persist_entry_orders:4660-4669`:

| column | value for `CO_PLUS_TGT` | source |
|---|---|---|
| `leg` | **`'ENTRY'`** | `:4663` (literal) |
| `variety` | **`'co'`** | `:4656` `co_variety` ← `order_protocol` |
| `product` | **`'CO'`** | `:4655` `_product_resolver.resolve(intent)` |
| `order_type` | `'SL'` | `:4665` |

⇒ ✅ **§R7 corollary 2 re-confirmed by a second, independent route: `eod_squareoff` is clean and
its CO path is reachable.** And `kill_switch`'s `leg IN ('ENTRY','CO')` is a *superset* — it
tolerates a `leg='CO'` row that the entry path never actually writes.

⭐ **This is §6's lesson a second time, in the opposite direction, in the same file.** §6 recorded
a grep that would have "proved" a live thing dead; this is a schema comment plus a plausible
call-site that would have done the same. **Neither was reported until the writer was found.**

## A3. ⭐⭐ §7 ITEM 3 IS **REFUTED** — `_cancel_trade_resting_exits` NEEDS NO `variety` CHANGE

§5 raised the concern that the helper cancels a CO bracket with the wrong variety. **Measured, it
cannot:**

1. It selects **`leg IN ('SL','TGT')`** only (`:1451`, live) — the CO bracket is `leg='ENTRY'`,
   so it is **never in the result set.**
2. **`CO_PLUS_TGT` writes NO `SL` row at all** — `order_placer:1622-1624` guards the SL leg with
   `result.order_protocol != "CO_PLUS_TGT"` (*"CO_PLUS_TGT has SL bundled into the CO at broker
   side"*), and `_persist_entry_orders` only appends an SL spec `if result.sl_broker_order_id`,
   which `order_protocol_co.py:159` sets to `""`.
3. Its **`TGT` row is genuinely `variety='regular'`** (`:4693`) — `order_placer:1651`: *"both
   LIMIT_TRIPLE and CO_PLUS_TGT place a separate TGT order."*

⇒ For a `CO_PLUS_TGT` trade the helper finds **exactly one row, the TGT, and the default
`"regular"` is CORRECT for it.** ⛔ Teaching it `variety="co"` would make it **wrong**.
**§7 item 3's first half is closed as REFUTED**; its second half (whether a failed cancel should
still permit the reverse order) is untouched and remains open.

## A4. THE SEAM — ✅ EXTRACTABLE WITHOUT ALTERING `eod_squareoff`. THE CARD'S STOP IS NOT TRIGGERED.

The #2d card's item 1 says: *"If extraction cannot be done without altering `eod_squareoff`'s
behaviour, STOP and report."* **Measured: it can.** The answer is a **5-line** seam —
`eod_squareoff.py:1188-1192` — under a contract that **neither logs nor raises**:

```
cancel_co_bracket(adapter, broker_order_id) -> (ok: bool, reason: str)
```

Everything else stays verbatim in each caller. The interleaving is what forces this boundary —
§4 called the bookkeeping "not extractable", and the stronger statement is that the extractable
pieces are **not contiguous**:

| `eod_squareoff` line | disposition |
|---|---|
| `:1165` `is_co = (order_protocol == "CO_PLUS_TGT") and (entry_variety == "co")` | ⛔ **caller's** — see §A5, the inputs do not exist at the kill sites |
| `:1172-1185` missing-id guard → CRITICAL `CO_SQUAREOFF_NO_BROKER_ID` → `raise BrokerError` | ⛔ **caller's** — EOD *raises*, the kill sites must *refuse*; the helper re-guards defensively, EOD's own guard still fires first ⇒ behaviour unchanged |
| **`:1188-1190` `cancel_order(entry_broker_id, variety="co")`** | ✅ **the helper** |
| **`:1191-1192` `getattr(..., "success")` / `getattr(..., "reason")`** | ✅ **the helper** (normalisation only) |
| `:1193-1198` CRITICAL `CO_SQUAREOFF_CANCEL_REJECTED` | ⛔ **caller's** — EOD-specific wording + grep sentinel, asserted by `test_eod_squareoff.py:480` |
| `:1199-1200` `_mark_exit_failed` + `failed += 1` | ⛔ **caller's** |
| `:1208-1234` `leg='EOD'` marker INSERT + `succeeded += 1` | ⛔ **caller's** |

⚠️ **The two grep sentinels are load-bearing and must not move into the helper** —
`CO_SQUAREOFF_CANCEL_REJECTED` is asserted by an existing test (`tests/unit/test_eod_squareoff.py:480`).
A helper that logs would change EOD's alert stream, which *is* the altered behaviour the card
forbids.

## A5. ⛔⛔ THREE RULINGS THE CARD ASSUMED AWAY — ✅ **ALL THREE RULED 04-Aug 00:55 (§G2)**

The card reads: *"Sites A and C — **CO detected** ⇒ obtain the parent id ⇒ cancel path."* All
three of the following sit **upstream of the helper**, so settling the seam did not settle them.
They were raised here unresolved and **ruled the same night**; each ruling is recorded beneath
its measurement, with the reasoning, per §0 item 2.

### R-a · **HOW is CO detected at the kill sites?** The two candidate predicates are NOT equivalent.
EOD's `is_co` is a **two-column** test over `t.order_protocol` and `o.variety`. **Neither column
is fetched by `kill_switch`'s open-trades query** (`:1540-1548` selects `trade_id, symbol,
qty_filled, direction, product`). So:
- **(i) mirror EOD** ⇒ add **`t.order_protocol` AND `o.variety` AND `o.order_id`** — ⛔ **three**
  new columns, not the *"one more column"* §3 anticipated;
- **(ii) use `raw_product == 'CO'`** ⇒ **already in hand at Site A, zero query change**, and it is
  the vocabulary `kill_switch` already speaks (`_EMERGENCY_FLATTEN_PRODUCTS`,
  `_PRODUCT_TO_INTENT`; `core/constants.py:7` maps `"CO" → "COVER_ORDER"`).

⚠️ They can diverge: `product` derives from `intent` and `variety` from `order_protocol` — set
from **different inputs**, two lines apart (`order_placer:4655-4656`). EOD's two-column form is
the conservative one.

> ### ✅ RULED (§G2, 04-Aug-2026 00:55) — **BOTH, IN TWO STAGES. Neither option alone is right.**
> The reason is what the **action** needs: **a bracket cancel is only valid if a bracket exists,
> and a bracket exists iff `variety='co'`** — *not* because the product says CO.
> 1. **GATE on `raw_product == 'CO'`** — already in hand at Site A, **zero query change**, and it
>    is *necessary*. ⛔ **Do NOT add three columns to the query for the common path.**
> 2. **CONFIRM `variety='co'` by a targeted lookup, for gated rows only**, before cancelling.
>    CO is dormant, so that lookup **costs nothing in practice and everything in correctness.**
>
> ⭐ **AND MAKE THE DIVERGENCE LOUD, NOT SILENT.** `product='CO'` with `variety != 'co'` means
> **there is no bracket to cancel** ⇒ **CRITICAL + refusal.** ⛔ **Never a fallthrough to a
> reverse order** (Audit 3.1). §A2 measured that the two are set two lines apart and therefore
> agree *today* — **"they agree in practice" is precisely the class of claim this campaign keeps
> disproving**, so the check earns its place as a free detector rather than resting on that
> agreement.

### R-b · **Does a CO trade handled at Site A join `handled_symbols`?** Both answers cost something.
- **Not added** ⇒ the broker sweep (`:1678`) still sees the position — a bracket cancel is not
  instantaneous at the broker — and under **D1 Site B refuses + CRITICALs a position Site A just
  correctly handled.** A false CRITICAL, on the kill path, on the item whose purpose is to stop
  false alarms.
- **Added** ⇒ a same-symbol **MIS** row is skipped by the sweep — precisely the hazard
  `:1592-1596` documents for the CNC case (*"Kite `positions()` is per-product, so this symbol may
  ALSO hold a live MIS row the sweep below must still flatten"*).

> ### ✅ RULED (§G2, 04-Aug-2026 00:55) — **NO. ⛔ DO NOT ADD.**
> ⭐ **The two costs are not comparable, and that is the whole ruling:**
> **not added ⇒ a duplicate CRITICAL — NOISE.** **Added ⇒ a live intraday position left
> unflattened during a HARD_KILL — a MONEY-PATH FAILURE.** ⇒ **Take the noise.**
>
> ⭐ **This is not a new trade-off — it is the ruling #2b already made for the CNC spare, for the
> same reason, at the same line range** (`:1592-1596` documents it). ⇒ **consistency, not a fresh
> judgement call.**
>
> Two obligations ride with it:
> 1. **Site B's CO refusal message must account for the in-flight case** — *"a bracket cancel may
>    already be in flight from the local pass"* — so a duplicate alert **informs rather than
>    misleads.** (⛔ A duplicate that reads as a second, unrelated failure is worse than no
>    duplicate; this is the difference between accepted noise and manufactured confusion.)
> 2. **The duplicate alert is registered as an ACCEPTED RISK** — `campaign_practices.md` **§AR8**,
>    with its reopen condition: *if CO ever becomes live and the duplicate proves to cause real
>    operator confusion.*

### R-c · **Where does the CO branch go in Site A's sequence?**
Recommendation, with the reason: **before `:1617` `_cancel_trade_resting_exits` and before
`:1625` `determine_close_direction`.** The second is not stylistic — `determine_close_direction`
→ `broker_net_qty` is **#2e's territory**, and the card's own stop line is *"if you find yourself
reasoning about `broker_net_qty`, you have crossed into #2e."* **Branching before `:1625` avoids
#2e by construction rather than by discipline.** ⚠️ Per §A3 the TGT cancel is still wanted for a
CO trade, so "before `:1617`" means **reordered, not skipped** — that ordering is part of the ruling.

> ### ✅ RULED (§G2, 04-Aug-2026 00:55) — **CONFIRMED. Before `determine_close_direction`.**
> Accepted as **structural, not stylistic**: `determine_close_direction` → `broker_net_qty` is
> #2e's stop line, so branching before it **avoids #2e by construction rather than by
> discipline.** ⭐ **Same shape as #2c-R's site-local branch placed before the membership test —
> and discipline is the thing that fails at 2am, so prefer construction every time.**
> ⛔ **RECORD THE REORDER EXPLICITLY IN THE CODE COMMENT**: per §A3 the TGT cancel is still
> wanted, so this is **reordered, not skipped** — *"or a later reader will read the reorder as a
> removal."*

## A6. WHAT §7 NOW LOOKS LIKE

| §7 item | status after Step 1b |
|---|---|
| 1. Site B's answer | **CLOSED by D1** — refuse + CRITICAL escalate |
| 2. Whether the coercion changes | **OPEN**, untouched |
| 3. `_cancel_trade_resting_exits`'s variety | ⭐ **first half REFUTED (§A3)**; second half open |
| 4. Reachability — LATENT | **UNCHANGED.** ⭐ Re-confirmed by `core/constants.py:7`: `CO → COVER_ORDER`, coerced by `force_intraday_only`. Still a hard gate on enabling CO, **not on the flip.** |
| — | **NEW: R-a, R-b, R-c (§A5) — ✅ ALL THREE RULED 04-Aug 00:55.** |

## A7. STATUS AFTER STEP 1b — WHAT REMAINS BETWEEN HERE AND CODE

✅ **The design is complete.** Seam boundary settled (§A4), detection ruled (R-a), sweep
interaction ruled (R-b), sequence ruled (R-c), and one Step-1 item closed as refuted (§A3).

⛔ **The HALT is now purely the card's TIMING gate, no longer a design gap.** The remaining
precondition is unchanged and unmet at the time of writing: **Tuesday's 08:15 boot confirmed
clean.** No `.py` before that.

**Carried into implementation, so it is not rediscovered:**
1. The helper **neither logs nor raises** (§A4) — the two grep sentinels stay in their callers,
   and `test_eod_squareoff.py:480` asserts one of them.
2. **`_cancel_trade_resting_exits` is do-not-touch — but for the reason in §A3, not the reason
   the card gave.** ⛔ Its `variety="regular"` is *correct*; changing it would be a regression.
3. R-a's divergence detector, R-b's in-flight wording, and R-c's *"reordered, not skipped"*
   comment are **deliverables, not commentary** — each exists to stop a specific future misread.
4. **Label ceiling per §R11 unchanged:** `<BUILT>` → `<DEPLOYED>`/dormant-armed, ⛔ **never
   `<VERIFIED LIVE>`** — CO is doubly dormant and paper is vacuous here (§V1).
