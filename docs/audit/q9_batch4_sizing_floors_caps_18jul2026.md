# Q9 BATCH 4 — SIZING FLOORS + CAPS, WIRED · AND A REACHABILITY ANALYSIS

**Date:** 18-Jul-2026 (Saturday, IST, off-market; system DOWN since Fri 17-Jul, book flat)
**Author:** VS Code Claude · **Basis:** `58d10c4` (tag `deploy-18jul-q9-batch3` → `5fac11a`), schema v44
**Scope:** TEST-ONLY. No production file changed. No sizing behaviour, parameter or config value altered.
**Closes:** Q9 #4. (Q9 #5, post-restart capital restoration, is NOT in this batch.)

---

## 0. TL;DR

Sizing was Q9 "UNIT-ONLY". It was worse than that: **the integration suite invoked
`PositionSizer.calculate()` zero times.** The two "full lifecycle" integration tests bypass the sizer
outright — `test_full_signal_flow._drive_lifecycle` hard-codes `qty = 10` and calls
`fund_manager.reserve()` directly. Sizing now has 23 wired tests covering a 15-guard ladder, each
proving *which* guard bound, bite-proven with four value-shaped plants.

The batch's real product is §C. Q9's founding lesson was **CONFIGURED IS NOT COVERED**. This batch
adds its sibling — **WIRED IS NOT REACHABLE**:

> **Of the 15 sizing guards, 4 can bind under production config. 11 cannot.**
> Nine are dead by algebra or by config; two are dead because a whole subsystem was never wired.

The single most consequential finding is not in the sizer at all:

> **⚠️ `PerformanceAllocator` is never instantiated anywhere in production, and `perf_weights` is
> never passed to `SignalProcessor`.** `dynamic_by_winrate: true`, `min_multiplier: 0.5` and
> `max_multiplier: 2.0` in `system_config.yaml` are read by nothing on the sizing path.
> Empirically: `perf_weight_applied = 1.0` on **298 of 298** sized production trades.

And one reporting defect that has been actively misinforming the daily report:

> **⚠️ `reports/daily_report.py:464` reports 3,098 "capital" rejections. The true count is 0.**
> All 3,098 are CONCENTRATION rejections, matched because the reason string embeds `capital_qty=`.

---

## 1. §A1 — THE GUARD LADDER, IN BINDING ORDER

All guards live in `capital/position_sizer.py::calculate()`. Order below is execution order.

| # | Guard | Line | Action | Derived from |
|---|---|---|---|---|
| G1 | SL-distance vs `min_tick_size` | `:333` | **REJECT** `INVALID_SL_DISTANCE` | `min_tick_size` 0.05 |
| G2 | `qty_by_risk` | `:361` | clamp candidate | `risk_per_trade_pct` × total ÷ sl_distance |
| G3 | `max_single_order_qty` | `:365` | **REJECT** `QTY_EXPLOSION_GUARD` | tests `qty_by_risk` **alone**, before the `min()` |
| G4 | `qty_by_capital` | `:398` | clamp candidate | bucket avail ÷ (price ÷ leverage) |
| G5 | `qty_by_concentration` | `:402` | clamp candidate | `max_concentration_pct` × total ÷ price |
| G6 | `raw_qty = min(G2,G4,G5)` + tie-break | `:407-413` | clamp | CAPITAL wins ties, then RISK, else CONCENTRATION |
| G7 | `raw_qty <= 0` early exit | `:425` | **REJECT**, named by the binding arm | — |
| G8 | ZERO_MULTIPLIER (M-C6) | `:466` | **REJECT** `ZERO_MULTIPLIER` | `tier_mult × max(0, perf_weight) <= 0` |
| G9 | tier multiplier | `:503` | clamp down / raise | `tier_mult × perf_weight` |
| G10 | FIX-133 floor-1 / ceiling-2× | `:506` | **FLOOR (raises)** + 2× ceiling | `max(1, min(tiered, raw_qty*2))` |
| G11 | flat ceiling | `:516-519` | clamp `FLAT` | `flat_value_rs` — else-branch, `enabled: true` |
| G12 | lot-size rounding (PS6) | `:530` | clamp down | `lot_size` |
| G13 | lot skew rejection (FIX-021) | `:534` | **REJECT** `REJECTED_LOT_SKEW` | skew > 0.25 |
| G14 | `max_position_value_pct` (FIX-144) | `:562` | **REJECT** `POSITION_VALUE_CAP` | 0.40 × total |
| G15 | BELOW_MIN | `:592` | **REJECT** `BELOW_MIN` | `final < lot_size` or `< min_qty_threshold` |

