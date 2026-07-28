# T2 MULTI-STOCK ARM — BLOCKER REPORT

**Written 28-Jul-2026 ~22:4x IST, for the 29-Jul arm decision. Investigation only —
nothing built, no script modified, no runbook rewritten, no config changed.**

---

## ⭐⭐ THE HEADLINE: NOTHING IS BLOCKED, AND ONE CONSTRAINT HAS SWAPPED

**No answer below requires a code change.** The proof-run script already supports
everything Rama asked for. The arm can proceed on the existing runbook.

⚠️ **But going from 1 stock to 3–5 has INVERTED which constraint binds.**
- Void risk **stops mattering** — with N≥3 the chance the test yields nothing falls to
  a rounding error, **even arming at 10:30**.
- **Rama's available MINUTES become the binding constraint instead**, and
  **10:30–11:00 no longer fits N=5.**

That reframing is the single most decision-relevant thing in this report.

---

## §B — THE SEPARATION. T2 IS ISOLATED. NEITHER CAPITAL ITEM NEEDS A CHANGE.

### B1 — Is the proof run isolated? ✅ **YES, from source, and it is guarded.**

| Dimension | Verdict | Evidence (`scripts/t2_cnc_gtt_realtest.py`) |
|---|---|---|
| **Live DB** | ✅ ISOLATED | `_throwaway_store_path()` → `data_store/t2_proof_<ts>/t2_proof.db`, in **its own subdir** because `StateStore` ATTACHes an `analytics.db` *beside* the main DB — a bare file would have attached the **live** analytics.db |
| **Guarded** | ✅ ENFORCED | `_assert_isolated()` raises `SystemExit` if the path or its analytics sibling resolves to a live DB (`:191-202`) |
| **Capital path** | ✅ NEVER TOUCHED | `_build_live_adapter()` (`:142-176`) builds its **own** `ZerodhaAdapter`. **No `FundManager` import, no bucket read, no `fm_ledger` write, no capital reservation anywhere in the file.** Orders go straight to `adapter.place_order()`, bypassing signal_processor / position_sizer / order_placer |
| **Broker account** | ⚠️ **SHARED — the one thing that is NOT isolated** | Real kite session, real money. Consequence is **cash**, quantified in C6 |

⇒ **B2 applies: the 70/30 split is irrelevant to tomorrow.** T2 never reads it.

### B2 — The 70/30 split ✅ **ALREADY 70/30. NOTHING TO CHANGE.**
`config/system_config.yaml:136-137`:
```
intraday_bucket_pct: 0.70   # 70% of total capital for intraday (MIS/CO)
positional_bucket_pct: 0.30 # 30% of total capital for positional (CNC)
```
Rama's requested allocation **is the deployed default**. No config change is needed,
tomorrow or ever, to satisfy that instruction.

### B4 — "₹10,000" ✅ **A STATEMENT OF BALANCE, NOT A CONFIG VALUE.**
**There is no rupee capital figure in config.** Every capital key is a *percentage*
(`intraday_bucket_pct`, `risk_per_trade_pct`, `max_concentration_pct`,
`max_position_value_pct`). The rupee amount is seeded from **`broker.net`** at the
08:15 INIT — `capital/fund_manager.py:433` writes the INIT row, and `:1726`/`:1810`
state the seed equals `broker.net` by construction.

⇒ **Nothing to change.** If the account holds ₹10,000 at 08:15, the system seeds
₹10,000 by itself. (It seeded ~₹9,872 on 28-Jul.)

---

## §C — THE BLOCKER LIST

### C1 — Multi-symbol support? **NO / NOT BLOCKED — run it N times, unchanged.**
- `--symbol` takes **one** value (`:541`, no `nargs`). The script is single-symbol.
- ✅ **`--qty` already exists** (`:543`, `type=int`) ⇒ "above 3 shares" needs **no modification**.
- ✅ **Run N times is safe. Checked, not assumed:**
  - Each run mints a **fresh timestamped throwaway DB** (`t2_proof_<YYYYmmdd_HHMMSS>/`)
    ⇒ **run 2 cannot overwrite run 1's evidence.** Runs are minutes apart; only a
    same-second start could collide.
  - `_T2_TRADE_ID = "t2"` is hardcoded but lives *inside each run's own DB* ⇒ no clash.
  - Step zero's hash is unaffected — running a file does not change it.
