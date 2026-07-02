# C-1 — Git History Purge RUNBOOK (prepared 02-Jul-2026 — for Rama to EXECUTE later)

**Status:** PREPARED, NOT executed. Run in a dedicated **off-market** window.
**Urgency:** LOW — the remote is **internal-only** (`trading-vm:~/trading-system.git`, no public forge) and the 6 sensitive secrets were already rotated 02-Jul (history values are DEAD). This purge is **defense-in-depth**, required **only before any public/GitHub push**. Best bundled with the optional api_key rotation (see § 7).
**Inventory it acts on:** `docs/audit/c1_credential_exposure_inventory_02jul2026.md`.

> ⚠️ This runbook contains **no secret values**. The `expressions.txt` file (§ 3) is authored by Rama at run time with the actual literals and **deleted afterwards** — it must never be committed.

---

## 0. Preconditions (verify before starting)
- [ ] Rotation done (api_secret/TOTP/bot/webhook — already 02-Jul). Optionally the 2 api_keys too (§ 7).
- [ ] The C-1 scrub is live at HEAD: `git show main:.env.example` shows only `FILL_*` placeholders (commit `7bc3367`, deployed in `9becf8c`).
- [ ] The api_key-in-scripts fix is merged to `main` (this task's `fix-c1-completion-02jul`) — so HEAD has NO hardcoded key; the purge then only has to clean **history**, not HEAD.
- [ ] `git-filter-repo` available: `pip install git-filter-repo` (into the venv) — it is NOT currently installed on the PC or VM.
- [ ] A quiet window (system inactive / off-market). No deploy or trading in progress.

## 1. BACK UP everything first (non-negotiable — this is the rollback)
On the **VM**:
```bash
ts=$(date +%Y%m%d_%H%M%S)
cp -a /home/ubuntu/trading-system.git            /home/ubuntu/backups/trading-system.git.$ts.bak
tar -czf /home/ubuntu/backups/vm_worktree.$ts.tgz -C /home/ubuntu/systems trading-system
```
On the **PC**:
```bash
cp -a /d/Projects/trading-system /d/Projects/trading-system.prepurge.$ts.bak
```
Confirm both backups exist and are non-empty before proceeding.

## 2. Work on a FRESH MIRROR clone (git-filter-repo best practice)
```bash
cd /tmp
git clone --mirror trading-vm:~/trading-system.git ts-mirror.git
cd ts-mirror.git
```

## 3. Author the replacement expressions (Rama supplies the literals; delete after)
Create `expressions.txt` (NOT in any repo) mapping each exposed literal → a redaction token. Cover:
- the real `.env.example` values that existed in commit **`9f58848`** (the 10 vars: LFL836 & DR6114 api_key/api_secret/totp, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_PRIMARY`, `TELEGRAM_PERSONAL_CHAT_ID`, `WEBHOOK_SECRET`);
- the hardcoded **LFL836 api_key** literal that was in the 3 scripts' history.

Format (one per line; `git filter-repo` literal form):
```
<literal-secret-1>==>***REMOVED-C1***
<literal-secret-2>==>***REMOVED-C1***
...
```
(Tip to enumerate the exact old values without re-typing them: `git -C /tmp/ts-mirror.git show 9f58848:.env.example` and the old `scripts/*.py` blobs — copy each value into `expressions.txt`. Do this in the quiet window; shred the file after.)

## 4. Rewrite ALL history
```bash
git filter-repo --replace-text expressions.txt --force
```
This replaces every occurrence across **all** commits/branches/tags. **Every commit SHA from the first exposing commit onward changes.**

## 5. Verify locally (in the mirror) BEFORE pushing
```bash
# no secret literal survives anywhere:
git log --all -p -- .env.example | grep -n 'REMOVED-C1' | head        # should show redactions
for lit in "$(cat /tmp/expressions_literals_only.txt)"; do :; done    # (or pickaxe each literal:)
git log --all -S'<literal-secret-1>' --oneline                        # expect: 0 commits
# HEAD .env.example still valid placeholders:
git show refs/heads/main:.env.example | grep -c FILL_                  # >0
```
All literals → 0 pickaxe hits; `.env.example` at every commit is redaction/placeholder only.

## 6. Publish the rewrite (bare-repo deploy model — the careful part)
The `post-receive` hook checks out `main` + auto-installs the crontab on every push. A force-push of rewritten history would fire it against identical file content — harmless, but disable it during the force-push to avoid surprises.

On the **VM**:
```bash
# 6a. disable the deploy hook temporarily
mv ~/trading-system.git/hooks/post-receive ~/trading-system.git/hooks/post-receive.disabled
```
From the **mirror** (PC/tmp):
```bash
# 6b. force-push the rewritten refs to the VM bare repo
git push --force --mirror trading-vm:~/trading-system.git
```
On the **VM**:
```bash
# 6c. re-checkout the working tree to the rewritten main (content identical → NO restart needed; deploy != restart)
git --git-dir=/home/ubuntu/trading-system.git --work-tree=/home/ubuntu/systems/trading-system checkout -f main
# 6d. re-enable the hook
mv ~/trading-system.git/hooks/post-receive.disabled ~/trading-system.git/hooks/post-receive
# 6e. (optional) confirm the crontab is still the canonical one
crontab -l | diff - /home/ubuntu/systems/trading-system/deploy/cron/trading-system.cron && echo "crontab OK"
```

## 6f. Re-sync the PC clone (its old history is now orphaned)
Simplest + safest — re-clone:
```bash
cd /d/Projects && mv trading-system trading-system.oldhist && git clone trading-vm:~/trading-system.git trading-system
# reinstall the local pre-commit hook + restore the gitignored real .env from the backup:
cd trading-system && cp deploy/hooks/pre-commit .git/hooks/ && chmod +x .git/hooks/pre-commit
cp /d/Projects/trading-system.oldhist/.env ./.env    # .env is gitignored, not in the repo
```
(Alt: `git fetch origin && git reset --hard origin/main` — but a re-clone is cleanest for a full rewrite.)

## 6g. Rebase any UNPUSHED feature branches onto the rewritten base
As of 02-Jul the unpushed branches are `fix-a2-timeout-no-retry-02jul` and `fix-c1-completion-02jul` (this one). If not yet merged to main before the purge, recreate them by cherry-picking their commits onto the new `main` (their old base SHAs no longer exist). Ideally: **merge/deploy A-2 + C-1 to main BEFORE the purge**, so there are no outstanding branches to rebase.

## 7. Optional (bundle here): rotate the 2 api_keys
`ZERODHA_API_KEY_LFL836` + `ZERODHA_API_KEY_DR6114` are still ACTIVE (non-authenticating alone). If regenerating them in the Kite developer console: do it in this same window, update `.env` (VM + PC), and include the OLD api_key literal in `expressions.txt` (§ 3) so the purge also retires it from history. The scripts already read from env (this task) → no code change needed.

## 8. VERIFICATION checklist (after the whole run)
- [ ] `git log --all -S'<each literal>' --oneline` → **0 commits** for every exposed secret + the old api_key.
- [ ] `git show main:.env.example` → only `FILL_*` placeholders.
- [ ] Bare repo HEAD == PC clone HEAD == VM working-tree HEAD (all the rewritten `main`).
- [ ] `post-receive` hook re-enabled; `crontab -l` == canonical.
- [ ] A trivial test push (e.g. a whitespace doc commit) deploys cleanly (checkout + crontab auto-install) — proves the deploy model still works.
- [ ] The real `.env` restored on the PC (gitignored) + present on the VM (untouched by the purge).
- [ ] `expressions.txt` + `*.oldhist` / `*.bak` clones **shredded** once verified.

## 9. ROLLBACK (if anything goes wrong)
The force-push is the only irreversible step from the VM's side — but § 1 backed up the bare repo:
```bash
# restore the bare repo from backup, then re-checkout the working tree:
rm -rf ~/trading-system.git && cp -a /home/ubuntu/backups/trading-system.git.<ts>.bak ~/trading-system.git
git --git-dir=~/trading-system.git --work-tree=/home/ubuntu/systems/trading-system checkout -f main
```
On the PC, the pre-purge clone backup (`trading-system.prepurge.<ts>.bak`) is the original history.

**Downtime estimate:** ~15–30 min of careful, supervised work, entirely **off-market** with the trading service inactive → **zero trading impact**. The service is not restarted (content is identical pre/post rewrite); only git metadata changes.

---
**Do NOT run any of this now.** It is staged for Rama's dedicated window. Prevention (placeholder `.env.example`, gitignored `.env`, secret-scan hook incl. the new .xlsx layer) is already live and stops *new* leaks regardless of when the purge happens.
