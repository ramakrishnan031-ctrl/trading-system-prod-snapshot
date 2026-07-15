# 15-Jul COMBINED DEPLOY — DONE (executed 16-Jul 00:0x–00:2x IST, off-market)

**Status: ✅ COMPLETE + VERIFIED.** VS Code Claude executed the full runbook
(`DEPLOY_DECISION_SHEET_TEMPLATE_15-Jul-2026.txt` Q1–Q6; Rama-authorized) end to
end: F0 SMTP credential installed + proven, `main`+tag pushed and the deploy
verified on the VM, and the Phase-B backlog prune completed with trades UNCHANGED
and `foreign_key_check` clean. No STOP condition was hit.

> Timing note: work began 15-Jul evening; the clock rolled past midnight during
> execution, so the deploy landed **16-Jul 00:1x IST** — still the same continuous
> off-market window (outside 09:00–15:30 and 15:30–17:05). **16-Jul is a trading
> day** (`holiday_guard.is_trading_day = True`), so the 08:15 boot loads the new
> code for the 16-Jul session as intended.

## Ground-truth state
| | Before | After |
|---|---|---|
| VM bare / deployed HEAD | `2dc69d5` | **`c9fb298`** |
| Rollback tag `deploy-15jul-combined` | (unpushed) | **pushed → `1645bd6`** |
| Live schema | v44 | **v44 (no migration — v44==v44)** |
| `signals` | 146,000 | **32,623** |
| `screener_results` | 122,453 | **23,363** |
| `trades` | 355 | **355 (UNCHANGED)** |
| SMTP email path | DEAD (535 BadCredentials) | **SMTP login OK** |
| alert-watcher | exit-2 SMTP loop | **exit 0 (healthy)** |

## 1. F0 — ALERT_SMTP_PASSWORD restored (secret NOT shown here)
- **Q3 said YES (already in .env) but that was mistaken:** the live `.env` mtime
  was **Jul 5** (untouched since; credential broke 14-Jul 21:20), so it still held
  the OLD expired app-password. Installed the fresh Q6 app-password as directed.
- Installed into the VM `.env` (`/home/ubuntu/systems/trading-system/.env`) as
  `ALERT_SMTP_PASSWORD`. **Secret handling:** extracted from Q6 without echoing;
  transmitted over SSH **stdin** (never a command-line arg → not in VM `ps`/shell
  history/logs); atomic temp-file + `os.replace`; perms preserved **600
  ubuntu:ubuntu**.
- **Surgical change proven:** `.env` diff vs a pre-edit backup = *only* the
  `ALERT_SMTP_PASSWORD` line (`IDENTICAL_EXCEPT_SMTP_LINE`); all **24** keys
  intact, none lost/gained, byte size unchanged (3489). (An install-log printout
  of `2755` was Python `len(str)` counting characters, not bytes — cosmetic.)
- **Proven valid:** direct Gmail SMTP **login OK** (STARTTLS, no send) AND the
  deployed canary reports `✅ email: SMTP login OK`.
- Pre-edit `.env.pre_f0_bak` (held the OLD expired password) was **removed** after
  verification.

## 2. Push (Rama-authorized delegation)
- Fresh pre-deploy DB backup first: `data_store/backups/pre_deploy_combined_20260716_001521.db`
  (357 MB, `quick_check=ok`).
- `git push origin main` → `2dc69d5..c9fb298`; post-receive hook ran:
  `Deploying main to /home/ubuntu/systems/trading-system` → `crontab AUTO-INSTALLED
  from canonical` → `Deployment complete`.
- `git push origin deploy-15jul-combined` → `[new tag]` (stored; no deploy action).
- Refspecs were explicit (`main` + the one tag) — no `--all`/`--tags`.

## 3. Deploy verification (all green)
- bare HEAD `c9fb298`; tag → `1645bd6`; deployed work-tree has **no modified
  tracked files** vs HEAD.
- Branch-B files present on VM: `scripts/monitoring_canary.py`,
  `scripts/deploy_assert.py`.
