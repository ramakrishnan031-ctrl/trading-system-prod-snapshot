# The LIMIT_TRIPLE stop-modify path — can the stop be trailed on the current order type?

**Investigation — READ-ONLY.** 2026-07-24 (IST). Deployed HEAD: `bc75406`. VM DB `mode=ro&immutable=1` (zero-trace); VM logs read-only. **No fix, no config change, no flag flip, no design, no deploy.** Feeds Rama's decision 5A (the order-protocol substitution). The exit-policy netting simulation (5B) is **not** authorised and was not run.

Companion to `trailing_stop_never_fired_2026-07-24.md`. Keeps the **stop** and the **target** separate throughout (§STOP-vs-TARGET).

---

## VERDICT (answer the two questions in the first three lines)

> **1. Trailing the stop does NOT require Cover Orders** — a LIMIT_TRIPLE SL is a regular, modifiable order and the `modify_order` capability exists at the adapter (`zerodha_adapter.py:950`). **But it is neither live-capital-safe today nor a flag-flip:** the as-built LIMIT_TRIPLE stop-mover (`BreakevenManager`) is **never constructed in main.py** (git-confirmed: zero references, ever), never wired to the candle feed, and its SL lookup selects a **column that does not exist** (`broker_order_id`). Three independent code defects, each blocking it alone.
> **2. `modify_order` has NEVER executed against the real broker in production** — 0 calls in the logs (23-Jun→24-Jul), 0 registrations to any exit engine, 0 in the DB counters (`sl_trail_count` / `smart_tgt_state` / `superseded_by` all 0). Its only 3 callers are the three dark exit engines; `eod_squareoff` uses `cancel_order`+place, not modify. Enabling any engine exercises an **entirely unproven broker path on live capital.**
> **3. This is the STOP only — it does not touch the fixed 1.5R TARGET ceiling where the measured money is** (companion report: ~+1.36R median left on winners). And the one measured prior — config-only BreakevenMgr 60/80 = **−0.117R, worse than nothing** — is a real in-sample measurement (115 trades, true 1-min paths, real costs), so the as-built rule's *policy* was already measured net-negative.

**Net for 5A:** ChatGPT is right that you don't need to switch thirteen strategies to a different broker order type to trail the stop — the modify capability works on the order type every trade already uses. But the as-built path that would do it is **unwired + defective + unproven-live**, and the policy it implements was measured net-negative. So 5A does *not* collapse to "flip a flag"; it is "wire, fix, and live-prove an unproven broker path to deploy a rule already measured worse-than-nothing." Both framings are Rama's to weigh; this report supplies the facts, not the decision.

---

## B1 — What `BreakevenManager` actually does (quoted, not inferred from its name)

It is **milestone-based one-shot SL advancement**, NOT a continuous trail (`breakeven_manager.py:5-6, 26` — docstring: *"milestone only; use SmartTgtManager for trailing"*). Two milestones, each firing **once** per trade (BM3), driven by candle close (BM5), measured as **% of the entry→target distance** (not R, not % of entry):

- **Breakeven milestone** — when progress ≥ `breakeven_trigger_pct` (default **60%** of target distance): `new_sl = info.entry_price` (`:241`) → move stop to **entry**.
- **Partial-lock milestone** — when progress ≥ `partial_lock_trigger_pct` (default **80%**): `new_sl` = entry + `partial_lock_sl_pct`% (default **40%**) of target distance (`:238, :248-252`) → lock **+40% of target**.
- Modifies the existing **SL order's trigger AND limit** (it is a stop-limit; `:280-283`) via `adapter.modify_order(broker_order_id, price=new_limit, trigger_price=new_trigger, symbol=…)` (`:289`). Ghost-SL-safe: broker first, mark milestone only on confirm (BM4). In-memory only, reset on restart (BM6).

This is the "Option B — BreakevenMgr 60/80" of `05_d4_exit_policy.md`. **It moves only the stop; it never touches the target.**

---

## B2 — Would the flag be sufficient on its own? Trace end-to-end. (No — ≥3 further gates)

Assuming there is another gate (there was, at every level):

| # | Gate | Where | State |
|---|---|---|---|
| 1 | `strategy.trailing_sl_enabled` | `strategies/schema.py:105` default `False`; **no strategy YAML sets it** | closed |
| 2 | **`BreakevenManager` is never constructed** | `main.py` — **zero `[Bb]reakeven` references**; `git log -S "breakeven_manager" -- main.py` = **empty, whole history** | closed — OrderPlacer gets `breakeven_manager=None` (`order_placer.py:575`), so registration at `:2163-2168` is dead **regardless of the flag** |
| 3 | **Not wired to the candle feed** | nothing calls `breakeven_manager.on_candle_close` (main.py has no reference) | closed — even if registered, no bar close would ever drive it |
| 4 | **SL lookup queries a non-existent column** | `_get_sl_broker_order_id` (`:390-397`) `SELECT broker_order_id FROM orders …` | closed — the live `orders` table has **no `broker_order_id`** (PRAGMA below; `order_id` IS the broker id) → `fetch_one` raises → caught → returns `None` → `_advance_sl` logs `no_sl_order` and no-ops |
| 5 | Two-SL-owner boot guard | `c1eea66` "boot-time guard against structure_exit + strategy trailing_sl" | would block co-enabling structure_exit **and** trailing_sl |

