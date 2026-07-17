# Strategy-Direction Registry — DONE

**Date (IST):** 17-Jul-2026 · **Base:** `6e2d6d3` · Regime-ranking prerequisite (the long-vs-short
tilt needs a reliable per-strategy direction). **No trading behaviour changed.**

Follows the ChatGPT-approved design: direction is ALREADY the authoritative structured field
`StrategyConfig.direction` (order side + `trades.direction` derive from it), so this is **not** a
reclassifier or a duplicate field — it is a **registry + auto-detect + notify + confirm +
DIRECTION_CONFLICT**, with consumers routed to the canonical field.

---

## What shipped (2 logical commits)

| Commit | Unit |
|---|---|
| `26885bb` | **feature** — registry YAML + `core/strategy_direction.py` + `scripts/strategy_registry_officer.py` (detect/register/notify/confirm/conflict) + cron entry + regenerated crontab + 12 tests |
| `c2f82ff` | **routing** — retire the GUI `_family_of` name-parse; route `eod_squareoff` summary grouping to canonical; 6 tests |

## Three separate concepts (ChatGPT mandate — never conflated)

`config/strategy_direction_registry.yaml`, per strategy (keyed on `StrategyConfig.name`, the stable
machine identity):
- **`direction`** LONG | SHORT — authoritative, **mirrors** `StrategyConfig.direction`, never overrides it.
- **`registration_status`** PENDING | CONFIRMED.
- **`health`** OK | DIRECTION_CONFLICT.
Plus `first_seen` + `evidence` (source YAML / scanner). Seeded with the current 16 strategies
(PENDING); the officer confirms the traded ones on its first run.

## Auto-detect + register + notify (mirrors the cron officer)

`scripts/strategy_registry_officer.py`, daily **16:22 Mon-Fri** (`monitored`), mirrors
`cron_officer._auto_discover` (:536) + `_roster_integrity` (:593):
- enumerate the ACTUAL set = loaded strategy YAMLs ∪ distinct `signals.scanner`/`signals.strategy`;
- diff vs the registry; each UNSEEN strategy → register **PENDING** (direction = `StrategyConfig
  .direction`) and **notify Telegram + email** (reusing `TelegramNotifier` + the critical-sentinel
  email path — no parallel notifier) with evidence;
- the daily job **OWNS registry writes** (like the cron officer never mutates its registry at
  runtime); a no-change run is **silent**. **Zero code for future strategy additions.**

## CONFIRM

A PENDING strategy with its first FILLED trade (`qty_filled>0`) → **CONFIRMED** (silent status
advance; direction unchanged). Derived from the DB each run (self-healing).

## DIRECTION_CONFLICT — with a corrected premise

> **Premise correction (FIX-182).** The instruction placed the conflict check on
> `ORPHAN_ADOPTION` (order_reconciler.py:21), assuming an adopted orphan is a strategy-attributed
> trade with an independent side. It is **not**: post-FIX-182 the system does **not** adopt untracked
> broker positions — they are treated as human orders (`trade_id=None`, no strategy;
> `_check2_orphan_adoption` :1461-1540), only flattening a system-oversell. **There is no
> orphan-with-strategy to conflict-check.**

So the conflict check is a **daily realized-vs-declared DB scan**: for each strategy, if any FILLED
`trades.direction` differs from the declared direction → **health = DIRECTION_CONFLICT**, the
declared value **KEPT** (never overwritten), and **notify**. This is additive (no live trade-path
hook), catches the same divergence class **and more** (any independent-side path, not just orphans),
and is self-consistent: config-path trades can never diverge, so a fire is a genuine anomaly
(a dual-direction strategy, a config change vs history, a future external import). Notify fires only
on the **OK → DIRECTION_CONFLICT transition**.

## Consumer routing — behaviour-neutral (audited)

A consumer audit found the routing is small: the live path already flows from
`StrategyConfig.direction`.

| Consumer | Finding | Action |
|---|---|---|
| **Allocator** (`long_short_skew_max`) | reads `candidate.side`, which derives from `strategy_obj.direction` (`signal_processor.py:812`) | none — already canonical |
| **Regime module** | consumes **no** strategy direction today (index-level BULL/BEAR only) | none |
| **Reports / Analytics** | intentionally read the realized per-trade `trades.direction` | none |
| **GUI direction value** | already read from YAML (`config_reader.py:71`) | none |
| **GUI `_family_of` grouping** | the ONE name-parse (`endswith('_long')/('_short')`) | **RETIRED** → strips the token matching the structured `basic.direction` |
| **`eod_squareoff` summary grouping** | a per-strategy direction **vote** over realized sides (would mislabel a dual-direction strategy) | **routed** to a canonical direction map (built by the caller so `_format_summary_body` stays PURE; default None → the legacy vote → byte-identical) |