- ⚠️ **Two cosmetic warts, neither affecting a PASS criterion:** the seeded `signals`
  and `trades` rows hardcode `symbol='IDEA'` (`:228`, `:236`) and `qty_planned=1`, so a
  YESBANK run writes an "IDEA" parent row in its throwaway DB. The store assertion
  checks `gtt_id`, `status` and `qty` only (`:392-394`) — **not symbol** — so the proof
  is unaffected. Worth knowing before someone reads that DB and is confused.
- ⚠️ **Operator-form gap, not a code gap:** runbook §2 has **one** blank for `GTT_ID`.
  N runs produce N ids, and Thursday's `--close-overnight <GTT_ID>` needs the right
  **symbol+id pair**. Rama needs an N-row list, not one blank.

### C2 — Void semantics: **PER-POSITION.** Multi-stock is strictly better evidence.
Each run is fully independent: own throwaway DB, own GTT, own broker position. The
question the test asks — *does a CNC sell of an overnight-held share need a TPIN
prompt?* — is answered by **any one surviving position**.

⇒ The metric that matters is **P(all N void) = the test yields nothing**, not
P(at least one voids). Both given, using this basket's own measured rate (C3):

| | p (per position) | N=3 | N=4 | N=5 |
|---|---|---|---|---|
| **P(≥1 voids)** — arm 13:00 | 3.53% | 10.2% | 13.4% | 16.5% |
| **P(≥1 voids)** — arm 10:30 | 10.59% | 28.5% | 36.1% | 42.9% |
| ⭐ **P(ALL void → test dead)** — 13:00 | | **0.004%** | **0.0002%** | **~0%** |
| ⭐ **P(ALL void → test dead)** — 10:30 | | **0.12%** | **0.013%** | **0.001%** |

⭐⭐ **THIS IS THE FINDING THAT CHANGES THE DECISION.** At N=1 the arm time was worth
7 percentage points of void risk (3.53% vs 10.59%). **At N=5 both columns are
effectively zero.** Going multi-stock has made the 10:30-vs-13:00 question **almost
irrelevant to test validity.**

### C3 — Is ~2.8% right for *this* basket? **NO — RE-DERIVED. Use 3.53%, not 2.8%.**
⛔ **All seven candidates have ZERO 1-minute candles in our data** (`analytics.db`
holds 577 symbols / 934 symbol-days; none of them is a candidate). So the rate cannot
be computed *for these symbols*. It **can** be computed for the same **price band**,
which is the honest proxy — re-derived tonight, read-only:

| cohort | anchor | symbol-days | voids | rate |
|---|---|---|---|---|
| all symbols | 13:00 | 934 | 22 | **2.36%** ← corroborates the inherited ~2.8% |
| **under ₹150** | **13:00** | 85 | 3 | **3.53%** |
| all symbols | 10:30 | 934 | 57 | **6.10%** |
| **under ₹150** | **10:30** | 85 | 9 | **10.59%** |
| under ₹150 | 11:30 | 85 | 8 | 9.41% ← corroborates the inherited ~9.0% band-alone figure |

⚠️ **LIMITS, STATED RATHER THAN BURIED:**
- **n = 85 symbol-days across 53 symbols. THIN.** Three voids at 13:00 — one more
  moves the rate by 1.2pp. Treat as an order of magnitude, not a precise number.
- ⛔ **The sub-₹50 question is UNDERIVABLE: our entire dataset holds ONE symbol under
  ₹50.** Rama's basket is **five of seven under ₹50** (see C4 table). Do not quote a
  sub-₹50 figure — none exists.
- ⚠️ **Different population.** The 577 are momentum-tracked *breakout candidates*,
  selected for movement; Rama's basket is large, liquid PSU/bank names. If anything
  these rates **over-state** his risk — but that is a judgement, not a measurement.