**Scope note (§B "SCOPE BOUND").** The instruction anticipated ~6 guards and allowed a split above
that. Fifteen were found, but they are homogeneous — one function, one return type, one fixture, one
binding ladder — so splitting would have separated guards that can only be understood against each
other (G3 is masked by G1; G15 is masked by G10). Kept as **one commit**, deliberately.

### §A2 — the binding order, and which guard masks which

```
CONCENTRATION binds  ⟺  sl_distance < (risk_pct / conc_pct) × price   ( = 10% of price )
CAPITAL       binds  ⟺  bucket_avail < conc_pct × total / leverage    ( = Rs 197.51 of Rs 6,912.92 )
```

Intraday stops run 1–3% of price, so the first condition is essentially always true. **Concentration
is the strict unique minimum on every realistic signal**, which means a naive test aimed at any other
sizing guard would in fact be watching concentration fire. Batches 1 and 2 both hit exactly this
shape. Every test in this batch therefore asserts the binding arm is the **strict unique minimum**
(`_assert_bound_by`), and refuses a tie as non-decisive. **No multi-way accepts anywhere.**

Masking relationships proven, not assumed:
- **G1 masks G3.** G3 needs `sl_distance < risk_rs / max_single_order_qty` = Rs 0.0099; G1 rejects
  everything below Rs 0.05 first, and G1 is checked 32 lines earlier.
- **G10's floor masks G15.** The floor guarantees `tiered_qty >= 1`; with `lot_size = 1` the rounding
  at G12 is an identity, so `final_qty >= 1` always and BELOW_MIN cannot be reached.
- **G5 masks G2, G4 and G14** on every production signal (§C).

---

## 2. §A3 — THE 13-JUL SIZING AUDIT, RE-VERIFIED AGAINST CURRENT CODE AND DATA

Source: `docs/audit/sizing_interaction_impact_report_13jul2026.md` (corpus 269 trades → 13-Jul).
Re-run read-only against the live DB (raw `sqlite3`, `mode=ro`, no app code ⇒ no migration-on-open),
current corpus **298 sized trades, 2026-06-22 → 2026-07-16**. Live capital **Rs 9,875.60**.

| # | 13-Jul finding | Verdict | Current evidence |
|---|---|---|---|
| 1 | `binding_constraint = concentration` on 100% | ✅ **VERIFIED** | **298 / 298**. Concentration is the *strict* unique minimum in 298/298 |
| 2 | `qty_by_risk` never binds → risk sizer dead by algebra | ✅ **VERIFIED** | `qty_by_risk <= qty_by_concentration` in **0** rows. Mean arms: risk **34.4**, capital **130.8**, conc **3.4** |
| 3 | actual risk ≈ Rs 5–7 vs intended Rs 99 | ✅ **VERIFIED** | median **Rs 4.92**, mean **Rs 5.87**, max Rs 17.64. Intended = 1% × 9,875.60 = **Rs 98.76** ⇒ **1/20th** |
| 4 | tier = 100% LOW (0.5×) | ✅ **VERIFIED** | `tier_weight_applied = 0.5` on **298 / 298** |
| 5 | ~7–10% of buying power deployed | ✅ **VERIFIED** (direction) | median position **Rs 442**, max **Rs 999**, vs Rs 9,875.60 capital |
| 6 | >Rs990 silently excluded, 23.3% of universe | ⚠️ **CHANGED — see §5** | **7,604 of 32,928** priced signals (**23.1%**) are above the Rs 987.56 cap. 3,098 reached sizing and were rejected. **NOT silent — but mis-attributed** |
| 7 | exclusion hits LONG ~4.5× harder than SHORT | ⚠️ **CHANGED (worse)** | LONG **2,804** vs SHORT **294** = **9.54×**, attributed via the authoritative `StrategyConfig.direction` registry (not inferred from names — it classifies `positional_sector_rotation` as LONG). Sums to exactly 3,098 |

