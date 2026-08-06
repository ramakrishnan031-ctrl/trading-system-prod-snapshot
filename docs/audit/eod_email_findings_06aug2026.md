# EOD-EMAIL FINDINGS — 06-Aug-2026 · ⛔ MEASURED AND FILED. **NOTHING FIXED.**

**Read-only, service stopped (20:32:19), DB quiescent.** ⛔ No code · no DB edit · no P&L adjusted ·
no check suppressed. **Fixes go through the full loop like everything else.**

---

# §1 · 🔴🔴🔴 THE DAY'S P&L IS UNDERSTATED BY MORE THAN ITS OWN REPORTED VALUE — **CONFIRMED**

## §1.1 · The measurement

**(P) the DB's own day-total matches the EOD report EXACTLY** ⇒ the report is faithful; **the
corruption is upstream of it.**

| symbol | entry | exit | qty | gross | charges | net | exit_reason |
|---|---|---|---|---|---|---|---|
| MAYURUNIQ | 798.20 | 788.90 | 1 | −9.30 | 0.84 | −10.14 | SL_HIT |
| ASKAUTOLTD | 656.60 | 676.20 | 1 | **+19.60** | 1.72 | **+17.88** | GTT_EXIT |
| DECNGOLD | 230.25 | 228.15 | 2 | −4.20 | 0.48 | −4.68 | SL_HIT |
| ASTERDM | 865.80 | 852.50 | 1 | −13.30 | 0.92 | −14.22 | SL_HIT |
| **reported total (n=4)** | | | | **−7.20** | **3.96** | **−11.16** | |

**(P) ATULAUTO's row, in full:**
```
symbol    status  entry_actual_price  exit_price  exit_time  gross_pnl  charges  net_pnl
ATULAUTO  OPEN    587.4               (empty)     (empty)    (empty)    (empty)  (empty)
```
> ⛔⛔ **THE SALE IS NOT MIS-PRICED — IT IS ABSENT.** Every closure field is empty and the status is
> still `OPEN`. **(S)** the mechanism is F6: `held==0` (`cnc_gtt_monitor.py:487`) is the sole door to
> `_finalize_gtt_exit`, and `abs()` at `:464` keeps `held=1` ⇒ **the trade never closes ⇒ no P&L row
> is ever written.**

## §1.2 · The corrected day

Entry **587.40**, sold **≈575.50** *(⚠️ **PROVENANCE: Rama's Kite order-book reading, NOT a system
measurement — the system has no record of the fill, which is the finding itself.** Bounded at source:
the GTT was `sl_trigger 575.65` / `sl_limit 558.35`, so any fill lies in **[558.35, 575.65]** and
575.50 is consistent).*

| | reported | **corrected** | |
|---|---|---|---|
| **gross** | **−7.20** | **≈ −19.10** | 🔴 **the missing loss (−11.90) is 165 % of the reported gross** |
| **net** | −11.16 | **≈ −23.9 … −24.8** | ⚠️ **bounded, not exact** — needs the contract note; ATULAUTO's **entry-side** charges settled on 05-Aug. Comparable same-day round trips: 0.84 (798) · 0.92 (866) · 1.72 (656→676) |

> ### ⭐⭐⭐ **THE REPORTED DAY IS −7.20 GROSS. THE REAL DAY IS ≈ −19.10. THE ERROR IS LARGER THAN THE FIGURE REPORTED.**

## §1.3 · 🔴 WHAT IT FEEDS — **not a display problem**

| consumer | effect |
|---|---|
| **the daily-loss limit** | the ₹≈280–300 cap is read against **−11.16**, not ≈−24 ⇒ ⭐ **a real loss is invisible to the control built to bound it** |
| **the expectancy corpus** | ⭐⭐ **the same 483-trade corpus every sizing conclusion this week rests on** |
| **strategy health** | ⛔ `positional_momentum_long` reports **100 % win** while its second delivery trade of the week lost |

## §1.4 · 🏷️ CLASSIFY AS F6 COST **#7** — and it may outrank the other six

> ⭐⭐ **Costs 1–6 consume capital, a slot, a symbol, live broker orders, an exit price, and an
> operator's evening. THIS ONE CORRUPTS THE MEASUREMENT LAYER** — and the corruption is **silent,
> permanent, and already flowing into the corpus we are drawing conclusions from.**

⚠️ **AND IT COMPOUNDS WITH COST #5.** `_resolve_exit_price` falls through to *today's* LTP, so when
the trade eventually closes it books **the wrong price on the wrong day**. ⇒ 🔴 **the loss is missing
today, and it will land wrong later.** ⛔ **Two independent corruptions of the same trade.**

---

# §2 · ⚠️ `reconcile_positions` EXIT 2 — **BOTH PREMISES REFUTED. IT DID NOT FAIL.**

## §2.1 · Exit 2 is a FINDING code, not a crash — **(S) `scripts/reconcile_positions.py:428-434`**