### C4 — Broker eligibility. **PARTIAL — and it found a real problem.**
Measured live from `kite.quote()` tonight:

| Symbol | LTP | Circuit band | ±10% GTT legs | Verdict |
|---|---|---|---|---|
| **YESBANK** | 22.81 | **10%** (20.53–25.09) | 20.5 / 25.1 | 🔴 **BOTH LEGS AT/OUTSIDE THE CIRCUIT** |
| IOB | 33.84 | 20% (27.08–40.60) | 30.5 / 37.2 | ✅ inside |
| TRIDENT | 24.78 | 20% (19.83–29.73) | 22.3 / 27.3 | ✅ inside |
| SOUTHBANK | 47.13 | 20% (37.71–56.55) | 42.4 / 51.8 | ✅ inside |
| MSUMI | 41.52 | 20% (33.22–49.82) | 37.4 / 45.7 | ✅ inside |
| **NHPC** | 78.28 | **10%** (70.46–86.10) | 70.5 / 86.1 | 🔴 **BOTH LEGS AT/OUTSIDE THE CIRCUIT** |
| SJVN | 67.65 | 20% (54.12–81.18) | 60.9 / 74.4 | ✅ inside |

⭐ **This is exactly the failure the runbook §8 names and could not previously check**
— *"GTT REJECTED at placement (band too wide for the broker's server-side cap, which
we cannot read from source)"*. **The circuit limit is readable, and it says two
candidates are at the boundary.** A rejected GTT burns that stock's day (the position
does end FLAT — `intentional_hold` stays False, so the `finally` squares it).
⚠️ Circuit *levels* reset daily off the previous close; the **10%-vs-20% band width is
a property of the scrip** and is what matters.
- ✅ **F&O ban list: NONE of the seven** (`fno_ban` table; only KAYNES appears recently).
- ⛔ **ASM / GSM / trade-to-trade: UNKNOWN — not exposed by the Kite API.** The narrow
  10% band on YESBANK/NHPC is *suggestive* of surveillance but **I have not verified
  it and will not infer it.** Kite's instrument page shows an ASM/GSM tag — **that is a
  30-second check Rama can do, and it is the one open item in C4.**

### C5 — GTT available on each? **UNKNOWN for certain, but no blocker found.**
Zerodha GTT is available for NSE equity CNC generally, and all seven are ordinary
NSE equity with `lot_size=1`. **There is no API that answers "is GTT permitted on X"
in advance** — the answer arrives as an accepted or rejected placement. ⇒ The
practical proxy is C4's circuit check, which is where the real risk sits.

### C6 — Margin interaction with Thursday. **NO material impact. Quantified.**
CNC blocks cash equal to the purchase value. For the proposed basket at qty=4 that is
**₹859.68** (see §E).
- Thursday's intraday bucket = 70% × broker.net. A ₹860 cash reduction lowers it by
  **~₹602**, from ~₹6,900 to ~₹6,300.
- **Measured against actual usage:** today's peak intraday deployment was ~₹664 of
  ₹6,910 (**9.6%**); individual reservations ran ₹56–158.
⇒ The binding constraints are `max_open_positions: 5` and the concentration cap, **not
available cash** — the book leaves ~90% of the bucket unused every day. **₹6,300 is
still ~10× peak deployment.** ✅ Not a blocker.

### C7 — Thursday is now N closes. **FITS 09:15–11:00 comfortably.**
`--close-overnight` per stock: LTP → SELL → poll (max 12×2 s = 24 s) → delete GTT.
~1–2 min each including the Kite confirmation ⇒ **N=5 ≈ 10–15 min**, inside a
09:15–11:00 window with large margin. ✅ (§7 imposes no clock time, only market hours.)

### C8 — Instrument cache. ✅ **ALL SEVEN PRESENT, tokens verified against the broker.**
`config/instruments.csv` (2,229 rows) — every candidate resolves, `exchange=NSE`,
`lot_size=1`, `tick_size=0.01`, and **every token matches what `kite.quote()` returned
tonight**. ⭐ Note the naming trap avoided: **"South Indian Bank" is `SOUTHBANK`**
(`SOUTHINDBANK` is **absent** — using it would have failed).

