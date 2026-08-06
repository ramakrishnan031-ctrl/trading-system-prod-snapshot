# THE TIER MULTIPLIER — AS MEASURED

**06-Aug-2026 · Step 1b of the sizing work · companion to `current_sizing_chain_06aug2026.md`**
⛔ **MEASUREMENT ONLY. No design · no rounding rule · no value · no new key · nothing changed.**
Citations at the deployed SHA `0197923`. Production corpus read at **~14:5x IST**.

> ## 🔴 HEADLINE — **THREE OF THE COMMISSIONING HYPOTHESES DID NOT SURVIVE THE DATA**
> 1. *"The tier and the floor cancel exactly, jointly a no-op"* — **true for 111 of 483 trades
>    (23 %), false for 372 (77 %).**
> 2. *"The tier's first real effect arrives at `raw_qty = 2`"* — **`raw_qty ≥ 2` on 372 of 483. The
>    tier has been acting all along; it is the dominant case, not a future condition.**
> 3. *"G2's doubling warning is true at scale and false at present capital"* — 🔴 **REFUTED. 77 % of
>    trades would change size today.** ⛔ **The register's unconditional warning is CORRECT and must
>    NOT be made conditional.**
>
> ⭐ **What DID hold: §2.2's prediction — every FIX-133 lift is modifier-induced, never cap-induced —
> and it holds structurally, not merely empirically.**

---

# PART 1 — THE CHAIN IN PLAIN ENGLISH

**A signal is scored. The score becomes a grade. The grade becomes a multiplier.**

### Step 1 — The scorer produces a number out of 100
Ten weighted steps produce `score`.

### Step 2 — The number becomes a grade
- **80 or more → HIGH** (multiplier 1.0)
- **65 to 79 → MEDIUM** (0.70)
- **below 65 → LOW** (0.50)

> 🔴 **THE HIGHEST SCORE EVER PRODUCED, IN 72,755 SCORED SIGNALS, IS 65.**
> ⇒ **HIGH is unreachable by construction** — the threshold sits **15 points above the ceiling**.
> ⇒ **MEDIUM is reachable only at a *perfect* score, and has happened 6 times in 72,755 (0.008 %).**
> ⇒ **LOW on 72,749 of 72,755 (99.992 %).**

### Step 3 — The grade becomes a multiplier, and is multiplied by a performance weight
`effective_mult = tier_multiplier × performance_weight`. In production the performance weight has
been **1.0 on every trade**, so `effective_mult` has been **0.5 on every trade**.

### Step 4 — The quantity is halved, rounded down
`tiered_qty = floor(raw_qty × 0.5)`.

### Step 5 — Then floored at one share (FIX-133)
`max(1, min(tiered_qty, raw_qty × 2))`. **A position can never be rounded away to nothing.**

> ⭐ **At `raw_qty = 1` steps 4 and 5 cancel:** `floor(0.5) = 0`, lifted back to `1`. **That is real —
> it has happened 111 times.** ⛔ **But it is the minority case.** At `raw_qty = 4` — the single most
> common value, 134 trades — `floor(2.0) = 2`, and **the tier genuinely halves the position.**

---

# PART 2 — THE MEASUREMENTS

## §1 · What computes the tier

| # | step | `file:line` |
|---|---|---|
| 1 | `QualityScorer` sums ten weighted steps → `total_score` | `screening/quality_scorer.py` |
| 2 | **band assignment** — `>= high_thr → HIGH`, `>= med_thr → MEDIUM`, else **LOW** | `quality_scorer.py:124-132` |
| 3 | thresholds from config: `high_score_threshold: 80`, `medium_score_threshold: 65` | `config/system_config.yaml:508-509`; defaults `config_loader.py:1188-1189` |
| 4 | carried on `ScreeningResult.tier` | `screening/secondary_screener.py:48` |
| 5 | passed **positionally, arg 6** into the sizer | `signals/signal_processor.py:963` · `:1901` · `:2222` |
| 6 | looked up in the multiplier table | `capital/position_sizer.py:467` |
| 7 | multiplied by the per-strategy performance weight | `:470` |
| 8 | applied: `tiered_qty = floor(raw_qty × effective_mult)` | `:527` |
| 9 | floored at 1, capped at 2× (FIX-133) | `:530` |

**§1.2 — derived FROM:** the **quality score**, and nothing else. ⛔ Not win rate, not strategy
identity, not symbol. *(The performance weight is separate and per-strategy.)*

### §1.3 · 🔴 WHY 0.5 ON EVERY TRADE — **a GENUINE COMPUTATION, not a default and not a clamp**

