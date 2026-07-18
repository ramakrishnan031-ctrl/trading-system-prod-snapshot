# Q10 Part A — the per-day active min-score control (18-Jul-2026, READ-ONLY)

Reconstructs the `min_pass_score` that was **actually active** on each of the 23 book days, and
quantifies what adding it as a control does to the regime analysis. No token needed; nothing
was changed (`mode=ro` throughout).

---

## LEAD ANSWER

**Authoritative source: `config_snapshots` (the full resolved `AppConfig` written at every
boot, `main.py:2099` → `core/config_snapshotter.py`) — but it only starts 02-Jul-2026,
covering 11 of the 23 book days.** A second, fully independent source — the *observed*
rejections in `signals.status` (`REJECTED_SCORE_<n>`) — confirms it exactly where the two
overlap, but only exists from 09-Jul. **For 15-Jun → 01-Jul (12 of 23 days, more than half the
book) the active threshold is NOT determinable, and I am not guessing it.**

**Rama's belief is confirmed: the book ran at both 55 and 60.** Determined days:
**6 at min=60, 4 at min=55, 1 mixed (06-Jul changed mid-session), 12 undetermined.**

**Does the min-score control leave the regime question answerable? NO — emphatically, and far
more firmly than before.** The largest determined cell (LONG/INTRADAY at min=60) has **6
trading days**; split across 3 regime buckets that is **~2 days per bucket**. At min=55 it is
**4 days → ~1.3 per bucket**. Nothing is remotely near the n<10 line — we are now at *one to
two days per cell*. Part A does not weaken the Q10 verdict; it **hardens it**.

**⚠️ And the one genuinely actionable finding: the threshold must be FROZEN while data
accumulates.** The 2.2-month estimate for detecting a large effect assumes a *single*
threshold throughout. Every mid-window change to `min_pass_score` splits the sample and resets
the clock for the affected group. If the regime feature is to be measured at all, `min_pass_score`
must stop moving for the duration.

---

## A1 — Where the active min-score is recorded (authoritative source)

| Source | Covers | Verdict |
|---|---|---|
| **`config_snapshots` table** — full resolved `AppConfig` JSON per boot (`main.py:2099`, writer `core/config_snapshotter.py:141-190`; schema `core/schema.sql:1459-1470`, v41) | **02-Jul → 17-Jul** (13 rows / 12 dates) | ✅ **AUTHORITATIVE** — it is what the process actually loaded, at `scoring.min_pass_score` |
| **`signals.status` = `REJECTED_SCORE_<n>`** — the score of every screen-rejected signal | **09-Jul → 16-Jul** | ✅ **Independent empirical confirmation** (see cross-check below) |
| **git history of `config/scoring_weights.yaml`** | all dates | ⚠️ **NOT SAFE ALONE** — see the 06-Jul proof below |
| strategy-level `min_score` overrides | all dates | ✅ **all 16 YAMLs = `0`**, and no commit in the window set one non-zero ⇒ **the global value governs universally** (no override confound) |

**⚠️ Why git dates alone are unsafe — proven, not assumed.** `config_snapshots` shows
**06-Jul had two boots**: `08:15:22` with `min_pass_score=60`, then **`11:20:12` with
`min_pass_score=55`**. But the git commit that lowers it (`8a3e0b7`) is dated **06-Jul 20:24 —
nine hours *after* it was already live on the VM**. So the VM config can *lead* git. Naive
"commit date → effective date" inference would have mis-dated this change. This is exactly why
I refuse to infer the June days from git alone.

The second-order rule also matters: config is **loaded once at boot**, so a change committed
after 08:15 only takes effect at the *next* boot. Confirmed by `61ae9cc` (10-Jul 10:04 → 60):
10-Jul still ran **55**, and 60 first appears at the **13-Jul** boot (11–12 Jul was a weekend).

**Cross-check (the two independent sources agree perfectly):**

| Date | `config_snapshots` | Empirical (max rejected score / any reject in 55–59) | Agree? |
|---|---|---|---|
| 09-Jul | 55 | max=54, zero rejects in 55–59 ⇒ 55 | ✅ |
| 10-Jul | 55 | max=54, zero rejects in 55–59 ⇒ 55 | ✅ |
| 13-Jul | 60 | max=59, 2 563 rejects in 55–59 ⇒ 60 | ✅ |
| 14-Jul | 60 | max=59, 1 133 rejects in 55–59 ⇒ 60 | ✅ |
| 15-Jul | 60 | max=59, 1 946 rejects in 55–59 ⇒ 60 | ✅ |
| 16-Jul | 60 | max=59, 103 rejects in 55–59 ⇒ 60 | ✅ |

*(Logic: a rejection recorded at score 55–59 can only happen if the threshold is above 59.)*

**Why the empirical method cannot reach June:** signal volume was 3–463/day through 08-Jul and
**no score-bearing status was recorded at all**; from 09-Jul volume jumps to ~7 800/day *and*
`REJECTED_SCORE_<n>` statuses appear. So there is simply no score record for the June days.

---

## A2 — Per-day active min-score (23 book days)

| Days | Active `min_pass_score` | Source | Confidence |
|---|---|---|---|
| 02-Jul, 03-Jul | **60** | `config_snapshots` | authoritative |
| **06-Jul** | **60 → 55 (changed at 11:20:12)** | `config_snapshots` (2 boots) | authoritative, **SPLIT DAY** |
| 07, 08, 09, 10-Jul | **55** | `config_snapshots` (+ empirical for 09/10) | authoritative |
| 13, 14, 15, 16-Jul | **60** | `config_snapshots` + empirical | authoritative |
| **15, 16, 17, 18, 19, 22, 23, 24, 25, 29, 30-Jun, 01-Jul** | **UNDETERMINED** | — | ⚠️ **not determinable — NOT guessed** |

