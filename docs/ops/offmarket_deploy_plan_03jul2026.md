# Off-Market Deployment + Validation Plan — A-2 · C-1 · B-1 · P1

**Date authored:** 2026-07-02 (Thu, late) · **For execution:** 2026-07-03 (Fri) onward
**Author:** Claude Code (VS Code) · **Type:** PLANNING / RUNBOOK ONLY — no build, no push in this pass.
**Deployed baseline:** `origin/main == main == 9becf8c` (A-1/E-1 orphan recovery + CHECK9 tag fix + C-2 Phase-2), **schema v41**.

> All four fixes are **already built and committed** on their branches. This document is the exact
> push → verify → shadow → cutover sequence. Nothing here changes trading behaviour on push: A-2 is a
> placement-path correctness fix that ships behind the existing signal-status machinery; B-1 and P1 both
> ship **dark** behind SHADOW flags that default OFF.

---

## 0. TL;DR — the one-screen version

1. **GATE:** wait for the **Fri 03-Jul 08:15 IST boot of the already-deployed `9becf8c`** to come up clean
   (prior-day SOFT_KILL auto-clears; no orphan/CHECK9 misfire). Deploy **nothing** before this.
2. **PUSH WINDOW:** **after Fri 03-Jul 17:05 IST** (trader has EOD-self-exited; market fully closed) **or any time over the weekend.** Never during **15:30–17:05**.
3. **PUSH ORDER (one push):** merge `C-1` (carries A-2) → `B-1` → `P1` into local `main`, then `git push origin main`. `post-receive` auto-installs the new crontab (adds the `@15:58` P1 job).
4. **SCHEMA:** v41 → **v42** (P1 only; pure-add `eod_broker_reconciliation`; no rebuild). **Back up the DB before the push.**
5. **ACTIVATION:** A-2 + B-1 (in-process code) go live at the **Mon 06-Jul 08:15 boot**. P1's first shadow EOD run is **Mon 06-Jul 15:58**. Both shadow flags stay **OFF**.
6. **SHADOW → CUTOVER:** earliest **B-1 enforce** flip ≈ **Mon 13-Jul**; earliest **P1 authoritative** cutover ≈ **Mon 20-Jul** — both contingent on the shadow criteria in §5.

---

## 1. Exact push order + timing

### 1.1 Why this order

| Fix | Touches the live trader? | Behaviour change on deploy? | Gating |
|---|---|---|---|
| **A-2** timeout→no-retry | **Yes** — `signals/signal_processor.py` entry-placement paths | Changes duplicate-order handling on `BrokerTimeoutError` | **Boot-gated** — layer only after the 03-Jul boot proves `9becf8c` clean |
| **C-1** secret-scrub completion | No (cron scripts + pre-commit hook + docs) | None to trading | Rides in the same push (contains A-2 as an ancestor commit) |
| **B-1** daily-loss unrealized-MTM | Yes — reconciler + risk-engine + fund-manager | **None** — `daily_loss_include_unrealized=false` (SHADOW): reconciler populates MTM + gate LOGS `would_reject`, but ENFORCES realized-only | Shadow-safe |
| **P1** EOD broker-reconcile | No (standalone `@15:58` cron script) + schema v42 | **None** — `eod_reconcile.authoritative=false` (SHADOW): runs alongside `eod_verify`, records verdict + mismatch, alerts INFO, gates nothing | Shadow-safe |

**A-2 boot-gate rationale.** `9becf8c` (A-1/E-1 tag-correlation orphan recovery + the CHECK9/tag truncation
fix) has not yet had a full live session observed since deploy. A-2 adds a new branch to the **entry-placement**
path. Confirm the deployed baseline boots and trades clean on Fri 03-Jul **before** stacking a placement change
on top. B-1 and P1 do not change placement and ship dark, so they are not themselves risky — but they are pushed
in the **same** batch (after the gate) to keep one clean cutover and one DB backup.

### 1.2 The window

- **GATE first:** Fri 03-Jul 08:15 boot of `9becf8c` confirmed clean (see §4.0).
- **Then push in an off-market window, ideally after Fri 03-Jul 17:05** (post Control-Tower, trader already
  EOD-self-exited ≈16:00 → **no live v41-code process is running when the v42 migration lands**) **or any time
  over the weekend.**
