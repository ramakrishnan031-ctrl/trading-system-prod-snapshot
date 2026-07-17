# Fixture-Blindness Investigation — auth-branch test coverage

**Date (IST):** 17-Jul-2026 · **Mode:** READ-ONLY (nothing changed, nothing pushed)
**Base:** `5e74b9d` (clean tree) · **Trigger:** the S4 boot outage — scope the test blindness
that let an auth change halt a trading day.

---

## ONE-LINE STEER

**NEITHER "small targeted fix" NOR "wider sweep" — the investigation's premise is STALE:
the webhook auth branch and the boot seam are ALREADY tested (the S4 fix closed its own
seam); of 11 security-gated surfaces, 10 have their secured branch tested, and the ONE
empty cell — `require_hmac=True` × `GET /health` — is holding a RUNTIME-PROVEN latent
production bug that arms itself the moment Rama flips `require_hmac`.**

So the build is **smaller than "small"**: one 3-line production fix + two tests + one
fixture variant. Not a sweep. There are no auth-blind components to sweep.

---

## 0. THREE PREMISES IN THE INSTRUCTION ARE WRONG — corrected with evidence

Per [[feedback-verify-the-finding-premise]], an audit finding is a hypothesis. Three of
this instruction's load-bearing premises do not survive contact with the code:

| # | Instruction's premise | Verdict | Evidence |
|---|---|---|---|
| 1 | "*every wired fixture builds the webhook receiver with `secret_token=None`, so S4's auth branch NEVER executes in the suite*" | **FALSE as stated** | `tests/unit/test_fix134_backpressure.py:151` `_make_receiver_with_secret()` builds a receiver **with** a secret and drives `/health` — 6 tests at `:170-214`. `tests/unit/test_webhook_receiver.py:290,302,322` build with `secret="mysecret"`. The auth branch executes routinely. |
| 2 | "*the 401 that killed production cannot occur in any test*" | **FALSE (stale)** | `tests/integration/test_hardening_scenarios.py:298-331` — a receiver built with `secret_token="a-prod-like-secret"`, a real `GET /health` asserting **401**, fed to the real `check_webhook_endpoint`. Added **by the S4 fix itself**. |
| 3 | Q2's "*There was not [an e2e test], on old code — confirm the current state*" | **Correctly answered: the seam IS now covered** | unit `tests/unit/test_startup_checks.py:768` (401→reachable) + `:789` (5xx/404/429 still unreachable — the anti-over-widening guard); wired `test_hardening_scenarios.py:300`. |

**Where the over-generalisation came from.** The original S4 report and
`test_hardening_scenarios.py:26-27` say the claim **correctly scoped**: *"Every other wired
case **here** passes `secret_token=None` … the 401 … cannot occur anywhere else **in this
suite**"* — i.e. the *integration/wired* suite. That is true and remains true. The memory
entry compressed it to "EVERY wired fixture … the 401 CANNOT occur in the suite
(SYSTEMIC)", and this instruction expanded that compression into "any test", which the unit
suite refutes. **The blindness was never "auth never executes"; it was "auth-ON never met
the boot self-check" — a seam, exactly as the S4 report said.**

---

## Q1 — The webhook receiver: every construction site, and what auth is tested

### Construction sites (all of them)

| Site | `secret_token` | Auth state |
|---|---|---|
| `main.py:2692` | `os.environ.get("WEBHOOK_SECRET")` | **PROD — auth ON** |
| `tests/unit/test_webhook_receiver.py:105` (`_make_receiver`, factory) | `secret=None` **default, overridable** | off by default, **overridden** by 5 tests |
| `tests/unit/test_fix134_backpressure.py:56` (`_make_receiver`) | omitted → `None` | off |
| `tests/unit/test_fix134_backpressure.py:158` (`_make_receiver_with_secret`) | `_FAKE_TOKEN` | **ON** ← dedicated auth-ON variant |
| `tests/unit/test_webhook_receiver.py:1128` (`_bl18_build`) | param | **ON** (BL-18 + G.1) |
| `tests/integration/conftest.py:332` (`wired_system`) | `None` | **off — the shared wired fixture** |
| `tests/integration/test_hardening_scenarios.py:250` (M-S2) | `None` | off |
| `tests/integration/test_hardening_scenarios.py:311` (S4) | `"a-prod-like-secret"` | **ON** ← hand-built, not from the fixture |
| `tests/unit/test_e4_event_loop_safety.py:314` | `None` | off |
| `tests/unit/test_v3_eod_route.py:55` | `None` | off |

### Auth-branch behaviour that IS tested

