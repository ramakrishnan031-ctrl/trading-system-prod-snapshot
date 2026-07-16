# DEPLOY DONE — M-C capital-safety cluster (16-Jul-2026)

**Executed:** 16-Jul-2026, 22:12–22:14 IST (OFF-MARKET — past the 17:05 EOD-cron
window, before 08:15). Executor: VS Code Claude, on Rama's handed instruction.
**Outcome: DEPLOYED + VERIFIED.** No code changed during the deploy. No schema change.

**VM == bare == `06a61cb`** (was `11abebb`) · **code == the drilled tag
`deploy-16jul-mc-cluster` → `341cc57`** · schema **v44 unchanged (no migration)**.

> **GOES LIVE AT THE NEXT 08:15 BOOT.** `trading-system.service` is halted on the
> planned operator SOFT_KILL; the boot auto-clears it. **No restart was owed or
> performed tonight.**

---

## 1. What was deployed — four capital-safety fixes as one validated unit

| Fix | What it does |
|---|---|
| **M-C4** | `record_api_failure` no longer holds the kill-switch RLock across `soft_kill`'s `bus.publish` + Telegram send. That lock gates `is_active()` — the last-mile order check on every entry/exit — and it was blocked for a measured **20.02s** during a broker wobble, i.e. exactly when it mattered. |
| **M-C8** | `hard_kill`'s 2h exit-retry loop no longer runs on the caller's thread. All six production callers are on the fill/commit path, so an emergency could starve the fill pipeline for up to two hours. Now a non-daemon worker; fire-and-return. **Also closes a today-latent race**: the eod-self-exit could exit the process mid-flatten because `count_active_positions()` is blind to `EXITING`. |
| **M-C5** | `commit_adopted_entry` is now safe on its own via a CAS claim. Two non-gating callers used to both commit; the loser's `ValueError` fired a **spurious hard_kill** — an emergency halt caused by nothing but a race. |
| **M-C6** | A zero/negative size multiplier now **skips** instead of flooring to 1 lot (capital against the sizing model's own intent). Behaviour-neutral today (`min_weight=0.5` clamp); the prerequisite for ever lowering it. |
| **(+)** | `test_main` no longer leaks a MagicMock into `main._log` — which had made *any* later test asserting on main's logging silently vacuous. |

**Deployed behaviour change tonight: none** — the engine is halted; everything takes
effect at the 08:15 boot.

---

## 2. The code-identity gate (the thing that made this safe)

Local `main` at run time was **`06a61cb`** — **not** the `341cc57`/`2c7e05d` written in
the consolidation report. Docs commits had ridden on top, exactly as the SHA caveat
predicted (that trap fired for real earlier today on the F1 deploy, where the
instruction pinned `f68d15d` and main had advanced two docs commits past it).

So the SHA was re-derived at run time and identity proven against the **tag**, both
before the push and again on the **deployed bare history**:

```
git diff --name-only deploy-16jul-mc-cluster..HEAD
  docs/audit/mc_cluster_consolidation_16jul2026.md      ← markdown ONLY
non-markdown delta: EMPTY
```

⇒ **the code deployed IS the drilled, regression-green tag `341cc57`.** The tag is an
ancestor of the deployed HEAD. **The tag is the code identity, not the branch SHA.**

---

## 3. Verification — all gates pass

| Check | Result |
|---|---|
| Off-market clock | ✅ 22:12 IST — outside 09:00–15:30 and 15:30–17:05 |
| Starting VM/bare (live, not the cached ref) | ✅ `11abebb` |
| Code-identity gate (pre-push) | ✅ markdown-only vs the tag |
| Pre-deploy backup | ✅ `pre_deploy_mc_cluster.db` — 357,146,624 B, `integrity_check` **ok**, 361 trades, v44 |
| Push | ✅ `11abebb..06a61cb` + tag; post-receive checkout + crontab auto-install |
| Deployed bare HEAD | ✅ `06a61cb` == the confirmed local HEAD |
| Code-identity gate (post-push, on bare) | ✅ markdown-only vs tag ⇒ code == `341cc57` |
| Checkout landed | ✅ `/home/ubuntu/systems/trading-system/`, `kill_switch.py` mtime 22:13:08 |
| Schema | ✅ **v44 unchanged — no migration** |
| DB integrity / FK | ✅ `integrity_check` ok · `foreign_key_check` clean |
| Services | ✅ token-watcher, alert-watcher (`active/running`, NRestarts=0), gui-dashboard all active |
| `trading-system.service` | ✅ `failed`/exit-**4** = the designed halt-on-active-kill (planned pause), **not a fault** |
| Deployed modules compile · config validates | ✅ COMPILE OK · CONFIG_LOAD_OK |
| PC tree clean / no code modified during deploy | ✅ 0 modified |

### 3.1 All four fixes verified present in the *deployed* tree

Verified on the VM against the checked-out files — and **M-C4 by AST, not grep**, since
it is a property of code *structure* (is the call inside the lock?) that a text search
cannot honestly answer:

```
M-C4  record_api_failure: soft_kill calls INSIDE the `with self._lock` block = 0
                          call site = line 750  →  OUTSIDE the lock   →  INTACT
M-C8  worker + dedicated _flatten_lock + FlattenState machine        →  INTACT
M-C8  main.py: _ACTIVE_FLATTEN_IN_PROGRESS gate + _shutdown drain + wired → INTACT
M-C5  fund_manager._commit_claims CAS                                →  INTACT
M-C6  position_sizer ZERO_MULTIPLIER skip                            →  INTACT
(+)   test_main _restore_main_log fixture                            →  INTACT
```

---

## 4. Go-live: the next 08:15 boot

Verified rather than assumed:

- **No migration will run.** Deployed `EXPECTED_SCHEMA_VERSION = 44`; live DB
  `schema_version = 44` ⇒ the boot skips migration. No migration-guard sentinel has
  fired (nothing opened the live DB with newer code).
- **The SOFT_KILL auto-clears.** The `kill_switch_state` row is
  `SOFT_KILL | 2026-07-16 | operator`. At the 17-Jul 08:15 boot that is a **prior-day**
  kill, and `main.py:1607` `clear_stale_state` wipes any prior-day kill regardless of
  type (the 2026-06-20 HEADLESS GUARANTEE). Confirmed present in the deployed `main.py`.
  **No operator action needed; no restart owed.**
- Token auto-refresh cron (08:15 Mon–Fri) present.

⇒ The cluster becomes live at the 17-Jul 08:15 boot.

---

## 5. ⚠️ The caveat that follows this into production

**M-C8 rewrites the EMERGENCY-EXIT path. The sandbox drill (35/35, no invariant
violation) used a MOCK broker — it cannot prove real-broker latency behaviour.**

**The first live HARD_KILL after this deploy is the real test.** Nothing to do now;
flagged so it is watched closely when it happens. What to look for in the logs:

- `kill_switch: HARD_KILL flatten dispatched to worker thread` — fire-and-return worked.
- `kill_switch: flatten worker finished — all N attempted position(s) flat` — the happy end.
- `flatten worker finished with N UNEXITED trade(s)` / `flatten worker CRASHED` /
  `SHUTDOWN WITH FLATTEN STILL RUNNING` / `could NOT start the flatten worker` — the
  loud paths, each meaning manual broker-truth verification.
- Never flatten from DB state — follow the 8-step broker-truth runbook.

Also standing: **M-C6 inverts a documented FIX-133 decision** (ratified; inert until
`min_weight` is lowered), and **KS6's "re-runs cancellation" is now path-specific**
(legacy re-runs; the adapter path is single-flight).

---

## 6. Rollback (all schema-free, no data migration)

- **L1** — revert an individual fix commit (the four are independent).
- **L2** — revert a whole `--no-ff` merge (`c0c9376` M-C4 / `8bc685a` M-C8 / `341cc57` M-C5+M-C6).
- **L3** — reset main to `16437ae` (pre-cluster = the previously deployed code point `11abebb` + docs).
- **DB** — `data_store/backups/pre_deploy_mc_cluster.db` (pre-deploy, verified).

Any rollback needs an off-market push; no restart is owed while the engine is halted.
</content>