The band logic is correct and live. **The bands are simply set above what the scorer can produce.**

**(P) The raw pre-band input, whole `screener_results` table — 72,755 rows:**

| tier | rows | min score | max score | avg |
|---|---|---|---|---|
| **LOW** | **72,749** | 0 | **64** | 50.5 |
| **MEDIUM** | **6** | 65 | 65 | 65.0 |
| **HIGH** | **0** | — | — | — |

```
highest score ever   65        reached MEDIUM (>=65)   6        reached HIGH (>=80)   0
```
⭐ **The code names it at the call site** (`quality_scorer.py:127`): *"unreachable today (IA-P2-03:
threshold 80 > ceiling 65)."*
⇒ **Not a default never overridden** — the override path runs on every signal and assigns LOW
correctly. **Not a clamp** — nothing is clipped. **A real computation whose input cannot reach the
bands.** ⭐ **And the 6 MEDIUM screens never became trades: all 483 sized trades carry 0.5.**

### §1.4 · Where `0.5` / `[0.25, 2.0]` come from — **config, not literals**
```yaml
config/system_config.yaml:178-181   tier_multipliers:  HIGH 1.0 · MEDIUM 0.70 · LOW 0.50
config/system_config.yaml:183-184   min_multiplier: 0.5   max_multiplier: 2.0    # perf weight
```
Module defaults exist (`position_sizer.py:58-62`) but config supplies the live values.
**Production floor** `0.5 × 0.5 = 0.25` — the code states it at `:488`. **Observed:** `perf_weight`
is **1.0 on 483/483**, so the min has never been approached.

### §1.5 · Scope
| thing | scope |
|---|---|
| the **tier** | **per signal** (from its own score) |
| the **multiplier table** | **global** — one table, both pipelines |
| the **performance weight** | **per strategy** |

### §1.6 · 🔴 **DOES DELIVERY HAVE ANY TIER CONTROL? — NO. NONE.**
**Width:** `config/system_config.yaml` · `core/config_loader.py` · `capital/position_sizer.py`,
patterns `delivery.*tier` · `tier.*delivery` · `delivery_tier` → **zero hits.**

> ⭐⭐ **This is the SECOND lever that moves delivery size and has no delivery control** — the first
> is concentration *(`current_sizing_chain_06aug2026.md` §2.2b)*. Risk and position-value have
> delivery twins; **concentration and tier, the two that actually bind, do not.**

## §2 · The cancellation, tested against real data

**Width:** whole `trades` table — **545 rows, 483 carrying a persisted sizing breakdown.**
⚠️ **The register's "438/438" is stale; the corpus is now 483.** *(It moved 482 → 483 between two
queries minutes apart — live data.)*

**(P) Uniform across all 483:** `tier_multiplier_mode = ON` · `tier_weight_applied = 0.5` ·
`perf_weight_applied = 1.0` · **`binding_constraint = concentration`.**

### §2.1 · `raw_qty` → predicted → actual — **the formula predicts actual on every group**

| `raw_qty` | `floor(raw×0.5)` | after FIX-133 | **actual `qty_planned`** | n | |
|---|---|---|---|---|---|
| **1** | **0** | **1** | 1 – 1 | **111** | 🔴 **LIFT** |
| 2 | 1 | 1 | 1 – 1 | 72 | |
| 3 | 1 | 1 | 1 – 1 | 80 | |
| **4** | **2** | **2** | 2 – 2 | **134** | ← modal |
| 5 | 2 | 2 | 2 – 2 | 31 | |
| 6 | 3 | 3 | 3 – 3 | 26 | |
| 7 | 3 | 3 | 3 – 3 | 8 | |
| 8 | 4 | 4 | 4 – 4 | 8 | |
| 9 | 4 | 4 | 4 – 4 | 8 | |
| 10 · 12 · 14 · 19 | 5 · 6 · 7 · 9 | 5 · 6 · 7 · 9 | exact | 1 each | |

⭐ **`min(qty_planned) == max(qty_planned) == predicted` in all 13 groups.** A check that could have
gone red and did not.

**FIX-133 lifted exactly 111 trades — every one of them at `raw_qty = 1`.**

### §2.2 · 🔴 THE PREDICTION — ✅ **CONFIRMED, and structurally, not just empirically**

**(P)** rows where `raw_qty <= 0`: **ZERO.**
**(S)** and it cannot be otherwise: `position_sizer.py:449` —
```python
if raw_qty <= 0:
    return SizingResult(success=False, ...)      # exits at :449-464
```
**...which returns BEFORE the tier block begins at `:466`.**

