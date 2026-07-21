# DESIGN — CT-Harness Safety  (21-Jul-2026)

**Status: NO IMPLEMENTATION NEEDED — the premise is stale, verified against the deployed code
(`32f42f9`). Read-only investigation; no code, harness never executed.**

> ⭐ **VERDICT (read this first, per the "if A2 is urgent, say so at the top" instruction — it is
> the OPPOSITE of urgent):** the crash-test harness does **NOT** open the live DB writable. That
> hazard was real on **16-Jul**, **FIXED on 18-Jul** (tag `deploy-18jul-ct-harness-safety`), and the
> current `tests/crash_test/ct_utils.py` is already **safe-by-construction** — it *is* the design §B
> asked me to propose. `pytest` collection opens **no** live DB writable; every full-suite run since
> 18-Jul (and today's) was safe. And the six parked CT scenarios were **never** blocked by the DB
> hazard — they are OS-destructive operator drills. This instruction carried the 16-Jul finding
> without the 18-Jul fix, and conflated the fix with the CT parking — an attribution-gloss instance
> (§ATTR), one afternoon after the sweep that named the class.

---

## A1 — THE HAZARD: real 16-Jul, unrepresentable since 18-Jul

**16-Jul (the original finding, now history):** `ct_utils.py` exported an ambiguous `DB_PATH`
pointing at the live DB, and `get_db_connection()` defaulted to **writable** — a destructive harness
holding a writable handle on production. Real live writes were recorded 05-Jun and 07-Jun 2026
(A3 below).

**18-Jul (the fix — the current code):** there is **no module-level DB open** anywhere in the
harness. `ct_utils.py` sets path *constants* only; the sole thing it opens at import is nothing.
Quoting the deployed file:

- `LIVE_DB_PATH` (`ct_utils.py:73`) is a `pathlib.Path` **constant**, not a connection. The old
  ambiguous `DB_PATH` "was **DELETED on purpose (18-Jul-2026)** … Importing the old name now fails
  loudly with `ImportError`" (`:66-72`).
- `assert_not_live_db()` (`:114`) — a **fail-closed** hard guard (`LiveDatabaseRefused`, "no override
  flag and no environment escape hatch", `:93-100`). It canonicalises the path (defeats
  relative/`..`/`~`/symlink/case), checks **both** live DBs and their `-wal`/`-shm` sidecars, and
  uses `os.path.samefile` to catch hardlinks/bind-mounts.
- `get_db_connection()` (`:176`): `readonly=True` → a `mode=ro` handle (SQLite refuses writes);
  `readonly=False` → **defaults to the SCRATCH DB** and routes through `assert_not_live_db(path)`
  (`:198`, "the load-bearing refusal") — a live path *raises* instead of opening.
- `make_scratch_db()` (`:157`) builds the harness its **own** scratch DB from `core/schema.sql`;
  `scratch_db_path()` guards even a `CT_SCRATCH_DIR` override that resolves onto a live DB.

**Trigger point (A1's question):** import-time opens **nothing**; the only DB access is inside
functions, on scratch or `mode=ro`. So there is no import-time hazard to reason about.

## A2 — BLAST RADIUS: pytest collection is safe (the urgent question, answered NO)

- **No pytest-collected module opens a live DB writable.** Every `get_db_connection()` /
  `StateStore(...)` call in the harness is **inside a function**, and every writable one targets
  **scratch** (`test_capital_invariant_violation.py`, `cleanup.py`, `test_kill_switch_edges.py`,
  `test_position_sizer_edges.py`, `ct_day2_isolated.py` — all `make_scratch_db()`/`DB_SCRATCH`).
- The only module-level opens are `vm_investigate.py:7` / `vm_investigate2.py:7` — both
  `get_db_connection(readonly=True)` (mode=ro, safe) **and** not `test_*.py`, so `pytest` never
  collects them.
- The six CT drills (CT114/…/135) are **YAML scenarios run by `scenario_runner.py`**, not pytest
  tests — collection never touches them.
- ⇒ **Every full-suite run since 18-Jul was safe.** Today's runs used `tests/unit tests/integration`
  and did not even collect `tests/crash_test/`. The urgent scenario §C3 warned about does not exist.

## A3 — HAS IT EVER WRITTEN?

**Pre-fix: yes.** The 18-Jul report records real live writes on **05-Jun and 07-Jun 2026** via
`cleanup.py` (`reports/crash_test/cleanup_log.jsonl` — "Kill switch cleared", "Released 37 orphan
reservations", a `hard_cleanup` that cancelled trades/orders and reset capital). That is the concrete
harm that motivated the fix. **Post-fix: impossible by construction** — `assert_not_live_db` fails
closed on any writable live open, with no escape hatch. (The `cleanup_log.jsonl` is a VM/local
artifact, absent on the PC checkout; the historical evidence is in `ct_harness_safety_18jul2026.md`.
Stated as evidence-of-a-past-write, not absence-of-evidence.)

## A4 / B4 — WHY THE SIX TESTS ARE PARKED (not the DB hazard)

Recovered from `SYSTEM_MAP.md:1176` (hygiene-pack 24-Jun) and `ct_harness_safety_18jul2026.md:76-79`:
the six are **destructive OS/operator scenarios**, `scenario_runner.py` with `shell=True` on the real
repo — **off-hours sandbox-VM drills, never the live box.** Per-test:

| test | what it does | DB-harness relevance | real blocker |
|---|---|---|---|
| CT114 Disk Full | `fallocate`s the disk under a running system | not DB-bound | needs a sandbox (fills a real disk) |
| CT127 Clock Backward | skews the clock | not DB-bound | needs a running system + clock control |
| CT130 Cron Silent Failure | breaks a cron | scratch carries the schema | needs a running system |
| CT132 Delete Config Mid-Session | `rm`s a strategy YAML | scratch carries the schema | mutates the real repo — sandbox only |
| CT133 Bad YAML / stop service | `sudo systemctl stop trading-system` | scratch carries the schema | stops the live service — sandbox only |
| CT135 Cleanup During Trading | verifies `cleanup.py --hard` refuses without `--force` | tests the gate logic, not the target DB | needs a running system |

⇒ The DB-harness fix **neither blocked nor unblocks** these. They were parked because they
`fallocate`/`rm`/`systemctl stop` the real machine — an operator-drill scheduling problem, not a DB
one. "Fixing the harness unblocks the crash tests" is a tidy-but-wrong claim (the exact shape the
sweep spent an afternoon on).

## B — THE DESIGN (already implemented; §B requirements checked against the code)

| §B requirement | met by |
|---|---|
| B1 unsafe state **unrepresentable**, not discouraged | the ambiguous `DB_PATH` deleted (ImportError); writable opens default to scratch and pass the fail-closed guard; no path-trusting default remains |
| B2 mechanism | the harness constructs its **own** scratch DB (`make_scratch_db`) **and** refuses any path resolving to a live DB (`assert_not_live_db`, canonical + samefile) **and** has no writable-live default — all three, not one |
| B3 **anti-vacuity — the guard can FAIL** | `test_ct_guard_invariant.py::test_invariant_detects_a_planted_bypass` plants a raw `sqlite3.connect` + `StateStore(db_path=LIVE_DB_PATH)` + live literal and asserts all three AST invariants **raise**, then pass again once removed; plus `test_scan_actually_covers_the_harness` (≥20 modules, `cleanup.py` present) guards a vacuous scan |
| B5 regression map | the AST invariant (`test_ct_guard_invariant.py`) runs in `tests/crash_test`; it enumerates all 31 harness modules and fails on any new raw-connect / live-literal / `StateStore(LIVE)` — so a future regression is caught automatically. No deploy needed; the property is already pinned. |

The 18-Jul design also encodes the **permanent engineering rule** in the invariant's docstring and in
`SYSTEM_MAP.md` (⚠️ ENGINEERING RULE): *a destructive harness must never hold a writable live handle;
all writable access through ONE fail-closed guard; live reset is an operator tool in `scripts/` with
backup+confirmation.*

## §ATTR — THIS INSTRUCTION IS AN ATTRIBUTION-GLOSS INSTANCE

The register I refreshed at 14:40 already carried the correct state ("CT-harness fix is done … 6 CTs
unblocked but unrun"). This instruction restated the **16-Jul** finding as current and added a false
causal link (hazard → parked tests). Per §5 of `attribution_gloss_sweep_21jul2026.md` — cite the
primary artifact, not a prior summary; a claim gets the tell when it is sharper than its source. The
primary artifact (the deployed `ct_utils.py` + the 18-Jul report) says the opposite.

## RECOMMENDATION

1. **No implementation.** The safe-by-construction design exists, is enforced by an anti-vacuous AST
   invariant, and the deployed code (`32f42f9`) carries it. Nothing to build or deploy.
2. **The six CT drills** are a separate operator activity — an off-hours **sandbox-VM** run (they
   `fallocate`/`rm`/`systemctl stop`), gated by scheduling and a non-live box, NOT by DB safety. Out
   of scope for a DB-safety item; do not run on the live machine.
3. **Optional hygiene (not in this task):** the register's CT line and `SYSTEM_MAP` §G can be tightened
   to say "the 6 CTs were never DB-harness-blocked" so this does not resurface a fourth time.

*Read-only; no code; the harness was never executed; no service touched. For ChatGPT review —
the review question is simply "confirm the 18-Jul design is sound and the premise was stale."*
