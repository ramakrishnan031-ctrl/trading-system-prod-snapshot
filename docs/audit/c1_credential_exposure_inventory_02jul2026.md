# C-1 — Credential Exposure Inventory + Remediation Plan (02-Jul-2026)

**Type:** READ-ONLY investigation. No rotation, no history rewrite, no file edits — those are Rama's ops calls after review.
**Scope:** working tree + FULL git history. **No secret VALUES appear in this document — names + locations only.**
**Source finding:** `docs/audit/system_security_audit_02jul2026.md` § C-1 (CRITICAL).

---

## STEP 3 first — EXPOSURE SURFACE (the severity driver)

`git remote -v` → **`origin  trading-vm:~/trading-system.git`** (the VM bare repo, over SSH). **NO GitHub / GitLab / Bitbucket / any public forge remote anywhere.**

→ Exposure is **INTERNAL ONLY**: the PC clone + the VM bare repo + the VM working tree. Reaching it requires SSH access to the VM (passwordless key `trading_vm_secure`) or the PC itself. **Not publicly exposed.** Combined with the 02-Jul rotation (below), the residual risk is **LOW–MODERATE**, not the "live public leak" worst case. The history purge is defense-in-depth (and a prerequisite before any future public/GitHub push).

---

## STEP 1 — INVENTORY (exposure map)

### A. The real `.env` (actual live secrets) — NEVER committed ✅
`git ls-files` shows `.env` is **not tracked**; `git log --all -- .env` is **empty**; `.env` is gitignored (`.gitignore:6`). The real secrets never entered git.

### B. `.env.example` — real secrets in exactly ONE commit's tree
Commits that touched `.env.example`: `bcf03b5` (initial), `ebf59b7` (5-account schema), `9f58848` (test fixtures + paper config), `7bc3367` (C-1 scrub).

| Commit | `.env.example` state |
|---|---|
| `bcf03b5` initial | **placeholders** (`your_…_here` style) — not real |
| `ebf59b7` 5-acct schema | **placeholders** — not real |
| **`9f58848`** | **REAL secrets (10 vars)** ← the exposure |
| `7bc3367` scrub (HEAD) | **placeholders** (`FILL_WHEN_READY`) ✅ |

**Exposure window in history:** the real `.env.example` is in the tree of every commit in the range **`9f58848` .. `7bc3367^`** (from `9f58848` up to, but not including, the scrub). HEAD and everything from `7bc3367` onward is clean.

