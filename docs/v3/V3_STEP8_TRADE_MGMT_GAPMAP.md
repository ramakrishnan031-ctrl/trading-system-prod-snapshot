# V3 STEP 8 — 03.08 TRADE MANAGEMENT: SINGLE-SL-OWNER FINDING + VERIFY + GAP-MAP

**Date (IST):** 12-Jul-2026 · **Type:** ADAPT — VERIFY of the highest-frequency SL modifier (RAMCOIND-critical). · **LIVE trade-management BYTE-IDENTICAL** (no `orders/*.py` touched). · **For:** Web Claude + ChatGPT + Rama sign-off.

**Headline.** 03.08 is **COMPLETE and battle-tested** → **ZERO code change** (Step-7 discipline). All three SL modifiers move the SL **in place** (`modify_order`, never cancel-replace) and **monotonically** (ratchet-only). The single-SL-owner question (T1) is answered below: ownership is **protocol-partitioned by construction** for the live path; the only non-airtight seam (breakeven + structure-exit on the same LIMIT_TRIPLE trade) is guarded by **convention, not construction**, and is **triply-inert today** — flagged, not fixed (a capital-safety finding, like the gate-8 TOCTOU).

---

## T1 — SINGLE-SL-OWNER FINDING (the headline)

**Who can move an SL:** three managers, each registered at fill by `order_placer._handle_entry_fill` keyed on **order_protocol + config**:

| Manager | Owns | Registration gate (source) | Live status |
|---|---|---|---|
| **SmartTgtManager** | the **CO** trade's broker-managed SL (trigger_price) | `order_protocol=="CO_PLUS_TGT"` AND `smart_tgt.enabled` (`order_placer.py:2095-2124`) | **ACTIVE** — every live intraday strategy is CO_PLUS_TGT + `smart_tgt.enabled: true` |
| **BreakevenManager** | a **LIMIT_TRIPLE** trade's static SL leg (60/80% milestones) | `order_protocol=="LIMIT_TRIPLE"` AND `strategy.trailing_sl_enabled` (`order_placer.py:2067-2084`) | **INERT** — 0 strategies set `trailing_sl_enabled: true` |
| **StructureExitManager** | a **LIMIT_TRIPLE** trade's static SL leg (structure trail) | `structure_exit_enabled` master flag (`main.py:2789`) | **INERT** — `structure_exit_enabled: false` (not constructed) |

**Is ownership mutually exclusive?**
- **CO vs LIMIT_TRIPLE = AIRTIGHT BY CONSTRUCTION.** A trade is exactly one protocol, chosen at entry; SmartTgt only ever registers CO trades, Breakeven/Structure only ever register LIMIT_TRIPLE trades. **They can never touch the same trade.** So the live SL owner is **SmartTgtManager, and only SmartTgtManager** (all live intraday strategies are CO_PLUS_TGT; the 2 LIMIT_TRIPLE strategies are the positional/DELIVERY ones, dormant under `trade_type=INTRADAY`).
- **Breakeven vs Structure-exit (both LIMIT_TRIPLE) = NOT AIRTIGHT — convention only.** If a future operator sets BOTH `structure_exit_enabled: true` AND a strategy's `trailing_sl_enabled: true`, that strategy's LIMIT_TRIPLE trade would be registered with **both** managers, and both would move the **same** static SL leg. The only guard is a **comment convention** at `main.py:2788` ("Single SL owner — do NOT co-enable trailing_sl_enabled") + the config default `structure_exit_enabled: false`. **There is no construction/config assertion that rejects the co-enabled combination.**

**Could that produce two live SLs or a zero-SL window?** Not two SLs — a LIMIT_TRIPLE trade has exactly **one** static SL order; both managers would `modify_order` that same leg (so there is always one SL leg, never two). The real hazards under co-enablement are: (a) a **modify race** — both read `current_sl`, both issue a modify, the later-but-staler wins and could set a less-tight level than the other intended; (b) **structure-exit's terminal EXIT path** (`_cancel_trade_resting_exits`, `structure_exit_manager.py:365`) cancels the SL leg while breakeven concurrently issues a modify → the modify fails on a cancelled order (benign) but there is a **transient no-SL window until the flatten completes** (the flatten is EXITING-marked-first, so it's bounded, but it is a window). Neither can occur today (triply-inert).

**Verdict:** **Airtight for the live path (protocol partition).** The breakeven∩structure overlap is **convention-guarded, not construction-guarded** — currently inert, but a latent capital-safety seam. **NOT fixed unilaterally → flagged (T4-1)** with a recommended construction guard.

---

## T2 — GAP-MAP (03.08 requirement | existing implementation + path | present/GAP)

