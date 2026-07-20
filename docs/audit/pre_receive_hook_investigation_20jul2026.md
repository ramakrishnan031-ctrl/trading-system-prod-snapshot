# Pre-receive hook investigation + `require_hmac` record correction — 20-Jul-2026

**Read-only investigation. NOTHING armed, NO config changed.** Written Monday 20-Jul (market open, one
open position — `HUHTAMAKI`), so the docs commit for this file is **held** until the book is flat
(post-15:15) or Rama authorises. Deploy state at investigation: **PC `51e6759` == origin == VM bare
`51e67595…`**, tree clean, schema v44.

## TL;DR
1. **§A** — the candidate hook `deploy/hooks/pre-receive` runs **two** guards: a **market-hours deploy
   guard** (works, tested, valuable) and a **cron-integrity guard** (broken — see §B).
2. **§B — the cron-integrity guard WOULD FALSELY REJECT EVERY PUSH TODAY.** Not from real cron drift
   (the content is provably consistent) but from a **trailing-newline bug in the hook's own shell
   comparison**. Arming it in enforce mode = **immediate total deploy lockout**, blocking all three
   approved deploys. This is the "it would reject a legitimate push" finding, verified reproducibly.
3. **§C** — options laid out neutrally; the honest read is that the **market-hours guard is the valuable
   part** and the **cron-integrity guard is belt-on-suspenders AND currently broken**. Any "arm" option
   is gated on a one-line fix first.
4. **§D** — `require_hmac` is **NOT safe to flip**. Chartink cannot sign HMAC; flipping `true` rejects
   every incoming signal. Verified from the receiver's auth code. Record swept and corrected.

---

## §A — What the hook actually does

### A1 — Location & arming state
- **Candidate (in-repo, NOT armed):** `deploy/hooks/pre-receive` (bash). Its own header documents arming:
  `cp deploy/hooks/pre-receive ~/trading-system.git/hooks/pre-receive && chmod +x …`.
- **On the VM bare repo (`~/trading-system.git/hooks/`):** only the stock **`pre-receive.sample`** exists
  (544 B, git default). **No custom pre-receive is installed** — the hook is disarmed today.
- **`post-receive` IS armed** (the live deploy hook: `git checkout -f` + crontab auto-install).
- **Break-glass:** `rm ~/trading-system.git/hooks/pre-receive` (requires SSH to the VM).

### A2 — Every check, in order, with its failure mode
The hook loops over pushed refs and only acts on `refs/heads/main`. Two guards run in sequence:

**Guard 1 — market-hours deploy guard (FIX-065, `deploy/hooks/pre-receive:28-46` → `market_hours_guard.sh`)**
- **Validates:** the IST clock. Rejects a push during **09:15–15:30 IST on a weekday** so code cannot be
  swapped under a live session. Weekend / before-open / after-close → allow. Bypass: **`[force-deploy]`**
  in the HEAD commit message. Holidays deliberately NOT consulted (weekday+clock only; an NSE-holiday
  in-window is a false reject → use `[force-deploy]`).
- **On failure:** **HARD reject** (`exit 1`). **Deliberately does NOT honour `CRON_GUARD_DRYRUN`** — the
  dry-run switch belongs only to guard 2.
- **Failure mode: fail-CLOSED in-window; fail-OPEN on absence** — a missing/unreadable
  `market_hours_guard.sh`, or an unparseable clock, **allows** the push (never wedge deploys on the
  guard's own absence). This guard is **tested** (`tests/unit/test_fix065_market_hours_guard.py` runs the
  real `.sh`).

**Guard 2 — cron-integrity guard (Phase 3, `deploy/hooks/pre-receive:48-77`)**
- `git archive`s the **pushed tree's** `config/cron_registry.yaml`, `deploy/cron/trading-system.cron`,
  `scripts/generate_crontab.py`. Since `cron_registry.yaml` is committed in every tree, **this guard runs
  on effectively every push** (the "push doesn't touch cron → skip" branch at `:51` is almost never taken).
- **Check 2a — generator runs:** `GEN = generate_crontab.py --generate --registry <pushed registry>`. If
  it exits non-zero → **reject** ("generator FAILED"). **Fail-closed.**
- **Check 2b — canonical == generate(registry)** (`:60`): if `GEN != pushed deploy/cron/trading-system.cron`
  → **reject** unless `[cron-canonical-override]` in the HEAD commit msg. **Fail-closed.** ← **this is the
  broken check, see §B.**
- **Check 2c — blast radius** (`:68-75`): if the canonical changed **>40%** (`CRON_BLAST_PCT`, default 40)
  vs the old ref → **reject** unless override. **Fail-closed.** Guards against a generator bug making a mass
  change.