**Behaviour-neutral proof:** on real data every realized `trades.direction` equals the strategy's
declared direction, so both routings produce identical output today. `_family_of("gap_fade_long",
"LONG") == "gap_fade"` (== old); `eod` summary with `direction_map=None` falls back to the vote
(byte-identical). Existing suites green: `test_eod_squareoff` 64, `strategy_tower` 24, dashboard 372.

## RED-on-old (against the true pre-change tree `6e2d6d3`)

- New feature (registry/core/officer): RED by **absence** — the modules don't exist on base.
- **GUI `_family_of`:** base has the 1-arg signature + blind `_long`/`_short` loop →
  `test_family_of_direction.py` **3 failed** on base.
- **`eod_squareoff`:** base has no `direction_map` param →
  `test_strategy_direction_routing.py` **3 failed** on base.
Both restored from HEAD after the proof.

---

## Regression + deploy

**Full suite** (unit + integration + core), run across the Fri→Sat midnight: **44 failed / 4863
passed / 3 skipped**. **ZERO attributable:**
- 43 are the known PC-env baseline (`test_main` ×26, `test_fix135_auto_token` ×6,
  `test_order_placer_fix061` ×4, `test_fix129_ntp_check` ×2, `test_instance_lock` ×2, `test_fix181`
  ×1, `test_interactive_startup` ×1, `test_phase17_batch2` ×1 — [[pc-test-env-hygiene]]).
- **+1 is calendar-gated, not mine:** `test_daily_trade_review::test_main_defaults_date_to_today…`
  — the run crossed into **Saturday 18-Jul**, so `daily_trade_review.main()` hits the weekend/holiday
  skip and writes no report file, failing the file-exists assert. `reports/daily_trade_review.py` and
  its test are **untouched** by these commits (`git diff 6e2d6d3..HEAD` = no match) ⇒ base == HEAD ⇒
  it fails identically on the pre-change tree. My earlier **Friday** runs today (P1, regime-phase0)
  did not hit it — same code, different calendar day. Same class as the time-gated
  `test_interactive_startup` failure.
- **My 18 new tests passed** in the full run; **no failure in any file I touched**
  (`eod_squareoff` / `strategy_direction` / `strategy_registry_officer`). Dashboard venv: **372
  passed** (369 + 3 `_family_of`).

**Deploy (18-Jul ~00:2x IST, off-market, system DOWN — no flatten):** commit `c2f82ff`, tag
`deploy-18jul-strategy-registry`.
- **NO schema migration** — the registry is a YAML file; `trading_system.db` schema **v44
  unchanged**. Backup `pre_deploy_strat_registry_20260718_002539.db` (`quick_check=ok`).
- Push → post-receive; **"crontab AUTO-INSTALLED from canonical"** (canonical == generate(deployed
  `cron_registry.yaml`) — the pre-commit hook keeps the two in sync). **PC == origin == VM bare ==
  `c2f82ff`**; code-identity delta vs tag empty. Registry / officer / core module all present in the
  deployed tree; the crontab has `strategy_registry_officer [16:22 Mon-Fri]`.
- **Officer validated against real VM data** (`main() --dry-run`, weekend-skip bypassed):
  **`new=[] · confirmed=[the 11 traded strategies] · conflict=[] · total=16`** — 0 new (seed
  complete → no notification storm), the exact 11 BK-1-traded strategies would confirm PENDING→
  CONFIRMED (silent), 0 conflicts (declared == actual everywhere, as the investigation proved).
- **First live run:** Mon 20-Jul 16:22 (weekends skip) — writes the 11 confirmations (silent) and is
  otherwise silent. A genuinely-new strategy or a DIRECTION_CONFLICT would then notify.
- **Deploy-reset note:** the officer writes `config/strategy_direction_registry.yaml`; a future
  `git checkout -f` deploy resets it to the committed seed, and the officer re-derives
  status/health from the DB on its next run (self-healing) — a benign, documented behaviour.

**Rollback:** revert `c2f82ff` + `26885bb` (schema-free); the registry YAML is inert (no trading
path reads it).

## Not built (later, careful loop)

Regime Phase 1 (compute the ordinal regime → weighting / the long-vs-short tilt) is the next step;
this registry is its prerequisite (a reliable direction source). No duplicate outcome-derived
direction field; `StrategyConfig.direction` is never auto-overwritten.