```python
has_mismatch = any(r["status"] not in ("OK", "ERROR") for r in results)
has_error    = any(r["status"] == "ERROR" for r in results)
if has_mismatch: return 2      # <-- mismatches DETECTED
if has_error:    return 1      # <-- the ERROR path
return 0
```
⇒ ⭐⭐ **`2` = "mismatches found". The crash path is `1`** (including the `except Exception` at
`:424`). **The job ran to completion in 1.01 s, detected mismatches, and reported them by design.**
**(P)** today's log tail is **11 × `reconcile_positions.mismatch`** — the mismatch branch, reached
only *after* a successful broker fetch.

## §2.2 · ⛔ "For the FIRST time" — **FALSE.** Width: whole `cron_heartbeat` table

**(P) 38 runs: 33 SUCCESS · 4 FAILED · 1 SKIPPED.** Prior exit-2s on **29-Jul** and **31-Jul**, whose
mismatch set was `['IOB','MSUMI','SJVN','SOUTHBANK','TRIDENT']` — ⇒ ⭐ **no T+1 holding was involved
on those days. Exit 2 is the ordinary mismatch path, not a T+1 signature.**
🏷️ **The "blind to T+1" hypothesis is NOT supported by this evidence** — the register's claim about
`reconcile_positions` reading `positions()` only may still be true, ⛔ **but today's exit 2 does not
demonstrate it.**

## §2.3 · ⚠️ I NEARLY ATTRIBUTED THE WRONG CAUSE — recorded because the near-miss is the lesson

The log contains `reconcile_positions.broker_fetch_failed: ZERODHA_API_KEY and ZERODHA_ACCESS_TOKEN
must be set`. ⛔ **It is NOT today's** — it appears **exactly once in the whole file** (width stated),
and today's run reached the *mismatch* branch, which is unreachable without a successful fetch.
⭐ **The log carries no timestamps; `cron_heartbeat` is the authoritative source and it settled it.**

## §2.4 · What it cost — **nothing went unreconciled**

**(P)** `eod_broker_reconcile` 15:58:02 **SUCCESS**, and it independently reported:
`positions: broker≠local for ['ATULAUTO']`. ⇒ **the divergence was detected twice**, by both jobs.

## §2.5 · 🔴 THE REAL FINDING HERE — **a FINDING is being reported as a FAILURE**

The heartbeat wrapper maps **any non-zero exit** to `status=FAILED`, so a **designed
mismatch-detected code** arrives as `FAILED — exit code 2`, and from there into a CRITICAL email.
⛔ **The job is behaving correctly; the SEVERITY MAPPING is wrong.** 🏷️ Same family as §3 and as
`feedback_never_classify_by_free_text`. ⛔ **Recorded, not fixed.**

---

# §3 · THE DAILY-CRITICAL TREE DIFF — filed in `expected_alarms.md` §9, with its discriminator

*(See that file. Summary: a known-benign daily rewrite of `config/strategy_direction_registry.yaml`
makes the whole EOD report CRITICAL every day.)* ⛔ **The check is right; the CLASSIFICATION is
missing. Do NOT suppress the check.**

⚠️ **The second-order cost is not hypothetical — it landed tonight:** a report that is CRITICAL every
day trains the reader to skip its severity line, **and neither §1 nor §2 was noticed from the report.**

---

# §4 · `watchman.md` / `flow_trace.md` — ⭐ **THE CHECK IS THE DEFECT**

**(S) the checker:** `scripts/system_manager.py:409-410`
```
_check("watchman.md",   root / "reports/watchman"   / f"watchman_{day}.md", 200)
_check("flow_trace.md", root / "reports/flow_trace" / f"trace_{day}.md",    100)
```
**(S) the producer, width: whole repo, all file types — NONE.** The only hits are **the checker
itself**, a note in `SYSTEM_MAP.md`, and one June report. **No scheduled or scripted writer exists
for either path.**

**Corroborated by the record, not just by my grep:**
- `SYSTEM_MAP.md:1293` already states: *"Also checked and **not** a finding: `watchman.md`/`flow_trace.md`
  MISSING appears in the 22-, 23- and 24-Jul reports too."*
- memory `watchman`: **"NOT SCHEDULED, never has been (11 manual runs)."**

> ⇒ 🏷️ **ANSWER: not "expected to exist". `watchman` is a MANUAL, unscheduled producer and
> `flow_trace` has no producer at all.** ⭐ **So the CHECK is the defect — a daily warning for an
> artifact nobody produces any more.** ⛔ **Do NOT build either.** The decision owed is *retire the
> check, or restore a producer* — **Rama's, not this document's.**

---

# §5 · RAMA'S THREE-GTT SUMMARY — **all three readings CORRECT**

| | measured |
|---|---|
| **ATULAUTO** | ✅ correct on all three points — and §1 adds that **its loss is missing from the day's P&L** |
| **ASKAUTOLTD** | ✅ correct — 656.60 → 676.20, **+₹17.88 net**, `GTT_EXIT`, the clean path |
| **DIFFNKG** | ✅ correct — carries into Friday, protected, `330658430 → trd_010f8e21…` matched |

⭐ **What the emails add is what the positions page could not show: the missing P&L, the mislabelled
cron, and the daily CRITICAL.**

---

*⛔ Nothing fixed. Nothing adjusted. No check suppressed. §1.4's cost-#7 classification and §2.5's
severity-mapping defect are FILED as items, not actioned.*
