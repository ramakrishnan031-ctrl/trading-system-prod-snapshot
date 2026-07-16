# BATCH-1 — the 27 batch-safe items: what shipped, what escalated, what was found

**Date:** 16-Jul-2026 22:56 → 17-Jul ~00:0x IST (off-market) · **Base:** `362e166`
**Status:** COMPLETE. **NOTHING PUSHED.** Awaiting Rama's off-market deploy go.

**15 commits, one per item.** No schema change. No trading-path change.

---

## 1. Headline

| | |
|---|---|
| **Committed** | **15 items** (each its own commit; a test wherever it was a behaviour change) |
| **Escalated to LOOP** | **1** — P3-s14 (webhook insert-fail) |
| **Not actionable / deferred, with reasons** | **6** — A5, A6, C4, C5, D2, + the pytest-skew half of A3 |
| **Group-E findings (no fixes — the boundary held)** | **6** |
| **Full regression** | see §6 |

**The two results worth reading even if you read nothing else:**
1. **M-SC2 is now CLOSED — runtime-proven** (§5, E1). One of the four named gaps in the
   register's HONEST CLOSURE is closed with live evidence.
2. **A capital finding (§5, E4):** on every CHECK1/RMS/manual close, the daily-loss
   limit's input is **gross P&L, not net**. Finding only — the fix is LOOP.

---

## 2. Committed (15)

### Group B + C1 — data exposure and a leaked secret (2)
| Item | Commit | What |
|---|---|---|
| B1 | `681688e` | Deleted `reports/output/daily_report_b64.txt` — a **1.44 MB base64 XLSX with real trade data**, committed past the `*.xlsx` ignore. Decoding its head gives `PK..` — a real ZIP/XLSX. Closed the gap the file exploited (`*_b64.txt`, `*.b64`, `*.xlsx.txt`), verified with `git check-ignore`. `audit-1.jpg` was already gone. |
| C1 | `7b492fb` | `fetch_daily_candles.py:189` printed **8 chars of the live access token** into the cron log every run. Now prints `yes`/`MISSING`. Swept the repo: it was the only such site. |

### Group A — docs (4 of 6)
| Item | Commit | What |
|---|---|---|
| A1 | `67850b8` | **DEPLOYMENT.md pointed at a directory that does not exist.** `/home/ubuntu/trading-system/.env` — the real path is `/home/ubuntu/systems/trading-system/.env`. Dangerous because `/home/ubuntu/trading-system.git` DOES exist (the bare repo), so the wrong path is one character from a real one. Following the doc would scp secrets to a directory nobody reads while the service kept using the real `.env` — nothing would visibly break. **Precondition honoured: every edited line verified on the VM first** (`ls` + the systemd unit, which is the authority). |
| A2 | `63517f2` | **SYSTEM_MAP index.** It is ~450KB/1,600 lines (269KB at the audit — nearly doubled), 27 sections, no index, and the rules say read it before any system work. Added a HOW-TO-READ header + index by section title (not line number — those rot). **Deliberately NOT the split:** every report and memory entry points into it by section; a silent split loses those pointers. Verified pure addition — all 27 sections intact. |
| A3 | `55a9c59` | **PATHS.md** header said "Last updated: 2026-07-03" while carrying two weeks of banners at ~120KB. Corrected the date and labelled it honestly (a what-changed log, not a quick reference). |
| A4 | `bc5a2a9` | **The missing README.** A 585-file system placing real orders since 11-May had none; `ls README*` was empty. Leads with the money-safety warning and the five rules that cost money when a newcomer doesn't know them. Plus `ops/__init__.py` (the only implicit namespace package in a repo of regular ones) — verified import-neutral, control_tower's 34 tests pass. |

