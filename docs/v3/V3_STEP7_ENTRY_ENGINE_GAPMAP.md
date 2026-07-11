# V3 STEP 7 — 03.07 ENTRY ENGINE: VERIFY + GAP-MAP

**Date (IST):** 12-Jul-2026 · **Type:** ADAPT — VERIFY of the mature order/OCO path. · **LIVE entry BYTE-IDENTICAL** (no `orders/*.py` touched). · **For:** Web Claude + ChatGPT + Rama sign-off.

**Headline.** 03.07 is **COMPLETE and battle-tested** in the existing `orders/` path (RAMCOIND-hardened). **No genuine gap needed closing now → ZERO code change this step** (do not churn proven code — R2/WHAT-NOT-TO-DO). The one V3-specific item, the S&R-derived SL seam, is **already the existing `sl_price` parameter** threaded end-to-end — the V3 playbook (a later step) feeds `computed_sl` there with NO order-path change. LIVE SL derivation stays FIXED_PCT (ATR-into-live-SL remains the flagged, separate decision from Step 5).

---

## T1 — GAP-MAP (03.07 requirement | existing implementation + path | present/GAP)

| 03.07 requirement | Existing implementation (source) | Status |
|---|---|---|
| **Entry order type by trigger (LIMIT / SL-LIMIT; no unbounded MARKET chase)** | `order_protocol_limit.py::execute` Phase-1 = **ENTRY LIMIT** (`entry_order_type` default `"LIMIT"`, L179); `"MARKET"` only for the SNR-V2 retest-confirm (fills at LTP, off by default). Emergency/kill exits use a **marketable LIMIT capped 1%** (`price_math.marketable_limit_price` L127, `EMERGENCY_EXIT_BUFFER_PCT` 0.01) — never a raw MARKET. SL leg = **SL stop-limit** (P0: Zerodha rejects SL-M; `calc_sl_limit_price` L162) | **PRESENT** |
| **Slippage tolerance enforcement (no chase; min(SL_dist×0.22, Rs5), ceiling Rs10; override hierarchy)** | `order_placer._compute_slippage_tolerance` (L288) mode `sl_fraction` → `min(SL_distance×fraction, absolute_cap_rs)`; `_resolve_effective_sl_fraction` (L220) applies **Symbol > Strategy > Band > Global** (`max_slippage_fraction` 0.22); band tiers via `price_math.tier_slippage_tolerance_rs` (L60). Entry abort if LTP deviates > `max_entry_slippage_pct` 1.0% from trigger (**FIX-128**, L549/L618, `signal_trigger_price`). Config `slippage_model.yaml` | **PRESENT** |
| **TGT recalculated from ACTUAL fill (SL signal-fixed)** | `order_placer._handle_entry_fill`→ **FIX-013**: `actual_tgt_price = calc_tgt_price(entry=avg_fill_price, sl=fill_entry.sl_price, rr=…)` (L2766); **"SL price remains anchored to the original strategy-requested level"** (L2741/2769/2796). `tgt_risk_reward` frozen at placement (Slice 1) | **PRESENT** |
| **MANDATORY protective bracket on fill = exactly ONE SL + ONE TGT (software OCO)** | Exits DEFERRED to fill (`place_deferred_exits`, FIX-016) → `order_protocol_limit.place_exits`: **SL FIRST** then TGT (L263+). Software OCO: SL+TGT tracked in `_fill_map`+`order_monitor`; on either fill `_handle_exit_fill` → `close_trade` (double-close guard, BL-10a L2262) → **`_cancel_oco_siblings`** cancels the survivor (Audit #5, L2321). `tgt_retry_manager` re-places a failed TGT and **never makes a 2nd** | **PRESENT** |
| **NAKED-position handling if bracket fails after fill** | `place_deferred_exits` raises → **SL unplaceable** → `raise SLUnplaceableError` → order_placer CRITICAL `LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED` → `_emergency_market_exit` (FIX-148, L2834) → `_fire_hard_kill_for_unprotected_position` (L2838). **TGT** unplaceable → SL stands, `_partial_sl_only` + `mark_needs_tgt_retry`. LTP-validation error → exit-retry queue (FIX-061). Never re-raises inside the fill handler (reconciler is the backstop) | **PRESENT (NAKED_CRITICAL-equivalent)** |
| **Idempotency (no duplicate entry on retry) + reconcile-on-uncertainty** | A-2: a `place()` timeout is AMBIGUOUS → **UNKNOWN_IN_FLIGHT** (L1330-1343), **NO retry** (a retry = 2× entry); `order_reconciler` `_recover_in_flight_entries` (CHECK_UNKNOWN_IN_FLIGHT) is the SOLE owner — correlates by tag against the **authoritative broker book** and adopts+protects or FAILs on confirmed absence. BrokerRateLimit is pre-submission → safe re-queue (max 3) | **PRESENT** |
| **Partial-fill handling (bracket sized to FILLED qty)** | Exits placed at `event.filled_qty` (`_handle_entry_fill` L2015/2036; `qty=qty_filled` into `place_deferred_exits` L2795); capital committed at `actual_qty=filled_qty` (excess auto-returned). LIMIT_TRIPLE docstring: partial ENTRY → SL/TGT at filled qty | **PRESENT** |
| **MIS product coercion (+ CNC/GTT path present, delivery OFF)** | Product coercion at the broker boundary (`zerodha_adapter.place_order` force⇒MIS, Option-A double-lock); CNC OCO-GTT path present (`full_entry_engine.place_deferred_exits` L151 `intent=="DELIVERY"` → `cnc_gtt.place_for_fill`), **inert** (delivery double-locked OFF) | **PRESENT (delivery inert)** |
| **Circuit-band exit-clamp (wrong-side fill prevention)** | `price_math.clamp_exit_into_band` (single chokepoint, 3 sites in `order_protocol_limit.py`); SL-side raises `SLUnplaceableError`, TGT-side holds (SL-only). Pre-fill `REJECTED_CIRCUIT_PROXIMITY` in the screener | **PRESENT** |
| **RAMCOIND 4-layer fix (must stay intact)** | L1 G5b broker-book check + settling window (`order_reconciler.py`); L2 one-live-SL/one-live-TGT reconciler invariant; L3 CHECK2 SYSTEM_OVERSELL; L4 placement after-check flags. Tests `test_ramcoind_dup_exit_fix.py` + `test_ramcoind_oversell_prevented.py` | **PRESENT (G2 ✓ — 209 pass)** |
| **S&R-derived SL on the V3 path (the one V3 wiring)** | `sl_price` is a **plain parameter** threaded `signal_processor._derive_prices → order_placer.place(sl_price) → full_entry_engine.execute(sl_price) → protocol` (order path derives NO SL — "caller supplies entry/sl/tgt", `full_entry_engine` L20-23). The V3 playbook feeds `computed_sl` as `sl_price` LATER, gated to that path | **SEAM PRESENT (T2); wired in a later step** |

## T2 — V3 S&R-SL SEAM (no live change)

Confirmed: the entry/order path **never derives an SL** — it accepts `sl_price` as an input parameter the whole way down (`place`→`execute`→`place_deferred_exits`→`place_exits`). LIVE derives that SL via `signal_processor._derive_prices` (FIXED_PCT / `calc_sl_price`). The V3 playbook (03.03's `computed_sl` from 03.01 S&R) will supply `sl_price` **at the signal_processor seam on the V3 path only**, with **NO order-path change**. This step did NOT change how the LIVE path derives its SL. The seam is already open; nothing to wire in 03.07.

## T3 — genuine gaps closed

**NONE.** Every 03.07 requirement is already implemented and RAMCOIND-hardened. Per the disciplined rule ("if a gap is really a working-differently-but-fine case, say so and leave it alone; do not churn proven code"), **no code was changed.** The `sl_price` seam (T2) needs no order-path change now.

## T4 — REVIEW ITEMS (flagged, NOT changed)

1. **ATR-into-LIVE-SL (carried from Step 5 T5b).** LIVE SL = strategy FIXED_PCT; `core/candle_math.atr` exists but is NOT wired live. Wiring it would change every LIVE SL distance → LIVE TGT (RR-from-fill) → position sizes. **Blast radius = LARGE (money path).** Rec: DO NOT wire live; ATR/S&R SL is the V3-shadow-path intent via the `sl_price` seam + its own parity proof. Decision belongs to the V3 playbook step, not here.
2. **`entry_order_type="MARKET"` retest path.** The SNR-V2 WAIT_FOR_RETEST confirm places a MARKET entry (fills at LTP). Off by default (`wait_for_retest_enabled` false). Not a 03.07 change; noted for completeness — the default entry is a bounded LIMIT.
3. **No other item.** The OCO / slippage / RAMCOIND machinery is battle-tested and must not be restructured (WHAT-NOT-TO-DO).

## Acceptance

- **G1 (LIVE entry byte-identical):** **no `orders/*.py` file is modified by any V3 step (1–6b)** — the entry/order path is untouched → live entry is byte-identical by construction. (git status: 0 `orders/` files changed.)
- **G2 (RAMCOIND mandatory):** `test_ramcoind_dup_exit_fix.py` + `test_ramcoind_oversell_prevented.py` + `test_order_placer.py` + `test_tgt_retry.py` + `test_fix190_exit_safety.py` + `test_fix148_broker_gaps.py` = **209 pass**. The 4-layer fix is intact.
- **G3 (regression):** no code change → no new failures possible in the order path; the entry-path cluster is green (209). Full-suite baseline unchanged from Step 6b.
- **G4:** this gap-map + the T4 review items delivered for sign-off.

**No live behaviour changed; delivery NOT activated; the OCO/RAMCOIND machinery untouched.** VERIFY-only. NEXT after review = Step 8 (03.08 Trade Management — the highest-frequency SL modifier, also RAMCOIND-critical).