---

## §D — THE PRESENCE SPLIT FOR N POSITIONS

### D1 — Step classification (the arm)

| Step | Who | Why — from the step's own wording |
|---|---|---|
| §0 hash check | 🤖 SSH | `sha256sum`; no credential, no order |
| §1 P0 date · P2 service · P5 no CRITICAL | 🤖 SSH | mechanical reads |
| §1 P1 Tuesday clean · P4 dates | ✅ done | answered 28-Jul: **PROCEED**, schema 45 |
| §1 **P3 arm time** | 📱 **RAMA** | *"13:00 is an OPERATOR choice"* — a judgement |
| §1 **P6 ~30 min + at a screen Thursday** | 📱 **RAMA** | only he knows |
| **§2 THE ARM** | 📱 **RAMA** | 🔑 **from source, not inference:** the script's own docstring says *"This places REAL orders with REAL money on the live account. **Rama runs it MANUALLY** during market hours."* ⛔ It is an SSH command **but it is an order placement** |
| ✍️ record GTT_ID ×N | 📱 RAMA | output of his run |
| **§3 by-hand broker check** | 📱 **RAMA** | *"CONFIRM BY HAND AT THE BROKER, WHATEVER THE SCRIPT SAID"* · *"ALL THREE MUST BE TRUE BEFORE YOU WALK AWAY"* ⛔ **never automate — D4** |
| §5 Thursday orphan-GTT warning | 🤖 SSH | log/alert observation |
| §6 contingencies | 📱 RAMA | judgement |
| §7 close ×N | 📱 RAMA | live orders |

### D2 — ⚠️ **RAMA-MINUTES, AND THIS IS WHERE N=5 BREAKS**
⛔ **These are ESTIMATES, not measurements.** The only anchor is the runbook's own P6
budget of **~30 minutes for N=1** — and that is itself a budget, never timed. I have
decomposed it rather than scaled it, and I have **not shaved it (D6)**:

| | fixed overhead | per position | **total** | fits 10:30–11:00? |
|---|---|---|---|---|
| N=1 | ~20 min | ~4 min | **~24 min** *(runbook says ~30)* | ✅ yes |
| **N=3** | ~20 min | ~4 min ×3 | **~32 min** | ⚠️ **borderline — over by ~2 min** |
| **N=5** | ~20 min | ~4 min ×5 | **~40 min** | 🔴 **NO** |

Fixed = card, hash, preconditions, SSH setup. Per-position = one script run (~1 min:
LTP → BUY → poll ≤24 s → GTT → verify) **plus the §3 by-hand Kite check (~3 min:
Positions, Orders→GTT, Funds)**. The by-hand check is the part that genuinely
multiplies, and it is the part that must not be rushed.

⇒ **Contiguous? Yes** — the runs are sequential and there is no waiting between them.
**Latest start / earliest finish for the availability window:** to finish by 11:00 he
must start by **~10:28 for N=3** and **~10:20 for N=5** — i.e. **before the runbook's
own 10:30 ABORT floor.** ⛔ **N=5 cannot be completed inside 10:30–11:00 without
either starting earlier than the runbook permits or running past 11:00.**

### D3 — Can his part be done from a phone? ⚠️ **PARTIALLY — and the gap is the arm.**
- ✅ **§3's by-hand Kite check is phone-native** — that is exactly what the Zerodha app does.
- 🔴 **§2's arm is NOT.** It needs an SSH terminal running a multi-line command with
  `set -a && . ./.env && set +a` and the `--i-understand-this-places-a-real-cnc-order`
  flag. Possible via a phone SSH client, but it is a **real-money command that has
  never been rehearsed from a phone**, and the phone has been **off the tailnet 23
  days**, so it would go over public port 22.
⇒ **Reporting this as a finding, not endorsing it.** If the arm must happen from a
phone, that is a new unrehearsed path on the money leg.

### D5 — Arm-time options, minutes against void risk. ⛔ **PRESENTED, NOT CHOSEN.**

