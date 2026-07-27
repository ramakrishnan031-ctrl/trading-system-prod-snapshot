# The SENCO double-entry, 27-Jul-2026 — reconstructed from the data

**Read-only investigation during market hours. ⛔ Nothing fixed, nothing changed.**
⛔ Untracked today (the Monday card asserts 24 commits).

---

## The answer in one line

**It is a POLICY GAP, not a defect.** Every gate did exactly what it was built to do; there is no
rule against re-entering a symbol you have just exited, and the scanner had been signalling SENCO
continuously all morning. **⭐ But the more useful finding is underneath it (§5), and it is not
about re-entry at all.**

---

## 1. The timeline, from `signals` / `trades` / `orders` / `screener_results`

| | trade 1 `trd_c9675b4fae4a` | trade 2 `trd_910664791d6e` |
|---|---|---|
| signal received | **10:02:12** | **10:14:11** |
| scanner / strategy | `open_low_breakout_long` | `open_low_breakout_long` — **the same one** |
| screener score | **60** (eligible 60, tier LOW) → PASSED | **60** (eligible 60, tier LOW) → PASSED |
| entry | 10:02:17 @ **418.75** | 10:14:51 @ **425.25** |
| SL / TGT | 414.54 / **425.01** | **421.02** / 431.65 |
| exit | **10:13:31 @ 425.05 — TGT_HIT** | **10:25:53 @ 420.85 — SL_HIT** |
| net | **+5.84** | **−4.86** |

**⭐ Rama's reading is exactly right.** The second entry at **425.25** is **0.20 ABOVE the first
trade's exit at 425.05** — it bought back in fractionally above the price its own strategy had just
taken profit at. **Exit → re-entry gap: 10:13:31 → 10:14:51 = 80 seconds.**

**The pair nets +0.98.** The re-entry gave back **83 %** of the first trade's profit.

⚠️ And the second trade's **stop (421.02) sat ABOVE the first trade's entry (418.75)** — the whole
risk band had been dragged up to where the first trade had already been right.

---

## 2. The five explanations, answered

**A3 — same alert twice, or a new scan hit? → A GENUINELY NEW HIT, far outside any dedup window.**
Different `triggered_at` (10:02:00 vs 10:14:00), different fingerprints. **The dedup window is not
the thing that was too narrow.**

⭐⭐ **And this is where the real shape appears. `open_low_breakout_long` fired SENCO roughly every
five or six minutes, all morning:**

```
10:02 PROCESSED  (score 60)   <- trade 1
10:08 REJECTED_STRATEGY_POSITION_LIMIT (score 60, PASSED the screener)
10:14 PROCESSED  (score 60)   <- trade 2
10:19 REJECTED_SCORE_59 · 10:25 · 10:31 · 10:36 · 10:41 · 10:47 · 10:52 ... all 59
```

**The signal was not a second event. It was a continuous stream, held back by the open-position
guard, which released the instant the position closed — and the very next hit went in.** The 10:08
hit had an identical score of 60 and was blocked *only* because a position was open.

**A4 — different strategies? → NO. The same strategy both times.** Not a cross-strategy blind spot.

**A5 — was the first exit recorded when the second was evaluated? → YES. State was fresh, not
stale.** Exit 10:13:31; second signal 10:14:11, 40 s later. And the guards demonstrably worked while
the position *was* open: `REJECTED_STRATEGY_POSITION_LIMIT` at 10:08, `REJECTED_DUPLICATE_SYMBOL` at
10:03 and 10:09. **This is not a stale-state bug. A5 refuted.**

**A2 — did it satisfy every rule, or pass because nothing forbade it? → BOTH, and that is the
finding.** It satisfied every gate: score 60 against `min_pass_score: 60` — **a pass by exactly one
point**, tier LOW. From 10:19 onward the same scanner scored 59 and everything was rejected. SENCO
was oscillating on the threshold, and the 10:14 hit happened to land on the right side of it.

⇒ **A6: policy gap.** Nothing is broken. There is simply no re-entry rule, and the position guard —
the only thing suppressing a continuous signal stream — is released by design the moment the
position closes.

---

## 3. §B — the max-trade accounting, read from the counter

**FIX-181 is the fix Rama is asking about, at `core/state_store.py:678-705`. It is built as an
ALLOW-list, not a deny-list:**

```python
_EXECUTED_TRADE_STATUSES = ("PENDING_FILL","OPEN","PARTIAL","EXITING","CLOSED","CLOSED_MANUAL")
```

