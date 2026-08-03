# LEDGER #6 — `eod_verify` STUCK-PENDING + THE INVERTED SHADOW FLAG

**`<MEASURED — NOT BUILT, NOT AUTHORISED>`** 03-Aug-2026. Measurement pass only.
⛔ No code written. Building it is a separate authorisation.

**Headline: the finding is HALF WRONG, and the half that is wrong is the half the
register calls "cheap to fix, high unblocking value".**

---

## 1. ⭐⭐ IA-P8-01(i) IS A MISDIAGNOSIS — `PENDING` IS THE FIX, NOT THE BREAKAGE

**The audit says:** *"`eod_verify`'s persisted verdict has been stuck at PENDING for every
trading day since 08-Jul (18 days; last VERIFIED 07-Jul) while its heartbeat reports
SUCCESS 32/32 — **the verdict-finalize path silently stopped**."*

**Measured — `scripts/eod_verify.py:10-35`, its own header:**

> **HONEST-VERIFY (Audit-B Phase-8 + M-SC1, 07-Jul):** the P&L leg previously queried
> **NON-EXISTENT columns** (`system_net_pnl`/`broker_net_pnl`; the real ones are
> `broker_pnl`/`system_pnl`/`variance`) and **swallowed the resulting `OperationalError`
> into `pnl_variance=0.0`** → a spurious "no variance" → **a FALSE `VERIFIED` that never
> actually checked P&L (11/11 historical rows VERIFIED, variance 0.0)**.

⇒ **Nothing stopped on 08-Jul.** The **07-Jul boundary the audit measured is the date the
FIX LANDED**, and every PENDING since is the system **refusing to lie**:

| verdict | meaning (per the code's own contract) |
|---|---|
| `VERIFIED` | everything checkable is clean **AND CHECKED** — LIVE with broker P&L matched, or PAPER (P&L is N/A) |
| `PENDING` | positions & orders clean, but **P&L could NOT be checked in LIVE** (no broker feeder row). **"NOT a false VERIFIED."** |
| `ISSUES_FOUND` | positions OPEN / orders PENDING / a real P&L divergence |

**The `SUCCESS 32/32` heartbeat is also correct, not a contradiction:** exit code **0** is
documented for *"VERIFIED or PENDING (job ran; **PENDING = known gap until a broker feeder
exists**)"*, exit 2 for ISSUES. The job runs fine. **PENDING is a verdict, not a failure** —
reading the heartbeat and the verdict as though they disagreed is the mistake.

⛔ **So there is no "silently stopped finalize path" to repair.** The real condition is a
**MISSING FEEDER**, measured: **`reconcile_pnl` does not appear in
`config/cron_registry.yaml` at all** — confirming the docstring's *"reconcile_pnl
un-cronned / P1 not yet authoritative → 0 rows today"*. With `pnl_reconciliation` empty,
**`eod_verify` is STRUCTURALLY PINNED at PENDING in LIVE.** It cannot say `VERIFIED`, and
no change to `eod_verify` can make it.

⭐ **This is practice §M4 landing on the same day it was written:** the audit's description
is a hypothesis. Transcribed instead of measured, this item would have sent someone
hunting an imaginary regression in a finalize path — and the "fix" most likely to be
reached for (restore VERIFIED) would have **reinstated the false VERIFIED that 07-Jul
deliberately removed.**

## 2. ✅ IA-P8-01(ii) IS REAL — proven BY CONSTRUCTION, not just by its data

`scripts/eod_broker_reconcile.py`:

```python
p1_clean = (v.overall_status == VERIFIED)
ev_clean = (eod_verify_status == "VERIFIED") if eod_verify_status else None
mismatch = None if ev_clean is None else int(p1_clean != ev_clean)
```

A **3-state lifecycle field** (`VERIFIED`/`PENDING`/`ISSUES_FOUND`) is collapsed to a
**boolean verdict**. Combined with §1 — `eod_verify` is pinned at `PENDING` — the flag is
**inverted by construction**:

| the day | `p1_clean` | `ev_clean` | persisted `mismatch` |
|---|---|---|---|
| **clean** | `True` | `False` (PENDING ≠ VERIFIED) | **1** ← "mismatch" on a healthy day |
| **ISSUES** | `False` | `False` | **0** ← "agreement" on a bad day |