> ⭐⭐ **A CAP-INDUCED ZERO CAN NEVER REACH THE FLOOR.** The early return guarantees FIX-133 only ever
> sees `raw_qty >= 1`. ⇒ **every quantity FIX-133 has ever rescued is modifier-induced.**
> ✅ **The floor is papering over a rounding rule. The proposed taxonomy costs nothing in refused
> trades.** ⛔ *This is a fact about the code's shape, not a property of the current data — it cannot
> be falsified by a future trade.*

### §2.3 · 🔴 **HAS `raw_qty` EVER EXCEEDED 1? — YES. 372 of 483 (77 %).**

⛔ **The two claims the card asked to be separated, separated:**
- *"permanently 0.5"* — **tested for VALUE: yes, 483/483.**
- *"the tier has never had the opportunity to act"* — **FALSE.** It has had the opportunity **372
  times** and it **took** it: on every one of those trades the position is **half** what the cap
  layer permitted.

⇒ ⛔ **"The tier and the floor are jointly a no-op" is true for 23 % of trades and false for 77 %.**

### §2.4 · Is `floor()` used elsewhere? — **yes, five sites plus one integer division**
**Width: the whole sizer.**
`:382` `qty_by_risk` · `:420` `qty_by_capital` · `:423` `qty_by_concentration` · **`:527` `tiered_qty`
(the tier)** · `:540` `qty_by_flat` · and `:554` `final_qty = (tiered_qty // lot_size) * lot_size`.
⇒ **Every quantity in the ladder is floored.** The tier's floor is not special in kind — only in
that it is the one a floor-at-1 then rescues.

## §3 · 🔴 THE G2 COUPLING — **REFUTED. The warning is correct as it stands.**

**§3.1 — the hypothesis:** *"a modifier runs after the caps, so at `raw_qty = 1`, `floor(1 × 1.0) = 1`
— the same 1."* ✅ **True at `raw_qty = 1`.** ⛔ **And `raw_qty = 1` is only 111 of 483.**

**(P) Trades whose size would change if the tier moved LOW (0.5) → HIGH (1.0):**
```
size_would_change   372
total               483
                  77.0 %
```
At the modal `raw_qty = 4`: `floor(4 × 0.5) = 2` → `floor(4 × 1.0) = 4`. **A doubling, today, at
₹10,000 capital.**

> ### ⛔⛔ **DO NOT MAKE THE WARNING CONDITIONAL.**
> §3.2 asked for a trigger of the form *"G2 must not ship while the cap layer permits ≥ 2"* — ⭐ **that
> condition is ALREADY TRUE on 77 % of trades.** A trigger phrased that way would read as a future
> guard while describing the present.
> ⭐⭐ **§3.3's reasoning is sound and its premise is wrong here: this is not a conditional hazard
> described unconditionally. It is an unconditional hazard, correctly described.** Weakening it
> would be the error the section exists to prevent.
> ⛔ **Nothing added to the Must-Land-Before table** — the existing G2 ↔ tier co-requirement (Shape 2)
> already carries it, and it needs no condition attached.

---

# OPEN QUESTIONS FOR THE DESIGN STEP — ⛔ UNANSWERED, DELIBERATELY

1. **Should the tier bands move, or should the scorer's ceiling rise?** The gap is 15 points and the
   grade has been constant for the system's whole life. ⛔ These are different fixes with different
   blast radii.
2. **Should delivery have its own tier control?** It is the second binding lever with none.
3. **Is a constant multiplier doing any work at all?** A single value applied to 100 % of trades is
   indistinguishable from halving the concentration cap — ⛔ except that it is applied *after* the
   caps and *before* the floor, which is not the same thing at `raw_qty = 1`.
4. **What should happen at `raw_qty = 1`** — trade one share, or refuse? The floor currently decides,
   and it decides "trade", 111 times so far.
5. **Should the floor-at-1 exist on the delivery path?** At ₹10k it is the difference between one
   share and none, and a delivery share is carried overnight.
6. **Does the 2× cap (`min(tiered_qty, raw_qty × 2)`) ever bind?** It cannot while `effective_mult`
   ≤ 1.0 — ⛔ it is a guard for a configuration that has never run.
7. **Is `perf_weight = 1.0` on 483/483 a measurement or a default?** ⛔ Not established here; the
   PerformanceAllocator's own reachability is a separate thread.
8. **Should the tier be applied to quantity at all, or to risk?** Halving quantity halves risk *and*
   exposure; the two are not always the intended pair.
