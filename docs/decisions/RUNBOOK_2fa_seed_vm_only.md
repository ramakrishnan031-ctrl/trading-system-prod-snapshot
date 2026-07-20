# RUNBOOK — 2FA (TOTP) seed → VM-only. PLAN ONLY. NOTHING here has been executed.

## ⏰ TIMING — READ FIRST
**FRIDAY EVENING or SATURDAY only. NEVER the night before a trading day. Off-market, book flat.**
Reason (§D2 below): if the VM ends up holding a seed that does not authenticate, the **08:15 TOTP refresh
fails and the system boots into a state where it cannot trade.** A Fri/Sat window gives ~2 days to detect and
recover before the next session. (The token cron does **not** run on weekends, so weekend detection is
**manual** — see Verify.)

## What this changes and why
- **Verified premise (§D1, 20-Jul, read-only):** the prod TOTP seed is **hash-identical on the PC dev tree and
  the VM** (`sha256 = 23dcd7e5018f8b2b…` on both) → **both factors sit on two boxes.** Env var
  `ZERODHA_TOTP_LFL836` (base32; consumed by `pyotp.TOTP(secret).now()` in `scripts/auto_refresh_token.py`).
- **Never git-committed** — `.env` is gitignored (`.gitignore:6,59`); only `.env.example` is tracked and it is
  placeholders (`ZERODHA_TOTP_LFL836=FILL_WHEN_READY`). No repo-history exposure. ✅
- **Minor local hygiene:** PC `.env` is world-readable (`-rw-r--r--`, 644); the VM's is correct (`-rw-------`,
  600). Tighten the PC perms regardless of this change.
- **Goal:** get the seed **off the dev box** so a compromise of the PC does not hand over the second factor.

## §D2 — what breaks if the VM holds a stale/wrong seed (the failure mode)
`auto_refresh_token.py` at 08:15: `generate_totp(seed)` → POST `/api/twofa`. A wrong seed →
`_AuthError("2FA failed")` after one clock-skew retry → `main` returns **1** →
**Telegram CRITICAL + `cron_heartbeat` FAILED**, and **no token JSON is written** (`save_token` runs only on
success). Net: **it fails LOUDLY (a CRITICAL fires), but the system still boots into a non-trading state** — a
valid broker session never forms. Loud ≠ safe: the alert only helps if someone sees it and fixes it before
09:15. That is the whole reason this is weekend work.

## ⚠️ ROLLBACK — re-enrolment is effectively ONE-WAY. Rama must know before starting.
Re-enrolling TOTP at Zerodha **replaces the account's secret**, so the **old seed is invalidated the moment you
re-enrol — on the PC *and* the VM simultaneously.** There is **no rollback to the old seed.** The instruction's
"verify before removing the PC copy" does not give a fallback, because after re-enrol the PC copy is already
dead too. If the new seed does not work, the only recovery is **re-enrol AGAIN** (you own the Zerodha account)
or **log in manually with a fresh app OTP**. *(Confirm Zerodha's exact re-enrol behaviour before relying on
this — but plan for one-way.)*

## Two paths — pick deliberately (no recommendation)
| Path | What it does | Risk |
|---|---|---|
| **A — VM-only, NO rotation** | just **delete the PC copy** of the current, known-working seed; leave it on the VM | **Zero credential-chain risk** — the seed is unchanged and already proven on the VM. Achieves "not both factors on the dev box." Does *not* rotate a seed that has sat on the dev box. |
| **B — rotate + VM-only** (the instruction's intent) | re-enrol → new seed → VM-only → verify | Rotates a possibly-exposed seed, but re-enrol is **one-way** (above); verification-before-open is the whole game. |

## Path B — exact sequence (Rama's steps vs VS Code's steps)
1. **Rama (phone + authenticator app):** Zerodha console → 2FA/TOTP → re-enrol → scan the new QR → **capture the
   new base32 seed** (the manual-entry key behind the QR). *This is Rama's step alone — VS Code never re-enrols.*
2. **VS Code (VM only):** write the new seed to **`~/systems/trading-system/.env` `ZERODHA_TOTP_LFL836`**,
   perms **600**. **Do NOT touch the PC `.env` yet.**
3. **VERIFY — this is the whole runbook. Prove the new seed authenticates BEFORE market open.** On the VM,
   off-market: run `python scripts/auto_refresh_token.py` (its exit code is the test: `0` = Telegram INFO +
   heartbeat SUCCESS + a token JSON written; `1` = CRITICAL + FAILED). **Confirm exit 0, a `zerodha_token.json`
   dated today, and no CRITICAL.** Do NOT consider the change done until this is green. If red: the seed was
   placed wrong or re-enrol did not take — fix, or re-enrol again. (Do NOT run this during market hours.)
4. **Only after green:** remove the seed from the **PC `.env`** and confirm it is gone
   (`grep '^ZERODHA_TOTP' .env` on the PC returns nothing) + tighten PC `.env` perms.
5. **Next trading morning (08:15):** confirm the *cron* refresh — token JSON dated that day, `cron_heartbeat`
   `auto_refresh_token` = SUCCESS, no CRITICAL, and the boot settles into a trading-ready state (the 6-check
   boot pattern). **If it fails:** re-enrol again immediately (old seed is dead) or manual OTP — do not let the
   market open without a valid session.

## Ordering note
Historically this was to run **after** Q10 Part B so the backfill was not blocked behind a credential change —
but Q10 §A found the backfill is **moot** (regime reads Kite daily bars, not the candle table), so that specific
gate is gone. The general rule still holds: **do not rotate the TOTP seed while any other token-dependent,
time-sensitive work is mid-flight.**

## NOT IN SCOPE / NOT DONE
Nothing here was executed. No seed was re-enrolled, moved, copied, or deleted; no `.env` was touched; the live
token is untouched. **The runbook is the deliverable.**

*Read-only plan. Baseline: 20-Jul, `df560a9`, schema v44.*
