# LEDGER #8b — `scripts/check_vm_state.py --reset`: STEP-1 MEASUREMENT RECORD

**Measured 02-Aug-2026 (Sunday), read-only. ⛔ STEP 2 IS GATED — NOT IMPLEMENTED.**
Authority: the #8b card (Step-1 measure-only gate) · the #8 build disclosure ·
IA-XDOCS-01. Companion to ledger **#8** (the docs half, `<BUILT>` `17fb7a6`).

**Status: ~~`<MEASURED — FIX GATED, AWAITING RAMA'S GO AND THE RIDE-OR-HOLD RULING>`~~
→ ✅ `<MEASURED — OPTION A AUTHORISED, HELD OUT OF MONDAY; STEP 2 NOT YET STARTED>`.**
Nothing in this document changed any code. `scripts/check_vm_state.py` is **untouched**.

> ## ✅ THE RIDE-OR-HOLD QUESTION IS **ANSWERED** — Rama, 02-Aug-2026 ~16:32 IST
> - **R5(a) — APPROVED: Option A.** Remove `--reset` from `scripts/check_vm_state.py`
>   **entirely** (§10's recommendation, ratified). Basis as measured here: **no automation
>   caller** (§5) · **no runtime coupling** (§5) · **no second VM copy** (§6) ·
>   `clear_kill_switch.py` already serves the use case correctly in **both** modes (§4).
> - **R5(b) — HOLD.** ⛔ **#8b does NOT ride Monday's push.** Implement **only after
>   Monday's critical path has finished.**
> ⇒ **§10's open recommendation is now a RULING, and this record's question is CLOSED.**
> ⛔ **Step 2 is authorised but NOT started**; its trigger is Monday's critical path
> completing. Full ruling + provenance: `docs/MASTER_PENDING_01-Aug-2026.md` **§A-DEC**.

---

## 1. WHY THIS EXISTS

Ledger #8 removed the wrong kill-clear **procedure** from three recovery docs. This item
is the same wrong procedure in **executable** form: a doc fix cannot cure a script that
still does the wrong thing on demand.

## 2. WHAT `--reset` DOES (`:34-40`, verbatim)

```sql
UPDATE kill_switch_state
SET state='INACTIVE', reason='manual reset via script', triggered_by='operator'
WHERE id=1
```
…then `c.commit()` and prints `Kill switch RESET to INACTIVE`. Raw `sqlite3.connect`
(`:20`) — **not** `StateStore`. Fires only when `--reset` is `sys.argv[1]` exactly, and
only if the `id=1` row exists.

## 3. THE DEFECTS, WORST FIRST

### 3.1 ⭐ IT REPORTS SUCCESS FOR SOMETHING THAT DID NOT HAPPEN
`is_active()` reads **in-memory** `self._state` (`capital/kill_switch.py:487-491`), not
the DB. Against a **running** service the raw `UPDATE` changes **nothing** — and the
script still prints `Kill switch RESET to INACTIVE`. The operator is told the kill is
cleared while the process remains killed.
⚠️ **And it can be silently undone:** `_persist_state` writes `INSERT OR REPLACE`
(`:898`), so the next persist from the live object overwrites the row back to the killed
state. The edit is not merely ineffective; it is **impermanent**.

### 3.2 The HARD_KILL gate is bypassed entirely
`scripts/clear_kill_switch.py:65-68` **refuses** a HARD_KILL without `--force`, precisely
because *an emergency kill re-trips if the root cause is unfixed*. `--reset` performs **no
state check at all** — it clears HARD_KILL exactly as readily as SOFT_KILL.

### 3.3 No audit trail
No `system_events` write anywhere in the script (grepped: zero hits). The sanctioned
`resume()` (`kill_switch.py:688`) persists, publishes an event, and logs INFO with the
reason and `resumed_by`.

### 3.4 `triggered_at` left stale
`--reset` writes `state`, `reason`, `triggered_by` — **not** `triggered_at`. The row ends
up saying an operator cleared the kill *at the moment the kill fired*.
⚪ **No functional boot harm** — `clear_stale_state` early-returns on INACTIVE
(`:300-301`) and `detect_startup_scenario` only inspects state — so this is an
**audit-integrity** defect, not a runtime one. Recorded precisely rather than inflated.

### 3.5 Missing affordances the sanctioned path has
No `--dry-run`; no already-INACTIVE handling (it blindly rewrites a row that is already
clear, where `clear_kill_switch.py` reports and exits 0).

## 4. SANCTIONED vs `--reset`

| | `clear_kill_switch.py` | `check_vm_state.py --reset` |
|---|---|---|
| HARD_KILL gate | **refuses** without `--force` (`:65-68`) | **none** |
| Dry-run | `--dry-run` | none |
| Audit trail | `resume()` → persist + event + INFO log | **none** |
| `triggered_at` | rewritten to now | **left stale** |
| Already INACTIVE | handled, exit 0 | blindly rewrites |
| Access layer | `StateStore` / `KillSwitch` | raw `sqlite3` |
| Effect on a RUNNING service | n/a (operator stops it / `resume.sh` does) | **none, but claims success** |

## 5. CALLERS, COUPLING, COVERAGE

| question | measured answer |
|---|---|
| **Automation caller?** | **NONE.** Not in `config/cron_registry.yaml`, no hook, no systemd unit, no deploy script |
| **Runtime coupling (the ride-decision fact)?** | **NO.** Zero importers repo-wide; not reachable from `main.py` or any service module. A standalone, hand-invoked diagnostic. *(It has no `__main__` guard — all code is module-level — so an import **would** execute it; nothing imports it.)* |
| **Test coverage?** | **ZERO** |
| **Prior classification** | the 05-Jul audit independently listed it an **orphan script** ("zero code/doc/test references") and recommended deletion |

## 6. ✅ THE SECOND-COPY QUESTION — **CLOSED, RULED OUT** (Rama, 02-Aug)

Step-1 flagged that eleven allowlist entries invoke **`~/check_vm_state.py`** — the VM
*home* directory, not the repo path — raising the possibility of a second executable copy
that a repo-only fix would miss (the #8 lesson: a one-site fix is not permanent).

**Rama ran the VM check: `~/check_vm_state.py` DOES NOT EXIST, and no stray
`check_vm_state*.py` exists anywhere outside the repo.** The eleven entries point at a
path that is not there.

⇒ **The repo file is the ONLY executable location, so a repo-only fix IS permanent.**
This is a **cleared verification**, not an open blocker.

⭐ **Honest effect on the hold recommendation:** the *stronger* half of Step-1's
ride-or-hold reasoning — *"a repo-only fix is likely incomplete"* — has **dissolved**. The
hold now rests only on *"Monday's stack is already large and there is no urgency"*. That
is still sound and remains the recommendation, but it is **weaker than when it was
made**, and the ruling is Rama's.

## 7. WHAT MUST SURVIVE ANY FIX (the non-`--reset` half)

These are legitimate read-only diagnostics and **must not be removed**: the table
listing, the kill-switch state display, `--signals`, `--symbol=`, `--trades`,
`--positions`, `--health`, `--orders`.

## 8. ⛔ TWO FURTHER EXECUTABLE HAZARDS IN THE SAME SCRIPT — **DISCLOSURES ONLY**

⛔ **Not fixed, not in #8b's scope. Each needs its own authorisation and its own card.**

### 8.1 ⭐ `--cleanup-pending` (`:102-119`) — **THE LARGER HAZARD, not a footnote**
```sql
UPDATE trades SET status='CANCELLED', exit_reason='cleanup_stale_pending'
WHERE status='PENDING_FILL'
```
It mass-marks **EVERY** `PENDING_FILL` trade `CANCELLED` in the live DB, with **no capital
release** and **no audit trail**.
🔴 **The link that makes it worse than `--reset`:** `PENDING_FILL` is *precisely* the
state CHECK2 and CHECK6 arbitrate. This tool can therefore **strand capital reservations**
and **collide with the routing registered to debt-ledger #3** two commits ago — a trade
yanked to `CANCELLED` behind the system's back is exactly the class of divergence #3 owns.
⛔ **And unlike `--reset` it has NO sanctioned equivalent to redirect to** — so the fix
cannot be a simple "point at the right tool"; the correct behaviour has to be decided.
*(Already independently noted as a terminal-state write site in
`docs/design/terminal_state_write_guard_design_07jul2026.md:83`.)*

### 8.2 `--cleanup-orders` (`:122-148`) — same class
Mass-cancels orphaned `PENDING` orders (`UPDATE orders SET status='CANCELLED' …`). Same
absence of audit; **also no sanctioned equivalent.**

## 9. ⭐ THE GOVERNANCE FACT — the finding under the finding

**Registered OUTSIDE #8b** (debt-ledger row **11**, the IA-XSEC authorisation-surface
family, + §C.4 for Rama's review), because removing `--reset` does **not** address it.

`.claude/settings.local.json` → `/permissions/allow` holds **21 `check_vm_state` entries**,
of which **2 are `--reset`**, plus both cleanup flags — i.e. the allowlist
**pre-authorises an AI agent to run destructive mass-mutation commands against the LIVE
trading database without prompting.** Removing `--reset` from the script does **not**
narrow the allowlist.
⚠️ **And it is stale:** per §6, **eleven of those entries point at a path that does not
exist.**

## 10. THE RECOMMENDATION CARRIED FORWARD

✅ **RATIFIED 02-Aug ~16:32 — R5(a) approves Option A; R5(b) holds it out of Monday. What
follows is the reasoning that was ratified, kept as the record of why.**

- **Option A — remove `--reset`** (Step-1 verdict, unchanged): §5 shows no automation
  caller and `clear_kill_switch.py` already serves the use case correctly in **both**
  modes, so a second clearing path is the duplication class this campaign keeps removing.
  The two allowlist entries are a permission record, not a caller — they fail loudly if
  the flag is gone, which is the right outcome.
- **HOLD out of Monday's push** — still the recommendation, now on the narrower ground of
  §6: no urgency, and Monday's stack already carries #1 + #2 + #2b + #2c-R + docs.
- ⛔ **Both remain Rama's call. Step 2 is not started.**

## 11. LABEL HONESTY

`scripts/check_vm_state.py` is **operator-invoked**, so any fix reaches `<BUILT>` →
`<DEPLOYED>` when it ships. **There is no `<VERIFIED LIVE>` until an operator actually
reaches for the flag** — and since the intended end state is that the flag no longer
exists, "verified" here can only ever mean *the sanctioned path was used instead*.
