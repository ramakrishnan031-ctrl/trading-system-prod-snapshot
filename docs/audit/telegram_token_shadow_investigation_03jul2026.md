# Telegram Token Shadow — Investigation (READ-ONLY)

Date: 2026-07-03 (Friday), ~23:20 IST, off-market. **Investigation only — no
edit, no restart, no push.** No token value appears in this file. Web Claude
designs the permanent (parity-correct) fix from these facts next.

Follow-on to the webhook-rotation flag: `telegram.conf` (a systemd drop-in)
hard-codes `TELEGRAM_BOT_TOKEN` as an `Environment=` override that shadows
`.env`. Question: which token does the running process actually use, and does
it match the 02-Jul rotated value?

## 1. Definition sites (names/paths/lines, NO values)

| Source | Keys defined | Location |
|---|---|---|
| `.env` (EnvironmentFile) | `TELEGRAM_BOT_TOKEN` (L46), `TELEGRAM_CHANNEL_PRIMARY` (L49), `TELEGRAM_CHANNEL_SECONDARY` (L52), `TELEGRAM_PERSONAL_CHAT_ID` (L55) | `/home/ubuntu/systems/trading-system/.env`; referenced by unit `EnvironmentFile=` (unit L14) |
| systemd drop-in `telegram.conf` | `TELEGRAM_BOT_TOKEN` (L52), `TELEGRAM_CHANNEL_PRIMARY` (L53), `TELEGRAM_CHANNEL_SECONDARY` (L54) — all as `Environment="…"` | `/etc/systemd/system/trading-system.service.d/telegram.conf` (mtime **2026-05-18**, root:root 644) |
| systemd drop-in `watchman.conf` | none (only `[Unit] Wants=trading-watchman.service`) | same dir; **does not touch telegram** |
| Code (resolves from `os.environ`, no hardcoded token) | `main.py:245-246` (`os.environ.get`), `main.py:1879` (`bot_token=os.environ[tg_cfg.bot_token_env]`), `alerts/telegram_notifier.py:236-237` (`from_env`), `scripts/cron_officer.py:498`, `scripts/premarket_healthcheck.py:48`, `scripts/reconcile_positions.py:390`, `scripts/reconcile_pnl.py:351` | `bot_token_env` default is the NAME `"TELEGRAM_BOT_TOKEN"` (`core/config_loader.py:604`); config JSON stores env-var NAMES only, never values |

No `load_dotenv` in `main.py`; the main process gets env solely from systemd (EnvironmentFile + drop-in overrides). Cron scripts run **outside** the unit and source `.env` (they do not see drop-in `Environment=`).

## 2. Precedence — live source verdict

systemd applies main-unit directives then drop-ins; for the same key a drop-in `Environment=` **overrides** `EnvironmentFile=`. `telegram.conf` sets `TELEGRAM_BOT_TOKEN` as `Environment=`, so:

- **Main `trading-system` process → live `TELEGRAM_BOT_TOKEN` = `telegram.conf` (drop-in), NOT `.env`.**
- **Cron jobs (officer, premarket healthcheck, EOD system-manager, control tower, reconcile_*) → live `TELEGRAM_BOT_TOKEN` = `.env`** (unit drop-in does not apply to cron).

Same split applies to `TELEGRAM_CHANNEL_PRIMARY`/`SECONDARY` (also in the drop-in). `TELEGRAM_PERSONAL_CHAT_ID` is only in `.env` → not shadowed (both paths use `.env`).

## 3. `.env` vs drop-in — value-blind comparison (sha256[:12] of each; values never printed)

| Key | `.env` sha12 | drop-in sha12 | Verdict |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `79b64abe7c82` | `a581ca0b6831` | **DIFFERENT** |
| `TELEGRAM_CHANNEL_PRIMARY` | `d895e34184da` | `d895e34184da` | IDENTICAL (harmless) |
| `TELEGRAM_CHANNEL_SECONDARY` | `5321e13f2ab4` | `5321e13f2ab4` | IDENTICAL (both the `FILL_CHANNEL_ID_2` placeholder → secondary channel unconfigured in both) |
| `TELEGRAM_PERSONAL_CHAT_ID` | present | (not in drop-in) | not shadowed |

