# C6 — receiver auth collapsed to ONE call site (25-Jul-2026)

**Authorised** (VS Code handoff §B, 25-Jul). Built off the read-only scoping in `receiver_auth_c6_scoping_2026-07-25.md`, which established the shape: **the auth *state* was already shared** (`_secret`, `_require_hmac`, `_ip_limiter` are instance attributes both routes read), so C6 is *not* "introduce shared auth" — it is **collapsing two spellings of one three-way decision into a single call site**, without flattening the divergences that are there on purpose.

Off-market, `trading-system.service` inactive, book flat. **No service restart — this loads at the Monday 08:15 boot.**

---

## 1. What changed

`signals/webhook_receiver.py`, three edits, one new method:

| | before | after |
|---|---|---|
| `/health` (`:291-317`) | inline block; accumulates an `ok` boolean, one 401 at the end | `authed, _reason = receiver._authenticate(b"")` |
| `/webhook` → `_process_request` (`:472-494`) | inline block; early `return` from each branch | `authed, auth_error = self._authenticate(raw_body)` |
| — | — | **new** `WebhookReceiver._authenticate(hmac_payload) -> tuple[bool, str]` |

The return type mirrors `_cast_numeric_fields` (`-> tuple[bool, str]`) — the idiom already in this file. `(True, "")` on success; `(False, reason)` otherwise.

**The duplication that is gone.** `/health:300-311` carried an `elif receiver._require_hmac: ok = False` branch whose *entire purpose* was to reproduce, in boolean form, what `/webhook:482-489` did with an early return. Same decision, two idioms, two places — and they had already been patched separately (G.1 on `/webhook` 2026-04-25; the `/health` parity fix later, per its own comment). That is the maintenance hazard C6 removes.

## 2. Byte-identical behaviour — the whole branch matrix

`_authenticate` reproduces both trees exactly. `/health`'s old shape had no `else:` clause: with no signature header, `require_hmac` false and no token, it fell through every branch with `ok=False` into the shared 401 — the same outcome as `/webhook`'s explicit `else: → "Missing auth"`.

| branch | `/webhook` before → after | `/health` before → after |
|---|---|---|
| no secret configured | pass → pass | pass → pass |
| `sha256=` + match | pass → pass | pass → pass |
| `sha256=` + mismatch | 401 `HMAC signature mismatch` → same | 401 uniform → same |
| no sig + `require_hmac` | 401 `HMAC signature required…` → same | 401 uniform → same |
| no sig + token match | pass → pass | pass → pass |
| no sig + token mismatch | 401 `Invalid token` → same | 401 uniform → same |
| no credential at all | 401 `Missing auth:…` → same | 401 uniform → same |

## 3. ⛔ The four divergences, preserved — and pinned by tests

Per §B2, above all **the EOD early return**.

1. **HMAC payload is per-route.** `/health` signs `b""` (a GET has no body); `/webhook` signs `raw_body`. Passed as the `hmac_payload` argument rather than assumed by the helper. The consequence is documented in the docstring: a valid `/health` signature is a **constant** for a given secret and indefinitely replayable, while `/webhook`'s is body-bound. A shared helper must not paper over that.
2. **`/health`'s uniform 401 message.** The granular `reason` is returned by the helper and **deliberately discarded** at the `/health` call site. Naming the reason there would tell an anonymous caller on `0.0.0.0:5000` whether `require_hmac` is on. `/webhook` keeps all four distinct messages.
3. **⭐ The EOD route still returns at `:509` (now `:533`) BEFORE the kill-switch and entry-window gates.** Untouched — not a character of that block changed. This early return is what makes the **17:00 PB-01 capture** possible: a post-close alert is legitimate, so it must not be refused by an intraday gate. Auth still runs *before* it, so it is not reachable unauthenticated.
4. **Audit trail.** `/webhook` still writes a `webhook_audit` row for **every** outcome including 401 (the `finally` in `_handle_webhook`); `/health` still writes none.

**Gate order is unchanged on both routes** — shutting-down → per-IP limiter → **auth** → unknown-scanner 404 → EOD dispatch → kill → backpressure → entry window. The limiter still runs *before* auth on both, deliberately ("so a flood is cheap to reject").

## 4. Tests — `tests/unit/test_c6_auth_collapse.py` (21, all green)

- **`TestC6BranchMatrix`** (9) — every branch, both routes, asserted on the 401/not-401 boundary that `_authenticate` alone decides.
- **`TestC6DivergencesPreserved`** (8) — one test per divergence, including `test_d3_eod_route_is_reachable_AFTER_hours_and_under_a_kill`, which sets **kill active AND outside the entry window** and requires the EOD capture to succeed anyway.
- **`TestC6Structure`** (3) — `compare_digest` appears **only** inside `_authenticate` (re-inlining auth fails the build), both routes call the helper, and no `paper/live/mode` branch exists in the helper or either caller (**parity by construction**).

