# S4 — an authenticated `/health` halted the system at boot (17-Jul-2026)

**A live, self-inflicted outage: the system took 0 trades on a trading day.**
Found 17-Jul while verifying the VM before an unrelated deploy (P3-s14). Fixed the same
day, `e393b3f`, branch `s4-health-boot-fix` → `main`.

**It was found by verifying a premise, not by an alarm.** The P3-s14 instruction stated
*"System DOWN today"* as a given. That was true — but for the wrong reason. The author
believed it was still the planned pause; memory recorded the opposite expectation (*"the
system RESUMES TRADING 17-Jul with the swept code unless re-paused"*). Checking the VM
instead of accepting the premise is what surfaced it.

---

## 1. What happened

| Time (IST) | Event |
|---|---|
| 17-Jul ~02:00 | **S4** (`84cee3e`) deployed in the sweep — `/health` put behind the webhook secret (AB-910 §1.7) |
| 08:15:51 | first boot after S4. Service starts |
| 08:15:52 | prior-day SOFT_KILL auto-cleared — correct, as designed |
| **08:16:04** | `WARNING check_webhook: endpoint http://127.0.0.1:5000/health returned status 401`<br>`CRITICAL main — Webhook endpoint not reachable at http://127.0.0.1:5000/health` |
| **08:16:09** | `Deactivated successfully` — **the system shut itself down** (exit 0) |
| → 15:30 | market open and closed with the system down. **0 trades** |

The exit is **clean (status 0)**, which is why it reads as a normal stop rather than a
crash — nothing restarts it, and `systemctl is-active` simply says `inactive`.

## 2. The chain (verified on the VM, not inferred)

1. **`webhook_receiver.py:290-304`** (added by S4) — if a secret is configured, an
   unauthenticated `GET /health` returns **401**. *Correct and intended.*
2. **`main.py:3238`** — the post-start self-check GETs `http://127.0.0.1:5000/health`
   with **no token and no signature**. Unchanged since the initial v2 foundation
   (`bcf03b5`).
3. **`utils/startup_checks.py:797`** — `reachable = status_code is not None and 200 <=
   status_code < 300` → **401 ⇒ not reachable**.
4. **`main.py:3240-3242`** — `_log.critical("Webhook endpoint not reachable")` +
   **`_shutdown_event.set()`** → the whole system shuts down.

**Evidence:**

| Check | Result |
|---|---|
| trades this week | 13-Jul **9** · 14-Jul **9** · 15-Jul **7** · 16-Jul **3** · **17-Jul 0** |
| 17-Jul a holiday? | **No** — a real lost session |
| secret configured on VM? | **Yes** (`.env`) → the auth branch is live |
| S4's diff | **only** `webhook_receiver.py` + `tests/unit/test_fix134_backpressure.py` |
| boot self-check ever authenticated? | **Never** — unchanged since `bcf03b5` |
| kill switch | INACTIVE (auto-cleared 08:15:52) — **not** the cause |
| book | flat, 0 OPEN |

## 3. Blast radius — narrower than it first looked

There are **two** different `/health` endpoints, and S4 only touched one:

| Endpoint | Auth after S4? | Consumers | Affected? |
|---|---|---|---|
| **:5000** `webhook_receiver` `/health` | **yes** | `main.py:3238` boot self-check — **the only in-repo consumer** | **YES — fatal** |
| **:8080** `scripts/healthcheck_server.py` `/health` | no | ops dashboard (`metrics_client.py`), preflight (`checks/engine.py`) | **no** |

So **monitoring was never blinded** — the dashboard and preflight read the :8080 server,
which S4 did not touch. The sole casualty is the boot self-check, which happens to be
fatal.

## 4. The fix

`utils/startup_checks.py:797` — **a 401 proves the server answered**, which is the only
thing this check exists to prove. Its own docstring: *"Called by main.py AFTER starting
the Flask webhook receiver, **to confirm Flask is listening**."*

```python
reachable = status_code is not None and (200 <= status_code < 300 or status_code == 401)
```

**The check is corrected, not weakened**: `5xx`, `404`, `429` and no-answer all still fail
the boot, and a test pins that. S4's security property is **fully preserved** — `/health`
still denies anonymous callers (asserted in the wired test).

**Rejected alternatives:**

| Option | Why not |
|---|---|
| boot check sends `?token=<secret>` | `main.py:3241` **logs the URL** on failure → re-introduces the exact token-at-rest leak the 17-Jul sweep just closed (19 files shredded). |
| exempt loopback from `/health` auth | behind the Tailscale proxy **every** caller looks like `127.0.0.1` (the S3 lesson) → it would silently disable the auth, partly undoing S4. |
| revert S4 | re-opens AB-910 §1.7 — anonymous internet callers get `kill_switch_active` + queue depth. |

## 5. ⭐ Why no test caught it — the important part

**Both sides were tested. Both sides were correct. Both sides were green.**

- S4's own tests (`test_fix134_backpressure.py`): *"unauthenticated `/health` → 401"* —
  **correct**, and passing.
- the boot check's tests (`test_startup_checks.py`): *"2xx → reachable"* — **correct**,
  and passing.

The defect lived in the **seam neither test owned**: nobody connected *"/health now 401s
unauthenticated callers"* to *"main.py GETs /health unauthenticated at boot and treats
non-2xx as fatal"*. A unit test on each side of a seam can both pass while the seam is
broken.

**And it was structurally invisible:** every wired/integration fixture builds the receiver
with **`secret_token=None`** (`conftest.py:332`, `test_hardening_scenarios.py:242`). With
no secret, S4's `if receiver._secret:` branch **never executes in the entire suite** — so
the 401 that took production down *cannot occur in any test*. The suite was green because
it was configured unlike production, not because the system worked.

**This is the same defect class as P3-s14 itself** — *the path nobody enumerated* — which
is why the two shipped together.

## 6. Tests added

| Test | Type | RED-on-old? |
|---|---|---|
| `test_startup_checks.py::test_webhook_401_is_reachable` | unit | **yes** — `assert False is True` (reachable=False on 401) |
| `test_hardening_scenarios.py::TestS4AuthenticatedHealthStillBootsWired` | **wired** | **yes** — the REAL receiver's REAL 401 into the REAL boot check |
| `test_startup_checks.py::test_webhook_still_unreachable_on_server_error` | unit **guard** | **no, by design** (see below) |

The wired test is the one that matters — it is the only place in the suite that builds the
receiver **the way prod is configured** (with a secret), and it reproduces the outage
exactly:

```
AssertionError: main.py:3240-3242 fires _shutdown_event when this is False
  -- the system halts at boot and trades nothing
assert False is True
 +  where False = WebhookEndpointResult(reachable=False, status_code=401, ...).reachable
```

**Honest note on the third test:** it passes on old code too, so it is *not* evidence of
the fix — it is a **guard** that my fix did not weaken the check. It would have failed had
I written the looser `reachable = status_code is not None`. Recorded explicitly rather
than counted as a RED-on-old proof ([[feedback-verify-rc-not-output]]: a green check is
evidence only if it could have been red).

**RED-on-old method:** committed first, then `git checkout 8eb155c -- utils/startup_checks.py`
reverted **only** the production file; grep-confirmed absent (`status_code == 401` → 0 hits,
rc=1); both rc values checked. Never `git stash`.

## 7. Follow-ups (recorded, NOT done here)

1. **⭐ The `secret_token=None` fixture blindness is systemic, not local.** Every wired
   test configures the receiver unlike prod, so *any* future defect in an auth branch is
   invisible to the suite. Only the one new test opts out. Worth a deliberate decision:
   should the wired fixture default to a secret (as prod does)? — **loop item.**
2. **`_shutdown_event.set()` on a failed self-check is a very sharp edge**: one
   non-2xx from one local endpoint silently halts a whole trading day, with a clean exit 0
   that looks like a normal stop. Worth asking whether this check should be *blocking* at
   all, or should fail loud-but-degraded. — **decision item, Rama.**
3. **⭐ Nothing alarmed — and the canary actually reported HEALTHY.** This is the most
   actionable follow-up, and it now has evidence rather than a suspicion:
   - `data_store/canary_service_state.json` @ **08:20:05** (4 minutes after the system
     died) reads **`{"nrestarts": 0, "iso": "2026-07-17T08:20:05..."}`**. The canary
     watches for a **restart loop**, not for **liveness** — so a cleanly-dead service
     scores `nrestarts: 0`, which is the *good* value. **A clean exit 0 with zero restarts
     is indistinguishable from a healthy system.**
   - 5 CRITICAL alerts were delivered today and **none** of them mentions the boot or the
     webhook (`grep "not reachable" data_store/critical_alert_20260717*` → no match). The
     boot CRITICAL went to the log; nothing escalated it.
   - Net: the system announced its own death in the journal at 08:16:04 and **every
     monitor read green for the rest of the day.**
   ⇒ Worth a dedicated "service should be up during the service window but is not" check —
   the one signal that would have caught this in minutes instead of a session. **Loop
   item / Rama.**

## 8. Parity

`utils/startup_checks.py` has no paper/live branch — the boot self-check runs identically
in both modes. Parity is free.

Related: [[feedback-verify-the-finding-premise]] [[feedback-verify-rc-not-output]]
[[sweep-done-17jul]] [[ab910-ops-security-audit-16jul]] [[p3s14-done-17jul]]
</content>
