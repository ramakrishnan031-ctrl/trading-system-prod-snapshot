# ☀️ THURSDAY MORNING — 06-Aug-2026. **ONE SCREEN. START HERE.**

> ## ⚡✅ **YOUR PC BEING OFF 07:55–08:25 DOES NOT AFFECT THE BOOT. SHUT IT DOWN WITHOUT WORRYING.**
> **The 08:15 token refresh IS PC-INDEPENDENT** — verified at source, not assumed from the fact that
> it worked yesterday *(evidence below)*. **Everything happens on the VM, which is remote and
> unaffected by your PC.**
> ⭐ **What the power-down costs is WATCHING it live — not the boot itself.** You return ~08:25 and
> read the aftermath from the logs; **the market opens 09:15, so there is a full hour of margin even
> on the worst branch.**
>
> **Why it is PC-independent — (S), with the width stated:**
> · the cron line is entirely VM-resident — VM working dir, VM `.env`, VM venv python, VM script,
>   VM log *(live crontab: `15 8 * * 1-5 … scripts/auto_refresh_token.py`)*;
> · the script is **"fully headless … via TOTP (no browser, no manual OTP)"** — **five outbound
>   HTTPS calls to `kite.zerodha.com` / `api.kite.trade`** and nothing else;
> · a width-stated grep across the script **and both modules it reuses**, for `tailscale` ·
>   private-IP ranges · `/mnt/` · `smb`/`nfs` · `scp`/`rsync` · `listen`/`bind`/`socket` ·
>   `localhost` · Windows paths, returns **only the Zerodha URLs** *(control: the same pattern
>   matches elsewhere in the repo, so it is not a broken search)*;
> · the single `localhost` hit is a `print()` inside `run_login_flow()` — the **interactive** path,
>   and it is **not imported** *(only `exchange_request_token` and `save_token` are)*;
> · **(P)** the VM has **no network mounts at all**, and **(P)** every credential is present in the
>   VM's own `.env` under the account-specific names `config/accounts.csv` declares for the primary
>   account.
>
> ⚠️ **The one honest bound: the mechanism is verified, the OBSERVATION is not.** This will be the
> **first** run with the PC off. ⛔ **That is a reason to check the token file first thing at 08:25 —
> not a reason to stay up.** *(A failed refresh is SILENT: no token ⇒ no watcher start ⇒ no boot,
> with no error and no alert. The token file is the first thing to look at, always.)*

> **Last night ended well.** The service was stopped cleanly at **22:46:36** with the delivery
> position still held. It squared off nothing, cancelled nothing, released no capital, closed no
> position — and the census was recovered (`mismatches=0`). **Nothing is outstanding from Wednesday.**

---

## ▶️ **YOU ARE EXPECTED TO BE IN BRANCH A.**

The service exited cleanly, so the **08:15 boot happens by the ordinary path** — which is exactly
what last night's stop was for. Everything below lives in **`THURSDAY_CONTINGENCY_06-Aug-2026.md`**.
⛔ **Commands are not repeated here — run them from that file, so there is one copy.**

➡️ **Open it, run the ONE COMMAND at the top, then go to `BRANCH A CONTINUED`.**

---

## ⚠️ THE ONE THING TO KNOW BEFORE YOU START

**Steps 1 and 2 will both look fine.** That is the expected outcome, not a lucky one.

🔴🔴 **AND STEP 3 IS THE MEASUREMENT OF THE WEEK, SITTING DIRECTLY BEHIND THEM.**
⛔ **This is the easiest place in the whole sequence to stop reading** — a normal-looking morning
gives you every reason to close the laptop at step 2.

| step | what it is | how it will feel |
|---|---|---|
| **1** | confirm the boot — **the timestamp**, not `is-active` | fine |
| **2** | confirm the kill actually cleared | fine |
| **3** | 🔴 **THE T+1 CARRY** — does CHECK1's delivery skip hold once the position has left `positions()` for `holdings()`? | **this is the one** |
| **4** | did T+1 actually happen? | **tells you whether step 3 measured anything at all** |
| **5** | the boot-seed reading | routine |
| **6** | read Wednesday's census | routine |

