# Missing-direction alert — DONE (18-Jul-2026)

**Task.** A strategy YAML that is *missing* `direction`, has an *invalid* `direction`, or
otherwise fails to validate must raise a **loud Telegram + email alert naming the file**,
not fail quietly. Then deploy.

**Context.** `direction` is the required, enum-validated field `StrategyConfig.direction`
(`strategies/schema.py:58`, validator `:143-150`) on which the whole regime long-vs-short
tilt depends. Rama's concern: if he adds a strategy and forgets to declare
`direction: LONG|SHORT` (or mistypes it), the strategy must not silently fail to trade — he
must be told loudly, for the current 16 YAMLs and all future ones.

System DOWN today (Saturday 18-Jul, book flat); off-market waived; **no flatten** — the alert
is exercised at the next boot's strategy load.

---

## §2 — Investigation: fail-boot vs silent-skip  → **path (A): FAIL-BOOT**

Traced the real boot path for a bad strategy YAML:

1. `StrategyConfig` validator raises `ValueError` on a missing/invalid `direction`
   (`schema.py:143-150`); `validate_strategy` wraps it as `ConfigSchemaError`
   (`schema.py:370-375`, `raise ... from exc`).
2. `StrategyLoader.load_all_strategies` (`loader.py:67-69`) calls `validate_strategy` in a
   loop with **no try/except** — the first bad file's `ConfigSchemaError` **propagates**.
   The docstring is explicit: *"ANY invalid file raises ConfigSchemaError immediately — no
   partial loads."* → **This is path (A): the loader raises; it does NOT silently skip.**
3. The boot's *actual gate* is the **preflight**, not the live load. `run_all_startup_checks`
   (`main.py:1887`) runs `check_strategy_configs` (`startup_checks.py:1266`), which calls
   `load_all_strategies`, **catches** the exception (`startup_checks.py:1288`), and appends
   `"invalid_strategy_configs"` to `blocking_failures` (`:1616-1618`). `main.py:1992-1997`
   then aborts with `_log.critical("Startup checks failed: …")` + `return 3`.

**The silent gap.** Today, a missing/invalid `direction`:
* aborts the boot cleanly (`exit 3`) — correct, a broken strategy set must not run;
* logs **one** `check_strategy_configs: strategy config validation failed: …` line naming
  only the **first** bad file (the loader stops at the first);
* sends **no Telegram and no email at all**. The reason is buried in a log line, and the
  `notifier` object is not even constructed until `main.py:2048` — *after* the `return 3`.

So the fix is exactly the §3 **path (A)** branch: **keep failing the boot**, but fire a loud,
consolidated Telegram+email alert **before** the abort, naming **every** bad file + reason.

§2 also asks whether the same concern applies to *any* validation failure, not just
`direction`: **yes** — the scan covers every schema failure (any field, unknown field, bad
YAML, non-mapping), and the alert lists them all.

---

## §3 — Build (path A: alert **then** keep failing the boot)

Two additive pieces + one call site (one commit). **No schema change. No change to how any
valid strategy loads or trades.**

**1. `strategies/loader.py` — `scan_strategy_errors(dir) -> list[(filename, reason)]`**
(pure, Layer-2 clean: only stdlib + pydantic + `strategies.schema` + `core.exceptions`).
Globs `*.yaml`, validates each via the existing `validate_strategy`, and collects **all**
bad files in one pass (unlike `load_all_strategies`, which stops at the first). It **never
raises** — a per-file failure becomes a reason string — so it is safe on the abort path.
Reasons come from pydantic's structured `.errors()` (reached via
`ConfigSchemaError.__cause__`), rendered field-first:
* missing → `missing required field 'direction'`
* invalid → `direction: direction must be LONG or SHORT, got 'FOO'`
* unknown field → `unknown field 'frobnicate' not permitted`
* non-schema (bad YAML / non-mapping) → the raw `ConfigSchemaError` text (fallback).

**2. `main.py` — `_alert_invalid_strategy_configs(bad_files, config_dir)`** (module-level,
fail-safe). Reuses the **same** Telegram + critical-email path as the strategy-registry /
cron officers — **no parallel notifier**:
* **Telegram:** `TelegramNotifier.from_env(...)` → `send(severity="CRITICAL", …,
  write_sentinel=False)`. `write_sentinel=False` because this method writes the **one** email
  sentinel itself — so exactly one Telegram + one email, **no duplicate** (the documented
  "caller owns the email backup" pattern).
* **Email:** `write_critical_sentinel(…, content_type="text/plain", plain_fallback=body)` →
  the alert-watcher delivers it. Guarantees an inbox record **even if the Telegram token is
  unset**.
* **FAIL-SAFE:** every send is wrapped in `try/except` — an import or delivery failure is
  logged and swallowed, so the alert can never crash or mask the abort it describes.
