# gemini_watchman 21-Jul alerts — verification (READ-ONLY)

**Verdict: every alarming alert is a Gemini CONFABULATION. The one that could have mattered — "6 active
vs max 5" — is REFUTED by ground truth (max concurrent open = 1). The watchman is observe-only and is
NOT a consumer of `balance_after`. No fix; nothing was changed.**

## The mechanism (why this matters for reading every alert)
`scripts/gemini_watchman.py` is an **LLM observer**, not a system component. It tails `logs/system_*.log`
for WARNING+/ERROR/CRITICAL lines, batches them every 5 min, and pipes the batch text to the `agy`
(Gemini) CLI with an "observe ONLY" prompt. Gemini writes prose issue-lines to
`reports/watchman/watchman_<date>.md` and, on critical keywords, may Telegram them. **The "alerts" are
Gemini's paraphrase of log text — and Gemini confabulates specific numbers and timestamps that are not in
the logs.**

**Proof of confabulation (three independent tells):**
1. **Future timestamps.** The batch written at watchman-clock **14:46:12** reports a `[15:30:00]` event
   ("₹9,908.50 / closing capital ₹0.00") — 44 min in the future. A live tail cannot see the future.
2. **Strings absent from every log.** `"closing capital"`, `"100% loss"`, `"max limit of 5"` → **0 hits in
   any log, any date.** `"9908"`, `"37552"`, `"30579"`, and any `"N active vs max M"` phrasing → **0 hits
   in the 21-Jul logs.** (The thousands of earlier hits were all other-date logs.)
3. **The same event, different invented numbers each batch** — ₹9,908.50, then "100% loss", then "19.54%
   of gross P&L (₹2.11)" — classic LLM confabulation, not a stable log-derived fact.

---

## §A — the two "100% loss / Rs 0.00" CRITICALs
- **A1 (what produces "closing capital Rs 0.00"): nothing — it is invented.** The watchman reads **logs**,
  not `fm_ledger.balance_after` and not the `daily_report` xlsx. The phrase appears in no log. It is
  Gemini dramatising the routine `KILL SWITCH ACTIVE AT STARTUP: SOFT_KILL circuit_breaker_force_close_15:15`
  line (a real, designed, every-day log entry) into a "capital wiped out" narrative.
- **A2 (where does ₹-9,908.50 come from): nowhere in the data — invented.** `"9908"` is absent from the
  21-Jul logs. It matches none of the real figures (₹9,857.30 open · ₹9,846.16 @11:57 · ₹9,838.48 close)
  precisely because Gemini fabricated a plausible "≈ full capital" round-ish number. **There is no fifth
  source and no fifth reader.**
- **A3 (recurring): the CRITICAL recurs, the specifics vary.** The `SOFT_KILL`-at-startup CRITICAL fires
  most trading days (the 15:15 circuit breaker persists overnight by design — confirmed in the 06-22 and
  07-06 watchman files). The alarming capital-loss *specifics* are non-deterministic Gemini output that
  varies day to day. So: a CRITICAL most days, dressed in different invented numbers — the exact
  alert-fatigue shape C1 and B2′ addressed, here at the LLM-observer layer.

### ⚠️ A4 — amends the instruction's premise, not tonight's §A3 verdict
The instruction expected the watchman to be a **fifth consumer of `balance_after`**. **It is not.** It never
reads that field — it reads log text and confabulates. So `broker_closing_capital_zero_21jul2026.md` §A3
("nothing in any control/gate/sizing consumes the value; four readers") **stands unchanged** — the
watchman's "Rs 0" is an **independent** LLM hallucination that coincidentally rhymes with the real
last-ledger-row defect. The correct amendment is narrower: the watchman is a **false-CRITICAL generator via
LLM confabulation**, a fourth daily-false-signal class alongside B2′'s 401, C1's CRITICAL count, and the
`[3_Capital]` REVIEW — but by a *different mechanism* (hallucination), not by reading the ₹0.

