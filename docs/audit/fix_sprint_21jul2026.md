# FIX SPRINT — Tuesday 21-Jul-2026

**Run:** 21-Jul-2026, from ~11:28 IST (Rama's trigger), market open, book flat (Rama-confirmed).
**Scope:** soft-kill investigation → B2′ (preflight `/health` 401) → C1 (daily-report CRITICAL
count) → two design items recorded. **Capital path untouched; entry suppression NOT applied;
service kept running so tonight's 18:15 E4/W10 verification is preserved.**

> **Two findings today are bigger than the fix.** The soft-kill attempt exposed a real
> architectural limit (§1), and the mid-session restart it forced proved a capital mechanism that
> had never run in production before (§2). Both are recorded here and in two memory lines (a
> deliberate exception to the one-line budget).

---

## 1 — THE KILLSWITCH LIMITATION (architectural finding, not a mishap)

**Rama asked for "soft-kill the system for the entire day" — a state the architecture cannot
occupy.** The investigation:

- **A persisted kill = HALT-ON-STARTUP, not "run with entries blocked."** `KillSwitch` holds its
  state **in-memory** (`is_active()` reads `self._state` only, KS12); the DB row is loaded **once
  at boot** (KS3). Applying `SOFT_KILL` to the DB + restarting makes `detect_startup_scenario`
  return `HALT`, and `main.py:1791-1798` **exits 4** ("use --resume to clear"). Observed live:
  boot at 11:37:53 logged `KILL SWITCH ACTIVE AT STARTUP … EMERGENCY reason … Manual --resume
  required`, then `Startup scenario: HALT`, `status=4/NOPERMISSION`, unit `failed` (stable, not a
  crash-loop — `Restart=on-failure` does not restart exit 4).
- **The in-process form has no external trigger.** Every `soft_kill()` caller is an internal
  safety path (live-feed, drift, token-expiry, circuit-breaker, EOD, auto-trip). No signal
  handler (`main.py` handles only SIGINT/SIGTERM), no control endpoint (webhook `:5000` and
  healthcheck `:8080` are read-only; the GUI risk API is all `GET`). So **"running + entries
  blocked" is unreachable by an operator.**
- **The reason choice was verified against the auto-clear code.** `clear_stale_state` clears only
  *prior-day* kills (`triggered_date >= today → keep`); `auto_clear_scheduled_kill` clears only
  *scheduled* reasons. An **emergency same-day** reason therefore survives restarts and
  auto-clears at tomorrow's boot — correct, but it still halts (above).

**The general consequence (F1 below): a halted day silently forfeits the daily-loss reset.**
`reset_daily_pnl()` — which writes the `RESET_PNL` row — is reachable **only** from
`eod_squareoff._fire()` (in-process). So any halted afternoon writes no `RESET_PNL`. Today that
would have forfeited the E4/W10 verification; in general it applies to **every** halted day.

**Verdict / decision.** The two goals — "block entries all day" and "preserve tonight's
verification" — genuinely conflict on this architecture, and neither was mine to trade away.
**Stopped and asked; Rama chose to keep the verification.** Resumed the service via
`deploy/resume.sh` (SOFT_KILL → INACTIVE, restart); it came up `active`, WARM restart 11:57:32,
kill INACTIVE. Entry suppression was **not** applied (§E of the instruction; the book is flat,
watched, and the entry window closes 15:00).

**Correction recorded:** my in-session warning "liveness will spam DOWN alerts while halted" was
**overstated** — `liveness_probe.classify_liveness` treats a persisted `SOFT_KILL` as
`OPERATOR_HALT` and stays silent. It did not change the decision (which rested on the EOD
forfeit), but a correction that inflates its own case is worse than none.

**Mechanism recorded for identical reversal** (in case it is ever wanted deliberately, e.g. an
end-of-day park): engage = `systemctl stop` → `KillSwitch.soft_kill(reason=…, triggered_by=…)`
(emergency reason) → `systemctl start` (service then HALTS — this is the point of a park);
reverse = `sudo bash deploy/resume.sh`. Backup taken pre-change: `pre_softkill_21jul.db` (97 MB,
`quick_check=ok`).

---

## 2 — ⭐ PHASE-2 P&L CARRYOVER — PROVEN IN PRODUCTION (first time)

The 11:57 resume forced a **mid-session restart with real same-day state** — a scenario no plan
would have scheduled, and one that was on the never-proven-in-production list. It ran clean:

```
fund_manager.rehydrate_complete  anomaly_count=0  daily_pnl=-12.57
                                 replayed_pnl_rows=3  replayed_trades=0  total=9846.16
```

- **This is the exact mechanism the entire M-C1 re-derivation concerned.** Until today every
  production observation of the restore path was a `0/0/0` no-op on a flat book (Monday included).
  This replayed **3 real P&L rows**, reconstructed the day's realized **−12.57** exactly, landed
  `_total` on `broker.net` (9846.16), and flagged **zero anomalies** — **on the new E4/W10
  contract, with non-zero carryover.**
- **It comes off the unproven list.** Still unproven afterwards (do not conflate): Phase-1 replay
  of a live *open* position, same-day-kill survival with the service *running* (shown impossible,
  §1), first live HARD_KILL.

---

## 3 — B2′ — PREFLIGHT `/health` 401 (deployed `aff5256`)

**Defect:** `scripts/preflight/checks/signals.py::WebhookResponsiveCheck` hit the local
`:5000/health` **unauthenticated** and treated **2xx-only** as healthy. AB-910 §1.7 put `/health`
behind the webhook secret, so it now answers **401** to this by-design-unauthenticated caller →
`_failed("webhook /health HTTP 401")` = a **false CRITICAL every trading day** (seen again today
at 09:19:47, Readiness 33%). Third and last unfixed instance of the S4 `/health`-401 class.

**Fix (root):** treat **200 or 401** as "the endpoint answered / Flask up / signals can arrive";
`0`/5xx/404 still FAIL. Mirrors the S4 boot self-check `utils/startup_checks.py:807`. The
**LOCAL-only / not-`check_scanner`** caveat is baked into the code comment — `check_scanner:703`
calls *external* Chartink, where a 401 IS an anomaly (that refusal stands, with evidence).

**Proven able to fail:** the new test went **RED on the unfixed code** producing the *predicted*
string — `CheckResult(status=FAIL, detail='webhook /health HTTP 401')` — plus anti-vacuity cases
`0`→FAIL and `503`→FAIL. **Live confirmation** against the running endpoint: raw
`GET :5000/health → HTTP 401`, deployed check now returns `Status.PASS | "webhook /health 401 —
Flask up"`.

**The `/health` 4th-instance sweep — family CLOSED, no 4th instance.** Runtime callers of the
authenticated `:5000/health` are exactly two: `startup_checks.py:807` (fixed, gates the boot) and
`signals.py:27` (B2′, advisory). `main.py:3325` uses the fixed helper; `startup_hook` launches
preflight as a **detached subprocess** (`subprocess.Popen`), never importing checks in-process,
and Phase C is not even force-launched; `liveness_probe` uses `systemctl` not `/health`; the
`:8080/health` callers (`engine.py`) are a different, no-auth endpoint; the rest are tests. So the
**trading service never reaches `signals.py` in-process** (isolation verified, not assumed) — the
preflight-scoped regression (164 tests, BASE == MERGE, `comm -23` empty) was proportionate.

**Deploy:** preflight is a **cron** script — **no service restart**, zero live impact; PID 1684235
unchanged. The daily false CRITICAL ends at the next `preflight_phase_c` run.

---

## 4 — C1 — DAILY-REPORT "CRITICAL Count" counted a routine event (deployed `5c70def`)

**Defect:** `daily_report.py` "System Health → CRITICAL Count" matched event_type substrings
(`"CRITICAL"` or `"KILL"`). The event_type universe (queried live) is `STARTUP / SHUTDOWN /
CONFIG_DIFF / KILL_AUTO_CLEARED`, and **the only KILL/CRITICAL match ever written is the routine
`KILL_AUTO_CLEARED`** (the daily prior-day kill auto-clear at boot). So "CRITICAL Count" read
`>= 1` **every trading day** — a routine event wearing an incident label — and double-counted with
"Kill Switch Events".

**Fix (root, structured):** extracted `_is_critical_event(event_type)`, keyed on the structured
event_type — exclude routine `KILL_AUTO_CLEARED`, keep genuine KILL/CRITICAL types. The auto-clear
is still tallied under "Kill Switch Events" (reclassified, not hidden).

**Anti-vacuity (the C1 question — cannot silence anything genuinely urgent):** confirmed from the
data that genuine incidents never appear as a KILL/CRITICAL *system_event* (they live in the
sentinel/alert path), so excluding the routine type silences nothing real; and the test pins a
genuine `HARD_KILL_TRIGGERED` / `CRITICAL_FAILURE` **still counts**. Demonstrated old-vs-new:
old substring logic on `KILL_AUTO_CLEARED` = `True` (the bug), new helper = `False`.
Regression: daily-report-affected tests BASE 134 / MERGE 137 (+3), `comm -23` empty. Reporting
layer only; `daily_report` is a standalone cron, not on the service path.

---

## 5 — DESIGN ITEMS (outlive the sprint — recorded, NOT built)

**F1 — Any halted day forfeits the daily-loss reset.** `reset_daily_pnl()` (writes `RESET_PNL`)
is reachable only from `eod_squareoff._fire()`, in-process. General to *every* halt, planned or
not; nothing records the coupling today. **Question for Rama:** should the reset have a cron
fallback, or is in-process-only correct and merely undocumented?

**F2 — The operator soft-kill gap.** The architecture supports *halted* or *running*, but not
*running with entries blocked by an operator*. Every `soft_kill()` caller is an internal safety
path; there is no operator-facing in-process trigger. Rama asked for a state the system cannot
provide — a legitimate design request, now specified by this investigation. **Queue as a design
item** (e.g. an authenticated control endpoint or a signal handler that calls `soft_kill()` on the
running process, with the entry gate honouring it live).

---

## STATE / PARITY

- **Deployed:** `5c70def` (= `deb1819` + B2′ `aff5256` + C1 `5c70def`). PC == origin == VM bare.
  Schema v44 (no migration). **E4/W10 still UNVERIFIED — tonight's 18:15 is the proof.**
- **Service:** `active/running`, PID **1684235**, WARM restart **11:57:32**, kill **INACTIVE** —
  **never restarted by either push** (both are cron scripts).
- **Today:** 3 closed trades, realized **−12.57 net** (Σcosts ≈ 1.43 > 0 ⇒ tonight's signature is
  readable). Mid-session restart occurred (11:57) — **the ledger was reconstructed intact**
  (−12.57, 3 rows, 0 anomalies), but the day is **not a clean single-session day** and tonight's
  report must say so.
- **Full suite (§B confirmation):** `5011 passed, 10 failed, 4 skipped` (849 s). **Zero failures
  in changed files** — the full diff `deb1819..5c70def` is exactly the 4 files above, and all 10
  failing test files (`test_main`, `test_order_placer_fix061`, `test_fix181`, `test_phase17_batch2`)
  are byte-identical/unchanged ⇒ pre-existing PC-env failures, **none attributable to B2′/C1.** No
  surprise; the scoped regressions were proportionate.

## §C REMAINING (deferred — lower value, documented for continuation)

- **C2** `Rejected (Sizing/Capital)` → `Rejected (Sizing)` (`daily_report.py:510`,
  `build_sheet_0_dashboard`). The relabel is **correct** — the tally is the `REJECTED_SIZING_*`
  family; `CAPITAL` is one binding constraint already shown in the "Binding Constraint" sub-row,
  not a co-equal family. **Deferred:** the funnel label does not surface in the sample-generated
  sheet 0 even scanning all cells (multi-column dashboard layout / fixture path), so a
  discriminating test needs a layout dig **disproportionate to a one-word cosmetic relabel**.
  Shipping it untested would break the careful-loop discipline, so held for a later batch with a
  proper sheet-0 assertion. (Judgment per §3: don't let cosmetics crowd tonight's verification.)
- **C3** `build_taxonomy_map() --config-dir` · 403 signal-count instrumentation ·
  **C4** the stray `phase-a-pre-spine-fix` tag · **C5** attribution-gloss sweep (docs-only — add
  today's two corrected premises: the §1 soft-kill "stays running" premise, and the overstated
  "liveness will spam DOWN" warning; both the same shape). Not started.

## TONIGHT (unchanged — the day's real obligation)

- 15:17 squareoff + in-process EOD reset must fire (nobody stops the service).
- After 18:15, run §B of the verification file. Signature: **`RESET_PNL.pnl_delta = −Σpnl_delta`**
  (E4/W10, ≈ **+12.57** on current numbers), **NOT** `−(Σpnl_delta − Σcosts)` (≈ +14.00, old
  Option-B). Note the mid-session restart caveat. Do NOT flag 20-Jul's own `RESET_PNL` of 19.61
  (old-code history).

---

*Read-only except the two deployed fixes (both cron-script, off the capital path, no service
restart) and this report. Entry suppression NOT applied. `scripts/forward_shadow_record.py` NOT
run; no `scripts/*.py --db`. Backup `pre_softkill_21jul.db` retained.*