| Branch | Test | Line |
|---|---|---|
| `POST /webhook` missing HMAC when secret set → **401** | `test_hmac_absent_when_required_returns_401` | `test_webhook_receiver.py:288` |
| `POST /webhook` wrong HMAC → **401** | `test_hmac_mismatch_returns_401` | `:300` |
| `POST /webhook` correct HMAC → **200** | `test_hmac_valid_returns_200` | `:319` |
| `POST /webhook` no secret → open, **200** | `test_hmac_none_mode_no_header_check` | `:341` |
| `require_hmac=True` + `?token=` → **401** (G.1) | `test_g1_require_hmac_true_rejects_token_only_request` | `:1201` |
| `require_hmac=True` + valid HMAC → **200** | `test_g1_require_hmac_true_accepts_valid_hmac` | `:1221` |
| `require_hmac=False` → token fallback preserved | `test_g1_require_hmac_false_keeps_legacy_token_path` | `:1243` |
| `require_hmac=True` + bad HMAC + valid token → **401**, no fall-through | `test_g1_..._does_not_fall_through` | `:1262` |
| BL-18 ctor: `require_hmac=True` + no/empty secret → `ValueError` | `test_bl18_*` (5) | `:1137-1180` |
| **`GET /health`** anonymous + secret set → **401** | `test_unauthenticated_health_is_denied_when_a_secret_is_configured` | `test_fix134_backpressure.py:170` |
| `GET /health` 401 body leaks no state | `test_unauthenticated_health_leaks_no_system_state` | `:176` |
| `GET /health` correct `?token=` → **200** + detail | `test_health_with_correct_token_returns_full_detail` | `:184` |
| `GET /health` wrong token → **401** | `test_health_with_wrong_token_is_denied` | `:192` |
| `GET /health` no secret → stays open **200** | `test_health_without_a_secret_configured_stays_open` | `:198` |
| `GET /health` rate-limited → **429** | `test_health_is_now_behind_the_per_ip_rate_limiter` | `:206` |

### Auth-branch behaviour that is NOT tested — the one real gap

**`require_hmac=True` × `GET /health` is an empty cell across the whole suite.** The two
auth-ON fixture families are **disjoint**, and neither covers it:

- `_make_receiver_with_secret()` (`test_fix134_backpressure.py:151`) drives `/health` — but
  its `_make_config()` (`:35-48`) has **no `webhook` attribute at all**, so
  `webhook_receiver.py:145`'s `getattr(_webhook_cfg, "require_hmac", False)` resolves
  **False**. Every `/health` auth test runs `require_hmac=False`.
- `_bl18_build()` + `_make_config_with_require_hmac(True)` (`test_webhook_receiver.py:1110`)
  sets `require_hmac=True` — but all four G.1 tests POST to `/webhook/gap_go_long`
  (`:1209`). **None GET `/health`.**

---

## 🔴 THE FINDING: `/health` does not honour `require_hmac` (CONFIRMED, latent)

The empty cell is empty because **the branch does not exist in production code.**

`signals/webhook_receiver.py:290-304` — `GET /health`:
```python
if receiver._secret:
    sig_header = request.headers.get("X-Webhook-Signature", "")
    token_param = request.args.get("token", "")
    ok = False
    if sig_header.startswith("sha256="):
        expected_hex = _hmac.new(receiver._secret.encode(), b"", hashlib.sha256).hexdigest()
        ok = _hmac.compare_digest(sig_header[7:], expected_hex)
    elif token_param:                                   # ← accepted unconditionally
        ok = _hmac.compare_digest(token_param, receiver._secret)
    if not ok:
        return jsonify({"error": "authentication required"}), 401
```

`signals/webhook_receiver.py:459-479` — `POST /webhook`, for contrast:
```python
if self._secret:
    ...
    if sig_header.startswith("sha256="):
        ...
    elif self._require_hmac:                            # ← the branch /health lacks
        return jsonify({"error": "HMAC signature required (require_hmac=True); "
                                 "token param is not accepted"}), 401
    elif token_param:
        ...
```

**`self._require_hmac` is read at exactly one request-time site — `:469`, on `/webhook`.**
`/health` never consults it.

**Consequence.** `webhook_receiver.py:165-168` states the contract: *"When True the
token-param fallback is disabled — HMAC is the sole accepted auth surface (token in URL is
logged by nginx and weaker than HMAC over the body)."* Under `require_hmac=True` that
promise holds for `/webhook` and **silently breaks for `/health`** — the endpoint that is
deliberately exposed on `0.0.0.0:5000`, and the one AB-910 §1.7 flagged as a
kill-switch/queue-depth reconnaissance oracle. The URL-token surface the flag exists to
eliminate survives on the more exposed of the two routes.