**Distribution of the 23 days: 6 × min=60 · 4 × min=55 · 1 mixed · 12 undetermined.**

For the record, git *suggests* the June days ran 55 from 16-Jun (commit `7614941`, 15-Jun
20:45) and 60 from 19-Jun (commit `6450ee9`, 18-Jun 12:31) — but given the 06-Jul proof that
the VM can lead git, and with no snapshot or signal evidence to corroborate it, **that is a
hypothesis, not a finding.** It is recorded here only so it can be checked if another source
ever surfaces.

---

## A3 — The confound, quantified: what the controls actually leave

Trades / trading-days per cell, once split by min-score:

| Group | Cell | trades | **days** | net P&L | win% | sum R | flag |
|---|---|---:|---:|---:|---:|---:|---|
| **min=60** (6 days) | LONG/INTRADAY | 38 | **6** | −54.4 | 42.1% | −6.93 | |
| | SHORT/INTRADAY | 5 | 3 | +14.9 | 80.0% | +1.29 | unreliable |
| | LONG/POSITIONAL | 3 | 2 | −10.2 | 33.3% | −0.75 | **anecdotal** |
| **min=55** (4 days) | LONG/INTRADAY | 25 | **4** | −54.1 | 28.0% | −10.98 | |
| | LONG/POSITIONAL | 8 | 4 | −29.7 | 37.5% | −1.26 | unreliable |
| | SHORT/INTRADAY | 3 | 2 | −0.2 | 66.7% | +0.36 | **anecdotal** |
| **06-Jul mixed** | (all) | 8 | 1 | −4.4 | — | −1.58 | **anecdotal** |
| **undetermined** (12 days) | LONG/INTRADAY | 39 | 10 | +0.9 | 35.9% | −5.94 | unusable (threshold unknown) |
| | LONG/POSITIONAL | 21 | 8 | +48.0 | 38.1% | +6.34 | unusable |
| | SHORT/INTRADAY | 5 | 5 | +14.2 | 60.0% | +2.26 | unusable |

*(Totals reconcile: 46 + 36 + 8 + 65 = 155 trades; 6 + 4 + 1 + 12 = 23 days.)*

**Now apply the full control set — direction × horizon × min-score × 3 regime buckets:**

| Cell | trading days | **days per regime bucket** |
|---|---:|---:|
| LONG/INTRADAY, min=60 | 6 | **2.0** |
| LONG/INTRADAY, min=55 | 4 | **1.3** |
| LONG/POSITIONAL, min=60 | 2 | **0.7** |
| LONG/POSITIONAL, min=55 | 4 | **1.3** |
| SHORT/INTRADAY, min=60 | 3 | **1.0** |
| SHORT/INTRADAY, min=55 | 2 | **0.7** |

**Does ANY cell clear the n<10 unreliable line? NO. Not one — and not by a small margin.** The
best case is **2 days per bucket**. We are no longer discussing weak statistics; there is
effectively **one observation per cell**. Any Bull-vs-Bear comparison inside a properly
controlled cell would be comparing single days to single days.

There is a second, subtler damage: the 12 undetermined days are **June, the better-performing
period** (LONG/POSITIONAL +48.0, +6.34R there vs −0.75R in July at min=60). Dropping them for
being uncontrollable would bias whatever remains toward the worse July stretch — so their loss
is not statistically neutral either.

---

## A4 — Side finding: 55-days vs 60-days (observational only)

Determined days only (6 vs 4; 06-Jul excluded as mixed):

| Group | days | trades | net P&L | win rate | sum R | **R / trade** |
|---|---:|---:|---:|---:|---:|---:|
| **min = 60** | 6 | 46 | −49.8 | **45.7%** | −6.39 | **−0.139** |
| **min = 55** | 4 | 36 | −83.9 | **33.3%** | −11.89 | **−0.330** |

**Directionally, the lower threshold looks worse** — lower win rate (33.3% vs 45.7%) and about
**2.4× worse R per trade** — which is what you would expect if a lower bar admits weaker
signals. **Do not act on this yet.** It is 6 days against 4, both groups are loss-making, and
the two groups are *different calendar weeks*, so market conditions, scanner mix and everything
else are confounded with the threshold. It is a genuine input for the D3 / `min_pass` decision
and it points the same way as intuition — but it is an observation, not evidence.

---

## What this changes

1. **The Q10 verdict is unchanged and strengthened.** "Not determinable at n=23" becomes "not
   determinable, and the properly-controlled cells hold ~1–2 days each".
2. **Freeze `min_pass_score` if you want this measured.** The ~2.2-month estimate for a large
   effect assumes one threshold throughout; each change re-fragments the sample. This is the
   single most actionable output of Part A.
3. **`config_snapshots` is the right instrument going forward** — every future day is
   self-documenting, so this reconstruction problem does not recur. The gap is historical only.
4. **Part B is still worth running** (backfill + tercile bands) — it is the Phase 3 calibration
   asset and it starts the clock — but it will not, and now clearly cannot, deliver a verdict.

---

## Not done / not in scope

Part B was **not** started: it requires a refreshed Kite token and Rama's confirmation, neither
of which has been given. No backfill, no Phase 1 code, no new axis, no window config, no
weighting. **`regime.enabled` and `v3_chain_mode` untouched** (the V3 chain scores 8/40 Context
from regime; flipping mid-soak would break pre/post comparability of the F1/V3 shadow rows). No
schema change. No token refresh attempted.
