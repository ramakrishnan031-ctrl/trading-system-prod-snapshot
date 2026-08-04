# LEDGER #11 — SECRETS CONCENTRATION SURFACE — MEASUREMENT — 04-Aug-2026

**Status: `<MEASURED — NOTHING TOUCHED, NOTHING ROTATED, NOTHING MOVED>`.**
Measured at **`2d9c114`**. Register: `MASTER_PENDING_01-Aug-2026.md` debt-ledger **#11**
(IA-XSEC family). Topical after **R12**, which surfaced a `sed -i` that comments out
`WEBHOOK_SECRET` and 15 allowlist entries POSTing to the live webhook.

## 0. ⛔ METHOD CONSTRAINT, AND IT WAS HONOURED

**No credential value was read, printed, copied, rotated or moved.** Every figure below
comes from one of: key **names** (`grep -o '^NAME='`, which cannot emit a value by
construction), file **metadata**, **counts**, or **AST** structure. Where a question could
only have been answered by reading a value, it was **not answered** — and that is stated
rather than quietly skipped.

---

## 1. ⭐⭐ THE CONCENTRATION, MEASURED: the blast radius is **5 accounts wide, the need is 1**

`config/accounts.csv` (**tracked**) defines **5 Zerodha accounts**. Exactly one is live:

| account | `is_primary` | `enabled` | `capital_share_pct` | credentials in `.env` |
|---|---|---|---|---|
| **LFL836** | **TRUE** | **TRUE** | **1** | key + secret + TOTP |
| DR6114 | FALSE | **FALSE** | 0 | key + secret + TOTP |
| D351962 | FALSE | **FALSE** | 0 | key + secret + TOTP |
| ZA004 | FALSE | **FALSE** | 0 | key + secret + TOTP |
| ZA005 | FALSE | **FALSE** | 0 | key + secret + TOTP |

⇒ **15 broker credentials are present; 3 belong to the one enabled account.** The other
**12 serve accounts the system cannot select** — `AR3 primary()` returns the single
`is_primary` row and `AR10 get_enabled_accounts()` returns only `enabled=True` rows.

⚠️ **Two of the four disabled accounts are REAL personal accounts** (their labels are
person names, in the tracked `accounts.csv`); two are `Placeholder-*`. ⇒ **A compromise of
one `.env` exposes live API credentials for two real people's brokerage accounts that this
system never uses.** That asymmetry — *the file's blast radius exceeds the system's need by
4 accounts* — **is** ledger #11's concentration, stated concretely.

⛔ **Not a recommendation to delete them.** Removing credentials is a change to a live
production secret store and is Rama's call; the placeholders may also be deliberate future
capacity. **Recorded, not acted on.**

---

## 2. PC ↔ VM DIVERGENCE: the VM holds a **strictly larger** secret set

| | PC | VM |
|---|---|---|
| key count | **20** | **24** |
| `.env` mode | ACL: **owner-only** (see §3) | **600** `ubuntu:ubuntu` |
| tracked in git | **no** (never committed) | n/a |

**VM-only keys (4):** `ZERODHA_PASSWORD` · `ZERODHA_USER_ID` · `ALERT_SMTP_PASSWORD` ·
`GEMINI_BIN`.

⭐ **The first two matter most: the VM holds the account PASSWORD and user id, the PC does
not.** That is required by design — `scripts/auto_refresh_token.py` performs the headless
login (password + TOTP → `request_token` → `access_token`), which only the VM runs. ⇒ **the
VM is the higher-value target of the two, and by more than "it is the production host".**

✅ **`ALERT_SMTP_PASSWORD` present is CONSISTENT with the standing record**, not a
contradiction of it: the note that "`ALERT_EMAIL_*` is ABSENT from VM `.env` ⇒ the
notifier's own email fallback is INERT" concerns the `ALERT_EMAIL_*` names, which are still
absent. The SMTP password serves the separate `alert_watcher` service. **Checked because the
two look like they disagree; they do not.**

---

## 3. PERMISSIONS — ✅ both correct, and ⛔ one false finding avoided

- **VM `.env`: mode `600`, `ubuntu:ubuntu`.** ✅
- **PC `.env`: `Get-Acl` shows a single ACE — `DESKTOP-029USHU\rama :: Write, Read,
  Synchronize (Allow)`, inheritance disabled.** ✅ Owner-only.

⛔⛔ **git-bash `stat` reports the PC `.env` as `644`, which reads as world-readable and is
MEANINGLESS ON NTFS.** The recorded 25-Jul remediation was an **ACL** change, not a `chmod`,
so the POSIX view was never going to reflect it. **Reporting "the PC `.env` is
world-readable" would have been a false finding on a security item.** ⇒ **On Windows, only
`Get-Acl` is authoritative; `stat`/`ls -l` in git-bash are a translation layer, not evidence.**

