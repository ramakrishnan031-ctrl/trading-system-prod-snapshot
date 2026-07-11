# V3 STEP 9 — 03.09 EXIT ENGINE: VERIFY + GAP-MAP + DELIVERY-EXEMPTION LEDGER

**Date (IST):** 12-Jul-2026 · **Type:** ADAPT — VERIFY of the FINAL shared-engine module (terminal exits / squareoff / kills). · **LIVE exits BYTE-IDENTICAL** (no exit-path file touched). · **For:** Web Claude + ChatGPT + Rama sign-off. · **This COMPLETES the shared engine (03.01–03.09).**

**Headline.** 03.09 is **COMPLETE and battle-tested** → **ZERO code change** (Step-7/8 discipline). Ratified semantics honored: **15:15 = entry cutoff only** (no position flatten), **15:17 = EOD squareoff** (MIS/CO only). The **CNC/DELIVERY exemption Slice 2.5 depends on EXISTS and is test-enforced** (CNC is not squared off by the 15:17 flatten). The T2 ledger below states precisely which CNC exemptions EXIST vs which are PARKED (Slice 2.5).

---

## T1 — GAP-MAP (03.09 requirement | existing implementation + path | present/GAP)

| 03.09 requirement | Existing implementation (source) | Status |
|---|---|---|
| **Terminal exits (SL/TGT/trailed-SL fill) → close trade → CANCEL THE OCO SIBLING (no orphan resting leg)** | `order_placer._handle_exit_fill`: `close_trade` FIRST (double-close guard, BL-10a) → `_cancel_oco_siblings` (`order_placer.py:4346`, Audit #5) cancels the survivor before `release_used`; a racing sibling fill finds the trade CLOSED and short-circuits | **PRESENT** |
| **15:15 ENTRY CUTOFF (blocks new entries; NO flatten)** | `market_windows.is_past_eod_entry_cutoff` (`:177-190`, "15:15, 2 min before EOD squareoff at 15:17"); enforced at `order_placer.place`. `is_entry_allowed` = `entry_start ≤ t < entry_end` (`:150`). **No 15:15 position flatten anywhere** | **PRESENT (ratified — unchanged)** |
| **15:17 EOD SQUAREOFF (MIS/CO)** | `orders/eod_squareoff.py` (EOD1/EOD6): cancel pending INTRADAY/CO entries + flatten filled INTRADAY(MIS)/CO with MARKET; two-pass (cancel resting SL/TGT then exit all open); CO squared via bracket-collapse (Audit 3.1) | **PRESENT** |
| **DELIVERY/CNC EXEMPTION from the 15:17 squareoff** | **CNC is filtered OUT** — broker-position filter `p.product in ("MIS","CO")` (`eod_squareoff.py:1023`, FIX-015) + the holdings-path filter (`:1390`) + EOD6 DB-side ("DELIVERY (CNC) positions NOT touched", `:23-34`). Test `test_open_delivery_positions_not_touched` | **PRESENT (G3 ✓ — the Slice-2.5 crux)** |
| **SYSTEM_OVERSELL recognition (system's own oversell → CRITICAL + auto-flatten, classified SYSTEM not human)** | `order_reconciler._detect_system_oversell` (`:1557`) + `_flatten_system_oversell` (`:1604`) → CHECK2 (RAMCOIND LAYER 3, `:323`); `check_name="SYSTEM_OVERSELL"`, CRITICAL log + auto-flatten of the untracked residual | **PRESENT** |
| **SCHEDULED vs EMERGENCY kill distinction** | `kill_switch`: `auto_clear_scheduled_kill` (`:250`) auto-clears scheduled reasons (`_is_scheduled_reason` `:95`); **HARD_KILL is NEVER auto-cleared** (`:256/264`); `clear_stale_state` (`:199`) clears prior-day kills (incl. a prior-day HARD) at headless boot; manual `resume()` | **PRESENT** |
| **HARD-KILL cancellation (cancel resting legs FIRST, then flatten)** | HARD flatten: `_mark_trade_exiting` FIRST (`:956`, prevents double-select) → `_cancel_trade_resting_exits` cancels resting SL/TGT at broker (`:971`, FIX-190 Bug E — no orphan re-fire) → marketable-LIMIT flatten (FIX-181, `:173`) via `_exit_all_trades_indestructible`; `determine_close_direction` (FIX-190 Bug A) | **PRESENT** |
| **Orphan / squareoff-failure handling (retry + CRITICAL; never a silent orphan or a position left open past intended flat)** | eod two-pass re-queries open positions (Pass 2 exits phantom fills); write-ahead IN_PROGRESS→COMPLETE row (M-3); reconciler A-1/E-1 in-flight recovery + FIX-183 orphan-GTT adoption + CHECK1..CHECK9; HARD-kill indestructible retry (`_HARD_KILL_MAX_RETRY_HOURS` 2.0, FIX-180) | **PRESENT** |
| **RAMCOIND 4-layer intact** | L1 G5b + settling window, L2 one-live-SL/TGT invariant, L3 CHECK2 SYSTEM_OVERSELL, L4 after-check flags. Tests `test_ramcoind_dup_exit_fix` + `test_ramcoind_oversell_prevented` | **PRESENT (G2 ✓ — 241 pass)** |

## T2 — DELIVERY-EXEMPTION LEDGER (the Slice 2.5 input — EXISTS vs MISSING)

**EXISTS TODAY (active — PROTECT a pre-existing/carried CNC holding so the live INTRADAY machinery never harms it):**
1. **15:17 squareoff exemption** — CNC filtered out at the broker-position filter (`eod_squareoff.py:1023`) + holdings-path filter (`:1390`) + EOD6 DB-side. Test-enforced.
2. **Reconciler delivery-exclusion** — trades with an ACTIVE `gtt_state` row are EXCLUDED from CHECK1 (no CLOSED_MANUAL), G5b, CHECK9 (no spurious SL); kept in `local_symbols` so CHECK2 won't orphan-adopt a held CNC (`order_reconciler.py:785-797`).
3. **FIX-183 orphan-GTT adoption prepass** — reconstructs a row-less live broker GTT + correlates to its open delivery trade before CHECK1 (`:754-760`).
4. **FIX-008 CNC overnight bootstrap check** — at startup WARNs if the broker holds CNC; does NOT exit or soft_kill (CNC may be legitimate) (`:668-745`).
5. **CncGttMonitor WIRED** (`main.py:2338/2364`, SLICE2.5-P2 4a startup + 4b 15-min in-hours cadence) — manages a carried CNC via its GTT. Tests `test_cnc_gtt_slice25_p1/p2` pass.
6. **GTT-protection primitives ungated** — place/modify/delete/get GTT + `get_holdings` are not blocked by force_intraday_only, so a pre-existing overnight holding stays protected.

**MISSING / PARKED (Slice 2.5 — the ACTIVE delivery TRADING lifecycle; delivery is OFF):**
1. **CNC ENTRY is OFF** — `delivery_enabled: false` + `force_intraday_only: true` + `trade_type: INTRADAY` → **no NEW CNC order is ever placed live**; product coerced to MIS at the broker boundary (Option-A double-lock). The 3 `positional_*` DELIVERY strategies are DORMANT.
2. **Live CNC lifecycle proof** — the T2 canary (branch `854112b`): same-day BUY→GTT→SELL PASSED, but the **DDPI overnight-carry leg is UNPROVEN** (needs the Mon→Tue `--arm-overnight` run). Parked.
3. **Delivery SIZING knobs** — the Step-5 inert scaffold (`delivery_risk_per_trade_pct` / `delivery_max_position_value_pct`, default None) is present but not activated.
4. **Next-day carry validation** — the full "a carried CNC survives EOD, its GTT re-arms overnight, and every check treats it as legitimately-held across the day boundary" is DESIGNED (the exclusions above) but not yet **live-proven** end-to-end.

**Honest summary:** the **protective** exemptions (don't square off / don't mis-close / don't place a spurious SL on a held CNC) **EXIST and are tested**. The **active delivery lifecycle** (place a CNC, carry it overnight, validate DDPI) is **PARKED** behind `delivery_enabled=false`. Slice 2.5 is the activation + the overnight-carry proof, not the construction of the exemptions.

## T3 — genuine gaps closed

**NONE.** Every 03.09 INTRADAY requirement is implemented and RAMCOIND-hardened. Anything delivery-related is Slice 2.5 (ledgered above, NOT built — delivery stays OFF). **No code changed.**

## T4 — REVIEW ITEMS (flagged, NOT changed)

1. **No new exit-path flag.** The exit/squareoff/kill machinery is battle-tested; nothing to add for the INTRADAY path.
2. **Carried from prior steps (unchanged):** the Step-8 breakeven∩structure single-SL-owner convention-not-construction flag (T4-1); the Step-5/7 ATR-into-LIVE-SL flag. Both remain review items for their respective steps.
3. **Slice 2.5 activation** (delivery entry + DDPI overnight proof) is Rama's parked decision — the T2 ledger is its precise input.

## Acceptance

- **G1 (LIVE exit byte-identical):** **0 exit-path files (`orders/*.py`, `capital/kill_switch.py`, `core/market_windows.py`) modified by any V3 step** → live exits byte-identical by construction.
- **G2 (RAMCOIND mandatory):** `test_ramcoind_dup_exit_fix` + `test_ramcoind_oversell_prevented` + `test_eod_squareoff` + `test_kill_switch` + `test_order_reconciler` + `test_cnc_gtt_slice25_p1/p2` = **241 pass**.
- **G3 (delivery-exemption verification):** `test_open_delivery_positions_not_touched` proves CNC is NOT squared off by the 15:17 MIS/CO squareoff; the T2 ledger honestly states protective-exemptions EXIST vs active-lifecycle PARKED.
- **G4 (regression):** no code change → no new failures possible; the exit cluster is green (241).
- **G5:** this gap-map + the T2 delivery-exemption ledger + the T4 items delivered.

**No live behaviour changed; no 15:15 flatten added; delivery NOT activated; OCO/RAMCOIND/kill machinery untouched.** VERIFY-only. **This COMPLETES the shared engine (03.01–03.09).** NEXT after review = the next phase: intraday pipeline plumbing → stateful watchlist + next-morning entry → PB-01 in shadow.
