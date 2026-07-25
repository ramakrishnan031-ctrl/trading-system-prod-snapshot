# F2 — trigger-surface re-check after C6

**25-Jul-2026. READ-ONLY: no code, no design, nothing built.** Re-verifies
`f2_surface_enumeration_24jul2026.md` against the current tree (`e754c7e`+), because
C6 (`e8ec604`) collapsed the receiver's auth into one call site after that doc was written.

---

## The three headline answers

1. **Does C6 deliver clean auth reuse? PARTLY — and better than expected, for a reason
   that is not the one C6 was argued on.** `_authenticate` is still a **bound method**
   needing `self._secret` + `self._require_hmac`, so nothing "inherits" it for free. But
   it reads Flask's thread-local `request`, and **`:8080` turns out to be a Flask app
   too** — so the decision is *portable* to a control route, which it would not have been
   had `:8080` been a stdlib `http.server`. C6's real delivery is that there is now **one
   named three-way decision with a written contract** to call, instead of two spellings
   to choose between.
2. **What does adding auth to `:8080` cost? Less than the brief assumes — you do NOT have
   to retrofit the three existing routes.** The cost is one injected callable plus a
   decision about `threads=1`. The genuinely expensive part is elsewhere: **the
   healthcheck app has NO reference to the live `KillSwitch` object** — it reads kill
   state from the DB — and **kill state is memory-authoritative**, so a route there cannot
   halt anything until the object (or a bound callable) is injected.
3. **Both forks are still open and both are still Rama's.** Reachability (loopback+SSH vs
   dashboard proxy) is unresolved, and the overnight auto-clear question turns out to
   **collide with an explicit prior decision** — Rama's own 2026-06-20 HEADLESS GUARANTEE
   — not with an oversight. That reframes it from "a gap" to "a deliberate guarantee that
   F2 may need an exception carved into".

---

## B1 — What changed in the tree since the enumeration

| | 24-Jul enumeration | now (`e754c7e`+) |
|---|---|---|
| receiver auth | two spellings, patched separately (G.1 on `/webhook`, the `/health` parity fix later) | **ONE** `_authenticate(hmac_payload) -> (ok, reason)`, `webhook_receiver.py:267` |
| `:8080` routes | 3 read-only GET | unchanged — `/health` `:69`, `/metrics` `:133`, `/metrics/prometheus` `:263` |
| `:8080` bind | loopback | unchanged `127.0.0.1` (`:301`, C-3 moved it off `0.0.0.0`) |
| kill state | memory-authoritative | **re-verified from source**, see B2 |

Nothing else in the enumeration moved.

## B2 — Does a `:8080` control route inherit auth cleanly?

**The contract now exists.** `_authenticate` returns `(True, "")` or `(False, reason)` and
documents the WR8 method order: `X-Webhook-Signature: sha256=…` HMAC over the payload,
else `?token=` **only when `require_hmac=False`** (G.1). That is exactly the "one idiom,
not two" that C6 was required to deliver before F2 — **confirmed delivered.**

**But it is not free to reuse, for three concrete reasons:**

1. **It is a bound method**, depending on `self._secret` and `self._require_hmac`
   (`:299`, `:311`). A separate Flask app has neither.
2. **⭐ It reads Flask's `request` global** (`request.headers`, `request.args`) rather than
   taking them as parameters. I expected this to be fatal — it is not, because
   **`scripts/healthcheck_server.py` is itself Flask** (`from flask import Flask`,
   `app = Flask("healthcheck")`, served by waitress). *Premise checked and corrected: an
   earlier reading of this as "different server type, not reusable" was wrong.*
3. **The GET/POST payload asymmetry is already documented and matters here.** `/health`
   passes `b""`, so a valid `/health` signature is *a constant for a given secret and
   indefinitely replayable*. **A control route MUST NOT copy that** — it needs a
   body-bound (or nonce/timestamp-bound) payload, or it inherits a replayable trigger for
   halting the system. This is stated in `_authenticate`'s own docstring; it would be easy
   to miss by copying the `/health` call site.

**Two reuse shapes, both small:**
- **(a) inject a callable** into `_create_app(...)` alongside the existing
  `metrics_provider` / `tgt_retry_provider` / `daemon_liveness_provider`. This matches the
  established idiom exactly — that factory is already a provider-injection point.
- **(b) extract** `_authenticate`'s body to a module-level pure function taking
  `(secret, require_hmac, headers, args, payload)`, called by both. Better separation,
  larger blast radius (touches the live webhook auth path).
**(a) is the smaller change and does not touch the receiver.**

## B3 — What adding auth to `:8080` actually costs

- ⭐ **You do not have to authenticate the three existing routes.** They are
  unauthenticated *by design*, mitigated by the loopback bind (C-3, which moved them off
  `0.0.0.0` precisely because they expose P&L / capital / kill posture). Adding an
  authenticated POST route alongside them is additive; retrofitting the GETs is a separate
  decision with its own consumers (preflight, monitoring) to check.