- **Hard exclusion:** never push during **15:30–17:05** (square-off → EOD verify/reconcile → Control-Tower window).
- **Avoid the Friday morning/intraday window** (09:15–15:30): technically the trader keeps old in-memory code so
  A-2/B-1 wouldn't activate until Monday anyway, **but** P1's `@15:58` job would then fire the **same Friday**
  and migrate the DB to v42 while the v41-code trader is still up until ~16:00. Pure-add makes that low-risk, but
  the after-17:05 / weekend window removes the overlap entirely. Prefer it.

### 1.3 When each piece takes effect (recommended after-17:05-Fri / weekend push)

| Component | Activates | Mechanism |
|---|---|---|
| Crontab `@15:58` P1 job | **immediately on push** | `post-receive` regenerates + installs crontab (iff `generate==canonical`) |
| Schema v41 → v42 | first `state_store` open with new code — the **Mon 06-Jul 08:15 boot** (or P1's first `@15:58` run, whichever is first) | pure-add migration on open |
| **A-2** timeout handling | **Mon 06-Jul 08:15 boot** | in-process code; running trader keeps old code until restart |
| **B-1** MTM refresh (shadow) | **Mon 06-Jul 08:15 boot** | reconciler loop picks up new code on restart |
| **P1** first shadow EOD verdict | **Mon 06-Jul 15:58** | new cron job |
| **C-1** script api_key→env, pre-commit scanner | next invocation of each script / next local commit | cron scripts + local hook |

> If Rama wants A-2/B-1 live **the same** trading day he pushes, he must **restart the trading service after the
> push** (deploy-before-resume — the LESSON: a running trader keeps old in-memory code until restart). For an
> after-close Friday push this is moot: the trader is already down and comes up fresh Monday.

---

## 2. Branches / commits + exact merge commands

### 2.1 Branch topology (all off `9becf8c`)

```
main / origin/main ....... 9becf8c   (deployed, schema v41)

fix-a2-timeout-no-retry-02jul
  └─ fd09a38  fix(a2): stop retrying BrokerTimeoutError on entry placement

fix-c1-completion-02jul  (docs-carrier; CONTAINS A-2 as ancestor)
  └─ fd09a38  (A-2)
     aa02f9f  fix(c1): remove hardcoded api_key from 3 scripts + scan .xlsx + purge runbook
     3d34357  docs(audit): P1 assessment + remediation map + B-1 design
     a0c8605  docs(design): P1 build design + flip B-1 pointer
     b5660f6  docs: flip P1 pointer to IMPLEMENTED          ← current HEAD
     (+ this deploy-plan doc commit, appended here at deploy time)

fix-b1-daily-loss-mtm-02jul
  └─ f5fd4d9  feat(b1): wire live unrealized MTM into the daily-loss gate (SHADOW default)

fix-p1-eod-broker-reconcile-02jul
  └─ 4817032  feat(p1): broker-authoritative EOD reconcile (SHADOW), schema v42

PARKED / NOT in this push:
fix-t2-import-02jul  └─ b826ae0  (T2 proof-script import fix — deferred; see §8)
```

> **A-2 travels inside C-1.** Do **NOT** merge `fix-a2-timeout-no-retry-02jul` separately — its commit
> `fd09a38` is already an ancestor of `fix-c1-completion-02jul`. Merging C-1 brings A-2 in as a distinct commit.

### 2.2 Shared-file overlap analysis (verified clean-merge)

Two files are edited by **both** B-1 and P1 — but in **disjoint regions**, so git's 3-way merge does not conflict:

| File | B-1 edits | P1 edits | Collision? |
|---|---|---|---|
| `config/system_config.yaml` | +1 line in the `risk:` block (~L173, `daily_loss_include_unrealized`) | new `eod_reconcile:` section after `order_reconciler:` (~L274) | **No** (disjoint) |
| `core/config_loader.py` | +field in `RiskConfig` (~L474) | new `EodReconcileConfig` class (~L1195) + field in `SystemConfig` (~L1228) | **No** (disjoint) |

`signals/signal_processor.py` is touched by A-2 and by C-1, but C-1 *contains* A-2 (same commit) → no conflict.
`tests/unit/test_signal_processor.py` is touched by A-2/C-1 (+141) and B-1 (+4) in non-adjacent regions → clean.

**Merge dry-run results (verified with `git merge-tree`, git 2.53):**
`C-1 onto main` = clean · `B-1 onto main` = clean · `P1 onto main` = clean · **`P1 onto (main+B-1)` = clean.**

**Docs note:** `docs/SYSTEM_MAP.md` and `PATHS.md` are edited by C-1 (and this deploy-plan commit rides on C-1).
B-1 and P1 do **not** touch them, so no merge conflict arises from the batch. This deploy-plan's SYSTEM_MAP
entry is appended at **EOF** (the dated ops list); C-1's other SYSTEM_MAP edits are mid-file (cron/secret
section) → no self-conflict.