**Validity confirmed by a read-only, value-blind `getMe` on each token** (GET to Telegram's own API; token never printed; both are the same bot id `8648177777`, public username `@Trade_sysbot`):

- **`.env` token → VALID** (`ok=true`, `@Trade_sysbot`).
- **drop-in `telegram.conf` token → HTTP 401 Unauthorized (DEAD).** The 02-Jul rotation regenerated the token for the same bot, which **revoked** the old one still sitting in the drop-in.

## 4. Bug or not — REAL BUG (not benign redundancy)

**The 02-Jul rotation is NOT live for the main process.** The main `trading-system` process holds a **revoked (401) bot token** (the drop-in's, pre-02-Jul). The valid, rotated token lives in `.env` and is used only by cron paths. This is why Rama still receives Telegram alerts — those are **cron-originated** (Cron Officer briefing/EOD, control tower, healthcheck) and use the valid `.env` token; the main process's alerts do not.

**Runtime impact — precise, not overstated:**
- `failed_alerts.log` has **no entries since 02-Jul 18:50** (the rotation time). By the G8 tier routing (`telegram_notifier.py:308-311`): **INFO/WARN send failures are dropped SILENTLY** (no log), ERROR/CRITICAL failures write `failed_alerts.log`, and CRITICAL also writes a sentinel → `alert_watcher` **email backup**.
- Consequence: any INFO/WARN Telegram alert emitted by the **main process** with the dead token fails **invisibly** (no log trail) — this is the dangerous part (silent by design). No ERROR/CRITICAL main-process Telegram failure has been logged since 02-Jul (so either none fired, or they also went silent — not distinguishable from logs). CRITICAL alerts still reach Rama by **email** regardless of the dead token, further masking the gap.
- I did **not** find a clean, positively-logged main-process Telegram failure for today (03-Jul); an earlier broad grep that suggested ~174 hits was a **false positive** (matched unrelated `signal_processor` score-rejection lines) and is retracted. The mechanism is confirmed; a positive runtime failure line is not asserted.

## 5. Blast radius + parity

- **Shadowed keys:** `TELEGRAM_BOT_TOKEN` (DIFFERENT → dead for main process = the bug), `TELEGRAM_CHANNEL_PRIMARY` (identical → harmless), `TELEGRAM_CHANNEL_SECONDARY` (identical placeholder → harmless). `TELEGRAM_PERSONAL_CHAT_ID` not shadowed.
- **Same EnvironmentFile-shadow pattern as the webhook finding**, but here the drop-in value has actually diverged (revoked), turning latent redundancy into a live defect.
- **Parity:** the same unit + `telegram.conf` drop-in governs the process in **both paper & live** (mode is a runtime arg, not a separate unit) → the shadow affects paper and live **identically**. Confirmed.
- **Split-brain summary:** main process = dead token (drop-in); cron = valid token (`.env`). Any future `.env`-only telegram rotation will again miss the main process until the drop-in is reconciled.

## 6. Fix direction (for Web Claude — NOT applied here)

Reconcile to a single source: remove `TELEGRAM_BOT_TOKEN` (and the redundant `TELEGRAM_CHANNEL_*`) from `telegram.conf` so `.env` (EnvironmentFile) is authoritative for the main process too, then `daemon-reload` + restart at the next off-market window. Parity: one change covers both modes. **Not done in this investigation.**

## Deviations
- Added two **read-only** diagnostics beyond a pure config read: value-blind `sha256` comparison and a value-blind `getMe` validity probe (GET to Telegram's own API, token never printed). Both are non-mutating and were necessary to answer "which token is live" definitively rather than by inference. No edit/restart/push.