**Nothing inverted.** Five findings verified outright, two changed in magnitude while holding in
direction. Finding 6's "silent" characterisation is the one that needed correcting, and the
correction is more actionable than the original claim (§5).

### §A4 — runtime probe (rule F: verify a seam before depending on it)

| Probe | Result |
|---|---|
| Bare webhook POST → sizing? | **NO.** `REJECTED_STEP_ERROR: price_action` — screener has no market data. `calculate()` called **0** times |
| Webhook + rich quote patched → sizing? | **YES.** `PROCESSED`; `calculate()` called **exactly 1** time |
| Real inputs observed | `('RELIANCE','BUY', 999.0, 991.008, 'INTRADAY', 'HIGH', 1)`, `perf_weight=1.0` |
| Arms at that call | risk **625** · capital **1751** · **concentration 50** ← bound |
| `sl_distance` | 7.992 = **0.8% of price** — inside the concentration-binds band |
| Existing integration coverage of the sizer | **ZERO calls** — `_drive_lifecycle` hard-codes `qty=10` |

Rule F earned its place again: the obvious seam (post a webhook, expect sizing) does not reach
sizing at all. A test written on that assumption would have passed while proving nothing.

---

## 3. §B — WHAT THE TESTS PROVE

`tests/integration/test_q9_sizing_floors_caps_wired.py` — 23 tests, all on the existing
`wired_system` fixture. No parallel harness.

| Guard | Positive | Negative | Binding-order proof |
|---|---|---|---|
| G5 concentration | webhook → `PROCESSED`, qty = conc arm, position value at 99.9% of cap | cheap price → conc does not clamp | strict unique min; `raw < risk` **and** `raw < capital` |
| G2 risk | stop > 10% of price → `RISK` | 1% stop → `CONCENTRATION`, not RISK | strict unique min; arm equals its formula |
| G4 capital | bucket drained by **real** reserves → `CAPITAL` | fresh bucket → `CONCENTRATION` | strict unique min; arm fell vs fresh |
| G7 >cap rejection | price above cap → reject, conc arm = 0 | price just under cap → qty 1 | risk & capital arms both > 0, isolating concentration |
| G9 tier multiplier | each tier = exactly `floor(raw × mult)` | tiers must differ; arms unchanged | — |
| G8 M-C6 | full wired path → skip (§3.1) | positive multiplier still trades | `raw_qty > 0` proves the skip came from the multiplier |
| G10 floor | floor produces 1 where mult rounds to 0 | swept across 3 tiers × 9 prices | qty ≤ every arm (§3.2) |
| G10 ceiling | overshoot multipliers clamp to exactly 2× raw | — | — |
| G1 / G3 / G13 / G14 / G15 | each fires on its own scenario | each has a paired must-not-fire case | G3 asserted to fire *ahead of* the `min()` |

