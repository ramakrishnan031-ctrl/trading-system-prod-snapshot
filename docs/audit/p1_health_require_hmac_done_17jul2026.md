# P1 — GET /health honours require_hmac — DONE (+ P2/P3/P4)

**Date (IST):** 17-Jul-2026 · **Base:** `5e74b9d` (origin/main == VM bare) → **code HEAD `695aa84`**,
tag **`deploy-17jul-p1-health-hmac`** (`4c340c6` → `695aa84`)
**Deploy target state at deploy time:** `trading-system.service` **DOWN** (inactive/dead since
08:16:09 IST, this morning's S4 outage) ⇒ per policy, **deploy freely; no flatten built or run.**
**Parity:** free — the receiver has zero mode references, so P1 holds identically for paper and live.

---

## What shipped

Follows the fixture-blindness investigation
(`docs/audit/fixture_blindness_investigation_17jul2026.md`). Four commits, one concern each:

| Commit | Item | Class |
|---|---|---|
| `6e2a826` | **P1** — `/health` honours `require_hmac` (fix + tests, incl. RED-on-old + S4 seam proof) | LOOP (signal-entry-path component) |
| `7e101aa` | **P2** — `wired_system_authenticated` fixture; S4 test uses it, not a hand-built receiver | test-infra |
| `f10b2f1` | **P3** — route-map-driven dashboard guard invariant | test-infra |
| `695aa84` | **P4** — comments: BL-18 constructor-only; TestHealthAuth require_hmac=False | comments |

---

## P1 — the fix (mirrors `/webhook` :469)

**Before:** `GET /health` authenticated with HMAC-or-token but had **no `require_hmac` refusal
branch**, unlike `/webhook` (`_handle_webhook`, `elif self._require_hmac`). So under
`require_hmac=True`, a `GET /health?token=<correct>` still returned **200 + `kill_switch_active`
+ `queue_depth`** — the URL-token surface `require_hmac` exists to disable stayed open on the
more exposed of the two routes, re-opening the exact AB-910 §1.7 reconnaissance oracle.

**Fix** (`signals/webhook_receiver.py`, the `/health` auth block):
```python
if sig_header.startswith("sha256="):
    ...                                   # valid HMAC still authenticates
elif receiver._require_hmac:
    # G.1 parity with /webhook: token fallback disabled when require_hmac=True.
    ok = False                            # -> falls through to the shared 401
elif token_param:
    ok = _hmac.compare_digest(token_param, receiver._secret)
```
Uses the same `_require_hmac` the constructor already resolved — no second config read, no new
auth idiom, no config/schema change. The 401 body stays uniform (`"authentication required"`)
rather than naming `require_hmac` as `/webhook` does, because on `/health` a distinct message
would itself leak posture to an anonymous caller — the one thing this route must not do.

**LATENT when shipped** (`require_hmac: false` in prod) — inert at runtime until that flag flips,
which is an open operator action. Shipped ahead of the flip so the flip cannot create the bug.

### RED-on-old proof (against the true pre-change file, no git stash)
`git checkout HEAD -- signals/webhook_receiver.py` (the docs commit did not touch it, so HEAD ==
the true pre-P1 code), grep-confirmed the new branch absent, ran the fix-test:
```
require_hmac=True × GET /health?token=<correct>
  OLD code:  200  (AssertionError: assert 200 == 401)   <- token-only WAS accepted
  NEW code:  401                                          <- now refused
CONTROL  POST /webhook?token=<correct> -> 401 both       <- /webhook already correct
```
Working-tree fix restored afterward; committed as one unit.

### SEAM-UNCHANGED proof (the S4 guard) — `TestS4SeamUnchangedByP1`
P1 touches the exact surface that halted production on 17-Jul, so it must prove the
**unauthenticated** boot self-check is untouched:
- `GET /health` with no token / no signature returns a **byte-identical 401**
  (`{"error": "authentication required"}`) under **both** `require_hmac=False` and `=True`.
- The real `check_webhook_endpoint` still maps that 401 to `reachable=True` under both settings.

If this class ever goes red, P1 has re-created the boot outage. It is green.

---

## P2 / P3 / P4

- **P2** — `wired_system` builds its receiver `secret_token=None` (open); added
  `wired_system_authenticated` (same wired stack, prod-like secret). Both delegate to one
  `_build_wired_system` generator (teardown moved into `finally`, so `yield from` tears down
  exactly as before and now also on test error). `SystemContext.webhook_secret` added.
  `TestS4AuthenticatedHealthStillBootsWired` now drives `ctx.receiver` instead of hand-building.
- **P3** — `test_every_non_exempt_route_denies_anonymous` walks `app.url_map`: every endpoint
  ∉ `_LOGIN_EXEMPT` must deny an anonymous caller (`/api/*`→401, else→302 `/login`). Covers **37**
  guarded routes incl. the Flask `static` endpoint and `/logout`, which the manual lists never
  did. A new blueprint can no longer skip coverage by omission; `checked >= 15` floor guards it.
- **P4** — comments marking the BL-18 block **constructor-only** (delete request-time enforcement
  and all 5 still pass) and `TestHealthAuth` **`require_hmac=False` throughout**, each pointing at
  where the real request-time / `require_hmac=True` coverage lives.

---

## Regression

Full suite, NOT scoped (`tests/unit` + `tests/integration` + `tests/core`), run at ~20:40 IST
(OUTSIDE the market window):

- **My tree:** **43 failed / 4842 passed / 3 skipped** (12m37s).
- **True pre-change tree** (base `webhook_receiver.py` from `5e74b9d`, my new tests kept):
  **44 failed / 4841 passed**.
- **Attribution — the diff between the two failure sets is EXACTLY ONE line:**
  ```
  < FAILED test_fix134_backpressure.py::TestHealthRequireHmac::test_token_only_is_refused_when_require_hmac
  ```
  That is *my own* P1 fix-test, which fails on base (token-only /health → 200) and passes on
  mine. **The 43 environmental failures are byte-identical base-vs-mine → ZERO regressions from
  P1**, and this is a full-suite re-confirmation of RED-on-old (the fix-test could only pass
  because the fix is present). Cross-checked earlier by an isolated 5-file base-vs-mine run
  (33 == 33, byte-identical).

The 43 pre-existing failures — none in any file P1–P4 touched, all matching documented PC-env
categories ([[pc-test-env-hygiene]]), all green on the VM:

| File | n | Category |
|---|---|---|
| `test_main.py` | 26 | MagicMock/wiring, full-suite ordering (main.py grew with S4 + liveness) |
| `test_fix135_auto_token.py` | 6 | TOTP/pyotp + network mocking |
| `test_order_placer_fix061.py` | 4 | thread-timing mock harness |
| `test_fix129_ntp_check.py` | 2 | NTP drift check |
| `test_instance_lock.py` | 2 | Windows concurrent-process / file-lock flake |
| `test_fix181.py` | 1 | reconciler mock harness |
| `test_interactive_startup.py` | 1 | the outside-window FIX-189 guard (11th; passes with `TS_IGNORE_MARKET_WINDOW=1`) |
| `test_phase17_batch2.py` | 1 | `int(Mock)` artifact |

`tests/unit/test_gui_secret_key.py` is `--ignore`d in the main venv — it imports the dashboard
(`pyotp`), which lives in `ops_dashboard/.venv`; pre-existing cross-venv split, no P1–P4 commit
touches that import chain.

- **Dashboard venv** (`ops_dashboard/.venv`): **369 passed** (incl. P3 ×2 schema-param).
- **Targeted**: `test_fix134_backpressure` **32 passed** (16 prior + 10 P1, + P4 comments);
  `test_hardening_scenarios` 5 passed; full `tests/integration` 32 passed.

---

## Deploy verification (17-Jul ~21:1x IST, off-market, target DOWN)

- **Fresh backup:** `data_store/backups/pre_deploy_p1_health_hmac_20260717_211747.db`
  (sqlite `.backup` — DB is touched by EOD crons even while trading-system is down;
  `quick_check=ok`, schema v44).
- **Push:** `5e74b9d..695aa84 main` + tag; post-receive checkout + crontab auto-install OK.
- **PC == VM:** bare HEAD == local HEAD == **`695aa84`**; tag `4c340c6` → `695aa84` on both.
- **Code identity:** `git diff --name-only deploy-17jul-p1-health-hmac..HEAD` = **empty** (HEAD is
  exactly the tagged code).
- **P1 present in the deployed working tree:** `grep -c "elif receiver._require_hmac"` = 1.
- **Schema:** deployed `EXPECTED_SCHEMA_VERSION = 44` == live v44 ⇒ **no migration** at boot.
  `PRAGMA integrity_check` = ok; `PRAGMA foreign_key_check` = clean.
- **Services:** `trading-system` **inactive** (expected — down since 08:16:09; loads this code at
  the next 08:15 boot, Mon 20-Jul); token-watcher / alert-watcher / gui-dashboard active.
  `security-watcher` is `activating/auto-restart` (Type=simple run-a-pass-and-exit,
  ExecMainStatus=0, "0 new alerts") — **pre-existing**, the same respawn pattern alert-watcher had
  before its 16-Jul `--loop` fix; untouched by this deploy. Hygiene candidate, not a P1 issue.
- **Runtime effect:** P1 is **inert until `require_hmac` flips** (still `false`), so the deploy is
  behaviour-neutral; it simply means the flip can no longer open the `/health` token oracle.

**Rollback:** schema-free — `git revert 6e2a826` (P1) or reset main→`5e74b9d` + re-push; the backup
above restores the DB if ever needed (not required for this change).

---

## Structural note (NOT in scope — a separate Rama LOOP decision)

The bug existed because the receiver implements auth **twice, per route** (`/health` and
`/webhook` each have their own block) and the two drifted. The dashboard implements it **once,
globally** (`app.py:169` `before_request`) and cannot drift. A single-global-auth refactor of the
receiver is the real preventative — but it is a refactor of the live signal entry path, so it is a
LOOP decision for Rama. **P1 does not depend on it** and closes the concrete gap now.