- **Dry-run:** guard 2 honours `CRON_GUARD_DRYRUN=1` — rejects become `WOULD REJECT` log lines and the push
  is **allowed** (`:20-23`).

### A3 — Untested checks
- The **cron-integrity guard's shell path is untested end-to-end.** Tests exist for the market-hours guard
  (`test_fix065`), the generator (`test_generate_crontab`), and the drift checker's Python `content_drift`
  (`test_check_cron_drift_content`) — but **none exercises `deploy/hooks/pre-receive`'s `GEN="$(…)"` +
  `printf '%s'` comparison**, which is where the bug lives (§B). The header itself says to arm in DRY-RUN
  first "once real pushes confirm zero false-rejects" — i.e. it has **never run against a real push**. The
  record's "untested guard that defaults to enforce" is accurate.

### A4 — What a rejected push looks like from the PC
Git relays the remote hook's output with a `remote:` prefix, e.g.:
```
remote: pre-receive: REJECT — deploy/cron/trading-system.cron != generate(config/cron_registry.yaml)
remote:   Fix: python scripts/generate_crontab.py --generate > deploy/cron/trading-system.cron
 ! [remote rejected] main -> main (pre-receive hook declined)
error: failed to push some refs to 'trading-vm:~/trading-system.git'
```
This is **clearly a hook rejection** (`pre-receive hook declined` + the explicit `REJECT —` line naming the
file and the fix), and is distinguishable from a network/auth failure (which produces no `remote:` hook
output). The market-hours reject is similarly explicit (`market_hours_guard: REJECT — cannot deploy during
market hours…`). **So the rejection message is NOT a hazard** — it is legible and actionable.

---

## §B — ⭐ Would the cron-integrity guard pass TODAY? **NO — it would falsely reject.**

### B1 — What it compares (precise)
The committed **`deploy/cron/trading-system.cron`** vs **`generate_crontab.py --generate` of the committed
`config/cron_registry.yaml`** — an **in-tree consistency check** (NOT live `crontab -l`; that is
`check_cron_drift.py` PASS 2, a different comparison). Byte comparison via `diff -q`.

### B2 — Run manually, read-only, against the current tree (`51e6759` on the VM)
Two comparison methods, same inputs:

| Method | Command shape | Result |
|---|---|---|
| **post-receive style** (LIVE hook) | `generate … \| diff -q - canonical` (direct pipe) | **MATCH (rc=0)** — content is consistent |
| **pre-receive style** (candidate hook) | `G="$(generate …)"; printf '%s' "$G" \| diff -q - canonical` | **DIFFERS → WOULD REJECT** |

`od -c` proves the content is **byte-identical**, both the generator's raw stdout and the canonical file
ending `… 2>&1\n`. The only difference the pre-receive path sees is a **trailing newline**:

> **Root cause:** `GEN="$(…)"` (`deploy/hooks/pre-receive:53-55`) — command substitution **strips all
> trailing newlines** — then `printf '%s' "$GEN"` (`:60`) re-emits with **no** trailing newline, and it is
> compared against a canonical file that legitimately **ends in a newline**. `diff` reports a difference on
> the last line every time. The post-receive hook avoids this by piping the generator **directly** into
> `diff` (`deploy/hooks/post-receive:34`), preserving the newline.

**Verdict: the cron-integrity guard would FAIL on essentially every push, deterministically — not from cron
drift (the cron is provably correct) but from a bug in the hook's own shell plumbing.** This is consistent
with the readiness batch's finding that the live crontab is byte-identical to canonical: the **content is
fine**; the **hook is broken**.

**One-line fix direction (NOT applied — out of scope):** at `:60`, either compare like post-receive (pipe
the generator directly) or emit the newline: `printf '%s\n' "$GEN"`. Because `$(…)` collapses the trailing
newline to none and the canonical has exactly one, `printf '%s\n'` restores parity. (The blast-radius check
at `:71` shares the `printf '%s'` idiom but only trips at >40%, so a one-line artifact is immaterial there.)
Any such change is a careful-loop item, tested by actually arming in dry-run and pushing.

### B3 — Would the three queued deploys pass?
The cron content is unchanged by all three, so **on a correct hook they would all pass**; on the hook **as
written they would all be falsely rejected** (off-market, where guard 1 lets them through to the buggy
guard 2):