### PROVEN, not inferred — runtime probe (read-only, fresh temp DB)

The finding was not left as a reading of the code. A probe built the receiver exactly as the
BL-18/G.1 tests do (`require_hmac=True` + a secret, fresh `StateStore` in a temp dir — never
the live DB) and drove both routes with a token-only request. **`/webhook` is the control**:
if it had returned 200 the probe would have been void, and if `/health` had returned 401 the
finding would have been retracted. Neither happened:

```
receiver._require_hmac = True   (probe valid)

  CONTROL  POST /webhook?token=<secret>  -> 401  HMAC signature required (require_hmac=True);
                                                 token param is not accepted
  SUBJECT  GET  /health?token=<secret>   -> 200  {"kill_switch_active": false,
                                                  "queue_capacity": 20, "queue_depth": "0/20",
                                                  "queue_size": 0, "status": "ok"}
```

The 200 body is the finding's whole point: under `require_hmac=True` a URL-borne token still
returns **`kill_switch_active` and `queue_depth`** — *precisely* the "is the trading system
halted right now, and how loaded is it" oracle that AB-910 §1.7 and the comment at
`webhook_receiver.py:273-281` exist to close. Probe script:
`scratchpad/probe_health_require_hmac.py` (session-local, not committed).

**Severity: LATENT, not live** — `require_hmac: false` today, so `/webhook` and `/health`
currently agree. It arms the moment `require_hmac` flips true. **That flip is an OPEN
RAMA-ACTION** ("`require_hmac` decision" on the owed list), so this is a bug scheduled to
be created by a change already on the board. Same character as AB-910's "fail-open 2FA is
LATENT NOT live".

**Same shape as S4** — two green, correct test suites (G.1 owns `require_hmac` on
`/webhook`; TestHealthAuth owns auth on `/health`), and the bug lives in the cell neither
owns. This is the S4 lesson recurring inside the very component S4 touched.

**Secondary observation (design, not a defect).** `/health`'s HMAC is computed over `b""`
(`:296`), so for a given secret the expected signature is a **constant** — a bearer token in
a header, not a body-bound signature. Its cryptographic strength over `?token=` is therefore
only "not in the URL". That is precisely the leak `require_hmac` targets (cf. AB-910 §1.1,
where the Telegram credential leaked via URL paths into logs), so the fix is still worth
making — but the honest framing is *"stop putting the secret in URLs"*, not *"/health is
cryptographically weak"*.

---

## Q2 — The boot → /health seam: COVERED (post-S4-fix)

| Layer | Test | Line | What it pins |
|---|---|---|---|
| Unit | `test_webhook_401_is_reachable` | `test_startup_checks.py:768` | 401 → `reachable=True` |
| Unit | `test_webhook_still_unreachable_on_server_error` | `:789` | 500/502/404/429 → **still unreachable** (the allowance did not over-widen) |
| **Wired** | `TestS4AuthenticatedHealthStillBootsWired` | `test_hardening_scenarios.py:298-331` | real receiver **with a real secret** → real `GET /health` → real **401** → real `check_webhook_endpoint` → `reachable=True` |

**Answer to Q2: yes on both counts.** `startup_checks`' "401 ⇒ reachable" is under test, and
the seam — the self-check meeting an auth-ON `/health` — is owned by the wired test.

**Caveat, stated honestly.** The wired test is a *stitched* seam, not a live socket: it
takes the real 401 from the real receiver, then hands `check_webhook_endpoint` a lambda
returning that canned `(status, body)` (`:324`) rather than letting it make a real HTTP
call. Both halves are real and the join is real; what is **not** proven is the actual
`urllib`/socket round-trip against a bound Flask server. That residual is the same one
`main.py:3238` has in prod and is acceptable — but it means "e2e" is an overstatement.
Flagged, not widened.

---

## Q3 — How wide is the pattern? The deliverable table

**11 security-gated surfaces. 10 have their secured branch tested. 1 cell is empty.**