### RED-first (§B3) — by planting, because a refactor cannot be red on HEAD

A behaviour-preserving collapse changes no outcome, so "red before the fix" is not available. The honest demonstration is that each new test **goes red when the flattening it guards against is planted**. All five ran, all five went red, and the source was restored **md5-identical** (`8c269f55075c3e94d94b4b7e21734a70`) after each:

| plant | test that went RED |
|---|---|
| P1 helper ignores `hmac_payload` (one payload for both routes) | `test_d1_health_shaped_signature_does_not_authenticate_webhook` |
| P2 `/health` returns the granular reason | `test_d2_health_message_is_UNIFORM_across_every_failure_branch` |
| P3 **EOD early return moved below the kill gate** | `test_d3_eod_route_is_reachable_AFTER_hours_and_under_a_kill` |
| P4 `/health` writes a `webhook_audit` row | `test_d4_webhook_audits_a_401_but_health_does_not` |
| P5 auth re-inlined into `/webhook` (C6 undone) | `test_c6_auth_is_decided_in_exactly_one_place` |

Script: `scratchpad/red_proof.py`. **The tests are not vacuous.**

### The suites that own the old behaviour — all still green

`test_webhook_receiver.py` (G.1 + BL-18), `test_fix134_backpressure.py` (`TestHealthAuth`, `TestHealthRequireHmac`, **`TestS4SeamUnchangedByP1`**), `test_c2_webhook_lockdown.py`, `test_startup_checks.py` → **184 passed**, plus the one pre-existing PC-env failure below.

**`TestS4SeamUnchangedByP1` is the one that matters most.** S4 cost a whole trading day on 17-Jul because a change to `/health`'s auth altered what the unauthenticated boot self-check saw. It asserts the no-credential response is *exactly* `{"error": "authentication required"}` with status 401 under **both** `require_hmac` settings, and feeds that real 401 to the real `check_webhook_endpoint`. It is green. **This refactor has not re-created S4.**

## 5. Regression — BASE vs MERGE, same window, same shell

Per the standing rule: a fresh BASE taken in the same session and window, same shell, same interpreter (`venv/Scripts/python.exe`, `-p no:randomly`), sets compared with `comm`. **No remembered failure count was used as a baseline** — BASE was measured, not recalled. Both runs are well clear of midnight (08:33 and 08:56 IST), so the regression-attribution rule is satisfied.

| | failed | passed | skipped | wall |
|---|---:|---:|---:|---:|
| **BASE** (pre-edit) | 13 | 5,091 | 5 | 822.60s |
| **MERGE** (post-edit) | 13 | **5,112** | 5 | 846.07s |

- **MERGE-only failures: EMPTY** (`comm -13`) — nothing new broke.
- **BASE-only failures: EMPTY** (`comm -23`) — nothing was masked.
- **The two failure sets are byte-identical** (`diff -q` → IDENTICAL).
- **+21 passed = exactly the 21 tests in the new file.** No other test changed state.

The 13 are the known PC-env failures (Mock-config, WSL-stub, instance-lock). One of them, `test_phase17_batch2.py::test_fix077_flask_max_content_length`, fails inside `WebhookReceiver.__init__` at `:212` (`int()` on a `Mock`) — the same file this batch edits, so worth naming explicitly: it is **in the BASE set**, it fails in the constructor, and this batch changed nothing in `__init__`.

**One post-gate edit:** an unused `import pytest` was removed from the new test file after the gate ran, and that file was re-run (21 passed). Removing an unused import cannot change an outcome, but the full-suite gate ran on the byte-preceding version and this states it rather than implying otherwise.

## 6. Parity

`_authenticate` and both callers are pure request-path logic — no `paper`/`live`/`mode` branch anywhere, asserted mechanically by `test_c6_parity_no_mode_branch`. The behaviour is identical in both modes **by construction**, not by two test runs.

## 7. This unblocks F2

F2 (**Option 2**, Rama's choice) is an **authenticated in-process operator trigger into the existing `KillSwitch`**. Its authentication is now a single named function with a stated contract — `_authenticate(hmac_payload) -> (ok, reason)` — instead of two inline idioms a new surface would have to pick between (and, historically, would have had to be patched in both). Recorded in the ledger.

**Still NOT started:** the F2 design, and the trigger-surface investigation. C6 removes a blocker; it does not authorise the build.

## 8. Deploy

Pushed off-market with the book flat and the service down. **No restart** — the receiver is constructed at boot, so this loads at **Monday 08:15**. PC == origin == VM bare == VM tree verified.

> ⚠️ Unchanged and still true (from the scoping): **the Monday risk for PB-01 is boot wiring, not auth.** Grep the 08:15 boot log for `"V3 Step 10b PB-01 watchlist: ENABLED"` (`main.py:3206`); its absence, or the `"pb01 watchlist wiring failed"` ERROR, is the tell — hours before 17:00, with time to act.