### 2.3 Exact commands

```bash
# — local, off-market, AFTER the 03-Jul boot gate (§4.0) —
git fetch origin
git checkout main
git merge --ff-only origin/main                      # confirm main == origin/main == 9becf8c (no-op)

# BACK UP THE DB FIRST (P1 migrates v41 -> v42). On the VM:
#   cp ~/systems/trading-system/data/trading_system.db{,.pre-v42-$(date +%F)}
#   cp ~/systems/trading-system/data/analytics.db{,.pre-v42-$(date +%F)}   # two-file DB (v28 split)

git merge --ff-only fix-c1-completion-02jul          # main -> b5660f6 (+deploy doc); brings A-2 + C-1 + docs
git merge --no-ff  fix-b1-daily-loss-mtm-02jul  -m "merge B-1: daily-loss unrealized-MTM (SHADOW default)"
git merge --no-ff  fix-p1-eod-broker-reconcile-02jul -m "merge P1: broker-authoritative EOD reconcile (SHADOW, schema v42)"

# sanity before push:
git log --oneline -8
python -c "import ast; ast.parse(open('core/config_loader.py').read())"   # loader parses
grep -n "daily_loss_include_unrealized: false" config/system_config.yaml   # shadow flag OFF
grep -n "authoritative: false" config/system_config.yaml                    # shadow flag OFF

git push origin main                                 # -> trading-vm bare repo; post-receive installs crontab
```

- `fix-c1` is a linear fast-forward of `main` (`--ff-only` succeeds → `main` becomes `b5660f6`+doc).
- `fix-b1` / `fix-p1` are each a single divergent commit → `--no-ff` merge commits (both proven clean).
- Prefer merge commits over rebasing the branches (preserves the reviewed commit hashes A-2 `fd09a38`,
  B-1 `f5fd4d9`, P1 `4817032`). If Rama wants a linear history instead, rebase B-1 then P1 onto the advancing
  main — the same disjoint hunks apply cleanly.

---

## 3. Schema versions before / after

| | Version | Change |
|---|---|---|
| **Before (deployed `9becf8c`)** | **v41** | `config_snapshots` (W0 report-redesign foundation) |
| **After (this batch)** | **v42** | **+`eod_broker_reconciliation`** (P1) — one row per date: per-dimension statuses + overall verdict + shadow-comparison columns (`eod_verify_status`, `mismatch`) |

- **P1 is the ONLY schema change in this batch.** A-2, C-1, B-1 = **no** schema change.
- The migration is **PURE ADD** (`CREATE TABLE IF NOT EXISTS eod_broker_reconciliation ...` + a trailing
  `schema_version` bump to `'42'`). **No table rebuild, no data rewrite, no downtime.**
- `core/state_store.py::EXPECTED_SCHEMA_VERSION = 42`; the migration runs on first DB open with the new code.
- Still a two-file DB (`trading_system.db` + `analytics.db` ATTACHed, v28 split) — back up **both** files.

---

## 4. Verification checklist (post-deploy)

VM paths: repo `~/systems/trading-system`, venv `~/systems/venv`. Run from the repo dir.

