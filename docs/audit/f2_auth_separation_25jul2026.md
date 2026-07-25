# F2 — separate control auth, and the honest cost read

**25-Jul-2026. READ-ONLY: no design, no build.** Closes §C of the batch and answers §D.
Companions: `f2_surface_recheck_25jul2026.md`, `f2_injection_scoping_25jul2026.md`,
`healthcheck_threads_25jul2026.md`, `kill_persist_busy_timeout_25jul2026.md`.

---

## C3 — `WEBHOOK_SECRET` is untouched. Re-confirmed.

The 25-Jul rotation changed **exactly two keys**: `TELEGRAM_BOT_TOKEN` and
`ZERODHA_TOTP_LFL836` (verified structurally — 2 changed value lines, key order and line
count identical, 68/68). **`WEBHOOK_SECRET` is present and unchanged on the VM.**

⚠️ **Stated so the two are never conflated:** a future control secret would be a **third,
distinct** credential. `WEBHOOK_SECRET` is required in **every** mode (C-2, `main.py:217`,
`:226`) and belongs to the Chartink ingress path. Rotating one must never be assumed to
rotate the other.

## C1 — What does "separate" cost?

**Reusing `_authenticate` looks cheaper than it is, because it drags a posture with it.**

| | reuse `_authenticate` | a separate control secret |
|---|---|---|
| new env var | none | **one** (`CONTROL_SECRET`), + the startup-required-secret check |
| verification code | reuse the existing three-way decision | ~15 lines: HMAC over the body, `compare_digest`, reject on mismatch. **No token-param branch, no `require_hmac` fork** — the two branches that make `_authenticate` three-way both exist for Chartink compatibility and are exactly what the ruling excludes |
| coupling | ⛔ **inherits `require_hmac=False`**, which must stay false for Chartink (READ-FIRST A) ⇒ the control path would accept `?token=` **whether or not F2 wants it** | ✅ independent posture: HMAC-only by construction, no flag to get wrong |
| blast radius of a change | shared — tightening control auth would touch the live signal ingress | isolated |
| rotation | one secret rotates both surfaces | rotate independently |

⭐ **The decisive point is not effort, it is coupling.** `_authenticate`'s `?token=` branch is
live *because* `require_hmac=False`, and that flag cannot be flipped without killing Chartink
ingress (every POST would 401 → zero signals). So reuse does not merely *permit* a URL
token on the halt path — **it makes it unavoidable while Chartink works the way it does.**
A separate secret costs ~15 lines and one env var, and removes that entanglement entirely.
**On this evidence, "separate" is cheaper than "reuse" in every dimension except line count.**

## C2 — ⚠️ The ruling makes reachability HARDER, not easier

**This is the consequence worth being blunt about.** Ruling out the URL token means the
control path needs a **signed body**. And:

> **Producing a correct HMAC-SHA256 over a JSON body, by hand, on a phone, under stress, is
> not a realistic operator interaction.**

So the "loopback + SSH" fork does not survive contact with the actual moment of use — not
because SSH is hard, but because *signing* is. The options that remain:

| option | what the operator actually does | cost / catch |
|---|---|---|
| **A helper script on the VM** (`~/halt.sh`) that signs and POSTs | `ssh trading-vm ./halt.sh "reason"` — one line, no signing by hand | ⭐ the smallest thing that works. ⚠️ **but the script must read the secret**, so anyone with shell has the halt — which is *already true* (they could `systemctl stop`), so it adds no real exposure |
| **The ops dashboard proxies it** | click a button | converts observer → actor. **MEASURED: it has no write channel today**; adds its own auth surface, its own audit trail, and a failure mode where the dashboard is up while the trading process is down (a button that silently does nothing) |
| **A pre-signed emergency token** (long-lived, single-purpose) | paste a fixed string | ⛔ this is the replayable-URL-secret the ruling just excluded, wearing a different hat |
| **Do nothing — keep `systemctl stop`** | `ssh trading-vm sudo systemctl stop trading-system` | already works, already used; see §D |

