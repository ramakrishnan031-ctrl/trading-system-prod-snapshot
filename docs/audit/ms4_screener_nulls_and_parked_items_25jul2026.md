# §C — the screener's None inputs · §D — two parked items

**25-Jul-2026. READ-ONLY. No build.**

---

# §C — `secondary_screener` passes None for atr / rsi / prev_close

**Location corrected:** the item cites `secondary_screener.py:407-410`; the file is at
**`screening/secondary_screener.py`** (not `signals/`), and the block is `:406-412`.

```python
# Still not in Kite quote API; would need instruments cache
"atr": None,
"rsi": None,
"sector": None,
"prev_close": None,
"avg_volume_20d": None,
```

## C1 — What those None values DO. **Not fail-open. Not fail-safe. WORSE: they are constants.**

The three-way question (reject / admit / silently zero) has a fourth answer, and it is the
one that matters. **Four of the ten scoring steps read fields that are hardcoded `None`, so
each returns a FIXED value for every signal ever screened:**

| step | reads | on None | ⇒ permanent value |
|---|---|---|---|
| 1 volume surge | `avg_volume_20d` | `if not avg_vol: return 0.0` | **always 0.0** |
| 3 ATR filter | `atr` | `if not atr or not ltp: return 0.0` | **always 0.0** |
| 4 RSI range | `rsi` | `if rsi is None: return 0.5` (neutral) | **always 0.5** |
| 6 sector strength | `sector` | `1.0 if sector else 0.5` | **always 0.5** |

⇒ **`prev_close` is currently read by nothing** (only `atr`, `rsi`, `avg_volume_20d`,
`sector` are consumed by the executor), so it is inert rather than harmful.

**So it does NOT fail open** — nothing is admitted *because* data is missing. Two steps score
zero (which is conservative) and two award a fixed half-credit. **C3's "report immediately"
trigger is NOT met.**

⭐ **But the honest characterisation is not "fail-safe" either.** It is that **40% of the
screening score is a constant.** Steps 1 and 3 can never be earned; steps 4 and 6 are always
half-earned. The score varies only across the other six steps — while presenting as a
ten-factor score.

## C2 — Reachable? **Yes, but the effect is a fixed offset, not a variable risk.**

The score gates on `min_pass_score` (60 across all 15 strategy YAMLs), so it is live on every
signal — unlike M-O9 and M-S3, which were inert. **Reachability is confirmed, not assumed.**

**But the consequence is different from a normal live defect:** because the four values are
*constants*, they do not create per-signal variance or a data-dependent hole. Every signal
carries the same handicap. **Nothing is admitted that should have been rejected; some things
are rejected that might have passed.**

## C3 — ⭐ Why this matters, and it is not as a defect ticket

**The threshold was calibrated against this scorer.** `min_pass_score` was set to 60 by
observing the distribution that *this* scorer produces — a scorer in which two steps can
never contribute and two always contribute half. **The 60 is therefore not a statement about
signal quality; it is a statement about signal quality as measured by a partly-constant
instrument.**

That connects directly to the week's headline (§4 of the register: the entries buy
extension, three independent lines converging) and to **D3/min_pass_score**, which is
already downgraded and open. ⚠️ **If the four inputs were ever populated, every score would
shift and `min_pass_score` would need re-deriving — a config change alone would silently
re-tune the entry gate.** That is the thing to write down.

**⛔ NOT A BUILD ITEM.** Populating them means wiring `core/daily_stats.py` (which already
computes `prev_close` / `avg_volume_20d` / `atr14` / `rsi14`) into the screener's market_data
— which is a **scoring change**, not a bug fix, and belongs with the entry work, not before
it. **Gated on D2/D3 remains the right call — but for a better stated reason than "gated on a
strategy decision": it cannot be changed without re-deriving the threshold.**

---

# §D — Two parked items

## D1a — S5 second-half: **STILL REAL, still correctly parked.**

Per-job functional criteria for the **7 cron jobs that read reconciliation/CAPITAL state**
(the other 27 are execution-only, already settled). The benign functional-status set was
closed by `ada0caa`; what remains is deciding, per job, what "functionally succeeded" means
beyond "exited 0". **Not superseded** — nothing since has addressed it. **Keep parked**: it is
a genuine gap, but it is 7 individual judgements that must not be taken in bulk, and nothing
is failing for want of it.

## D1b — X2 `eod_verify` columns: ⭐ **STILL REAL — and MEASURED tonight as dead.**

`eod_verify.py:60-68`'s P&L-variance check queries the wrong column names, so the branch is
silently dead. **Production evidence:**

```
eod_verification:  26 rows
pnl_variance NOT NULL:  26/26
distinct pnl_variance:  [0.0]        ← ONE distinct value, across every row ever written
status:                 PENDING 13 / VERIFIED 13
```

⭐ **It is not NULL — it is a constant `0.0`.** The column *looks* populated and measured; it
has never carried a computed variance. **Same epistemic shape as the sibling pattern
recorded this week: a value written constantly that has never actually been measured.**

⚠️ **The reason it is still parked is unchanged and still valid:** fixing it *makes a dormant
P&L check LIVE*, which means new alerts on a path that has never alerted, and it interacts
with the B3 shadow-mismatch story. **Do not fix it as a one-line column rename** — that is
precisely the "silently arms a dormant check" shape. **Keep parked; close it only with the
alert consequence considered.**

## D2 — The three older register editions in the Recycle Bin

**19-Jul (30,119 B) · 21-Jul (37,990 B) · 22-Jul (24,169 B)** remain in the bin. The 02:1x
25-Jul edition **was restored** by Rama, which repaired the only pointer that mattered
(corrections C-3…C-32 live there).

**Does anything still point at the three?** The current register cites them only as lineage
(*"that file STAYS for history, as do the 22/21/19/16-Jul files"*) — **no correction, decision
or open item references their contents.** The 16-Jul edition is untouched on disk.

⇒ **Nothing depends on them. The bin can be emptied safely** — with one caveat worth stating
plainly: **they are the only record of how the board looked at those dates.** That is
historical value, not operational value. Restoring them costs one drag; emptying the bin is
irreversible. **Rama's call; no operational risk either way.**