**So the flag is not sufficient.** Making the as-built breakeven rule actually run requires: construct the manager, wire its candle callback, pass it to OrderPlacer, **fix the `broker_order_id`→`order_id` column bug**, and flip the per-strategy flag — a code+test change, not a config flip. (Live `orders` columns, PRAGMA: `order_id, trade_id, leg, leg_index, transaction_type, order_type, product, variety, qty_requested, price, trigger_price, status, qty_filled, avg_fill_price, placed_at, filled_at, updated_at, rejection_reason, reconciliation_status, superseded_by` — no `broker_order_id`.)

---

## B3 — Has `modify_order` EVER succeeded in production, for any purpose? (No — it was never called)

**The most important question. Answer: it has never executed against the real broker — measured, not assumed.**

- **Only 3 production callers of `.modify_order(`** (grep, whole tree): `breakeven_manager.py:289`, `smart_tgt_manager.py:588`, `structure_exit_manager.py:569` — the three dark exit engines. The adapter wrapper `zerodha_adapter.py:997` (`self._kite.modify_order`) is reached only through them. No other production path calls it.
- **`eod_squareoff` does NOT use `modify_order`** — it uses `cancel_order` + place MARKET (`eod_squareoff.py:876, 973, 1175`; LIMIT_THEN_MARKET). So the hypothesised "EOD promote-to-MARKET proves the path" is **false** — EOD proves `cancel_order` and `place_order`, not `modify_order`.
- **Production logs (23-Jun → 24-Jul, all system/debug logs):**
  - `trailed SL for` = **0** · `breakeven_manager.sl_advanced` = **0** · `breakeven_manager.registered` = **0** · `structure_exit_manager`/`STRUCTURE_EXIT` = **0** · `SmartTgtManager.register_trade` = **0**.
  - Any line matching `modify_order|modify_rejected|modify_failed|order.*modified` = **none**.
  - The *only* `smart_tgt_manager` log lines are `"SmartTgtManager stopped"` (daily 16:00 self-exit lifecycle) — construction/teardown, never execution.
- **DB counters (full book, immutable read):** `sl_trail_count` 0/423, `smart_tgt_state` 0 rows, `orders.superseded_by` 0/713 — corroborate for 15-Jun onward (beyond the ~30-day log window).

**Conclusion:** `modify_order` has never run in production because **no exit engine ever registered a trade to call it** (0 registrations everywhere). It is not "wired and failing" — it is **never invoked**. The adapter method is implemented and unit-tested (`test_zerodha_adapter.py`) but the live seam is entirely unexercised. Enabling any exit engine therefore fires an unproven broker call on live capital — *wired is not working* (standing rule 1), and here it is not even wired-through.

---

## B4 — Why are all three engines off? Deliberate, or never enabled? (A mix — different per engine)

- **`SmartTgtManager` — miswiring / dead-config (not a measured decision).** Constructed + enabled + wired (`main.py:2465`, `enabled=True`), but registration needs `CO_PLUS_TGT` and `order_placer.py:950` assigns the LIMIT_TRIPLE default unconditionally, discarding the 13/16 strategies' declared `CO_PLUS_TGT`. The strategies *ask* to trail; the router silently ignores them. This looks like an oversight in protocol routing, not a decision.
- **`BreakevenManager` — oversight (never wired), later given a post-hoc justification.** Built in FIX-132a (`3315597`) and actively **maintained** since (FIX-148 retry `a0c9a20`, FIX-179 `7614941`, FIX-181 tick-snap `7f93c2f`, `478673f`, `a8d8164`) — yet `git log -S "breakeven_manager" -- main.py` is **empty across the entire history**: it was never wired into the boot path, ever. The 13-Jul backtest (`4362d1c`) *later* measured the policy as net-negative, which happens to justify leaving it off — but the not-wiring predates and is independent of that measurement. Provenance: **oversight, not a decision to disable.**
- **`StructureExitManager` — deliberate, staged default-off.** Built as "SNR-V2 Phase B ... **default-off, NO schema**" (`5257ed0`), wired behind the `structure_exit_enabled` config gate (`main.py:3170-3196`), with a boot guard against co-enabling with `trailing_sl` (`c1eea66`). This is an intended, not-yet-activated staged rollout — **honour it as a decision** until new evidence overturns it.