**Describing, not choosing.** But note the shape: once signing-by-hand is off the table, every
usable option is *"something else holds the secret and signs on your behalf"* — a helper
script or the dashboard. **That is a meaningful narrowing, and it is the direct consequence
of the ruling.**

---

# §D — Has F2 grown past its value? An honest read.

## How the cost moved, in the order it was discovered

| when | what was believed | what was found |
|---|---|---|
| Option 2 chosen | "an authenticated in-process trigger into the existing KillSwitch" — an afternoon | — |
| C6 shipped | auth was the blocker; collapse it and F2 is unblocked | ✅ delivered — one named decision with a contract |
| surface re-check | a route on `:8080` inherits auth | ⚠️ **the app has NO reference to the live `KillSwitch`** — it reads the DB, and kill state is memory-authoritative ⇒ **a DB write halts nothing.** An injection is required. |
| injection scoping | the Telegram leg (8 s) was the latency worry | ⚠️ **worst case ≈ 38 s of `/health` dead** — dominated by a 30 s DB timeout nobody had costed |
| this batch | `threads=1` might force a 202-and-weaker-confirmation design | ✅ **it is an unexamined default; raising it is safe** — one thing got *smaller* |
| this batch | reuse `_authenticate` | ⚠️ **ruled out** — needs a separate secret **and** a signed body ⇒ **needs a helper script or the dashboard to be usable at all** |

**Net: five investigations, one item got smaller, four got bigger.** The build is now:
inject two callables + a new secret + a new env var + a signed-body route with nonce/replay
handling + a helper script (or a dashboard write channel) + a decision about `threads` + an
unresolved decision about overnight persistence.

## What it replaces

**`ssh trading-vm sudo systemctl stop trading-system`** — which exists, works today, and has
been used. Its shortcomings are real but narrow:
- it is a **process stop**, not a SOFT_KILL: it does not leave a recorded, audited kill state
  with a reason, and it stops exits/monitoring too, not just entries;
- restart-based halting loses in-memory state (the whole reason persisted-kill semantics
  matter — a SOFT_KILL + restart currently exits 4);
- it needs SSH — **exactly the same reachability constraint F2 now has**, since the signed
  body forces a helper script anyway.

⭐ **That last line is the crux: F2's headline benefit was "halt without SSH". After the
auth ruling, the realistic F2 interaction is `ssh trading-vm ./halt.sh` — which is the same
gesture as `ssh trading-vm systemctl stop`.** The remaining genuine gains are *semantic*
(a proper audited SOFT_KILL that permits exits) rather than *ergonomic*.

## My recommendation, plainly

**⭐ RECOMMEND: do not build F2 now. Re-scope it as "operator halt semantics", and decide
that question before writing any HTTP route.**

Reasoning:
1. **The ergonomic case has largely evaporated.** If the operator ends up typing an `ssh`
   command either way, the new route buys a better *recorded outcome*, not a better *reach*.
2. **The remaining value is real but narrower than "Option 2" implied** — an audited
   SOFT_KILL that blocks entries while permitting exits is genuinely better than a process
   stop. That is worth having. It is not worth six coupled sub-decisions taken under the
   banner of a route.
3. **Two of the sub-decisions are not F2's to make** — the 30 s persist behaviour affects
   every automated kill path, and the overnight auto-clear is Rama's 2026-06-20 HEADLESS
   GUARANTEE. Building F2 first would quietly settle both by implication.
4. **Nothing is currently failing for want of F2.** No incident in the record was worsened
   by its absence.

**If Rama still wants it — which is entirely reasonable — the cheapest honest path is the
one that skips the web surface**: a small authenticated **CLI on the VM** that calls
`kill_switch.soft_kill()` in-process via the existing signal/IPC route. It delivers the
semantic win (audited SOFT_KILL, exits allowed) with **no new HTTP surface, no second
secret, no replay window, no `/health` interaction, and no `threads` decision.** ⛔ Not
designed here — flagged because the four investigations kept pointing at the route, and the
route is the expensive part, not the halt.

**⛔ THIS IS A REPORT, NOT A DECISION.** He chose Option 2 when it looked like an afternoon;
he is entitled to choose it again knowing it is not. The point is that he should choose it
knowing, rather than discover it mid-build.