- **schema still v44** (read-only `schema_meta`).
- **Canary registered:** `20 8 * * *` `monitoring_canary.py` (08:20 daily);
  `eod_cleanup` 15:50 cron intact.
- Services: `token-watcher` + `gui-dashboard` **active**; `trading-system`
  **inactive** (expected — clean 16:00 self-exit; boots 08:15). alert-watcher
  restarted onto new code + new credential → `ExecMainStatus=0`, journal shows
  `Started → Deactivated successfully` (normal periodic-oneshot; the rising
  `NRestarts` + `activating/auto-restart` is documented-normal, NOT a crash loop).
- **Canary end-to-end:** `✅ email · ✅ telegram · ✅ sentinel (0 pending) · ✅
  dashboard` — email path restored, sentinel backlog delivered, no F1 degraded
  marker.

## 4. Phase-B prune (Q4=YES, window=7 → cutoff 2026-07-09)
- Fresh pre-prune backup: `data_store/backups/pre_prune_20260716_002038.db`
  (357 MB, `quick_check=ok`).
- Dry-run preview (via `run_eod_cleanup(dry_run=True)` for count visibility, since
  the `__main__` entrypoint self-skips the fingerprint prune through
  `skip_if_non_trading_day`; today is a trading day, so the normal path would also
  run): `fingerprints_pruned=113,377`, stale_signals/orders/orphaned all 0.
- Real run: **113,377 signals pruned** (children-first, FK-safe, batched @2000/commit;
  60.8s; no exception). Ran the deployed FK-safe `eod_cleanup` logic.
- **Post-prune safety (all pass):** `trades=355` (unchanged); `0` trades whose
  `signal_id` was deleted; `foreign_key_check` **CLEAN**; `integrity_check=ok`;
  `schema=44`.
- **Steady state:** `system.eod_cleanup.signal_retention_days: 90` left unchanged
  (committed config); the 15:50 cron now maintains the small daily increment at 90d.

## 5. Security note (Rama)
- The decision sheet **contains the app-password in Q6** (it was typed there despite
  the sheet's own no-secrets rule). It has been **excluded from git locally**
  (`.git/info/exclude`; `git status` clean, `!!` ignored, 0 tracked). **Please
  DELETE** `D:\Projects\trading-system\DEPLOY_DECISION_SHEET_TEMPLATE_15-Jul-2026.txt`.
- There is **no separate Gmail *account* password** field in the sheet (only the
  app-password), so nothing else to scrub from it — just delete the file. If you
  saved the account password anywhere else, remove it there.
- VM `.env` is gitignored (`.gitignore:6`) and untracked in the bare repo — the
  installed secret can never be committed.

## 6. Next-EOD checklist (Rama — at the 16-Jul close)
- [ ] `eod_cleanup` 15:50 cron: PASS at 90d (small increment; no FK error).
- [ ] `generate_screened_csv`: PASS (M-SC2b read-only path).
- [ ] `monitoring_canary` 08:20: all four paths ✅ (email stays OK).
- [ ] Cron Officer shows FUNCTIONAL status (F2); `check_cron_drift` false-alarm gone.
- [ ] **EOD emails actually arrive** (the real proof F0 held through a full session).

## 7. Rollback (documented — NOT executed)
- **L1 (preferred):** `git revert` the specific fix commit(s) on `main` + one
  off-market `systemctl restart trading-system` (schema-free; the fixes carry no
  migration). Config-only alt for monitoring: none needed.
- **L2 (full):** `git reset --hard` to the tag's first parent
  (`deploy-15jul-combined^`), push, off-market restart. The annotated tag
  `deploy-15jul-combined → 1645bd6` is the single-tag rollback anchor (the two docs
  commits `cb567cb`/`c9fb298` ride on top, code-inert).

## 8. Git state after this deploy
- **VM==bare==`c9fb298`** (deployed code); tag `deploy-15jul-combined` on the VM.
- PC `main` will be **one docs-only commit ahead** (this report + PATHS/SYSTEM_MAP
  notes) — unpushed, per the standing "docs ride unpushed" convention; zero runtime
  effect. Push whenever convenient or let it ride the next deploy.