| # | Component | Has an auth/security branch? | Secured branch tested? | Evidence |
|---|---|---|---|---|
| 1 | `POST /webhook/<scanner>` — HMAC gate | YES | ✅ **YES** | `test_webhook_receiver.py:288,300,319,341` |
| 2 | `POST /webhook` — `require_hmac` token refusal (G.1) | YES (`webhook_receiver.py:469`) | ✅ **YES** | `test_webhook_receiver.py:1201,1221,1243,1262` |
| 3 | `WebhookReceiver.__init__` — BL-18 ctor guard | YES (`:146`) | ✅ **YES** | `test_webhook_receiver.py:1137,1146,1155,1163,1171` |
| 4 | `GET /health` — secret gate (AB-910 §1.7 / S4) | YES (`:290`) | ✅ **YES** | `test_fix134_backpressure.py:170,176,184,192,198` |
| 5 | `GET /health` — per-IP limiter before auth | YES (`:285`) | ✅ **YES** | `test_fix134_backpressure.py:206` (429) |
| 6 | **`GET /health` — `require_hmac` token refusal** | ❌ **BRANCH ABSENT** (`:290-304`; cf. `:469`) | ❌ **NO — empty cell** | no fixture pairs `require_hmac=True` with a `/health` GET |
| 7 | Boot self-check ← authenticated `/health` (**the S4 seam**) | YES | ✅ **YES** | `test_startup_checks.py:768,789`; wired `test_hardening_scenarios.py:298` |
| 8 | ops_dashboard login — PBKDF2 + TOTP + throttle | YES (`auth.py:208`) | ✅ **YES** | `test_auth.py:25` (TOTP empty → **fail-closed**), `:36` (flag is the only escape), `:46`, `:58` (throttle ≠ lockout), `:96`, `:113` |
| 9 | ops_dashboard route guard — `before_request` default-deny | YES (`app.py:169-177`) | ✅ **YES** | `test_api_contract.py:90` (8 APIs → **401**), `:97` (7 pages → **302** `/login`), `:106` (positive), `:113` (exempt); + `test_g5c_analytics.py:139`, `test_g5d_operations.py:99`, `test_g2b2_screens.py:281` |
| 10 | ops_dashboard session key persistence | YES (`app.py:53`) | ✅ **YES** | `test_gui_secret_key.py:37,45,58,69,77,90` (6) |
| 11 | `scripts/healthcheck_server.py` `/health` + `/metrics` | **No auth by design** — control is loopback bind + payload scrub | ✅ **YES** (control tested) | bind `test_healthcheck_server.py:156`; scrub `:163,172,179` |

*(Adjacent, non-auth: config-snapshot secret redaction, `core/config_snapshotter.py:49-82`
— tested by `tests/unit/test_config_snapshotter.py`.)*

**The dashboard deserves explicit credit — it is the OPPOSITE of blind.** `app.py:169`
enforces login in a single `before_request` with a 2-endpoint exempt set
(`_LOGIN_EXEMPT = {"auth.login_get", "auth.login_post"}`, `:43`) covering **all** routes
including `/static/*`. A forgotten `@login_required` decorator is **structurally
impossible**, and the default-deny is proven by anonymous clients. This is the design the
webhook receiver's two divergent per-route auth blocks do not have — and #6 is exactly the
bug that divergence produces.

---

## Q4 — The fixture root: SHARED, not scattered (high-leverage)

Auth-off defaults live in **one place per domain**, and an auth-ON variant **already exists**
in two of the three:

| Domain | The auth-off default | Auth-ON variant? |
|---|---|---|
| Unit — webhook | `test_webhook_receiver.py:85` `_make_receiver(secret=None)` — a **parameter default**, not a hardcode | ✅ overridden by 5 tests |
| Unit — /health | `test_fix134_backpressure.py:51` `_make_receiver()` | ✅ **`_make_receiver_with_secret()` at `:151`** — the dedicated auth-ON variant this investigation was scoped to *propose building* **already exists** |
| **Integration — wired** | **`tests/integration/conftest.py:332` `secret_token=None` — ONE line, the shared `wired_system` fixture** | ❌ **none** — the S4 test hand-builds its own receiver (`test_hardening_scenarios.py:308-312`) instead of using the fixture. **That hand-build is the tell.** |
| Dashboard | `ops_dashboard/tests/conftest.py:617-618` `client` pre-seeds `sess["user"]="tester"`; `:600-603` auth block has **empty** `password_hash`/`totp_secret` | ✅ `anon = app.test_client()` in 4 test files; `test_auth.py` builds its own `auth_cfg` |

**Answer: SHARED — one line each.** The only domain missing an auth-ON variant is the wired
fixture (`conftest.py:332`). That is a small, high-leverage addition.

---

## Q5 — False-confidence inventory

Tests that pass today **even if the corresponding auth branch were completely broken**:

### Genuine false confidence (a build should supplement these)