- **The real blocker is not auth — it is that the app cannot reach the kill switch.**
  `_create_app` receives `state_store`, `logger` and three read-only providers. It checks
  kill state with `SELECT state, reason FROM kill_switch_state WHERE id = 1` (`:56`).
  **MEASURED FROM SOURCE:** `KillSwitch.is_active()` returns `self._state` (`:461-471`),
  and KS12 states *"is_active() depends ONLY on current state"* — there is no runtime
  re-read of the DB. ⇒ **a route that writes the DB row would change what `/health`
  displays and halt nothing.** F2 on `:8080` therefore requires injecting the live
  `KillSwitch` (or a bound `soft_kill`/`resume` callable) — a new injection, not a query.
- ⚠️ **`threads=1`** (`:320`, waitress). A control route that blocks — and `soft_kill`
  publishes to the event bus and can perform a Telegram send — **blocks `/health` for its
  duration**, which is what external monitoring polls. Whatever F2 does there must be
  either fast or handed off. (M-A2's 8 s bound now caps the Telegram leg; before this
  weekend it was unbounded.)

## B4 — Fork 1: reachability. Both sized; NEITHER chosen.

**(i) Loopback + SSH (the current shape).** Cost to build: ~0 — the listener already
exists, bound correctly, in-process. Cost to *use*: the realistic moment is a phone, under
stress. That means: unlock phone → SSH client → key/passphrase → type a curl with a valid
HMAC signature. **Producing a correct `sha256=` signature by hand on a phone is not
realistic**, so this shape in practice implies either the `?token=` fallback (which
`require_hmac=False` currently permits — see READ-FIRST A: it must stay false for Chartink)
or a small helper script on the VM. Worth stating plainly: *the auth method chosen decides
whether this fork is usable at all.*

**(ii) Ops dashboard proxying to it.** Cost to use: low (it is already a browser page).
Cost to build: **higher than it looks, and it changes what the dashboard IS.**
**MEASURED: the dashboard has NO write channel to the trading process today** — every
`kill_switch` reference in `ops_dashboard/backend/` is a read for display
(`operations.py:63`, `summary_bar.py:44`, `risk_capital.py:54`), and there is no
`requests.post`/HTTP client to `:5000` or `:8080` anywhere in it. It is a pure observer
over the DB. Adding a proxy converts observer → actor, which brings its own auth surface
(the dashboard's own session), its own audit trail, and a new failure mode: *the dashboard
being up while the trading process is down* would make a halt button that silently does
nothing.

**Not chosen. The decision is which cost Rama prefers to pay, and it is genuinely a
judgement, not a technical fact.**

## B5 — Fork 2: the overnight auto-clear. ⚠️ It collides with a prior decision.

**The brief frames this as an open question. It is more than that — it is an explicit,
dated, documented guarantee.** `KillSwitch.clear_stale_state()` (`:266`) says:

> *"HEADLESS GUARANTEE (Rama's 2026-06-20 decision): a new trading day ALWAYS starts with a
> clean slate — EVERY prior-day kill is cleared regardless of type (SOFT_KILL / HARD_KILL,
> scheduled, emergency, loss-limit, System Manager EOD). The system never blocks the
> next-day startup; the safety net shifts from 'block startup' to the EOD report's analysis
> of what was cleared."*

So the behaviour F2 might want to change is not an oversight to fix — **it is the
guarantee, chosen deliberately, with a stated compensating control.** Any F2 exemption is
a *revision* of that decision and should be argued as one.

**There is already a hook, which makes an exemption architecturally natural:** the *other*
auto-clear path, `auto_clear_scheduled_kill()` (`:317`), classifies by reason against
`SCHEDULED_KILL_REASONS = {"circuit_breaker_force_close_15:15", "EOD_SQUAREOFF"}` (`:114`),
with the comment that *"Emergency kills are everything else — they require manual
--resume."* ⇒ the codebase already distinguishes scheduled from emergency; only
`clear_stale_state` is deliberately blind to the distinction.

**The options, stated, not chosen:**

| | behaviour | what it costs |
|---|---|---|
| **A. Leave it** | an operator halt clears at the next 08:15 | the halt silently expires overnight. He halted it *because something was wrong*, and that rarely fixes itself by morning. |
| **B. Exempt an `OPERATOR_HALT` reason from `clear_stale_state`** | it persists until `--resume` | **revises the 2026-06-20 headless guarantee** — reintroduces exactly the "blocks the next-day startup" class it was made to remove. Needs the EOD-report compensating control extended to say *why* the system did not start. |
| **C. Persist but alert loudly at boot instead of blocking** | starts clean, but the morning is unmissable | keeps the guarantee intact; relies on an alert being seen — the same assumption that failed for PB-01 for eleven days. |
| **D. Expire on a timer** (e.g. clears after N hours, not "at the next day") | bounded persistence | new concept, new edge cases around weekends/holidays; most code for least clarity. |

**No recommendation. C is the cheapest that preserves the guarantee; B is the only one
that actually does what an operator halt implies.**

---

## What was NOT done

No build, no design, no config. `f2_surface_enumeration_24jul2026.md` stands with the
corrections above folded in by reference (its `:8080`/loopback/route facts all still hold).
The one factual correction to the *brief*: adding a control route does **not** require
adding auth to the existing three GET routes.