Every money-path test asserts the full capital picture before and after (rule A), the global capital
identity (batch 3's I1 — the *global* form; the per-bucket form does not hold), and that the kill
switch never armed.

### 3.1 M-C6 — the zero-multiplier skip (§B2, the highest-risk case)

Driven end-to-end through the real seam: `SignalProcessor._perf_weights` (the constructor argument
read at `signal_processor.py:893`), not by calling the sizer in isolation.

```
status      = REJECTED_SIZING_ZERO_MULTIPLIER
broker calls= []          ← asserted on adapter.place_order's call record, not a status string
trade rows  = []          ← no ENTRY row constructed
capital     = byte-identical: avail 350,000.00 · reserved 0.00 · used 0.00
raw_qty     = 50 (> 0)    ← anti-vacuity: the skip came from the multiplier, not an exhausted arm
```

A negative tier multiplier is covered too: `PositionSizingTierConfig` types HIGH/MEDIUM/LOW as bare
floats with no `ge=0` bound, so a config typo loads cleanly. It must skip, not floor to 1 lot.

### 3.2 §B3 — can the min-lot floor breach a cap?

**No, and the reason is structural rather than incidental.** G7 (`:425`) rejects outright when
`raw_qty <= 0`, so anything reaching the floor has `raw_qty >= 1`, and `raw_qty` is by construction
`<= ` every one of the three arms. Flooring to 1 therefore lands at or below all three caps. Proven,
not argued: asserted directly at a price where the multiplier rounds to zero, and swept across
3 tiers × 9 prices with `qty <= every arm` at each point.

**However — the ceiling on the same line is a different story.** See §4.

---

## 4. ⭐ §C — REACHABILITY

**Production config:** capital **Rs 9,875.60** · `risk_per_trade_pct` 0.01 (Rs 98.76) ·
`max_concentration_pct` 0.10 (Rs 987.56) · `max_position_value_pct` 0.40 (Rs 3,950.24) ·
leverage 5.0 · intraday bucket 70% (Rs 6,912.92) · tiers 1.0/0.7/0.5 · `min_tick_size` 0.05 ·
`max_single_order_qty` 10,000 · `lot_skew` 0.25 · `min_qty_threshold` 1 ·
**`lot_size: 1` on all 16 strategies** · `position_sizing.enabled: true`.

| Guard | Verdict | Algebra |
|---|---|---|
| **G5 concentration** | ✅ **REACHABLE** | Binds 298/298. Strict unique minimum on every signal |
| **G7 >cap rejection** | ✅ **REACHABLE** | `floor(987.56 / price) = 0` ⟺ price > Rs 987.56. **3,098** signals rejected |
| **G9 tier multiplier** | ✅ **REACHABLE** | Applied on 298/298, always 0.5 (LOW) |
| **G10a floor-at-1** | ✅ **REACHABLE** | conc arm = 1 ⟺ price ∈ (493.78, 987.56]; `floor(1 × 0.5) = 0` → raised to 1. **65 of 298 trades (21.8%)** got their quantity from this floor — without it they would have been rejected BELOW_MIN |
| **G2 risk** | ❌ **UNREACHABLE** | Binds ⟺ `sl_distance > 0.10 × price`. Intraday stops are 1–3%. **0/298 rows.** *The primary risk control never governs anything* |
| **G4 capital** | ❌ **UNREACHABLE** | Binds ⟺ intraday avail < Rs 197.51 of Rs 6,912.92 (97.1% reserved). Max concurrent margin = 5 × Rs 197.51 = Rs 987.56, leaving Rs 5,925. **0/298** |
| **G3 explosion guard** | ❌ **UNREACHABLE** (masked) | Needs `sl_distance < 98.76/10,000 = Rs 0.0099`; **G1 rejects below Rs 0.05 first**. Reachable only if `risk_rs ≥ min_tick × max_single` ⟺ **capital ≥ Rs 50,000** |
| **G8 ZERO_MULTIPLIER** | ❌ **UNREACHABLE** | `effective_mult = tier_mult × perf_weight`; tier ∈ {1.0,0.7,0.5}, perf_weight ≡ 1.0 ⇒ min 0.5. **Never ≤ 0** |
| **G10b 2× ceiling** | ❌ **UNREACHABLE** | Needs `effective_mult > 1` ⇒ `perf_weight > 1`. Never happens (§4.1) |
| **G11 FLAT** | ❌ **UNREACHABLE** | Dead `else` branch while `position_sizing.enabled: true` |
| **G12 lot rounding** | ❌ **UNREACHABLE** (no-op) | `lot_size = 1` on all 16 strategies ⇒ `(q // 1) * 1 == q`, an identity |
| **G13 lot skew** | ❌ **UNREACHABLE** | Guarded by `if lot_size != 1` — never true |
| **G14 position-value cap** | ❌ **UNREACHABLE** | Ceiling is conc (10%) × tier (0.5) ≈ **5% of capital**; cap is **40%**. Observed max position Rs 999 vs cap Rs 3,950.24 — **4× of headroom**. Would need `conc_pct > 0.40` |
| **G15 BELOW_MIN** | ❌ **UNREACHABLE** | G10's floor guarantees `tiered >= 1`; `lot_size = 1` ⇒ `final >= 1` |
| **G1 invalid SL** | ⚠️ **CONDITIONAL** | Needs `sl_distance < Rs 0.05`; a 1% stop reaches that only below ~Rs 5. Scanners screen Rs 100–5,000. It is a data-error net, not a routine guard |

**Score: 4 reachable · 10 unreachable · 1 conditional.**

### 4.1 ⚠️ The finding underneath five of those verdicts

`PerformanceAllocator` (`capital/performance_allocator.py`) **is never instantiated anywhere in the
codebase outside its own docstring**, and `perf_weights` **is never passed to `SignalProcessor`**
(`main.py:2914`). `SignalProcessor._perf_weights` is therefore always `{}`, and
`signal_processor.py:893` always resolves to the `1.0` default.

Verified three ways: static (no `PerformanceAllocator(` construction outside its module), wiring
(`main.py` never passes `perf_weights=`), and runtime (`perf_weight_applied = 1.0` on **298 of 298**
sized trades).

Consequently these `system_config.yaml` keys are **read by nothing on the sizing path** —
`config_loader` defines them, one `main.py` line logs them, the ops dashboard displays them:

```yaml
dynamic_by_winrate: true    # FIX-133 Item 21: PerformanceAllocator weights applied to sizing
min_multiplier: 0.5
max_multiplier: 2.0
```

**Note for the record:** the M-C6 comment at `position_sizer.py:462` explains G8's unreachability as
*"performance_allocator clamps min_weight=0.5 (PA3/PA8) … so effective_mult >= 0.25 in production"*.
The conclusion is right; the stated reason is not — the allocator does not run at all, so the floor
is 0.5, not 0.25. Recorded, not changed (the code is correct; only the comment's premise is stale).

### 4.2 ⚠️ A cap that CAN be exceeded — currently unreachable, and the config already asks for it

`position_sizer.py:506` — `tiered_qty = max(1, min(tiered_qty, raw_qty * 2))`.

For any `effective_mult > 1`, the final quantity can reach **twice the tightest clamp arm**.
Demonstrated at runtime on the wired fixture:

```
perf_weight=1.0 → qty= 50   (= the concentration arm; position value Rs  50,000 = 10.0% of capital)
perf_weight=2.0 → qty=100   (= 2 × raw;               position value Rs 100,000 = 20.0% of capital)
                              binding_constraint recorded: 'concentration'   ← while qty is 2× it
```

Three properties worth stating plainly:
1. It **exceeds the concentration cap by 2×** — 20% of capital against a 10% limit.
2. **G14 cannot catch it**: 2 × 10% = 20% < the 40% position-value cap. Nothing downstream objects.
3. The **stored audit column still records `binding_constraint = 'concentration'`**, so the trades
   table would show a concentration-bound trade at double the concentration limit.

**This is not a live capital finding today** — `perf_weight` is pinned at 1.0 (§4.1), so
`tiered_qty <= raw_qty` always and the ceiling is never approached. It is recorded because
`max_multiplier: 2.0` and `dynamic_by_winrate: true` are *already in production config*: wiring the
allocator is a one-line change that would make this live silently. The test file pins both halves —
the behaviour, and a guard test that **fails** if `PerformanceAllocator` is ever instantiated or
`perf_weights` ever passed.

**§C2 — what would have to change for each unreachable guard to bind** (stated, not recommended):

| Guard | Would bind if… |
|---|---|
| G2 risk | stops widened past 10% of price, **or** `max_concentration_pct` raised above ~0.30 |
| G4 capital | intraday bucket deployment reached ~97%, i.e. far more concurrent positions or far larger ones |
| G3 explosion | capital ≥ Rs 50,000 (so `risk_rs ≥ min_tick × max_single_order_qty`) |
| G8 / G10b | `PerformanceAllocator` wired **and** `min_multiplier` lowered to 0 (G8) / any weight > 1 (G10b) |
| G11 FLAT | `position_sizing.enabled: false` + `flat_value_rs` set |
| G12 / G13 / G15 | any strategy with `lot_size > 1` (F&O) |
| G14 | `max_concentration_pct` raised above 0.40, or the 2× ceiling engaged with conc > 0.20 |

---

## 5. §C3 — THE EXCLUSION: NOT SILENT, BUT REPORTED AS ITS OPPOSITE

**Are signals above the price threshold dropped without a logged reason? No.** They carry a status
and a detailed per-signal reason:

```
status           = REJECTED_SIZING_CONCENTRATION            (3,098 signals, 67 distinct symbols)
rejection_reason = "qty=0: CONCENTRATION exhausted for GRAVITA
                    (risk_qty=6 capital_qty=17 conc_qty=0)"
price range      = Rs 990.60 → Rs 4,579.60                  (cap = Rs 987.56)
```

**But two things make it effectively invisible in the daily report, and one of them is worse than
silence:**

**(a) It is counted as its own opposite.** `reports/daily_report.py:464`:

```python
rejected_capital = sum(1 for s in data.signals
                       if "CAPITAL" in (s.get("rejection_reason") or "").upper())
```

The CONCENTRATION reason string embeds `capital_qty=`. Measured on the live DB:

| | |
|---|---|
| CONCENTRATION rejections whose reason contains "CAPITAL" | **3,098 of 3,098 (100%)** |
| What the daily report prints as `rejected_capital` | **3,098** |
| Actual `SIZING_CAPITAL` rejections | **0** |

⇒ **A 100% false-positive rate.** The daily report has been reporting the system as capital-starved
when capital has never once been the binding constraint — while the arm that *is* binding, on 100%
of trades, is not named.

**(b) It is fragmented.** `daily_report.py:549-555` buckets by `rejection_reason` first, falling back
to `status`. The concentration reason embeds the symbol and the three arm values, giving **246
distinct strings for 3,098 rejections** — so instead of one line reading
`REJECTED_SIZING_CONCENTRATION: 3098`, the report emits 246 low-count lines that sort to the bottom.

**Scale of the exclusion (current data):**

| Measure | Value |
|---|---|
| Priced signals above the Rs 987.56 cap | **7,604 of 32,928 (23.1%)** |
| Of those, reaching sizing and rejected there | **3,098** (the rest die at earlier gates) |
| Distinct symbols excluded | **67** |
| LONG vs SHORT | **2,804 vs 294 = 9.54×** (via the `StrategyConfig.direction` registry; sums to 3,098) |
| Price bands | Rs 988–1,500: **1,668** · 1,500–2,469: **1,219** · 2,469–3,950: **190** · >3,950: **21** |

This is a **selection** effect as much as a sizing one: roughly a quarter of what the scanners
surface cannot be traded at the current capital level, and it removes expensive names — which are
disproportionately LONG candidates — from the universe entirely.

*Both (a) and (b) are reporting defects, not sizing defects. Sizing is arithmetically correct
throughout. Nothing was changed; `reports/daily_report.py` is untouched.*

---

## 6. §B5 — PARITY (Rule #5)

**Structural, not assumed** — the same standard batches 2 and 3 met.

- `capital/position_sizer.py` contains **no** `paper_mode` / `is_paper` / `live_mode` branch, and
  `calculate()` takes no mode argument. It reads only `FundManager.get_snapshot()`, which is likewise
  mode-free (batch 3 established this).
- `signal_processor.py:885` is the entry-path sizing call site (3 total in the module) and sits
  **upstream** of any mode decision; the paper/live divergence is downstream at
  `broker/zerodha_adapter.py:348` (`paper_mode → _paper_place_order`).
- ⇒ One shared sizer, one code path. **A quantity proven in paper is the quantity live computes.**

`TestParity` asserts these structural facts so a future duplicate cannot drift in unnoticed, and
`test_there_is_exactly_one_sizing_call_site_on_the_entry_path` fails if a new sizing path appears.

---

## 7. §B6 — PROVEN TO BITE

Four **value-shaped** plants (not crashes). Each: plant → run → observe a failure naming the guard
and the quantity → restore → re-verify green.

| Plant | Break | Result |
|---|---|---|
| **A** | concentration cap loosened 10× | **12+ failures.** `narrow stop: expected CONCENTRATION to bind, but POSITION_VALUE_CAP did. arms={'RISK': 500, 'CAPITAL': 1750, 'CONCENTRATION': 500} raw=500` |
| **B** | tier multiplier silently dropped (`raw × 1.0`) | **3 failures.** `tier MEDIUM (multiplier 0.7) sized 50, expected floor(50 * 0.7) = 35` |
| **C** | M-C6 skip neutered (`if False`) | **2 failures.** `M-C6 did not fire; status=PROCESSED` — and the log shows `risk_engine.approve … approved=True margin=199.80`, i.e. a real position on a signal sizing said to skip |
| **D** | 2× ceiling widened to 4× | **1 failure** (after a fix — see below). `perf_weight=3.0 produced qty=150; the ceiling should clamp it to exactly 2 x raw_qty (50 -> 100)` |

**⭐ The batch-3 lesson applied.** Plant A was caught partly by production's *own* `POSITION_VALUE_CAP`
— reassuring about the guard, worthless as evidence about the tests. Plant **B** is the meaningful
one: dropping the multiplier yields qty 50 instead of 35 — a perfectly *legal* quantity, inside every
cap, positive, correctly recorded. **Production's runtime capital-invariant guard fired 0 times**
(measured: `CapitalInvariantViolation` occurrences = 0, invariant-violation log lines = 0) while the
tests failed. That is the E4/W10 class, in sizing.

**⭐ Two plants exposed real gaps in the tests and were closed:**
- **Plant B initially under-bit**: every test used the HIGH tier (multiplier 1.0), so removing the
  multiplier altogether changed nothing they asserted. Added
  `test_the_tier_multiplier_actually_scales_the_quantity`, asserting each tier equals exactly
  `floor(raw × its configured multiplier)`.
- **Plant D was not caught at all** (23 passed): a `perf_weight` of 2.0 asks for exactly 2×, so it
  never touches the ceiling and cannot detect where the ceiling sits. Added
  `test_the_ceiling_itself_is_exactly_two_times_raw`, using overshoot multipliers (3×, 5×, 20×).

After restore: `capital/` and `orders/` show **0 modified files**; 23 passed.

---

## 8. REGRESSION + DEPLOY

### 8.1 ⭐ A METHODOLOGY FINDING — the "~30-test flake band" is not flake

The standing note records a ~30-test band between same-tree runs as an uncharacterised
observation. **It has a cause, and it compromises the attribution method.**

`config/instruments.csv` (75,749 bytes) is **git-ignored**. A throwaway `git worktree` therefore
does not contain it → `main.py:1949` cannot build the InstrumentCache → preflight fails → the
fallthrough guard at **`main.py:2014` (`assert instrument_cache is not None`)** trips, taking down
~30 boot-path tests in `test_main.py` plus the preflight/T4 set.

Established causally, not by correlation:

| Check | Result |
|---|---|
| `test_clean_shutdown_returns_0` **in isolation** in the worktree | **FAILED** in 0.56s ⇒ not ordering, not contention |
| Same test in isolation in the main tree | **passed** |
| Failure message | `main.py:2014: AssertionError` on `assert instrument_cache is not None` |
| Copy the one git-ignored file into the worktree, re-run | **passed** |
| Full base re-run with the file present | **42 failures → 15** |

**⚠️ Why this matters beyond tidiness.** The mandated method allows `git checkout <base> -- <files>`
*or* a throwaway worktree, and forbids `git stash`. But **a bare worktree silently inflates the base
failure set**, and inflation is the **masking** direction: a genuine new failure can coincidentally
match one of the ~30 spurious base failures and be waved through as pre-existing. That is the
"stale baseline" trap in a new disguise.

**📌 Sharpened rule: prefer `git checkout <base> -- <files>` in the main tree (it preserves
git-ignored runtime data), or seed a worktree with the ignored files before trusting its numbers.**
`git stash` remains forbidden.

It did not bite this batch — `comm -13` was empty against *both* bases, the new file's 23 tests all
passed inside the full run, and a test-only addition cannot affect `test_main.py`'s boot path — but
the first base was re-run rather than relied upon.

### 8.2 Regression — same-window, true base

All three runs 19:15–19:55 IST, same evening window (the batch-3 lesson: a morning baseline is not
valid for an evening run).

| Run | Failed | Passed | Skipped | xfail | **Collected** |
|---|---|---|---|---|---|
| BASE 1 — worktree @`58d10c4`, **no** ignored data (discarded) | 42 | 4914 | 7 | 1 | 4964 |
| **BASE 2 — worktree @`58d10c4` + ignored data (authoritative)** | **15** | **4941** | **7** | **1** | **4964** |
| **MINE — `f177c60`** | **12** | **4969** | **5** | **1** | **4987** |

- **Attribution: `comm -13` EMPTY against BASE 2 ⇒ ZERO ATTRIBUTABLE.** (Also empty against BASE 1.)
- **Totals reconcile exactly: 4964 + 23 = 4987.**
- BASE-2-only failures (3, all `test_t4_deploy_preflight` tz cases) pass in MINE — base-only, so MINE
  is strictly better; documented PC-env class.
- **`xfailed = 1`, `XPASS = 0`** in both ⇒ batch 3's E4/W10 contract xfail is still genuinely
  xfailing, not silently passing.
- MINE's 12 failures are all documented PC-env / calendar-gated: `test_main.py` (4),
  `test_order_placer_fix061` (4), `test_daily_trade_review` (Saturday), `test_interactive_startup`
  (`main.py:1686-1695` service window), `test_fix181`, `test_phase17_batch2`. **Zero failures from
  the new file.**

### 8.3 Deploy verification

| Step | Result |
|---|---|
| Tag `deploy-18jul-q9-batch4` | → `f177c60` |
| Fresh VM backup `pre_deploy_q9b4_20260718.db` | **SOUND**: `quick_check=ok` · schema **44** · **361** trades · **0** FK |
| Push `main` + tag | `58d10c4..f177c60`, post-receive checkout OK |
| Bare HEAD == re-derived local HEAD | `f177c60` == `f177c60` ✓ **PC == VM** |
| Schema | **v44 unchanged — no migration** |
| Integrity / FK | `quick_check=ok` · **0** violations · 361 trades unchanged |
| Kill-switch state | **INACTIVE** (unchanged) |
| Services | `trading-system` inactive (expected, system DOWN) · `alert-watcher` active · `gui-dashboard` active |
| **New tests ON THE VM** | **23 passed** (incl. the source-scan guards, against the deployed tree) |

Test-only: **no runtime behaviour change**.

---

## 9. WHAT WAS **NOT** DONE (deliberately)

- **No sizing behaviour, parameter or config value changed.** Whether to size up is decision **D1**
  and it is Rama's. §C is evidence for it, not an action on it.
- **`reports/daily_report.py:464` left as-is** despite the 100% false-positive finding — it is
  outside this batch's test-only scope; recorded for the careful loop.
- **E4/W10 not touched.** No schema change, no flag flip, no destructive CTs, no restart.
- **`PerformanceAllocator` not wired.** That is a sizing-behaviour change and belongs to D1.

---

## 10. OPEN ITEMS THIS BATCH CREATES

| Item | Owner | Note |
|---|---|---|
| **D1 sizing decision** now has its full evidence package | Rama | §C reachability table + §5 exclusion + the **9.54×** LONG skew (authoritative, via `StrategyConfig.direction`; an earlier name-inferred 7.34× is superseded) |
| `daily_report.py:464` mis-attributes 3,098 CONCENTRATION rejections as CAPITAL | careful loop | 100% false positive; one-line substring test, but it is a report the operator reads daily |
| `daily_report.py:549` fragments one rejection class into 246 lines | careful loop | group by `status` before `rejection_reason` |
| `position_sizer.py:506` 2× ceiling can exceed every clamp arm | careful loop / D1 | latent; pinned by a failing-on-change test |
| `position_sizer.py:462` comment's premise is stale | hygiene | conclusion correct, reason wrong |
| `dynamic_by_winrate` / `min_multiplier` / `max_multiplier` are decorative | Rama | wire the allocator, or remove the keys — not both left as-is |

---

*Read-only investigation; test-only build. No production code, config, schema or flag changed.*