### Group F — trivial code, off the trading path (6)
| Item | Commit | What |
|---|---|---|
| F1 | `7afbd25` | `sqlite3` was imported **only inside one function**, while a *different* function catches `except sqlite3.IntegrityError`. Python evaluates the exception class when the handler is checked ⇒ the first IntegrityError would **NameError from inside the handler** — the candle fetch would die on the exact path written to survive a duplicate. Masked by `INSERT OR IGNORE`, so latent, not harmless. Proven by AST, not by eye (the two functions are 170 lines apart). |
| F4 | `5193b48` | Candle **trade-date came from a naive `datetime.now()`** — the host's timezone decides which day's candles are fetched. Now `time_authority.today_ist()`, whose docstring already stated the rule this file broke. |
| F5 | `803ec0d` | **Documented, NOT "fixed" — the tidy-up would have been harmful.** Strategy `entry_start_time: "09:25"` vs global `entry_start: "10:00"` looks like drift. Verified the global genuinely binds (`market_windows.is_entry_allowed_for_strategy` checks both). But the global is `[LAUNCH-PHASE]` — "Relax toward 09:20 as the account scales". Rewriting the 16 strategies to 10:00 would silently hold them out of 09:20–10:00 **the day that floor is relaxed** — a behaviour change disguised as cleanup. Comment-only (11 insertions, no value line). |
| F3 | `dbeba4a` | **M-K5** — the resolved config was persisted **unredacted** to `config_snapshots.config_json`, a durable table that rides into every backup. Production is safe only by accident (name-indirection leaves the password empty). Now redacts populated secrets; `*_env` NAMES preserved. **Only non-empty values are touched, and that is the design:** verified on the REAL config that the JSON is byte-identical and the hash unchanged ⇒ provably a no-op live (no spurious snapshot row). |
| F2 | `2d56666` | **The GUI logged Rama out on every restart.** `secret_key` is configured nowhere (repo, local, VM — all checked), so the ephemeral fallback always fired. Now generated once and persisted 0600 under the gitignored `data_store/`, with a fail-safe: any write problem degrades to the old behaviour rather than refusing to boot. |
| F6a | `71c75bc` | **Two win rates on one report.** The headline divided by `len(realized)` (which counts breakevens); per-strategy divides by decided. **Live and reachable:** the book has 61W/91L **and exactly 1 breakeven** ⇒ headline 39.87% vs per-strategy 40.13%. The headline now *calls* the one definition. |

