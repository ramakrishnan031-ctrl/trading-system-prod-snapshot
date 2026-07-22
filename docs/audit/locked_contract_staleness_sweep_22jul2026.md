# Locked-Contract Staleness Sweep (22-Jul-2026)

**Premise.** Tonight's OP-NS5 correction turned up a second stale rationale
(OP-NS4) of the identical class one line above it — two out of one header block.
This sweep asks, of every locked design contract that states a *reason*, one
mechanical question: **did a later change make the STATED REASON no longer
describe what the code does?** (Not "is the rule still right" — the rules are
fine — but "is the reason still true.")

**Method.** Read-only source trace + `git`/code cross-check. Corrections are
**doc-only** (dated notes, original left legible, `py_compile` clean, no code path
touched); made **only where the supersession is unambiguous**, else reported and
left. Scope of the sweep and what was *not* exhaustively checked is stated in
§4 — this is a bounded pass, not a proof of completeness.

---

## 1. Headline — NOT "just the two"

**7 stale statements across 3 files, but they collapse into exactly TWO
supersession events** — so this is a bounded, explicable pattern, not scattered
rot. Every stale statement names the fix that superseded it. All were unambiguous
and are now corrected (dated notes). No arguable stale contract was corrected;
the few genuinely-arguable observations are **reported, not touched** (§3).

| Cluster | Superseding event | Stale contracts | File(s) |
|---|---|---|---|
| **A — order-placement two-phase / June exit wave** | OP-NS1 two-phase deferred exits (24-Apr) + FIX-148/179/190 (Jun) | OP-NS4, OP-NS5, OP-BL19d, OP-LM3 | `order_placer.py`, `locked_decisions.yaml` |
| **B — kill-switch adapter-driven flatten** | FIX-087 (adapter injected) + M-C8 async flatten + FIX-181/190 (Jun) | KS5, KS11, KS13 | `kill_switch.py` |

**The two clusters are BOTH in the capital/order-safety path** — the most
consequential place for a stale contract, because (per the premise) a stale
*contract* misleads someone deciding whether to change behaviour, which is what
Decision 7 nearly became.

---

## 2. The stale contracts, each with its superseding fix

| # | Contract | Stated reason (now stale) | Superseded by | What the code actually does now | Action |
|---|---|---|---|---|---|
| 1 | **OP-NS4** `order_placer.py` | "INTRADAY uses SL-M" | P0 **FIX-179** (15-Jun) | Every SL leg (INTRADAY + DELIVERY) is order_type `SL` (stop-limit); Zerodha rejects SL-M via API entirely | corrected *(prior turn)* |
| 2 | **OP-NS5** `order_placer.py` | HARD_KILL "protects a naked position" | **FIX-148** (03-Jun) | `_emergency_market_exit` flattens BEFORE the kill; the kill is now an anomaly circuit-breaker | corrected *(prior turn)* |
| 3 | **OP-BL19d** `order_placer.py` | "LimitTriple cancels ENTRY on SL fail" | **OP-NS1** two-phase (24-Apr) | `engine.execute` places the ENTRY LIMIT only; SL/TGT are deferred to `place_exits` at fill — no SL is placed during `execute()` to fail | corrected |
| 4 | **OP-LM3** `order_placer.py` header + `locked_decisions.yaml` | "LIMIT_TRIPLE TGT: raise" / blanket "Raises OrderRejectedError" | OP-NS1 + **FIX-190 Bug C** | A TGT empty-id/BrokerError returns a partial **SL-only** result (`order_protocol_limit.py:485-495`), does NOT raise; SL failure escalates via order_placer, not "cancel ENTRY" | corrected (header note + YAML comment) |
| 5 | **KS5** `kill_switch.py` | constructor sig = `(state_store, bus, logger, on_hard_kill_cancel_fn, api_failure_threshold, enable_auto_trip)` | **FIX-087** + FIX-181 | `__init__` also takes `adapter`, `notifier`, `mode`, `emergency_exit_buffer_pct` | corrected (consolidated note) |
| 6 | **KS11** `kill_switch.py` | "NO cancel logic inside (injected callback)" | M-C8 + FIX-181/190 | The kill_switch has the whole flatten worker (`_exit_all_trades_indestructible`, `_cancel_trade_resting_exits`) inside it | corrected (consolidated note) |
| 7 | **KS13** + "What This Module Does NOT Do" bullet `kill_switch.py` | "Does not perform broker order cancellations (injected callback)" | M-C8 + FIX-181/190 | Performs the flatten + cancellations **directly** via the injected adapter; `on_hard_kill_cancel_fn` is now only a **legacy fallback** when no adapter is injected (`_run_cancel_fn ~:904/939`) | corrected (note + bullet) |

**Why these went stale and stayed stale (the self-sustaining mechanism):** each
contract was written 16–19 Apr; the superseding redesigns landed 24-Apr (two-phase)
and in June (adapter flatten, FIX-148/179/181/190). The contracts were never
revisited **because they are marked locked** — the lockedness is exactly what stops
anyone re-reading them. Both redesigns were large and correct; they simply left the
older descriptions behind.

---

## 3. Checked and left — reported, not corrected