⇒ exactly the audit's measured VM data (`mismatch=1` on all 14 VERIFIED days, `0` on both
ISSUES days). **Two independent lines — the code's algebra and the VM's rows — agree.**

⚠️ **DUPLICATION HAZARD any fix must handle: those three lines exist TWICE** — `:291-294`
inside `persist()` and `:400-402` in the caller, computing the same value independently.
⛔ Fixing one site would leave the persisted flag and the logged/alerted flag disagreeing.
This is the *classify-by-the-wrong-field* family, with a *second-authority* rider.

⛔ **Could not corroborate locally:** the PC DB's `eod_verification` and
`eod_broker_reconciliation` tables are **empty** (it is a stale v44 partial copy). The
audit's counts are VM-side and were not re-measured here — **stated rather than implied.**
The construction proof above does not depend on them.

## 3. DOES IT TOUCH ANYTHING THAT RUNS **TONIGHT**? — **NO**

| job | schedule (`cron_registry.yaml`) |
|---|---|
| `eod_verify` | **15:55 Mon-Fri** |
| `eod_broker_reconcile` | **15:58 Mon-Fri** |

Both run **~2¼ hours before** the 18:15+ push window, so tonight's run happens on the
**current deployed code** either way. ⛔ **Nothing built for #6 could execute tonight.**

⭐⭐ **BUT THE SEQUENCING IS THE REAL ANSWER, AND IT IS NOT "HARMLESS":** a change pushed
tonight **first executes on TUESDAY at 15:55/15:58** — which R2 assigned as the
**SHAKEDOWN day: one more clean observation + THE FIRST REAL EOD CENSUS**, with the
buy-day filter dormant-armed. That is the one day this week whose entire purpose is to
have **no new variables in it.**

## 4. WHAT DOES IT ACTUALLY UNBLOCK? — **ONLY PART OF THE COLUMN**

G23/B3's gate is *"a clean shadow week"* — agreement between P1 (`eod_broker_reconcile`)
and `eod_verify`.

- **Fixing (ii)** stops the **inverted noise**: clean days would stop being recorded as
  mismatches.
- ⛔ **It does NOT produce a clean week.** With `eod_verify` pinned at `PENDING` (§1), the
  honest comparison becomes **"P1 says clean; eod_verify says NOT CHECKED"** — which is
  **"cannot compare"**, not **"agree"**. A gate that needs agreement is still unservable.
- ⇒ **the remaining blocker is the MISSING BROKER P&L FEEDER** (`reconcile_pnl` un-cronned
  / P1 not authoritative). **That is a different item and it is the load-bearing one.**

⭐ **And the end-state makes the shadow transitional anyway:** `eod_broker_reconcile.py:29`
— *"When authoritative, ISSUES/UNVERIFIED alert at CRITICAL and **`eod_verify` is
retired**."* So the comparison exists to license P1's promotion, after which one of the two
compared things disappears.

⇒ **Honest statement of value: fixing #6 makes the evidence column HONEST, not GREEN.**
The register's *"cheap to fix, high unblocking value"* is **half right** — cheap, yes; but
it unblocks the *measurement*, not the *gate*.

## 5. RECOMMENDATION — ⛔ **DO NOT RIDE TONIGHT.** (Rama's call.)

**Reasons, in order of weight:**
1. **Its first run would be Tuesday's shakedown/census day** (§3) — the day deliberately
   reserved for a clean observation. Changing what writes the EOD verification column on
   the morning the first real census is read adds a variable to the one day chosen to have
   none.
2. **There is no urgency.** The flag has been inverted for **18+ trading days**; it is
   noise in a **shadow** column that is **not authoritative** and that nobody acts on. One
   more week costs nothing.
3. **The fix does not deliver the gate** (§4), so riding it early buys no schedule.
4. **It is a two-site change** (§2) in EOD verification — small, but the kind where the
   second site is missed.

**Better slot:** after Wednesday's flip, together with — or after — a decision on the
broker P&L feeder, since that is what actually unblocks G23.

⛔ **Not built. No register row. 231 stands.**