### Group D + C — tests and alerting (3)
| Item | Commit | What |
|---|---|---|
| F6b | `2bb9194` | **`daily_trade_review` had no non-trading-day guard.** `cron_registry` declares `market_day_only: true`, but **that field is metadata — nothing enforces it**, and the crontab (`7 16 * * 1-5`) only excludes weekends. Every mid-week NSE holiday it built and emitted a review for a day with no trading. The skip **still heartbeats** (`SUCCESS` + `functional_status=SKIPPED`) — going silent on a monitored job would trade a spurious report for a spurious "no heartbeat" alarm. |
| D1 | `6d38d22` | **A test that was vacuous on Windows — and the source of the register's NR-4.** `Path("/nonexistent_xyz/...")` isn't absolute on Windows; it resolves to `D:\nonexistent_xyz\` and **the write succeeds**, so the "unwritable path" branch was never exercised — green, testing nothing. It also *created* `D:\nonexistent_xyz\` every run: **that is where NR-4's "stray D:\ folders" come from.** Now uses a parent-is-a-file target: raises on both platforms, confined to tmp. |
| C2 | `ffdeffe` | **An unreadable cron registry silently downgraded a critical FAILED alert** to ERROR — and only CRITICAL carries the email fallback, so the alert that most needed to escalate lost its escalation exactly when the config was broken. Now tri-state: `FAILED`+unknown → CRITICAL (under-alerting is the expensive mistake); `SUCCESS`+unknown → silent (a blanket "assume critical" would spam every success and train the operator to ignore the channel). The registry error is now logged — it was swallowed. |

---

## 3. ESCALATED to LOOP (1) — the guardrail firing

**P3-s14 — webhook insert-fail "dark window".** Classified log-only. Opening it changed
what the item *is*: `_process_signal` (`webhook_receiver.py:736-888`) has **no generic
handler around the INSERT** — only `IntegrityError` and `queue.Full`. The 300s dark
window is that when the INSERT raises anything else, the fast-path **dedup claim is
never rolled back** (the QUEUE_FULL path explicitly rolls it back and says why), so the
sender's retries are DUPLICATE-bounced for the whole dedup window.

The real fix is that rollback — **control flow on the signal entry path, deciding
whether a signal enters at all** ⇒ "changes what the system trades". A log-only half
would make the dark window visible while leaving it open. **Exited to LOOP, not
finished.**

---

## 4. Not actionable / deferred — with reasons (6)

| Item | Why not done |
|---|---|
| **A5** (BK-3, 6 Word docs) | `docs/system_manuals/` holds **3** `.docx`, all **gitignored binaries** that never deploy. Authoring Word binaries from here is neither sensible nor verifiable. **Rama content-authoring, outside git.** |
| **A6** (BK-4, "Windows Phase-1 doc updates") | The only trace anywhere is a one-line table row: `| BK-4 | Windows Phase-1 doc updates | OPEN | non-code |`. **No description of what the updates are.** Underspecified — executing it would be inventing scope. Needs Rama. |
| **A3-part** (pytest skew) | Root dev `pytest>=9.0.3` (installed 9.0.3) vs `ops_dashboard/backend/requirements.txt:9 pytest==8.3.4`. It is a **decision** (which version wins), and bumping a test framework without running that suite risks the safety net every other judgement in this batch depends on. |
| **C4** (Q10, F2 functional-criterion tail) | ~20 jobs, each needing its own criterion **decision**, several reading reconciliation/capital state (`eod_verify` VERIFIED, `reconcile_positions` clean). A focused pass, not the tail of a batch — 20 unverified criteria at midnight is the failure mode the guardrail exists to prevent. |
| **C5** (P5-2, B-1 observability) | "heartbeat/metrics before the flip" is a **metrics design task**, not a chore, and it gates a Rama decision (B3). |
| **D2** (BK-5, CT drills) | **The precondition cannot be honoured.** `tests/crash_test/ct_utils.py:63` hardcodes `DB_PATH = data_store/trading_system.db` — a **real DB**, opened **writable** by default (`:77` raises if absent, i.e. it *requires* one). It is a module constant, not injectable. Making the drills scratch-safe is a harness change = its own task. *(The 6 pytest-collected crash tests — kill_switch_edges, ramcoind_oversell — DID run and pass; they are tmp-based.)* |

---

## 5. Group-E findings — the boundary held (finding only; no fixes)

**E1 — M-SC2 runtime: ✅ CLOSED. The proof exists.**
The register called this "the only outstanding proof" for M-SC2 (code closed by
`d3499b9`, deployed 16-Jul ~00:1x). The screened CSVs settle it:

| Date | Data rows | Size |
|---|---|---|
| 13-Jul, 14-Jul, 15-Jul (pre-fix runtime) | **1** | 40 B — empty stub |
| **16-Jul (first EOD after the fix deployed)** | **25** | 2,033 B |

⇒ **M-SC2 CLOSED, runtime-confirmed.** One of the four named gaps in the register's
HONEST CLOSURE is now shut.

**E4 — P3-s13 CHECK1/RMS `costs=0.0`: a CAPITAL finding. Fix = LOOP.**
`order_reconciler.py:1109` passes `costs=0.0` into `release_used`. The mechanism:
```
pnl = gross_pnl - costs                        # costs=0.0 ⇒ pnl is GROSS
projected_after = avail_before + margin + pnl  # available capital credited with GROSS
pnl_delta = pnl                                # → fm_ledger.pnl_delta
```
and the standing operational note records that **the daily-loss limit reads
`fm_ledger.pnl_delta`**. So on every CHECK1/RMS/manual close the daily-loss limit's input
is **gross, not net — understating the loss by the real costs** — and available capital
is credited with gross. The register says "capital_operational_note says by-design"; the
mechanism above is what that claim has to justify. **This matters more than it looks:**
the book's own numbers are gross ≈ −0.007R vs net −0.100R — costs are most of the loss,
so a control fed gross is fed the wrong number. **Finding only. Any fix is LOOP.**

**E2 — F4 confirm-benign: ✅ benign, confirmed.** `KILL_AUTO_CLEARED` events at
08:15:04 on 16-Jul, 15-Jul, 14-Jul, 13-Jul, 10-Jul — a clean daily cadence, exactly the
HEADLESS GUARANTEE firing at each boot. No unexpected re-arm. The current row
(`SOFT_KILL | 2026-07-16 | operator`) is Rama's planned pause and auto-clears at the next
08:15 boot.

**E6 — 16-Jul EOD delivery: healthy; inbox confirmation is Rama's.**
`cron_officer_eod` SUCCESS 18:50:03 · `system_manager_eod` SUCCESS 18:45:03 ·
`forward_shadow_record` SUCCESS 18:15:21 (`wrote=181 sim=181`). **0 pending sentinels**,
no `alert_watcher_degraded.json`, alert-watcher polling cleanly. The delivery path is
working; whether the mail landed is Rama's to confirm.
*(Method note: my first heartbeat query returned nothing because I used `last_run`; the
column is `executed_at`. An empty result from a wrong column name looks exactly like
"nothing ran" — check the schema before believing a negative.)*

**E5 — BK-8 config drift: the core is CLEAN (previously UNVERIFIED).**
No hardcoded config defaults (`cfg.get(key, <literal>)`) anywhere in `capital/`,
`orders/`, `signals/`, `screening/`, `core/` ⇒ the pydantic `extra="forbid"` loader
genuinely is the single source, as BK-8 asserted but nobody had checked. Residual: 5
**scripts** default `TRADING_MODE` to `"live"` outside the schema
(`compute_strategy_metrics`, `deploy_preflight`, `eod_broker_reconcile`, `eod_verify`,
`reconcile_pnl`). Worth a decision; not drift in the trading path. The CI check itself
remains to build.

**E3 — P3-r8 charges vs contract note: BLOCKED on Rama.** The contract note is a broker
document that exists outside the repo. Not derivable from code or DB.

---

## 6. Full regression — **GREEN**

Run 23:31–23:45 IST against the committed tree, verified to have no uncommitted `.py`:

```
11 failed, 4784 passed, 16 skipped in 821.45s (0:13:41)
```

**All 11 are the known time-gated PC-env set** (11 after 16:00 IST) — `test_main` ×4,
`test_order_placer_fix061` ×4, `test_fix181`, `test_interactive_startup`,
`test_phase17_batch2`. Identical to the set attributed earlier today against the true
pre-M-C8 tree. **ZERO new failures.**

**The pass count cross-checks the batch.** The M-C cluster tree ran **4,763 passed / 15
skipped**. This run: **4,784 / 16** — exactly **+21 passed, +1 skipped**, matching the 22
tests added:

| Suite | added |
|---|---|
| `test_config_snapshotter` (M-K5) | +4 |
| `test_gui_secret_key` (new file) | +6 (5 pass + 1 skip — the POSIX-mode test, on Windows) |
| `test_daily_trade_review` (win% ×3 + holiday guard ×4) | +7 |
| `test_cron_alerts` | +5 |
| **total** | **+22 → +21 passed, +1 skipped** ✅ |

Nothing lost to a bad merge, nothing unaccounted for.

The `PytestUnhandledThreadExceptionWarning: TypeError: MagicMock > int` is pre-existing
and benign — already attributed by A/B-ing `main`'s own `test_main` (fixture provably
absent → same warning count). Not a failure.

Attribution rule used throughout: any anomaly is attributed against the **true
pre-change tree** (`git checkout <base> -- <files>` + `grep -c` to confirm the change is
absent) — **never `git stash`**, which only stashes uncommitted work.

---

## 7. Deploy (later, on Rama's go)

Off-market: fresh VM backup → push `main` + tag → verify (bare HEAD == the SHA confirmed
**at run time**; schema v44; integrity/FK; services) → checklist → memory only after
verified. **Re-derive the SHA at deploy time — the tag is the code identity.**

**Rollback:** every item is its own commit ⇒ revert any one alone (L1), or reset to the
pre-batch base `362e166` (L2). All schema-free.

**What this batch changes in production:** nothing about what or when the system trades.
Observable differences: the GUI stops logging you out on restart; the daily review skips
non-trading days (and says so via a SKIPPED heartbeat); the headline win% moves
39.87% → 40.13% while a breakeven is in the window; a critical job's FAILED alert now
escalates when the registry is unreadable; the token no longer appears in the cron log.
</content>