⭐ **That is why B4's question has one answer instead of two: REJECTED, CANCELLED and FAILED are all
excluded by the same rule, because the rule names what counts rather than what does not.** A
deny-list fix would have covered one and missed the other.

**MEASURED against today's live data — it worked:**

| | |
|---|---|
| trade rows created today | **7** |
| `count_trades_today()` returns | **4** ← what the limit sees |
| CLOSED | 4 — counted |
| FAILED | 2 — excluded |
| REJECTED | 1 — excluded |

**3 of 7 rows excluded from the daily quota.** `max_daily_trades: 10`, so today used **4 of 10** —
nowhere near the cap. **No capacity was lost today**, and today does not test the cap.

⚠️ **A correction to the premise: there were ZERO broker-rejected ORDERS today.** Order statuses are
**COMPLETE 8, CANCELLED 4, REJECTED 0**. The four CANCELLED are OCO siblings — one per closed trade
(when TGT fills the SL is cancelled, and vice versa), which is normal LIMIT_TRIPLE behaviour, not
failures. What the screenshots show as "rejections" is the **signal**-level stream:
`REJECTED_SCORE_59` ×526, `REJECTED_STRATEGY_CONTROL` ×492, and so on out of **1,273 signals**.
Signal rejections and broker order rejections are different populations, and only the latter was
ever at risk of burning quota.

---

## 4. §C — the cooldown, measured. **The sample is TWO trades.**

Across the whole book (183 closed trades carrying a `net_pnl`, 181 symbol-days):

| | n | total | mean | win % |
|---|---|---|---|---|
| FIRST entries | **181** | **−71.91** | −0.397 | 40.3 |
| RE-ENTRIES | **2** | −6.53 | −3.265 | **0.0** |

By gap: **< 5 min → n=1** (today's SENCO, −4.86) · **5–30 min → n=0** · **30+ min → n=1**
(RALLIS, 21-Jul, 43.5 min, −1.67).

**C3 — both sides of a 30-minute rule:**

| | |
|---|---|
| trades it would have blocked | **1** |
| losses avoided | **+4.86** |
| **wins forgone** | **0.00** |
| net effect | **+4.86** over five weeks |

⚠️⚠️ **C4 — SAMPLE HONESTY, and it is decisive: n=2 is not a sample, it is two anecdotes.**
Re-entries are **1.1 %** of the book. "Wins forgone = 0.00" is **not evidence the rule is free** —
it is evidence that there is no data. A rule justified on one blocked trade is a rule justified on
nothing.

⭐ **And the one re-entry a 30-minute cooldown would NOT have caught (RALLIS, 43.5 min) also lost
money.** So even taking the two at face value, the cooldown catches one of two.

**Verdict: not evidence-backed either way.** Directionally it looks harmless and mildly positive,
but at n=2 that is indistinguishable from noise, and the total stake is about ₹5 over five weeks.

⛔ **And per the standing constraint this is ENTRY LOGIC — Rama's territory, ordered AFTER Slice
2.5. Since §A found a policy gap and NOT a defect, nothing here can be fixed now regardless.**

---

## 5. ⭐⭐ The finding that matters more than the one we were looking for

**The first-entry population is losing money: n=181, total −71.91, mean −0.397, win rate 40.3 %.**

The re-entry question is worth **₹4.86 across five weeks**. The base book is **−₹71.91** over the
same trades. **Spending a rule on re-entries is optimising a 1.1 % tail while the other 98.9 %
bleeds.**

⭐ **And 40.3 % is not a new number — it corroborates the 24-Jul entries finding from an independent
direction.** That work measured **38–39 % win against a 43.5 % breakeven**, with the geometric leg
(RR median 0.33) and the arithmetic leg converging. **Today's cut, computed differently and over a
different slice, lands in the same place.** The standing conclusion — *do not loosen the V3 gate or
`rr_floor`* — is reinforced, not challenged.

⚠️ Note also that today's two SENCO entries **both scored exactly 60 against a `min_pass_score` of
60**, tier LOW. Entries are being admitted at the threshold, and the same scanner scored 59 for the
rest of the morning. Whatever is decided about re-entries, **the marginal-score admission is the
larger lever.**

---

## 6. What was NOT done

⛔ No fix, no config change, no build. Read-only throughout; the live DB was opened `mode=ro`. The
recommendation on the cooldown is *insufficient evidence*, not *no*.