So "deliberate vs oversight" has no single answer: **one deliberate (structure-exit), one oversight (breakeven), one dead-config (smart-tgt).** The only *measured* prior that bears on enabling breakeven is B5.

---

## B5 — Provenance of the "−0.117R, worse than doing nothing"

**MEASURED, not hypothetical — but in-sample and modest-power.** Source: `docs/audit/exit_policy_backtest_13jul2026.md`.

- **Method:** 115 trades walked on **true 1-min candle paths**; harness self-check **PASSED** (P0 gross −0.007R = the independent Q2.7 baseline); costs at the real **0.105% round-trip**. So the −0.117R is a genuine simulation of the **policy's** P&L effect, not a guess.
- **Result:** `P1b BreakevenMgr 60/80 [CONFIG-ONLY] = −0.117R`, win 35%, vs `P0 current = −0.100R` → **worse by 0.017R** (it scratches winners that reach 0.9R then pull back, and locks at +0.6R trades that would have reached 1.5R).
- **Power / scope caveats (the doc states these itself):** 115 trades, **one regime, ~2 months, in-sample** (13-Jul, before the OOS period); explicitly "**directional**"; "**ENTRIES FIXED — re-derive all of this post-M-S4**." BE-after-0.5R's +0.09R is a **spike, overfit-suspect**.
- **Crucial distinction:** the backtest measured the **policy** (the 60/80 rule's effect on paths). It did **not** exercise the `BreakevenManager` **code** against the broker (B3). So "the rule is net-negative" (measured, in-sample) and "the code path is unproven live" (B3) are two separate facts, both against enabling.

**Net for B5:** a real measurement at modest power, in-sample. It largely answers Rama's *policy* question already (config-only breakeven 60/80 was measured worse than nothing); the netting simulation (5B), if ever authorised, would be **out-of-sample confirmation**, not discovery.

---

## STOP vs TARGET (read a stop finding as a stop finding)

Everything above concerns the **stop**. **None of it touches the fixed 1.5R target ceiling** — the place where the companion report found the measured, forward-persisting money (winners' true MFE ~2.2–2.4R out-of-sample vs a 1.5R exit; ~+1.36R median left on the table in-sample). `BreakevenManager`, `SmartTgtManager`, and `StructureExitManager` all move the **SL** only; the TGT LIMIT stays at 1.5R in every case. A better stop cannot capture upside the fixed target caps. Do not read "the stop-trail path is unproven/negative" as an upside finding — the upside question is a *target* question and is separate.

---

## Implication for 5A (implication, not recommendation — Rama decides)

- You do **not** need Cover Orders to trail the stop; the modify capability works on the LIMIT_TRIPLE SL order type. That part of 5A shrinks.
- It does **not** shrink to a flag: the as-built LIMIT_TRIPLE stop-mover is unwired (never in main.py), unfed (no candle callback), and defective (`broker_order_id` column bug); and `modify_order` has never run live.
- The as-built rule's **policy** (BreakevenMgr 60/80) was **measured net-negative** in-sample; the **code path** is **unproven** live. Both point the same way for *this specific rule*, for different reasons.
- The three engines are off for three different reasons (deliberate / oversight / dead-config), so "leave them off" and "they were decided against" are not the same statement — only structure-exit is an actual decision.

## Evidence appendix (reproduce)

- Code (`bc75406`): `orders/breakeven_manager.py:188-333` (milestones), `:387-403` (`broker_order_id` lookup); `orders/order_placer.py:575, 615, 2163-2168` (breakeven gate), `:950` (protocol); `main.py:2465` (smart_tgt), `:3170-3196` (structure_exit gate); `eod_squareoff.py:876,973,1175` (cancel+MARKET).
- Grep: `\.modify_order\(` → 3 prod callers + adapter; `BreakevenManager(` → tests only; `[Bb]reakeven` in main.py → none.
- Git: `git log -S "breakeven_manager" -- main.py` → empty; `3315597` (FIX-132a build), `5257ed0` (structure-exit default-off), `c1eea66` (two-SL-owner guard), `4362d1c` (13-Jul backtest).
- VM (`immutable=1` / read-only logs): `pragma_table_info(orders)` (no `broker_order_id`); log tags `trailed SL for`/`sl_advanced`/`registered`/`STRUCTURE_EXIT`/`modify_order` all 0 over 23-Jun→24-Jul; DB `sl_trail_count`/`smart_tgt_state`/`superseded_by` all 0.
- Prior: `docs/audit/exit_policy_backtest_13jul2026.md` (115 trades, true paths, −0.117R P1b, self-check passed).
