# Strategy-Direction Classification — investigation (READ-ONLY)

**Date (IST):** 17-Jul-2026 · **Mode:** READ-ONLY (sqlite3 `-readonly`; nothing changed, nothing
pushed) · **Base:** `2255d69`. Foundational for regime Phase 1 (which tilts LONG vs SHORT by
regime and so must know each strategy's direction). Folds in the ChatGPT addendum's build mandates.

---

## ONE-LINE STEER

**(i)** A strategy→direction mapping **already exists and is STRUCTURED** — `StrategyConfig.direction`
(a required, enum-validated YAML field), **not** parsed from the name. **(ii)** It is
**declaration-based, and the "outcome" is derived from it**: the Chartink webhook carries **no**
side, so direction originates at `StrategyConfig.direction` and propagates to the order
`transaction_type` and `trades.direction`. **(iii)** Direction IS readable per strategy and
**declared==actual agrees perfectly** across all 11 traded strategies (no strategy has both sides),
so an outcome-based classifier is feasible — **but it reads the same value the config field already
holds** (the outcome cannot independently contradict the declaration except on broker-adopted
trades). **(iv)** The cron officer gives a clean **auto-detect + notify** pattern to mirror.

> **STOP-CONDITION (addendum) — NOT triggered.** Q1/Q2 uncover **one authoritative source**
> (`StrategyConfig.direction`) with a consistently-derived outcome, **not multiple conflicting
> sources**. Empirically, declared config direction == realized `trades.direction` for every
> strategy, and no strategy has emitted both sides. Proceeding to the Q6 build recommendation. The
> one design nuance worth ChatGPT's eye is called out in Q6(d)/(e), not a blocker.

---

## Q1 — Does a strategy→direction mapping exist? Structured or name-derived?

**EXISTS, and it is STRUCTURED — a declared field, not a name parse.**

`strategies/schema.py:58` — `StrategyConfig.direction: str`, **required** (no default), enum-validated
to `{"LONG","SHORT"}` (`_VALID_DIRECTIONS`, `:24`; validator `_val_direction`, `:143-150`). Every
strategy YAML must declare `direction: LONG|SHORT`. Loaded by `strategies/loader.py:61`
(`load_all_strategies` globs `config/strategies/*.yaml`).

All 16 strategy YAMLs declare it explicitly (e.g. `gap_fade_short.yaml` → `direction: SHORT`,
`positional_sector_rotation.yaml` → `LONG`). **The direction is authored independently of the name
string** — `name: gap_fade_long, direction: SHORT` would validate and would place SELL orders; the
name is conventionally-aligned, not the source.

## Q2 — The outcome source of truth (and why it isn't independent)

**The signal has NO side of its own; direction originates at the config field and flows down.**

- **Webhook payload — NO side.** `webhook_receiver.py` accepts only `stocks, trigger_prices,
  triggered_at, scan_name` (WR4). There is **no** `transaction_type`/`side`/`BUY`/`SELL` in the
  Chartink payload.
- **`signals` table — NO side column.** `core/schema.sql` signals row: `scanner, strategy,
  triggered_at, status, trigger_price, fingerprint, …` — **no direction/side**. Direction is not
  stored on the signal.
- **Direction is assigned from the config at processing time.** `signal_processor.py:778`
  `direction=strategy_obj.direction`; `:812-813` `side = "BUY" if strategy_obj.direction in
  ("LONG","BUY") else "SELL"`. So the **order side is computed FROM `StrategyConfig.direction`**.
- **It is then persisted downstream:** `orders` table `transaction_type TEXT NOT NULL -- BUY|SELL`;
  `trades` table `direction TEXT NOT NULL -- LONG|SHORT` (`order_placer.py:953` writes it;
  `:918` `direction = "LONG" if side == "BUY" else "SHORT"`).

**Source-of-truth priority (addendum), mapped to what actually exists:**

| Addendum priority | Exists as a stored field? | Reality |
|---|---|---|
| (1) Signal side, known at signal time | **NO** (signals table has no side) | = `strategy_obj.direction`, computed at processing; not persisted |
| (2) Order `transaction_type` | **YES** (`orders.transaction_type`) | derived from (1) |
| (3) Executed `trades.direction` | **YES** (`trades.direction`) | derived from (2) |

**Empirical proof they agree (live DB, read-only):** for all 11 traded strategies, `trades.direction`
matches the declared YAML direction exactly, and `SELECT strategy, COUNT(DISTINCT direction) …
HAVING >1` returns **zero rows** — no strategy has both sides. So the declaration and the outcome are
**one consistent source**, not two that conflict.

**The load-bearing nuance:** because the outcome is *computed from* the declaration, an outcome-based
classifier **reads the same value the config field holds** and **cannot independently detect a config
mislabel** (a mislabel would propagate to the outcome and "agree"). The only place a trade's side
originates *independently* of the config is **`ORPHAN_ADOPTION`** — a broker position with no local
trade, adopted by `order_reconciler.py:21` from the **broker's** actual side. That is the one place a
true declared-vs-realized contradiction can arise — and exactly what a DIRECTION_CONFLICT check earns
its keep on.

## Q3 — Strategy registration / discovery

A strategy is defined by **config files, discovered at boot**:
- `strategies/loader.py:61` globs `config/strategies/*.yaml` → each becomes a `StrategyConfig`.
- `:84-143` S10 cross-validates `config/scan_webhook_map.yaml`: every scanner must map to a strategy
  that has a YAML (`_validate_scan_webhook_map`).
- (Per the reusable checklist: a scanner = exactly 3 config files — the strategy YAML, the
  `scan_webhook_map` routing entry, and the `chartink_scanners` connectivity entry.)

**A NEW strategy first APPEARS** two ways: (a) a new YAML + `scan_webhook_map` entry, loaded at the
next boot; (b) at runtime, a **webhook with a new `scanner_name`** (routed via `scan_webhook_map`;
`signals.scanner`/`signals.strategy` then carry it). Auto-detection must watch **both** the loaded
YAML set and the distinct `scanner_name`/`strategy` values appearing in `signals`.

## Q4 — The cron-registry auto-detect + notify pattern (the model to mirror)

`scripts/cron_officer.py`:
- **Detect-new:** `_auto_discover` (`:536-558`) walks the **live crontab**, resolves each job name,
  and flags any name **not in the registry** — "in crontab, not in registry — needs registry entry +
  contract" (`:557`). `:308-309` computes `added`/`missing` between registry and crontab.
- **Surface + escalate:** `_roster_integrity` (`:593`) block (a) compares `live == generate(registry)`
  and raises **WARN** on `unregistered` (`:609-612`); the report rides Telegram + the EOD email
  (`_alert_path_health` `:561`, FIX-191 fallback).

**The pattern to copy:** a daily job enumerates the ACTUAL set, diffs it against a REGISTRY, flags
anything NEW/unregistered, and surfaces it with escalating severity to Telegram/email — never
mutating the registry silently at runtime (`_auto_discover` is "RUNTIME ONLY — never mutates the
registry YAML").

## Q5 — Name-derived direction fragility (what to retire)

**The live trading path does NOT parse the name** — it uses `StrategyConfig.direction` (Q1/Q2). The
name-derived spots are display-only:

| Spot | file:line | What it does |
|---|---|---|
| GUI family grouping | `ops_dashboard/backend/services/strategy_tower.py:32-38` `_family_of` — `for suf in ("_long","_short"): if name.endswith(suf)` | strips the suffix for a UI **grouping key** (`gap_fade_long → gap_fade`); does not classify direction, but hard-codes the suffix convention |
| GUI template | `ops_dashboard/frontend/templates/strategies.html:332` `{ long:false, short:false }` | per-family long/short flags in the strategies screen |

**Not a direction source (avoid conflating):** `config_loader.py:1086` `regime_pref_direction`
(`{BULL:1.0, SIDEWAYS:0.5, BEAR:0.0}`) is a **regime→multiplier** map, not a strategy's LONG/SHORT.

So the "name fragility" is **narrow** — a GUI grouping helper, not the trading path. The build should
still route it (and any future consumer) to the structured field so the `_long`/`_short` convention
is never load-bearing.

---

## Q6 — Recommended build shape (RECOMMENDATION ONLY — not implemented)

Folds in the ChatGPT addendum (items 1–6). The design's job is **auto-detection + notification + a
single queryable registry**, because the *classification* is already structurally available.

**(a) Classify from the OUTCOME, not the name — with the honest caveat.**
Persist per strategy from the realized side, in the addendum's priority order — **(1)** the side the
signal is processed with (= `strategy_obj.direction`, known at first signal, *structurally, never
the name string*) → **(2)** `orders.transaction_type` → **(3)** `trades.direction`. All three agree
today. **State plainly for ChatGPT:** these are not independent of the config field — they derive
from it — so this classifier reproduces `StrategyConfig.direction` and cannot by itself catch a
config mislabel; independence exists only for `ORPHAN_ADOPTION` broker trades. The name is never read.

**(b) Auto-detect a new strategy** (mirror the cron officer): a periodic job (and/or a hook at first
signal) enumerates the ACTUAL set — loaded strategy YAMLs ∪ distinct `signals.scanner`/`strategy` —
diffs against the persisted `strategy_direction` registry, and for each unseen strategy: read its
side (priority 1 at first signal, else 2/3 once it trades), classify LONG/SHORT, **persist
permanently**, and record file/location/evidence. **Zero code for future additions.**

**(c) Persist ONE structured field** `strategy_direction = LONG|SHORT` in a new registry (a DB table
or a registry file mirroring `cron_registry.yaml`). **This becomes the ONLY direction source** the
regime module, strategy ranking, the Allocator (`long_short_skew_max`), Analytics, and Reports read —
and the build **routes all of them to it and retires** the Q5 name-parse (`_family_of`) and the
scattered `StrategyConfig.direction`/`trades.direction` reads.

**(d) A strategy with NO outcome yet.** Direction is knowable at **first signal** from the assigned
side (priority 1), so a strategy need not wait for a completed trade — classify on first signal.
Recommended: seed the registry at **YAML-load / first-signal** as `PENDING→LONG/SHORT`, and confirm
on the first realized order/trade. (If a stricter "outcome-only" stance is preferred, mark `UNKNOWN`
until the first trade and the regime uses neutral weights meanwhile — a Rama/ChatGPT choice; the
data supports either.)

**(e) A strategy that has emitted BOTH sides → `DIRECTION_CONFLICT`.** Never overwrite silently. On a
side opposite the persisted one, **flag `DIRECTION_CONFLICT`, keep the existing value, notify
(Telegram+email), and require investigation.** Today this cannot happen via the config path (single
source), so a fire means exactly the case worth catching: an `ORPHAN_ADOPTION` broker position whose
side contradicts the declared direction, a genuine config change, or a future independent side path.

**(f) Robust to renames / condition changes.** Because classification is from the structured side
(never `endswith("_long")`), renaming `gap_fade_long`→`gap_fade_v2` or changing its entry conditions
does **not** change its direction; only the *realized side* would. Keying the registry on a stable
strategy identity (not the display name) preserves history across a rename.

**(g) Notify (mirror the cron officer):** new-strategy-detected and DIRECTION_CONFLICT ride the same
Telegram + EOD-email path the cron officer uses, with evidence (strategy, first-seen signal/trade,
classified side, file/scanner). Healthy = silent.

**Sequence:** Web Claude designs → ChatGPT reviews → implement (signal-path-adjacent; a new registry
table is a schema add → test-on-backup + Rama-pause per the migration rule). **This task builds
nothing.**

---

## Explicit limits

Read-only, one DB snapshot; the declared==actual proof covers only strategies that have traded (11 of
16 — the other 5 have no trades yet, so their direction is declared-only, unverified by outcome). No
forward confirmation. This scopes the build; it is not the build.