**Why the premise was tempting, recorded:** an alert that *paraphrases* "Rs 0" looks like it *read* a "Rs 0"
value — but an LLM observer can invent a number that happens to match a real defect's output. "Sounds like
the same bug" is a hypothesis; the mechanism (log-tail + confabulation vs field-read) is the check.

### A5 — observe-only, confirmed in code (safe)
The watchman reads logs, calls an external LLM, appends a `.md`, and *may* send a Telegram (`severity=ERROR`).
It has **zero** connection to the engine, kill-switch, or order path (its prompt: "NEVER recommend trade
actions"). When it writes "kill switch triggered/activated" it is **describing** the 15:15 circuit breaker it
saw in the log — never causing it. The Antigravity/agy observe-and-report boundary holds.

---

## §B — "6 active vs max limit of 5" — **REFUTED** (this was the one that could have mattered)
- **Ground truth: max concurrent open positions on 21-Jul = 1.** The 5 closed trades ran fully sequentially:
  DYCL 10:05→10:07 · NELCAST 10:13→10:20 · RALLIS 10:36→11:19 · RALLIS 12:02→12:03 · CMPDI 12:31→14:37.
- Config cap is `max_open_positions: 5`; **never approached, never breached.** **Zero** OPEN_POSITIONS /
  portfolio-cap rejections in the 21-Jul logs. The strings "6 active" / "position cap" / "max limit of 5"
  appear in **no** 21-Jul log.
- **B3 (what it counted instead): it conflated the real concentration rejections.** There were **10 real
  `SIZING_CONCENTRATION` rejections** on 21-Jul (TCS/UTIAMC/BUILDPRO/SHYAMMETL/MAPMYINDIA "quantity exhausted
  due to concentration limits" are genuine) — Gemini fused those into a fabricated "6 active vs max 5
  position-cap breach". Different concept, invented framing.
- **B4 (order counts): reconciled.** 21-Jul trades = **16: 5 CLOSED, 7 FAILED, 4 REJECTED.** Not "25". The
  "2 of 14 placements failed / 5 rejected-or-failed" is roughly the shape of the real 7 FAILED + 4 REJECTED;
  "25 orders rejected due to capital constraints" is confabulated. Rejection reasons are the expected
  slippage-guard / concentration / R:R / circuit-proximity ones — nothing new.

---

## §C — the INFO lines
- **C1 (81.4% / 30,579 of 37,552 dropped):** thematically the known 403 blind spot (~338k uncounted), but
  the specific numbers `37552`/`30579` are **absent from the 21-Jul logs** — confabulated. Not a new drop
  path; a hallucinated restatement of the known gap. (If real drop counts are ever wanted, they must come
  from the 403 instrumentation, not this line.)
- **C2 (get_daily_realized_net_pnl double-subtract flagged): STALE/hallucinated, not real.** Zero
  `double.?subtract/count` hits in the 21-Jul logs. E4/W10 is verified correct (tonight); Gemini invented a
  "potential double-subtract" from seeing `get_daily_realized_net_pnl` in a log line. **It does not detect
  anything the verification missed.**

## Delivery note (secondary)
The watchman's confabulated 21-Jul CRITICAL was written to `watchman_2026-07-21.md` but **no 21-Jul Telegram
send is recorded** in `failed_alerts.log` (0 entries) — so there's no evidence it reached anyone. On other
dates the watchman *did* send (223 attempts logged, e.g. 16-Jun real ORPHAN_ADOPTION alerts) and **those
failed delivery** to chat_id `-1003977291784`. So the watchman is a **mixed signal that also fails
delivery** — real events on some days, confabulations on others, neither reliably delivered.

## Direction (NOT done here)
The watchman adds negative value on the capital path: it confabulates alarming specifics indistinguishable
from real breaches, and it's the kind of "CRITICAL most days" signal nobody can read a real event out of.
Observe-only ⇒ **nil control risk**, so no urgency — but if kept, its output should be treated as a prompt
to check the *real* data (as done here), never as fact; a tighter prompt (quote the exact log line, forbid
invented numbers/timestamps) would help. Queue, do not fix tonight.

*Read-only throughout (`mode=ro`); no code, config, schema, state, or the watchman touched.*