| Option | Rama-minutes needed | P(test yields nothing) | Fits availability? |
|---|---|---|---|
| **13:00 aim**, N=5 | ~40 min inside 11:00–17:00 | **~0%** | ❌ inside the unavailable window |
| **12:30–14:00 band**, N=5 | ~40 min inside 11:00–17:00 | ~0% | ❌ same |
| **10:30–11:00**, N=3 | ~32 min vs 30 available | 0.12% | ⚠️ **~2 min over** |
| **10:30–11:00**, N=2 | ~28 min | 1.1% | ✅ fits |
| **10:30–11:00**, N=5 | ~40 min vs 30 available | 0.001% | ❌ **does not fit** |
| **Slip to 4-Aug** | any | ~0% | ⚠️ collides with the flag-flip gate |

⭐ **The trade has changed shape.** At N=1 the question was *"how much void risk does
an early arm cost?"*. At N=3–5 void risk is gone and the question is *"how many of
Rama's minutes are available, and when?"* — **a scheduling question, not a validity one.**

---

## §E — QUANTITY AND BASKET (proposal, on validity grounds)

### E1 — Quantity: **4 shares per stock.** The smallest number satisfying "above 3".
`lot_size = 1` and `tick_size = 0.01` for all seven (instrument cache), so 4 is legal
everywhere. ⭐ **Size adds nothing to a lifecycle test and only adds loss** — the proof
is the OCO shape, the demat debit and the no-TPIN sell, none of which scale with qty.

| Symbol | LTP | ×4 |
|---|---|---|
| IOB | 33.84 | ₹135.36 |
| TRIDENT | 24.78 | ₹99.12 |
| SOUTHBANK | 47.13 | ₹188.52 |
| MSUMI | 41.52 | ₹166.08 |
| SJVN | 67.65 | ₹270.60 |
| **TOTAL NOTIONAL** | | **₹859.68** |

- **Worst case at the −10% SL leg: ~₹86.** The SL is a stop-*limit* a further ~3%
  below, so a full sweep is ~₹112. A gap through ~−13% leaves the share unsold —
  still held, still ~₹860 of stock.
- ⚠️ **The cost that actually scales with N, stated honestly: Zerodha's DP charge is
  per scrip per day on the SELL side (~₹15–16).** N=5 ⇒ **~₹75–80** on Thursday
  versus ~₹15 for one stock. Delivery brokerage is ₹0, but DP charges are not.
  Rama said cost is not the point — this is reported so it is not a surprise.

### E2 — Basket: **IOB · TRIDENT · SOUTHBANK · MSUMI · SJVN** (N=5)
- ✅ Keeps **four of his five preferred** — all pass C4/C5/C8.
- 🔴 **DROP YESBANK** (preferred) — 10% circuit band, ±10% GTT legs at/outside it
  ⇒ real placement-rejection risk. **This is a validity reason, not a preference.**
- 🔴 **DROP NHPC** (optional) — same 10% circuit problem.
- ✅ **ADD SJVN** (his optional list, 20% band) to restore N=5.
- ⭐ If he wants YESBANK specifically, the honest options are: accept the rejection
  risk on that one leg (it fails FLAT, costing only that stock's day), or narrow the
  band for it alone — ⛔ **which is a runbook/script change and therefore not tomorrow.**

### E3 — Voids are per-position (C2), so **more stocks is safer, not riskier.**
Fewer stocks would only be safer if a void were whole-test. It is not. ⛔ Still his call.

---

## THE ONE OPEN ITEM RAMA MUST CLOSE HIMSELF
**ASM/GSM status for the five proposed stocks** — not exposed by the API; visible on
each scrip's page in Kite. A T2T or 100%-margin scrip changes the test's meaning.
Everything else in C1–C8 is answered.

---

**Verified read-only tonight:** the script in full, `config/system_config.yaml`,
`config/instruments.csv`, `capital/fund_manager.py`, the live `fno_ban` table,
`analytics.db` candles (934 symbol-days), and `kite.ltp()` + `kite.quote()` for the
seven candidates. **No orders placed. No config, code, runbook or DB changed.**