### 4.0 GATE — Fri 03-Jul 08:15 boot of `9becf8c` (BEFORE any push)
- [ ] `systemctl is-active trading-system` → `active`; boot log shows a clean start (no traceback).
- [ ] Prior-day **SOFT_KILL auto-cleared** at boot (kill-switch state not SOFT_KILL; no stuck naked-position).
- [ ] No A-1/E-1 orphan-recovery misfire, no CHECK9 false-naked in the boot/first-cycle logs.
- [ ] `git -C ~/systems/trading-system rev-parse HEAD` == `9becf8c`.
> Gate PASS → proceed to push in the after-17:05 / weekend window. Gate FAIL → hold the whole batch; triage first.

### 4.1 After the push
- [ ] **HEAD parity:** `git -C ~/systems/trading-system rev-parse HEAD` == the pushed `main` HEAD (bare `HEAD` == working-tree `HEAD`; zero drift).
- [ ] **Crontab:** `crontab -l | grep eod_broker_reconcile` shows `58 15 * * 1-5 ... scripts/eod_broker_reconcile.py`; and `crontab -l == deploy/cron/trading-system.cron == generate(registry)` (self-maintaining cron invariant).
- [ ] **Schema v42:** ``PYTHONPATH=. ~/systems/venv/bin/python -c "from core.state_store import StateStore; s=StateStore(); print(s.schema_version())"`` → `42` (or grep the migration line in the boot/first-run log). `eod_broker_reconciliation` table exists.
- [ ] **Shadow flags OFF (both):**
      `grep -A1 daily_loss_include_unrealized config/system_config.yaml` → `false`;
      `grep -A2 '^eod_reconcile' config/system_config.yaml` → `authoritative: false`.
      And the resolved config in the log confirms both `false`.
- [ ] **A-2 in the placement paths:** `grep -n "except BrokerTimeoutError" signals/signal_processor.py` present in all three placement paths (main / gate / retest); on a real broker timeout the signal is marked `TIMEOUT`, the reservation is **held** (not re-queued), recovery owns it.
- [ ] **B-1 MTM refresh live** (after Mon 06-Jul boot): reconciler log shows the `_refresh_unrealized_mtm` cycle line with rising `_mtm_refresh_success`; `_mtm_refresh_failure` not climbing.
- [ ] **Drift-check clean:** reconciler capital-drift / CHECK9 quiet; no new orphan/naked alerts attributable to the deploy.
- [ ] Full-suite smoke on the VM optional (the 32 known Windows-PC env fails are green on the VM).

---

## 5. Shadow observation checklist + success criteria

Both fixes are **dark**. Watch them for real, then flip. Nothing enforces until you flip.

### 5.1 B-1 — daily-loss unrealized-MTM (flip `risk.daily_loss_include_unrealized` false→true)

**Observe (from Mon 06-Jul):**
- [ ] **Refresh health:** `_mtm_refresh_success` climbs every ~15s; `_mtm_refresh_failure` near-zero. On a `get_quote` outage: refresh flips to `mark_unrealized_mtm_refreshed(available=False)` → gate stays **realized-only + WARN** (no false trip). Confirm the stale→realized-only path fires and self-heals (~15s repopulate after recovery).
- [ ] **Shadow reject log:** watch for `risk_engine.daily_loss.would_reject_with_unrealized: shadow=… (realized=… + unrealized=…) >= limit=…` — i.e. cases where realized+unrealized WOULD reject but realized-only did not. Sanity-check each: is the MTM value correct and the reject warranted?
- [ ] **MTM-vs-broker spot-check** on ≥3–5 positions across ≥3 trading days, **including at least one SHORT** (verify the sign: `(ltp−avg)×qty×sign` — a SHORT losing money must show **negative** unrealized). Compare the reconciler's `_unrealized_mtm` to the broker's own position P&L; agree within rounding.

**Flip criteria (all hold):** refresh success-rate ≥ ~99% over a full trading week · stale-path proven to degrade safely · every `would_reject` case reviewed and correct · SHORT sign verified. **Earliest flip ≈ Mon 13-Jul** (after the 06–10-Jul week). Flip is a one-line config change + restart; fully reversible.