**The 10 real variables exposed in `9f58848` (names only):**
- `ZERODHA_API_KEY_LFL836`, `ZERODHA_API_SECRET_LFL836`, `ZERODHA_TOTP_LFL836`
- `ZERODHA_API_KEY_DR6114`, `ZERODHA_API_SECRET_DR6114`, `ZERODHA_TOTP_DR6114`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_PRIMARY`, `TELEGRAM_PERSONAL_CHAT_ID`  (channel/chat identifiers, not secrets)
- `WEBHOOK_SECRET`

(The 5-account placeholder accounts `D351962` / `ZA004` / `ZA005` and `TELEGRAM_CHANNEL_SECONDARY` were placeholders in `9f58848` too — never real.)

### C. Hardcoded live API key in tracked scripts (HEAD **and** history)
The **current LFL836 Zerodha api_key** is hardcoded (not a placeholder) in three tracked scripts — present at HEAD and throughout their history:
- `scripts/fetch_daily_candles.py:30`
- `scripts/gemini_data_integrity_check.py:45`
- `scripts/reconstruct_excursions.py:61`

(The DR6114 api_key is **not** hardcoded in any script.)

### D. SMTP / email app password — value NEVER committed ✅
`ALERT_SMTP_PASSWORD` appears in tracked files only as the **env-var NAME** (in `DEPLOYMENT.md`, `config/system_config.yaml` `password_env:`, `CONFIG_GUIDE.md`, `SYSTEM_MAP.md`, `report_data_contract.md`) and one **placeholder** (`docs/web_claude/.../v2_foundation_wrapup.txt:21` = `app_password_here`). The value lives only in the VM `.env` (gitignored). Not a leak. (SYSTEM_MAP even records this app-password as Gmail-535-invalid historically.)

### E. No other live credential found in tracked history
No other api_key/secret/token/password values in tracked files beyond B and C. The rotated secrets (below) do not appear anywhere in the tree (they are new post-rotation).

---

## STEP 2 — CLASSIFICATION (rotation urgency)

The 02-Jul rotation ([[post_rotation_creds_02jul]]) already rotated the api_secrets, TOTP seeds, bot token, and webhook secret. So:

| Secret (name) | Where exposed | Class | Note |
|---|---|---|---|
| `ZERODHA_API_SECRET_LFL836` | .env.example@9f58848 | **DEAD** | rotated 02-Jul → history value useless |
| `ZERODHA_TOTP_LFL836` | .env.example@9f58848 | **DEAD** | rotated 02-Jul |
| `ZERODHA_API_SECRET_DR6114` | .env.example@9f58848 | **DEAD** | rotated 02-Jul |
| `ZERODHA_TOTP_DR6114` | .env.example@9f58848 | **DEAD** | rotated 02-Jul |
| `TELEGRAM_BOT_TOKEN` | .env.example@9f58848 | **DEAD** | rotated 02-Jul |
| `WEBHOOK_SECRET` | .env.example@9f58848 | **DEAD** | rotated 02-Jul |
| `ZERODHA_API_KEY_LFL836` | .env.example@9f58848 **+ 3 scripts @HEAD** | **ACTIVE** | Zerodha keeps the app key (not rotated). Non-authenticating without the (rotated, uncommitted) secret. |
| `ZERODHA_API_KEY_DR6114` | .env.example@9f58848 | **ACTIVE** | as above; not in any script |
| `TELEGRAM_CHANNEL_PRIMARY` | .env.example@9f58848 | IDENTIFIER | channel id, not a secret; unchanged |
| `TELEGRAM_PERSONAL_CHAT_ID` | .env.example@9f58848 | IDENTIFIER | chat id, not a secret; unchanged |

**Net live risk:** two ACTIVE api_keys, each **non-authenticating on its own** (the matching secret was rotated and is uncommitted), on an **internal-only** repo. Everything else is dead or a non-secret identifier.

---

## STEP 4 — REMEDIATION PLAN (for Rama to review + execute — NOT executed here)

### 4a. Rotation (ordered by residual risk)
1. **DONE (02-Jul):** api_secret + TOTP (LFL836 & DR6114), Telegram bot token, WEBHOOK_SECRET — rotated + synced to VM+PC, token-gen PASS. ✅
2. **Optional / recommended:** regenerate `ZERODHA_API_KEY_LFL836` and `ZERODHA_API_KEY_DR6114` in the Kite developer console (per account). Low urgency (key alone can't auth), but it retires the only still-ACTIVE exposed values. If rotated: update the 3 scripts (§C) **and** `.env` (VM+PC) in the same pass.
3. **SMTP:** no exposure-driven rotation needed (value never committed).
4. Telegram channel/chat IDs: not secrets — no rotation; the purge (4b) removes them from history incidentally.

### 4b. History purge (defense-in-depth — plan carefully, off-market)
- **Tool:** `git filter-repo` (preferred) or BFG Repo-Cleaner. Targets: (i) `.env.example` across all history → force the placeholder content everywhere (or drop the file from pre-`7bc3367` history); (ii) the hardcoded api_key literal in the 3 scripts (`--replace-text`).
- **Bare-repo deploy-model impact (the hard part):** the rewrite changes **every commit hash**. It must be:
  1. run on a fresh mirror clone, then **force-pushed to the VM bare repo** (`trading-vm:~/trading-system.git`);
  2. the **PC clone re-cloned or hard-reset** to the rewritten history (old local refs are now orphaned);
  3. the **VM working tree** (`/home/ubuntu/systems/trading-system`, populated by the post-receive `checkout -f`) re-checked-out after the bare repo is rewritten — the post-receive hook + crontab auto-install still function on the new HEAD;
  4. any outstanding feature branches (e.g. `fix-a2-timeout-no-retry-02jul`, unpushed) rebased onto the rewritten base.
- **Risks:** rewrites ALL history; breaks every existing clone/branch/hash reference; requires a force-push; needs a backup of the bare repo first and a planned execution window. **Not urgent** given internal-only + rotated; **do it before any GitHub push** and ideally alongside the optional api_key rotation (4a.2) so the purge covers a value that's also been retired.

### 4c. Prevention — ALREADY DONE (deployed in `7bc3367`, live at HEAD) ✅
- `.env.example` = placeholders only (`FILL_WHEN_READY`). ✅
- Real `.env` gitignored + never tracked. ✅
- Pre-commit **secret-scan** hook: `deploy/hooks/pre-commit` + `deploy/hooks/secret_scan.py` (+ `tests/unit/test_secret_scan.py`). ✅
- **Residual prevention gaps** (follow-ups, not blockers): (i) the secret scanner does **not** scan `.xlsx` → `credentials.xlsx` relies on gitignore + deletion ([[post_rotation_creds_02jul]]); (ii) the 3 scripts hardcode the api_key — move to `os.environ` / `.env`; (iii) consider `gitleaks`/`detect-secrets` in CI for full-history scanning.

---

## Bottom line
- The real `.env` was **never committed**. The only real-secret commit is **`9f58848`** (`.env.example`, 10 vars), scrubbed at `7bc3367`; plus the **LFL836 api_key** hardcoded in 3 scripts (HEAD + history).
- **6 of the exposed secrets are already DEAD** (rotated 02-Jul). **2 api_keys remain ACTIVE** but are non-authenticating alone. Remote is **internal-only** (no public forge).
- **Outstanding:** (optional) rotate the 2 api_keys + clean the 3 scripts; (defense-in-depth) purge history — best done together, off-market, before any public push. Prevention controls are already live.