✅ **Value-blind leak probe on the world-readable files near the token workflow**
(`logs/token_watcher.log` mode 644, 2,261 lines; `logs/cron-auto-token.log` mode 664):
`grep -ciE "access_token|api_secret|password|totp"` = **0 and 0**. No secret-shaped string in
either. ⚠️ **This proves the four named patterns are absent, not that nothing sensitive is
present** — the check is exactly as wide as its pattern list.

---

## 4. THE READER SURFACE — 6 keys, 11 project files

AST census over project modules (⛔ excluding `sats/semgrep-env/`, `venv/`, `node_modules/`,
`site-packages/` — a first pass that included them returned **253 files** and a top hit of
`SEMGREP_REPO_NAME`, i.e. vendored third-party code, **not this system**):

| key | reads | where |
|---|---|---|
| `ZERODHA_API_KEY_LFL836` | 4 | `fetch_daily_candles:43` · `forward_shadow_record:101` · `gemini_data_integrity_check:48` · `reconstruct_excursions:68` |
| `ZERODHA_API_KEY` | 4 | `reconcile_pnl:99` · `reconcile_positions:131` · `refresh_instruments:340` · `t2_cnc_gtt_realtest:159` |
| `ZERODHA_ACCESS_TOKEN` | 4 | `reconcile_pnl:100` · `reconcile_positions:132` · `refresh_instruments:341` · `main.py:2546` |
| `TELEGRAM_BOT_TOKEN` | 3 | `cron_officer:568` · `monitoring_canary:264` · `main.py:265` |
| `TELEGRAM_CHANNEL_PRIMARY` | 1 | `main.py:266` |
| `WEBHOOK_SECRET` | 1 | `main.py:2938` |

⚠️ **A hardcoded account id is present in production scripts** — four scripts read
`ZERODHA_API_KEY_LFL836` **literally**, rather than resolving the primary through
`account_registry`. ⇒ **switching the primary account in `accounts.csv` would silently leave
those four pointed at LFL836.** Recorded; ⛔ not fixed.

### 4a. ⛔ A NEAR-MISS WORTH MORE THAN THE CENSUS

The static AST scan found **no reader at all** for `ZERODHA_API_SECRET_*` or
`ZERODHA_TOTP_*`. Taken at face value that supports a striking claim — *"12 credentials are
never read"*. **It is FALSE.**

The suffixed names are built **dynamically**: `main.py:224-225`
(`f"ZERODHA_API_KEY_{primary_account_id}"`, `f"ZERODHA_API_SECRET_{primary_account_id}"`),
`scripts/preflight/checks/broker.py:114`, `scripts/t2_cnc_gtt_realtest.py:159`; and the TOTP
env var **name** is carried as data in `accounts.csv` → `AccountRow.totp_secret_env` →
`auto_refresh_token.py`. An AST scan for string-literal keys **cannot see any of that.**

⭐ **It was caught only because the result contradicted a known fact** — the standing record
that `ZERODHA_TOTP_LFL836` was rotated and wired on 25-Jul. A scan finding zero readers for
a credential known to be in daily use is a **broken scan**, not a discovery. **A key-name
census over a codebase that constructs key names is structurally incomplete**, and any future
"unused secret" claim must clear that bar first.

---

## 5. WHAT IS IN GIT — identifiers yes, secrets no

- ✅ **`.env` was NEVER committed** (`git log --all -- .env` empty) and is ignored at
  `.gitignore:6`.
- ⚠️ **`.env.example` IS tracked** and its key-name set is **identical** to `.env`'s.
  Value-blind check: only **23** whole lines are shared between them (of 64 / 50), i.e. the
  example is **not** a copy of the real file — consistent with placeholders. ⛔ **The values
  were not read, so "placeholders" is an inference from line overlap, not a verified fact.**
- ⚠️ **`config/accounts.csv` IS tracked** and contains **5 account IDs, their broker, and
  person-name labels**. These are identifiers, **not credentials** — but they are permanent
  in git history and they name real individuals.

---

## 6. OPEN — ⛔ nothing decided, nothing changed

1. **Should the 12 credentials for disabled accounts remain in the production `.env`?**
   The concentration is real and measured; the remedy is a change to a live secret store.
   **Rama's call.**
2. **The four scripts hardcoding `ZERODHA_API_KEY_LFL836`** — a latent correctness trap if
   the primary ever changes, not a security defect.
3. **Whether `accounts.csv` should carry person-name labels in a tracked file.**
4. **Nothing here touches R12's open items** (the `Bash(ssh *)` channel, the 15 webhook
   POSTs, the `sed -i` that comments out `WEBHOOK_SECRET`). Those remain the authorisation
   decision, unchanged by this measurement.

⛔ **HALT.** No credential was touched, rotated, printed or moved; no file permission was
changed; no `.env` was edited on either host.