### 5.2 P1 — EOD broker-reconcile (flip `eod_reconcile.authoritative` false→true + retire `eod_verify`)

**Observe (from Mon 06-Jul 15:58, one row/day in `eod_broker_reconciliation`):**
- [ ] **Mismatch capture:** rows where `mismatch=1` (P1's `overall_status` disagrees with same-day `eod_verify_status`) — these are exactly the **`eod_verify` false-VERIFYs caught in the wild** (eod_verify is local-DB-only and cannot see a broker/local divergence). Confirm each mismatch is P1 being *right*.
- [ ] **UNVERIFIED fires correctly:** on a **real broker-unreachable EOD**, `overall_status` must be `UNVERIFIED` (never a false `VERIFIED`). Force at least one **weekend dry-run** with creds unreachable to prove the UNVERIFIED path (a REQUIRED dimension unavailable ⇒ UNVERIFIED, no partial-pass).
- [ ] **Day-P&L ties out:** P1's broker day-realized vs local realized (`pnl_status`, ±`pnl_tolerance`=₹100) reconciles, and P1's numbers tie to `daily_trade_review` (the Dashboard/Reconciliation sheets) for the same date.
- [ ] **DEEPEN the LEDGER dimension BEFORE authoritative:** today `ledger_status` only checks the local `fm_ledger` 3-balance invariant. Before cutover, upgrade it to a real cross-source capital reconcile (`Σ fm_ledger.RELEASE_USED.pnl_delta == Σ trades.net_pnl`, and vs broker where available) — this is a small follow-up build, **not** in this push (see §8). Do not go authoritative on the lightweight ledger check alone.
- [ ] Paper mode is labelled `SELF_CONSISTENCY` (`self_consistency=1`) and is **never** counted as broker-authoritative validation.

**Cutover criteria (all hold):** ≥2 clean shadow weeks · every `mismatch=1` explained (P1 correct) · UNVERIFIED demonstrated on a real unreachable EOD · day-P&L ties to `daily_trade_review` · **ledger dimension deepened**. See §7 for cutover steps. Earliest ≈ **Mon 20-Jul**.

---

## 6. Earliest safe date to evaluate P1 shadow results

- First shadow EOD run: **Mon 06-Jul 15:58.**
- Evaluate after **N = 5 clean shadow EODs** (one full trading week: Mon 06 – Fri 10-Jul).
- **Earliest evaluation date: weekend of Sat 11-Jul 2026** — *provided* the ledger-dimension deepening (§5.2/§8)
  is complete by then (otherwise the ledger dimension isn't trustworthy enough to score).
- If any EOD in the window is not "clean" (broker-unreachable for a non-weekend reason, an unexplained mismatch,
  or a P1 script error), reset the counter and extend.

---

## 7. Earliest realistic authoritative cutover + retirement

**Target: Mon 20-Jul 2026** (cutover done over the weekend of 18–19-Jul, effective the Monday boot), contingent
on the §5.2 criteria **and** the ledger deepening holding across **≥2 clean shadow weeks** (06–17-Jul) plus a
demonstrated UNVERIFIED.

**B-1 enforce flip** (independent, smaller) can precede it: **earliest Mon 13-Jul** once §5.1 holds.

### Cutover steps (P1 authoritative + retire the false-VERIFY path)
1. Re-confirm all §5.2 criteria on the accumulated `eod_broker_reconciliation` rows.
2. Flip `eod_reconcile.authoritative: false → true` (now ISSUES/UNVERIFIED alert at **CRITICAL**, not INFO).
3. **Retire `eod_verify`** (the local-only false-VERIFY it replaces): remove its `@15:55` job from
   `config/cron_registry.yaml`, regenerate the canonical crontab, let `post-receive` install it. Keep the module
   in git history for rollback; the registry drop stops the Cron Officer expecting it.
4. **Subsume `reconcile_pnl`:** P1 already does the broker-vs-local day-P&L reconcile with the correct columns
   and its own creds — drop `reconcile_pnl` from the registry too (it was UNSCHEDULED/dead-armed anyway).
5. `reconcile_positions` (@15:45, detect-only) may stay as an earlier tripwire, or be folded in later — not
   required for cutover.
6. Push off-market; verify crontab equality; watch the first authoritative EOD closely.
> P1 remains **DETECT + ALERT only** even when authoritative. Auto-fix of detected divergences is **P3** (a later,
> separate build) — cutover does not grant P1 any mutation of `trades`/capital.

---

## 8. Confirmation: no HIGH/CRITICAL open outside {A-2, C-1, B-1, P1}

From the 02-Jul security audit (1 CRIT + 4 HIGH), verified against `main == 9becf8c`:

| Finding | Sev | Status |
|---|---|---|
| **C-1** live creds in committed `.env.example` + hardcoded api_keys | CRIT | Scrub/rotation deployed in `9becf8c`; **completion in this push** (C-1 branch). Residual = history-purge + optional rotate (§9). |
| **A-1** timeout/crash → naked orphan | HIGH | **CLOSED / deployed** on `9becf8c` (tag-correlation recovery). |
| **A-2** timeout-retry duplicate entry | HIGH | **In this push.** |
| **B-1** dead unrealized-MTM daily-loss control | HIGH | **In this push** (shadow → enforce). |
| **C-2** open webhook | HIGH | **Phase-2 (paper-parity secret + per-IP rate limit) already on `main`** (`d922007`, ancestor of `9becf8c`). Residual = **network Phase-3** only (bind `0.0.0.0`, no HMAC/TLS) → deferred infra (§9). |

**Conclusion: every headline HIGH/CRITICAL is either already deployed or in this batch. The only open HIGH residual
is C-2 network Phase-3, which is an infrastructure decision (not a code push) and is deferred by choice.**

---

## 9. Deferred — NOT in this push (so nothing is lost)

- **C-1 git-history purge** — runbook prepared (`docs/audit/c1_history_purge_runbook_02jul2026.md`), **not run**.
  Defense-in-depth before any *public* push; the origin is an internal VM bare repo (no GitHub), so low urgency.
- **Optional api_key rotation** — the 2 api_keys are non-authenticating alone (need the rotated secret/TOTP);
  rotate at leisure.
- **C-2 network Phase-3** — bind `:8080` to localhost (C-3) + IP allowlist (needs static Chartink IPs) **or**
  reverse proxy + TLS + HMAC. Rama's infra choice; after this batch.
- **P1 ledger-dimension deepening** — upgrade `ledger_status` from the local 3-balance invariant to a real
  cross-source capital reconcile **before** the P1 authoritative cutover (§5.2/§7). Small follow-up build.
- **T2 CNC live-validation full-repair** — proof script bit-rotted (multiple breakages); `fix-t2-import-02jul`
  @ `b826ae0` fixes only the L71 import and is **parked/unpushed**. Repair the whole script off-market → clean
  dry-run → commit → run the complete T2 on a clean day.
- **`credentials.xlsx` deletion** — Rama; still present on the PC per the post-rotation note.
- **W10** — `get_daily_realized_net_pnl` double-subtracts costs (`gross − 2·costs`); **safe direction** (trips
  early, never late), tiny at current volume. Parity-safe fix worth doing, non-urgent. MEDIUM/LOW.
- **Secondary design question** (flagged, not built) — should ONE naked symbol SOFT_KILL the *whole* system, or
  just that symbol? Open.

---

## 10. Rollback

- **A-2 / C-1 / B-1 / P1 code:** `git revert` the relevant merge commit(s) and push; restart the trader.
- **B-1 / P1 behaviour:** just flip the shadow flag back to `false` (no revert needed) + restart — instant, safe.
- **Schema v42:** pure-add; nothing to roll back. If ever needed, the pre-push DB backups
  (`*.pre-v42-<date>`) are the fallback (v42-code will re-migrate them).
- **Crontab:** `post-receive` only installs when `generate==canonical`; a bad state WARN-skips (live crontab
  unchanged). Break-glass: reinstall the prior canonical crontab.

---

*Companion memory: `p1_eod_broker_reconcile_impl_02jul`, `b1_daily_loss_unrealized_mtm_impl_02jul`,
`a2_timeout_retry_impl_02jul`, `c1_secret_remediation_02jul`, `audit_remediation_status_02jul`.*