* **Mode-agnostic (paper == live):** `from_env` builds a fresh notifier, so a boot-blocking
  config error alerts in both modes, exactly like the cron officers. (Parity, Rule #5.)

**3. Call site — `main.py:1992` abort branch.** When `"invalid_strategy_configs" in
report.blocking_failures`, scan for all bad files and fire the alert **before** `store.close()
+ return 3`. Guarded by its own `try/except` so the alert can never mask the abort. The abort
itself (`store.close()`, `return 3`) is byte-identical to before — **fail-boot preserved**.

**Behaviour path taken: (A) alert + keep failing the boot.** The system still refuses to
start with a broken strategy set; it now says loudly and specifically *why*, naming each file.

---

## §4 — Tests + RED-on-old

**New tests (11):**
* `tests/unit/test_strategies.py` (6, `scan_strategy_errors`): all-valid → silent `[]`;
  missing-direction named (`missing required field 'direction'`); invalid-direction named
  (value + valid set); multiple-bad → all listed in one pass; a non-direction failure covered;
  never raises on garbage YAML (non-mapping / bad syntax).
* `tests/unit/test_main.py::TestHelpers` (5, `_alert_invalid_strategy_configs`): happy path =
  **one** CRITICAL Telegram (`write_sentinel=False`) + **one** email sentinel naming every
  file; fail-safe when **both** channels raise (no propagation); fail-safe when `send()` raises
  (email still attempted); `from_env → None` still emails; empty input → silent no-op.

**RED-on-old (proven on the true pre-change tree — a throwaway `git worktree` at base HEAD
`a9cc41d`, NOT `git stash`; rc-checked):**
* Base blobs contain **0** occurrences of `scan_strategy_errors` / `_alert_invalid_strategy_configs`.
* New tests copied onto base source: `test_strategies.py` → the 6 new scan tests FAIL with
  `cannot import name 'scan_strategy_errors'` (**31/37; FAILED: 6**); `test_main.py` → module
  ERROR, `ImportError: cannot import name '_alert_invalid_strategy_configs' from 'main'`.
  The tests pass **only** because of this change — non-vacuous.

**GREEN on new code:** `test_strategies.py` **37/37**; `test_main.py::TestHelpers` **12 passed**.

---

## §5 — Full regression + deploy

**Full suite (NOT scoped), run on BOTH trees back-to-back in the same time window** (Saturday
18-Jul, ~10:55→11:2x IST, so the time-gated PC-env failures are directly comparable). Base =
a pristine `git worktree` at HEAD `a9cc41d` — **never `git stash`**; rc-checked.

| Run | Result |
|---|---|
| **BASE** (`a9cc41d`, pristine) | **41 failed, 4877 passed**, 4 skipped (778s) |
| **MINE** (working tree) | **11 failed, 4918 passed**, 4 skipped (809s) |

* **NEW failures (`comm -13 base mine`) = EMPTY ⇒ ZERO ATTRIBUTABLE.** Every test that fails
  on my tree also fails on base.
* All 11 of my run's failures are the known pre-existing set: the Saturday calendar-gated
  `test_daily_trade_review`, plus the documented flaky PC-env group
  (`test_order_placer_fix061` ×4, `test_fix181`, `test_phase17_batch2`, and heavy
  `main()`-driving `test_main.py` classes).
* The 30 base-only failures are those same flaky heavy `test_main.py`/preflight tests moving
  in the **safe** direction (fail→pass). Counts reconcile exactly: 4877 + 30 + 11 new tests
  = 4918 passed.
* **None of my touched files' tests fail**; all 11 new tests pass.

**Deploy (18-Jul ~11:2x IST, off-market, system DOWN ⇒ no flatten):**

* Code commit **`34fd6a5`**, tag **`deploy-18jul-missing-direction-alert` → `34fd6a5`**
  (the code identity). **No schema change; no config/cron/schema file in the diff.**
* Fresh VM backup `data_store/backups/pre_deploy_missing_direction_alert_20260718.db` —
  **verified sound, not just present**: `quick_check=ok`, schema v44, 361 trade rows.
* Push → post-receive checkout + crontab auto-install OK.
* **Verified: PC == VM bare == tag == `34fd6a5`** (re-derived at run time). The deployed
  working tree really contains the code (`scan_strategy_errors` ×1, `_alert_invalid_strategy_configs`
  ×1, the call-site guard ×1). **Schema v44 == deployed `EXPECTED_SCHEMA_VERSION` 44 ⇒ no
  migration** at the next boot. `integrity_check=ok`, **0 FK violations** (identical to the
  pre-deploy baseline — code-only change). Services: `trading-system` inactive (expected, down
  Saturday; loads at **Mon 20-Jul 08:15**), `alert-watcher` **active** (so the email sentinel
  will be delivered), `gui-dashboard` active.

**⭐ PROVEN END-TO-END ON THE VM WITH THE DEPLOYED CODE** (read-only; the real config was
never modified):

* the **real 16 strategy YAMLs** scan to `[]` → **silent, no false alarm**;
* a **temp copy** with `direction` removed / set to `BUYSELL` yields exactly:
  `first_pullback_long.yaml -> missing required field 'direction'` and
  `first_pullback_short.yaml -> direction: direction must be LONG or SHORT, got 'BUYSELL'`;
* re-scan of the real config afterwards → still `[]` (untouched).

**When it bites:** the alert is exercised at the next boot's strategy load. With the current
(all-valid) config it stays **silent** — it only speaks when a YAML is actually broken.

**Rollback:** revert `34fd6a5` — schema-free, additive, no config/cron dependency.

---

---

## §7 — Not in scope (untouched)

Regime Phase 1 / weighting; any valid strategy or direction value; any parallel notifier; any
schema change; any change to how a strategy trades.