---

## 🔴 WHY STEP 3 IS THE POINT

**It is the only question the whole of Wednesday was spent making answerable, and it can only be
asked on a T+1 morning.** Its falsifiable expectation and Wednesday's baseline were both written
**before** the fact, so it is scoreable either way:

> **Expected:** the trade stays `OPEN`, its capital stays reserved, nothing is cancelled.
> **Refuted would look like:** `CLOSED` / `CLOSED_MANUAL`, and a `RELEASE` / `RELEASE_USED` row.

⛔ **Whatever it shows, it is RECORDED, not acted on.** A refutation is a finding. ATULAUTO's
protection is the broker-side GTT and is unaffected either way.

## ⚠️ AND STEP 4 IS NOT OPTIONAL — IT VALIDATES STEP 3

If ATULAUTO is **still in `positions()`**, T+1 has not happened and **step 3 measured nothing.**
⛔ **Say exactly that. Do not record step 3 as a pass** — a skip that was never asked for is not a
skip that worked, and it is not a refutation either.

---

## 📌 IF IT IS *NOT* BRANCH A

The contingency file routes you: **B** = no boot · **C** = HALT, `ExecMainStatus=4` ·
**D** = anything else, **including `ExecMainStatus=3`**, which is now named there rather than left
as "nobody predicted this". ⛔ **Do not improvise, do not run `resume.sh`, do not touch `gtt_state`.**

---

---

## 💰 TWO NUMBERS TO CAPTURE WHILE YOU ARE IN KITE ANYWAY

### **A. THE OPENING BALANCE — ⭐ it settles a `(d)` with ONE number off the Funds page**

**This test exists because of what you established last night:** costs *and* profits both settle on
the **next** trading day. So today's opening balance is the first moment the ledger can be checked
against the broker's own settlement.

> **Thursday's opening ≈ 9,883.70 − 587.40 + whatever P&L actually settles**

| you see | means | reading |
|---|---|---|
| ≈ **9,340.91** | **44.61 settled** | ✅ the ledger was right, and the **₹4.36 was an unrealised mark** on the held share |
| ≈ **9,336.55** | **40.25 settled** | 🔴 **the ledger over-books by 4.36** — points at the **cost model** |
| **neither** | — | ⭐ **that is itself the finding.** Write the number down |

⚠️ **Bound it, or you will over-read it:** ATULAUTO's own purchase costs settle on **its** contract
note too, so **an exact match is not required.** ⭐ **The two candidates are ₹4.36 apart — that gap
is the signal, not the absolute figure.**
⛔ **Record which. Do not adjust anything, either way.**

### **B. THE BOOT SEED** — cross-reference only
➡️ **`ADDENDUM_capital_drift_05-Aug-2026.md` §4b + §4c.** ⛔ **Commands live there, not here.**
Predicted: **seed ratio ≈ 0.940**, a **≈5.94 %** shrinkage, **both buckets identically** (fixed 70/30).

---

## 🔇 A PREDICTION OF **SILENCE** — and silence is interpretable today

> **The capital-drift CRITICAL should NOT fire at all today.**

**Why:** the boot seeds from `broker.net` **after** settlement, so local and broker **start equal** —
the ₹587.40 that was missing from `actual` all yesterday is now missing from **both** sides.

🔴 **IF A DRIFT CRITICAL FIRES TODAY WITHOUT A NEW CNC PURCHASE, THE DELIVERY-ONLY CHARACTERISATION
IS WRONG, AND THAT IS A REAL FINDING.** ⛔ Do not dismiss it as "the usual one".

> ⭐⭐ **A prediction of silence is still a prediction — and silence is only readable here because the
> alarm fired SIX times yesterday.** ⛔ **Do not read a quiet Thursday as "nothing happened": it is a
> scored result.**

---

⛔ **Nothing else is owed this morning.** The eight open delivery items are in the register
(`MASTER_PENDING_01-Aug-2026.md`, A6/A7 companion); **only the T+1 carry has a date, and it is
today.** Everything else can wait.