| 03.08 requirement | Existing implementation (source) | Status |
|---|---|---|
| **Partial booking at target levels (scale-out)** | Not a continuous scale-out; **milestone SL-advance** instead: `breakeven_manager` 60% → SL to breakeven (entry), 80% → SL to 40%-of-target (`breakeven_manager.py:8-9`, BM2/BM3 each fires ONCE). SmartTgt = continuous CO SL trail. **Works differently from a 1R/2R book-out but is a coherent, tested design** — NOT churned | **PRESENT (milestone, not scale-out — by design)** |
| **SL → breakeven after first target** | `breakeven_manager` 60%-milestone → SL to entry (`register_trade` BM6; `modify_order` BM8). Gated by `strategy.trailing_sl_enabled` (inert live) | **PRESENT (inert live)** |
| **MONOTONIC trailing (only-tighten / only-advance; never loosens)** | **SmartTgt:** `_compute_new_sl` returns None if not tighter (`smart_tgt_manager.py:518/522`) + `_modify_co_sl` re-guards after tick-round (`:566/568`; directional tick-round always tightens). **Structure:** ONLY-TIGHTEN LONG new>cur / SHORT new<cur (`structure_exit_manager.py:23`). **Breakeven:** forward-only milestones, once each. Tests: `test_long_price_retraces_sl_not_lowered`, `test_short_sl_never_raised`, `test_action_a_only_tighten_skips_when_not_higher` | **PRESENT (G3 ✓)** |
| **OCO-INVARIANT MODIFY: modify-in-place (never cancel-then-place); no transient two-SL / zero-SL; against the AUTHORITATIVE broker book** | ALL three use `adapter.modify_order` on the **broker order id** (SmartTgt ST1/P8_P13 `:6-7,11`; Breakeven BM8 `:20`; Structure SE `:49` "modify_order static legs only"). SmartTgt looks up the live CO `order_id` from `state_store.get_co_entry_order_for_trade` and modifies AT the broker. **No cancel-replace for an SL move** anywhere | **PRESENT** |
| **Ghost-SL safety (state updated only after broker confirms)** | SmartTgt ST5: "Update internal state and DB ONLY after broker confirms — never pre-update" (`smart_tgt_manager.py:533-534`); `test_failure_does_not_update_tracked_or_db` | **PRESENT** |
| **Modify-failure behaviour (old SL stands → retry → reconcile; never a 2nd SL, never zero SLs)** | Modify failure leaves the **existing** SL untouched (modify-in-place — the old order stays); per-trade consecutive-failure counter → **CRITICAL after `max_modify_failures` 3** (FIX-142, `smart_tgt.max_modify_failures`); next candle recomputes + retries (ratchet-safe); rate-limit skip is safe (`:571-575`) | **PRESENT** |
| **Partial-fill interaction (legs resized to remaining qty)** | Exits placed/registered at `event.filled_qty` (OP-NS1/NS2/NS3, `order_placer.py:105-115`); `_on_order_partially_terminated` (FIX-028) + Audit-#7 partial-cancel path both place exits at the actual filled qty; managers register the trade's filled qty | **PRESENT** |
| **OCO sibling / terminal exit (no naked re-open)** | Structure terminal exit marks **EXITING first** → `_cancel_trade_resting_exits` → reverse-aware flatten (`structure_exit_manager.py:301-405`, SE5); order_placer `_handle_exit_fill` double-close-guard → `_cancel_oco_siblings` | **PRESENT** |
| **RAMCOIND 4-layer intact** | L1 G5b + settling window, L2 one-live-SL/TGT invariant, L3 CHECK2 SYSTEM_OVERSELL, L4 after-check flags. Tests `test_ramcoind_dup_exit_fix` + `test_ramcoind_oversell_prevented` | **PRESENT (G2 ✓ — 154 pass)** |

## T3 — genuine gaps closed

**NONE.** Every 03.08 requirement is implemented, modify-in-place, monotonic, and RAMCOIND-hardened. The milestone-SL-advance vs continuous-scale-out difference is a **working-differently-but-fine** case (SAID SO, left alone). **No code changed.**

## T4 — REVIEW ITEMS (flagged, NOT changed — capital-safety)

1. **Breakeven ∩ Structure-exit single-SL-owner is convention-guarded, not construction-guarded (T1).** Today triply-inert (`structure_exit_enabled: false` · 0 strategies `trailing_sl_enabled` · LIMIT_TRIPLE strategies DELIVERY-dormant), so **zero live risk now**. **Blast radius if co-enabled:** a modify-race and/or a transient no-SL window on a LIMIT_TRIPLE trade during a structure-exit flatten. **Recommendation:** add a **boot-time fail-fast** (or config-auditor rule) that REJECTS `structure_exit_enabled=true` together with any strategy `trailing_sl_enabled=true` — turning the `main.py:2788` convention into a construction guard. Small, safe, no live behaviour change (both are off). **Decision requested** (do not implement unilaterally — mirrors the Step-5 kill_switch fail-fast + gate-8 TOCTOU flags).
2. **ATR-into-LIVE-SL (carried from Step 5/7).** Unchanged; belongs to the V3 playbook step + its own parity proof.
3. **No other item.** The trailing/partial/OCO machinery is battle-tested and must not be restructured.

## Acceptance

- **G1 (LIVE trade-mgmt byte-identical):** **0 `orders/*.py` files modified by any V3 step (1–6b)** → trade-management is byte-identical by construction.
- **G2 (RAMCOIND mandatory):** `test_ramcoind_dup_exit_fix` + `test_ramcoind_oversell_prevented` + the three managers + `test_tgt_retry` = **154 pass**. 4-layer intact.
- **G3 (monotonic proof):** each manager's ratchet is test-enforced (`test_long_price_retraces_sl_not_lowered`, `test_short_sl_never_raised`, `test_action_a_only_tighten_skips_when_not_higher`) + source-cited only-tighten guards.
- **G4 (regression):** no code change → no new failures possible; the cluster is green (154).
- **G5:** this T1 finding + T2 gap-map + T4 items delivered.

**No live behaviour changed; SL-ownership model untouched; OCO/RAMCOIND machinery untouched.** VERIFY-only. NEXT after review = Step 9 (03.09 Exit — terminal exits, OCO sibling cancel, 15:15 cutoff / 15:17 squareoff w/ delivery exemption, SYSTEM_OVERSELL, kills).