Genuinely arguable / decision-level items (per the "correct only where unambiguous;
a wrong correction to a locked contract is worse than a stale one" rule):

- **P8_P13 vs OP9 — default order protocol.** `locked_decisions.yaml` P8_P13 says
  "CO_PLUS_TGT default, LIMIT_TRIPLE fallback"; `order_placer.py` OP9 says
  "LIMIT_TRIPLE by default." This is a **decision drift** (which protocol is the
  live default), not a stale *reason*, and confirming the runtime default needs the
  strategy-YAML wiring. **Reported for Rama** — a decision-level question, not a
  doc correction (rule: decisions are recorded, not acted on).
- **config_loader `ltp_gating` (Audit 2.2/6.2)** mentions "LIMIT/SL/SL-M orders."
  The core reason (paper LTP-gating prevents unconditional fills inflating paper
  P&L) is **still valid**; the "SL-M" token is a minor artifact of the paper-synth
  order-type list, not a stated-reason defect. Left as-is.
- **config_loader EOD `exit_protocol` default `"MARKET"` labelled "(legacy)"**
  (Audit 3.3/5.2). Whether the EOD path should default to `LIMIT_THEN_MARKET` is an
  **eod_squareoff behaviour question** — explicitly out of scope tonight
  (eod_squareoff is tomorrow's single variable). **Flagged for the eod-path review**,
  not analyzed here.

## Checked and CURRENT (reason still holds — verified, no change)

`order_placer.py`: OP-NS1/NS2/NS3 (two-phase is the current design), OP-LM1/LM2,
BL-8 (OP-BL8a–f, hard_kill-on-persist-fail after broker ack), BL-19a/b/c (429 retry
loop). `zerodha_adapter.py`: BL-6 (429 detect + penalize; caller owns retry).
`capital/invariant.py`: INV7 (float-drift → round to paise; the IEEE-754 reasoning
is timeless). `kill_switch.py`: KS1–KS4, KS6–KS10, KS12 (modes, RLock, persistence,
auto-trip, prior-day clear — all structural and accurate).

---

## 4. Scope honesty — what this sweep did and did NOT cover

- **Swept exhaustively:** the in-situ *stated-reason* contracts in the
  order/exit/kill path module headers — `order_placer.py` (OP-LM/BL-8/BL-19/OP-NS/
  OP-AR/EF-2), `kill_switch.py` (KS1–KS13), `zerodha_adapter.py` (BL-6, ZA*),
  `order_protocol_limit.py`, `invariant.py` (INV*), and the operational block of
  `locked_decisions.yaml` (OP-LM). This is where the June/April redesigns landed, so
  the highest-yield ground.
- **Spot-checked, not exhaustively re-verified:** the ~170 architectural entries in
  `locked_decisions.yaml` (G/P/Q/E/EV/CL/L/ID/RL/…). These are *design* rationales
  ("why token bucket," "why 3 IDs," "why atomic config load") — stable by nature and
  largely outside the "superseded by a later fix" pattern. The one operational hit
  there (OP-LM3) was found and corrected. A full re-read of all 181 was **not** done
  (low expected yield; the sweep targeted the operational path).
- **Not touched:** `eod_squareoff.py` (tomorrow's single variable), and
  `SYSTEM_MAP.md`'s "TWO PERMANENT RULES" block — those are *method* rules (worktree
  hygiene, reachability), not code contracts stating "we do X because Y," so they are
  outside this sweep's question.

**A completeness critic would ask:** the design registry was not exhaustively
re-verified, and other module headers (signal path, reconciler RC1–RC20, EOD) were
not swept. If Rama wants certainty rather than a targeted pass, a follow-up could
walk those — but the expected yield is low, and the two redesign clusters found here
are almost certainly the bulk of the staleness (they are the two big behaviour
rewrites since the contracts were locked).

---

## 5. The result, stated plainly (§A9)

The answer is **not** "OP-NS4 and OP-NS5 were the only two." It is **7 stale
statements, but exactly two supersession events**, both in the capital/order-safety
path, every one now carrying a dated note that names its superseding fix. That is a
*better* outcome than "only two, closed" in one respect — it converts a suspected
class into a **named, bounded pattern** ("a big redesign supersedes an older locked
contract's description; lockedness prevents the re-read") with a concrete count and
two clear causes, rather than leaving five stale descriptions in the two most
safety-critical modules to mislead the next behaviour-change decision.

**Process observation (report-only, Rama's call — not acted on):** the pattern is
self-sustaining, so the durable fix is not this one sweep but a habit — **re-read the
locked contracts a module touches whenever a redesign changes its behaviour.** Both
clusters here would have been caught at redesign time by that one step. Recording the
observation; not proposing a mechanism.

---

## Appendix — corrections landed (all doc-only, `py_compile` + YAML-parse clean)

```
orders/order_placer.py     OP-BL19d (dated note), OP-LM3 (dated note)   [+ OP-NS4/NS5 prior turn]
capital/kill_switch.py     KS5/KS11/KS13 consolidated dated note + "does not perform..." bullet
docs/locked_decisions.yaml OP-LM3 dated YAML comment (decision text untouched)
```
Verified: `python -m py_compile orders/order_placer.py capital/kill_switch.py` → OK;
`yaml.safe_load(locked_decisions.yaml)` → 181 decisions, OP-LM3 present; `git diff
--stat` → insertions only, no code path. Read-only DB/service throughout.