1. **BL-18, 5 tests — `test_webhook_receiver.py:1137-1180`.** They look like `require_hmac`
   coverage; they assert only the **constructor** raises. Delete request-time
   `require_hmac` enforcement entirely (`:469`) and all 5 still pass.
   *Mitigation: G.1's 4 tests DO cover request-time — on `/webhook` only. Net effect:
   `/health`'s missing branch is invisible to both sets.*

2. **`TestHealthAuth`, 6 tests — `test_fix134_backpressure.py:166-214`.** They look like
   complete `/health` auth coverage. Every one runs `require_hmac=False` (because
   `_make_config()` at `:35-48` omits `webhook` entirely). **They cannot see finding #6, and
   they will still be green after `require_hmac` flips true and `/health` keeps accepting
   `?token=`.** This is the single most misleading set in the suite — it is *the* S4-shaped
   test: correct, green, and blind to the adjacent cell.

### Auth-off shared fixtures — real, but compensated (note, don't convert)

3. **`tests/integration/conftest.py:332` `wired_system`.** Every wired test using it runs
   auth-OFF; none proves anything about auth. *Compensated:* the S4 test hand-builds an
   auth-ON receiver. *Residual:* the next wired auth question has no fixture to reach for.

4. **`ops_dashboard/tests/conftest.py:617-618` `client`.** Pre-seeds an authenticated
   session; every API-contract test using it would pass if the guard were deleted.
   *Compensated — properly:* `test_api_contract.py:90-103` uses a separate `anon` client and
   proves default-deny on 8 APIs + 7 pages. *Residual:* the anon list is **manually
   enumerated**, so a NEW blueprint is not automatically covered. `before_request` still
   *protects* it; nothing *proves* it. (G5c/G5d authors did add their own anon checks —
   `test_g5c_analytics.py:139`, `test_g5d_operations.py:99` — so the convention is holding,
   by discipline rather than by construction.)

5. **`ops_dashboard/tests/conftest.py:600-603`** — `password_hash: ""`, `totp_secret: ""`.
   `authenticate()` (`auth.py:226`) returns *"Auth not configured"* for an empty
   `password_hash`, so **no test using the shared `gui_config` can perform a real login**.
   *Compensated:* `test_auth.py` builds its own `auth_cfg`. Worth knowing before someone
   writes a login test against the shared fixture and wonders why it never authenticates.

---

## RECOMMENDED BUILD SCOPE (prioritised — trading-day / security-critical first)

| P | Item | Class | Size |
|---|---|---|---|
| **P1** | **`/health` honours `require_hmac`** — add the `elif receiver._require_hmac: return 401` branch at `webhook_receiver.py:~299`, mirroring `:469`. **+2 tests**: `require_hmac=True` × `/health?token=<correct>` → **401**; × valid HMAC → **200**. Fills the only empty cell; disarms a bug scheduled to be created by an open Rama action. **RED-on-old must be shown** (the token-only GET returns 200 today). | **LOOP** — signal-entry-path component (though `/health` only; `/webhook` untouched) | ~3 lines + 2 tests |
| **P2** | **Auth-ON wired fixture** — `wired_system_authenticated` in `tests/integration/conftest.py`, so the S4 test stops hand-building its receiver and the next wired auth question has a fixture. Pure test-infra. | batch-safe | small |
| **P3** | **Route-map-driven dashboard guard test** — iterate `app.url_map`, assert every endpoint ∉ `_LOGIN_EXEMPT` denies anonymous. Converts a manual enumeration into an automatic invariant; a new blueprint can no longer skip it by omission. | batch-safe | small |
| **P4** | **Comments only** — mark the BL-18 block "constructor-only; G.1 owns request-time" and `TestHealthAuth` "`require_hmac=False` — see P1". Stops the next reader mistaking either for full coverage. | batch-safe | trivial |

**NOT recommended: a sweep.** There is no population of auth-blind components to sweep. The
dashboard is exemplary (global default-deny + anon tests); the healthcheck server's control
is a bind, and it is tested; the webhook receiver's auth is well covered on both routes with
exactly one cell missing. Building a broad "auth-ON fixture default" programme against this
codebase would be work without a defect to catch.

**The one structural lesson worth carrying:** #6 exists because the receiver implements auth
**twice, per route** (`:290-304` and `:459-479`), and the two drifted. The dashboard
implements it **once, globally** (`app.py:169`) and cannot drift. If a follow-up wants a
real preventative rather than a patch, that is it — but it is a refactor of the live signal
entry path, so it is a LOOP decision for Rama, not a batch item, and **P1 does not depend on
it**.

---

## What was NOT done (per §5)

No test, fixture, or production code was written or changed. No push. The
`_shutdown_event.set()` design question remains Rama's. The Monday 08:15 boot watch is
untouched.