| Queued deploy | Touches cron? | On a FIXED hook | On the hook AS-IS |
|---|---|---|---|
| **E4/W10** (NET pnl contract) | no | PASS (blast 0) | **REJECT** (newline) |
| **Prune retention #09** (`scripts/eod_cleanup.py:234`) | no (script logic only) | PASS | **REJECT** (newline) |
| **Boot-path pair** (main.py / startup_checks.py) | no | PASS | **REJECT** (newline) |
| a docs-only push | no | PASS | **REJECT** (newline) |

(During market hours all of the above are additionally hard-rejected by **guard 1** unless `[force-deploy]`
— correct behaviour. The approved deploys are off-market per their runbooks, so guard 1 would allow them and
guard 2's bug is what would bite.)

### B4 — False-reject recovery (walked)
- **Break-glass:** `rm ~/trading-system.git/hooks/pre-receive` on the VM — **instant**, next push proceeds.
- **State left behind by a rejected push:** **none.** A pre-receive rejection is atomic — the bare ref is
  **not** updated, post-receive never runs (no checkout, no crontab install), and the hook's `mktemp -d` is
  removed by its `trap`. The bare repo and the deployed tree are untouched.
- **Reassurance on the "requires SSH" caveat:** the git remote and the shell both use the **same**
  `trading-vm` SSH channel/key. **If a push can be attempted at all, SSH is up, so break-glass is
  available.** True lockout needs SSH/network fully down — in which case you cannot push anyway. So the
  residual "locked out of deploys" risk is narrower than it sounds — but it is still an SSH round-trip on a
  possibly-tired evening, per every legitimate push, until the bug is fixed.
- **Alternative escapes (worse):** amend the HEAD commit to add `[cron-canonical-override]` (rewrites the
  commit, defeats the guard's purpose) or re-arm with `CRON_GUARD_DRYRUN=1` (still an SSH edit, and noisy).

---

## §C — Sequencing options (stated neutrally; NO recommendation)

**Precondition applying to every "arm" option:** as-is, guard 2 false-rejects every push. So options 1–3
below all presuppose the **one-line newline fix in `deploy/hooks/pre-receive` + a dry-run soak that confirms
zero false-rejects on a real push.** Without that fix, "arm" means "lock out deploys."

| Option | What it protects against | What it risks |
|---|---|---|
| **1. Arm now (enforce)** | market-hours deploys under a live session; hand-edited/drifted canonical; mass-change generator bugs | **AS-IS: total deploy lockout** (guard-2 newline bug). Even fixed: needs the fix proven, or the 3 approved deploys are at risk. |
| **2. Arm after the queue clears** (E4/W10, prune, boot-pair ship first, then gate up on a quiet tree) | same protections, applied going forward | the 3 deploys go out with **no** automated guard (current discipline only); and arming still requires the bug fixed first, else same lockout later. |
| **3. Arm in DRY-RUN (`CRON_GUARD_DRYRUN=1`)** | guard 2 becomes log-only ("WOULD REJECT", push allowed) — surfaces drift without blocking | **noise:** a **false** `WOULD REJECT` on every push until fixed; **and guard 1 (market-hours) still HARD-rejects** in-window (it ignores dry-run) — so this is not a true "warn-only everything" (that needs a code change to guard 1). |
| **4. Leave disarmed (status quo)** | — | no automated pre-push guard; relies on operator discipline + the two existing backstops (below). |

**C3 — What the hook actually buys (honest):**
- **Guard 1 (market-hours) is the valuable part** — it structurally prevents an S4-class "deploy under a
  live session," which today is prevented **only by discipline** (this very instruction has the operator
  confirm book state before pushing). Arming it would add real, otherwise-absent protection.
- **Guard 2 (cron-integrity) is marginal even when fixed** — the **post-receive hook already refuses to
  install a drifted crontab** (`post-receive:34-38`, fail-safe: keeps the old crontab + WARNs), and
  **`check_cron_drift.py` alerts daily at 18:00** on live-vs-registry drift. Guard 2 only moves that check
  earlier (drift becomes *un-pushable* rather than *pushed-but-not-installed-and-alerted*). Given the
  current discipline (canonical always generated from registry; verified byte-identical), its marginal value
  is small — and as written it is a **liability**, not a protection.

---

## §D — ⭐ Record correction: `require_hmac` is NOT safe to flip

### D1 — Mechanism, verified from the receiver's auth code (current tree, not memory)
- **`config/system_config.yaml:212`** — `require_hmac: false  # Chartink cannot sign payloads; use ?token=
  auth`. The signal source (**Chartink**) **cannot** produce HMAC signatures; production auth is `?token=`
  (`WEBHOOK_SECRET`).
- **`signals/webhook_receiver.py:166-169, 300-305, 483-485`** — when `require_hmac=True` the **`?token=`
  fallback is disabled**; HMAC becomes the **sole** accepted auth (codified by
  `test_token_only_is_refused_when_require_hmac`).
- ⇒ Flipping `require_hmac=true` → **every Chartink POST returns 401 → zero signals → zero trades**, while
  the process boots fine and looks healthy. **Silent, S4-shaped.**
- **Boot gate `webhook_receiver.py:146-151` (BL-18):** `require_hmac=True` + empty `secret_token` ⇒
  **ValueError at construction ⇒ the app fails to boot.** So depending on whether `WEBHOOK_SECRET` is set
  on the VM, the flip either **bricks the next boot** or **401s every POST** — both outages.

### D2 — Record swept
| Location | Status | Action |
|---|---|---|
| `docs/decisions/ACTIONS_not_decisions.md:17` ("a one-line config flip, not an evidence-weighted decision") | **misleading** — reads as trivial/safe | **CORRECTED** here with a dated note (superseded claim left legible) |
| `MEMORY.md` RAMA-ACTIONS line | already said "DE-RISKED" (misreadable) | **CORRECTED 20-Jul** → "KEEP FALSE (Chartink can't sign…)" |
| memory `p1-health-require-hmac-17jul` ("that flip is now safe on both routes") | accurate re the /health oracle, misreadable in isolation | **ANNOTATED 20-Jul** with the Chartink caveat |
| **Master register** (`MASTER_PENDING_REGISTER_*.txt`, "now SAFE (P1 pre-armed it)") | **wrong** | **FLAGGED for Rama** — external / git-excluded, not editable here |
| `PATHS.md:52,54` · `docs/SYSTEM_MAP.md` changelog · `p1_health_require_hmac_done_17jul2026.md:149` · `fixture_blindness_investigation_17jul2026.md` | **accurate** ("INERT until flip" / "arms a bug on flip") but lack the Chartink caveat | left as historical record; this report is the cross-reference |
| `docs/architectural_audit_2026-06-01.md:485` · `audit_b_phase9…16jul:184` · `system_security_audit_02jul:89` · `audit_remediation_status_02jul:17` | **already correct** ("Chartink doesn't support HMAC signing"; Rama-owned accepted risk) | none |

### D3 — What P1 actually achieved (restated)
P1 (17-Jul) made the **receiver** side of `/health` honour `require_hmac`, so a *future* flip would not leave
a `/health` `?token=` recon oracle open. It **pre-armed the receiver's verification**, closing one secondary
bug on the /health route. It **did not, and could not, give Chartink the ability to sign.** The record
conflated "the receiver can verify a signature" with "the sender can produce one" — different things on
opposite sides of the integration boundary.

### D4 — Standing position going forward
**`require_hmac` stays `false` unless the webhook architecture changes** — i.e. a different signer, or a
**signing proxy in front of Chartink**. "HMAC webhook architecture" is a possible later **design item**
(reverse-proxy + HMAC or IP-allowlist Chartink's egress, per `audit_b_phase9…16jul:192`), **not a config
flip.** The `0.0.0.0` bind's exposure is already mitigated without HMAC: `?token=`/`WEBHOOK_SECRET` +
firewall/security-group + per-IP rate limit (C-2).

### D5 — Class note (5th instance)
This is the **fifth** register/record claim wrong in a way that would have **caused action**. Its shape
differs from the attribution-gloss class (a summary sharper than its source): here it is **a capability
assumed on the far side of an integration boundary** — the record credited Chartink with an ability
(signing) it does not have. Worth watching as its own failure mode: claims that silently assume the
*counterparty* of an integration can do something.

---

## Incidental findings (not in scope; flagged)
- **`kill_switch_state.is_active` column does not exist** — a read-only probe errored with
  `no such column: is_active`. **Tonight's `MONDAY_POST_SESSION_CHECKLIST.md` A4/A5 queries reference it and
  would error.** Worth checking the real column name before 18:15 (candidate for a checklist fix — separate
  careful item, not touched here).
- **`PATHS.md:381`** cites `system_config.yaml:179,187` for `bind_host`/`require_hmac`; the actual lines are
  **204/212** (stale by drift). Cosmetic; noted.

## What was NOT done
- The hook was **NOT armed** (no cp, no chmod, no install). No `require_hmac` change. No code/config/cron/
  systemd change. The **newline fix was NOT applied** (design-only recommendation).
- **No commit/push** — an open position (`HUHTAMAKI`) and market hours. This report + the `ACTIONS_not_decisions.md`
  correction sit uncommitted in the working tree pending a flat book / Rama's go.

*Read-only investigation. Baseline: 20-Jul, `51e6759`, schema v44.*
