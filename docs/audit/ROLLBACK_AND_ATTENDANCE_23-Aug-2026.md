# ROLLBACK · ATTENDANCE · V-7 — 23-Aug-2026 (Sun), evening

**Governed by** `docs/PRE_BUILD_REVIEW_GATE.md` (`23ea03d`).
🏷️ **⛔ SUPERSEDED HEADER — CORRECTED 23-Aug 21:5x. This file was written while the push was HELD.**
🔴 **CURRENT STATE (CORRECTED 25-Aug 22:53 IST): `origin/main` = `75e637c300c1bead4aad3cf549e6dbe9318c6b1f`** — the EOD lifecycle unit + P-3 closure (4 commits) were pushed at 22:53:12. ⚠️ **SUPERSEDED TEXT, retained: *"`origin/main` = `195436bb6323c981d68f313ad7c39858cc0a4653`, pushed 22:2x IST"*.** ⚠️ **SUPERSEDED 26-Aug-2026 19:2x IST — RETAINED VERBATIM, ⛔ NOT DELETED:** *"🛑 **⛔ THE ROLLBACK TARGET / RB-3 TREE DOES **NOT** MOVE — it STAYS `195436b`**, because the tree is *"the last SHA proven to boot"* and `75e637c` has ⛔ NOT booted yet (boot due 08:15 26-Aug). ⭐ Two facts, two clocks — ⛔ do not advance it on a push."* 🟢 **THAT CLAUSE'S OWN PRECONDITION IS NOW SATISFIED: `75e637c` BOOTED AND THE BOOT IS PROVEN (26-Aug 08:15, `STARTUP` row `3794` @ 08:15:29.559643 IST, AFTER `ExecMainStartTimestamp` 08:15:18). ⇒ THE TREE'S CLOCK TICKED — SEE THE ADVANCE RECORD IMMEDIATELY BELOW.** (after `742d9da` at 13:22 and `2f67bb8` at 21:44 — THREE pushes today). 🏷️ **DEPLOYED, ⛔ NOT `VERIFIED LIVE`.** ⭐ Everything in §1–§3 below still applies — ⚠️ but read §0 FIRST.

---

# 🟢 ADVANCE RECORD — 26-Aug-2026 19:2x IST · **THE TREE'S CLOCK TICKED AGAIN. TARGET IS NOW `75e637c`.**

> 🔬 **THE 26-Aug 08:15 BOOT IS *PROVEN* PER L-1.** `STARTUP` row **`event_id 3794`** @
> `2026-08-26T08:15:29.559643+05:30`, scenario `COLD` — written **AFTER**
> `ExecMainStartTimestamp=Wed 2026-08-26 08:15:18 IST`.
> ⭐ **L-1's exclusion is satisfied: it is ⛔ NOT an earlier same-day row** — the preceding
> `STARTUP` is `3791` on 25-Aug. ⛔ Not `systemctl is-active`, ⛔ not the T+15 s green line,
> ⛔ not a Telegram, ⛔ not the absence of *"Config load failed"*.
>
> ✅ **V-2 / V-3 / V-4 ALL HELD (re-confirmed 26-Aug 19:1x, ⛔ not re-captured):**
> `origin/main` (`git ls-remote`) == VM bare `refs/heads/main` ==
> `75e637c300c1bead4aad3cf549e6dbe9318c6b1f` · deployed `main.py` md5
> `ea62b0b876489f2897c288548acb2977` == its `75e637c:main.py` blob · **tracked drift 0**.
>
> ⇒ ⭐ **THE OPERATIVE SHAs BELOW HAVE BEEN ADVANCED `195436b` → `75e637c`:** the §0 current-state
> clause · the two-clock box's CURRENT TREE · the RB-2 recipe's tree literal · the RB-3 checkout
> target. ⚠️ **Superseded values are RETAINED and MARKED, ⛔ never deleted.**
> ⚠️ **The 24-Aug advance record immediately below is HISTORY — ⛔ it is NOT rewritten.**
>
> ## 🔴 A1e — WHAT THIS ADVANCE ✅ MEANS AND ⛔ DOES **NOT** MEAN
> ✅ **MEANS: `75e637c` is the latest tree PROVEN TO BOOT**, and is therefore the correct RB-3
> checkout target and RB-2 tree source.
> ⛔ **DOES NOT MEAN any feature is verified.** 🔴 **A CLEAN BOOT VALIDATES *NO* INDIVIDUAL ITEM.**
> **Still UNVERIFIED after this advance:**
> * the EOD gate's **DISCRIMINATING case** (26-Aug's 17:35 gate passed **TRIVIALLY** on `active == 0`)
> * a **real delivery carry** · the **carry comparator**
> * the **F1 / NI-4 rejection arm** · **NI-14** · **NI-17** · **NI-18**
> * `flatten_in_progress_fn()`
> * **Live/Paper parity (O-3)**
> * ⭐ **the resolver-fed exit path** — 26-Aug produced **ZERO** occurrences of
>   *"NO strategy resolver was supplied"* across three surfaces, but **NOT EXERCISED / ABSENCE
>   OBSERVED** is the correct label: ⛔ no live position reached that path. ⛔ **Absence is not proof.**
>
> ## 🔴 A1f — THE CLOSED EXPOSURE, RECORDED
> ⚠️ **From 25-Aug 22:53 (the `75e637c` push) until this correction on 26-Aug 19:2x, the rollback
> target on record read `195436b`.** An incident in that window would have rolled the deployed tree
> back to a tree **PREDATING the EOD lifecycle unit** — i.e. it would have undone the very code
> that had just been deployed, and (from 08:15 on 26-Aug) the very code that had just proved it boots.
> ⭐ **The exposure is now CLOSED.** ⛔ It is recorded rather than quietly corrected, because the
> window was real and lasted ~20 h 30 m.

---

# 🟢 ADVANCE RECORD — 24-Aug-2026 10:45 IST · **THE TREE'S CLOCK TICKED. TARGET IS NOW `195436b`.**

> 🔬 **THE 24-Aug 08:15 BOOT IS *PROVEN* PER §0.1 / L-1.** Service start `Mon 2026-08-24 08:15:20 IST`
> (`ExecMainStartTimestamp`); `STARTUP` row **`event_id 3788` @ `2026-08-24T08:15:33.134646+05:30`**
> — **AFTER** the start ⇒ it belongs to THIS attempt. `NRestarts=0` ⇒ there was only ONE attempt
> today, so the *"earlier same-day row from a different attempt"* trap is not reachable.
> ✅ **V-2 / V-3 / V-4 ALL HELD:** `origin/main` == deployed `HEAD` == `195436bb6323c981d68f313ad7c39858cc0a4653`
> (measured two ways), tree drift **0** — and the drift recipe was re-proven **NON-VACUOUS today**
> (same recipe against `4568385` ⇒ **29 paths**), with **no write of any kind**.
>
> ⇒ ⭐ **THE OPERATIVE SHAs BELOW HAVE BEEN ADVANCED `4568385` → `195436b`:** the §1 invariant box ·
> RB-2's tree · RB-3's `checkout -f` target · V-7's comparand · the phone-readable step 5.
> ⚠️ 🔬 **RB-2's PARENT is *not* pinned here — it is still DERIVED at use time** (`git ls-remote`);
> it happens to equal `195436b` today, ⛔ but that is a coincidence of timing, ⛔ not an identity.
> 🔴 **THE TWO SHAs REMAIN TWO FACTS ON TWO CLOCKS.**
>
> ⛔ **EVERY 23-Aug MEASUREMENT IN THIS FILE IS LEFT BYTE-INTACT.** What was true then is not
> rewritten — only the values an operator would *execute* have moved. 💭 The SHA↔boot link is still
> an INFERENCE (M-1); V-2/V-3/V-4 are its FALSIFIER, ⛔ not a measurement of it.
> 📄 Full evidence: **`docs/audit/BOOT_PROVEN_24-Aug-2026.md`**.
> ⚠️ 🔴 **AND THE CEILING DID NOT MOVE: 20 commits ran as ONE SET. ⛔ A clean boot validates no
> individual item and closes no open item.** 🏷️ Still **DEPLOYED**, ⛔ not `VERIFIED LIVE`.

---

# §0 — 🔴 MONDAY 24-Aug EXERCISES **20 COMMITS IN ONE RUN**. NOTHING IS SEPARATELY ATTRIBUTABLE.

🔬 **8 commits at 13:22 (`742d9da`) + 10 at 21:44 (`2f67bb8`) + 2 at 22:2x (`195436b`, NI-14 +
NI-15 L1) = 20, and NONE of them has executed yet.** Monday 08:15 is the first run of all
twenty, together.

⛔ **DO NOT ASSIGN ANY MONDAY BEHAVIOUR — good or bad — TO ANY INDIVIDUAL ITEM** without independent
evidence: ⛔ NI-5 · NI-9 · NI-11 · NI-12 · NI-13 · NI-17 · NI-18 · NI-19 · F1 · the job-1 test ·
NI-1/2/3/4/6/7 · the governance commit · NI-14 · NI-15 L1. ⭐ **The unit of attribution is THE SET, ⛔ not the commit.**
👤 Chosen knowingly — ⭐ and it is in the record precisely so a Monday symptom is not mis-pinned.

⚠️ **AND THE INVERSE ALSO HOLDS: a CLEAN Monday does not validate any individual item either.**
🏷️ Ceiling stays **DEPLOYED**. ⛔ `VERIFIED LIVE` requires a production artifact tied to the
specific behaviour, ⛔ not "the boot did not fail".

## ⭐ THE FIRST LINE OF THE MORNING, UNCHANGED AND MOST IMPORTANT

🔴 **A RED `zerodha_morning.ps1` AT 15 s IS ~50% LIKELY ON A *HEALTHY* CONFIG** — the script waits
**15 s** while `token_watcher` polls every **30 s**.
⭐ **WAIT 30 s AND RE-RUN BEFORE DOING ANYTHING ELSE.**
⭐ Only if it is still red: `grep "Config load failed" logs/system_<date>.log` — ⛔ **NOT
`journalctl`**, which carries no INFO.
⛔ **REVERT (RB-3), ⛔ NEVER TUNE**, if a VALID config is rejected.
⚠️ ⛔ **AND RB-2 OR A LEDGER ENTRY THE SAME DAY** — `post-receive` checks out **`main`**, ⛔ not the
pushed SHA, so an unreconciled VM-tree rollback **undoes itself at the next push**, even an
unrelated one.

---
---

# 🔴 §0.1 — A STEP, ⛔ NOT A NOTE: **AFTER A *PROVEN* BOOT, ADVANCE THE ROLLBACK TREE**

⚠️ **THIS FIRES MONDAY 24-Aug.**

🔬 The rollback TREE is *"the last SHA **proven to boot**"*. That value changes on a **PROVEN
BOOT** — ⛔ **not on a push.** The *re-derive after every push* rule elsewhere in this file is the
**right rule for RB-2's PARENT and the wrong trigger for the TREE**. ⇒ **two facts, two clocks.**

🔴 **If 24-Aug 08:15 boots — and the boot is PROVEN — `195436b` becomes the last SHA proven to
boot**, and RB-3's target and RB-2's tree both move to it.
⚠️ ⛔ If nobody advances them, this runbook rolls back **20 COMMITS** on a later incident when the
correct answer is **ZERO**.

---

## 🔴 L-1 — WHAT *"PROVEN TO BOOT"* MEANS HERE. **MEASURED, ⛔ NOT LEFT UNDEFINED.**

⚠️ **If this phrase is undefined on Monday morning it will be filled with *"the script went
green"* — and that is the ~9% false-green case.** So it is defined here, by artifact:

> ## ⭐ **PROVEN TO BOOT = the `STARTUP` row in `system_events` BELONGING TO *THIS* BOOT ATTEMPT.**
> ⛔ **NOT `systemctl is-active` · ⛔ NOT the T+15 s green line · ⛔ NOT a Telegram ·
> ⛔ NOT the absence of *"Config load failed"* · ⛔ NOT an earlier same-day row.**

### 🔬 Why that artifact, and why the obvious ones fail

| candidate | 🔬 verdict |
|---|---|
| `zerodha_morning.ps1` green line | 🔴 **REJECT.** It asserts only `systemctl is-active` at **T+15 s** (`deploy/zerodha_morning.ps1:77,81`) — the exact sample the 9% false-green measurement condemns |
| `systemctl is-active` generally | 🔴 **REJECT at T+15 s.** A crash-looping unit reads `active`/`activating` transiently, and `token_watcher` already mis-reads `activating` as *"running — nothing to do"* |
| Telegram **SYSTEM START** alert | ⛔ **Not usable** — 🔬 no such literal exists in the Python tree; it cannot be quoted as a defined artifact |
| ⭐ **`STARTUP` row in `system_events`** | ✅ **ACCEPT for the BOOT fact** — ⚠️ but see M-1: it does **not** carry the SHA |

🔬 **Why the `STARTUP` row is a real discriminator for the boot itself:**

* Written by `main.py` at **Phase 0h / MAIN12 (`main.py:3832`)** via
  `store.insert_system_event(event_type="STARTUP", …)`.
* 🔴 **`Config load failed` is logged at `main.py:1916` — ~1,900 lines EARLIER.** ⇒ a boot rejected
  on config **cannot** reach Phase 0h and **cannot** write the row. That is precisely Monday's risk.
* 🔬 **Production-proven, control fires:** latest row **`2026-08-21T08:15:37.905980+05:30`** against
  the systemd start of **Fri 21-Aug 08:15:25**; the table holds **77 STARTUP / 76 SHUTDOWN** rows,
  so the query is ⛔ not vacuous.

---

## 🔴 M-1 — **THE ROW PROVES A BOOT HAPPENED. IT DOES *NOT* PROVE WHICH CODE BOOTED.**

🔬 **MEASURED, and the answer is NO:**

* `system_events` schema is `event_id · timestamp · event_type · scenario · details`
  (`core/schema.sql:473-479`) — ⛔ **no SHA, no commit, no git column.**
* The payload is `{"mode": …, "version": VERSION}` and 🔬 **`VERSION = "2.0.0"` is a HARDCODED
  CONSTANT** (`main.py:105`).
* ⭐ **THE CONTROL:** the production payloads for **18 · 19 · 20 · 21-Aug** are **byte-identical**
  — `{"mode": "live", "version": "2.0.0"}` — across days on which `origin/main` demonstrably
  moved (`08b462b` → `7fc5d5a` → `4568385`). ⇒ 🔴 the field cannot discriminate code.
* `session.last_config_hash` fingerprints **config files only**, ⛔ not code, and it is
  `INSERT OR REPLACE` on `id=1` ⇒ ⛔ no history.

> ### ⇒ 💭 **"`195436b` IS PROVEN TO BOOT" IS AN INFERENCE, ⛔ NOT A MEASUREMENT.**
> It infers from *"the deployed tree was `195436b` at that moment"*. ⭐ It is a good inference —
> ⛔ but it is only true if the deployed SHA is captured **AT THE MOMENT OF THE BOOT**, ⛔ never at
> the moment of the query.

### 🔴 THE CONCRETE MONDAY THAT BREAKS IT

1. 08:15 — the boot fails. 2. Rama runs **RB-3** → the tree becomes `4568385`.
3. 08:25 — it boots. ⇒ 🔴 **A `STARTUP` row exists, dated today — and it proves `4568385` booted.
It says NOTHING about `195436b`.**
⚠️ Anyone reading *"a STARTUP row dated today"* would promote **the wrong SHA** to known-good.
⇒ ⭐ That is this weekend's recurring shape: **an artifact proves X and is read as proving Y.**

---

## ⭐ L-2 — THE SEQUENCE, AS STEPS (⛔ order matters)

🔴 **VERIFY THE RECORDED SHA — ⛔ DO NOT RE-CAPTURE IT AT 08:20 AND HOPE.**
⚠️ Re-reading the SHA at *measurement* time is a **snapshot with a race in it**: anything that
mutated the tree between the boot and your query is invisible to it. ⭐ The deployed SHA was
already written down **before** the boot, by the deploy record — so the safe move is to confirm
that recorded value **still holds**, which is a **verifiable invariant with a falsifier**, ⛔ not a
hope.

```bash
# ── 1. THE START TIME of the attempt you are judging. Do this FIRST. ──────────
systemctl show trading-system -p ExecMainStartTimestamp --value

# ── 2. VERIFY the SHA the last deploy record already names (26-Aug: 75e637c) ──
#     ⛔ Do not capture a fresh one. Confirm the recorded one still holds.
RECORDED=75e637c300c1bead4aad3cf549e6dbe9318c6b1f     # from the last DEPLOY_* record
#     ⚠️ 26-Aug 19:2x: advanced 195436b -> 75e637c. ⭐ RE-READ IT FROM THE LATEST
#     DEPLOY_* RECORD EVERY TIME — this literal is an example, not a constant.
git ls-remote origin refs/heads/main | cut -f1        # must equal $RECORDED
export GIT_DIR=/home/ubuntu/trading-system.git \
       GIT_WORK_TREE=/home/ubuntu/systems/trading-system \
       GIT_INDEX_FILE=$(mktemp)                       # ⭐ temp index: does NOT touch the shared one
git read-tree HEAD && git update-index --refresh >/dev/null 2>&1
git rev-parse HEAD                                    # must equal $RECORDED
git diff-index --name-only HEAD --                    # must be EMPTY
unset GIT_INDEX_FILE
#  🔴 IF origin/main HAS MOVED, OR HEAD != RECORDED, OR THE DIFF IS NON-EMPTY:
#     the SHA<->boot association is BROKEN. ⛔ DO NOT ADVANCE THE TREE.

# ── 3. A STARTUP ROW WRITTEN *AFTER* THE STEP-1 TIME (⛔ NOT merely "today") ───
sqlite3 "file:/home/ubuntu/systems/trading-system/data_store/trading_system.db?mode=ro" \
  "SELECT timestamp, scenario, details FROM system_events
   WHERE event_type='STARTUP' ORDER BY timestamp DESC LIMIT 3;"
# compare the top timestamp against step 1. AFTER it => PROVEN for THIS attempt.
```

🔬 **Both drift forms were tested on the live VM, 23-Aug ~23:0x, and the check is NOT vacuous:**
the plain `GIT_DIR`/`GIT_WORK_TREE` form and the `GIT_INDEX_FILE` form both read **0**; a
deliberately planted one-line change made the check read **1**, and restoring it read **0** again
(blob re-verified against `HEAD`, zero residue). ⭐ The `GIT_INDEX_FILE` form is preferred because
it ⛔ does not write the bare repo's shared index.

4. **Confirm healthy by step 3** — ⛔ nothing else counts.
5. **Only then advance** RB-3's `checkout -f <SHA>`, RB-2's tree (`git rev-parse <SHA>^{tree}`)
   and the §1 invariant box's `TREE = …` to `$RECORDED`.
6. **Record SHA + boot timestamp + the row's timestamp TOGETHER**, and label the SHA association
   **💭 inferred — verified by step 2, ⛔ not carried by the row** (see M-1).

⚠️ ⭐ **The association stays 💭 INFERENCE either way — M-1 settles that. What step 2 adds is a
FALSIFIER**: it converts *"I hope nothing changed"* into *"nothing changed, and here is the check
that would have said so."*

---

## 🔴 L-3 — THE NEGATIVE CASE, EQUALLY EXPLICIT

> ⛔ **IF THE STARTUP EVIDENCE IS NOT ESTABLISHED FOR *THIS* BOOT, DO NOT ADVANCE THE TREE.**
> ⭐ **A stale-but-true target is safe. A fresh false one is not.**

⚠️ An ungated trigger would **launder a false green into the one fact the whole rollback rests
on** — and the next incident would roll back *to a broken state*, believing it known-good.

### ⛔ KEEP THE GATE NARROW — a `STARTUP` row proves the boot gate and **nothing more**

⛔ It does **not** prove the strategy loop ran · the market-data feed · broker connectivity · the
absence of a later crash-loop · or that any of the 20 deployed commits is individually correct.

| | means |
|---|---|
| *"systemd started it"* | the unit entered `active` — ⛔ says nothing about the app |
| *"PROVEN TO BOOT"* | 🔬 a `STARTUP` row written **after this attempt's start time** |
| *"the day traded correctly"* | ⛔ a different question entirely, ⛔ not this gate |

⚠️ **Observation, ⛔ not an item:** 🔬 **77 STARTUP vs 76 SHUTDOWN** ⇒ one boot in history never
wrote a SHUTDOWN. ⭐ Benign — ⛔ but the pairing is **not guaranteed**, which matters if anyone ever
builds a control on SHUTDOWN rows. ⛔ Recorded, ⛔ not chased.

⚠️ 🔴 **THIS REMAINS AN F11-SHAPED ABSENCE — nothing checks that a runbook's SHA matches reality —
now with TWO clocks, ⛔ not one.** ⭐ §0.1 is a written procedure, ⛔ **NOT a control**, and ⛔ it is
not authorisation to close the absence.


---

# §1 — THE ROLLBACK, WRITTEN DOWN

> # 🔴 THE TWO SHAs ARE DIFFERENT FACTS. MEASURE THEM SEPARATELY.
>
> ▎ **The rollback TREE comes from the last SHA PROVEN TO BOOT.**
> ▎ **The rollback COMMIT PARENT comes from the current `origin/main`.**
> ▎ **They are DIFFERENT FACTS and must be measured INDEPENDENTLY.**
>
> ⛔ **Never collapse them into one "rollback SHA".** They go stale on **different clocks**:
>
> | | value | goes stale on | how to get it |
> |---|---|---|---|
> | **PARENT** (RB-2 only) | current `origin/main` | 🔁 **every PUSH** | `git ls-remote origin refs/heads/main` — derived, ⛔ never pasted |
> | **TREE / RB-3 target** | last SHA **proven to boot** | 🔁 **every PROVEN BOOT** (⛔ not merely a started one — ⭐ §0.1 defines it: a `STARTUP` row written AFTER this attempt’s start time) | the `system_events` record — ⛔ NOT derivable from `origin/main` |
>
> 🔬 **23-Aug (superseded): PARENT = `195436b` · TREE = `4568385`.** They were different because
> nothing had booted since **Fri 21-Aug 08:15:25**.
>
> ⚠️ **SUPERSEDED 26-Aug 19:2x — RETAINED:** *"🟢 🔬 **24-Aug 10:45, CURRENT: TREE =
> `195436bb6323c981d68f313ad7c39858cc0a4653`** — advanced because the 24-Aug 08:15:20 boot is
> **PROVEN** (`STARTUP` row `3788` @ 08:15:33.134646, AFTER the start; V-2/V-3/V-4 all held)."*
>
> 🟢 🔬 **26-Aug 19:2x, CURRENT: TREE = `75e637c300c1bead4aad3cf549e6dbe9318c6b1f`** — advanced
> because the 26-Aug 08:15 boot is **PROVEN** (`STARTUP` row `3794` @ 08:15:29.559643, AFTER
> `ExecMainStartTimestamp` 08:15:18; V-2/V-3/V-4 all held; ⛔ not an earlier same-day row —
> the previous `STARTUP` is `3791` on 25-Aug).
> ⭐ **PARENT is still DERIVED at use time and is ⛔ NOT written here.**
> ⚠️ **Both values happen to read `75e637c` at this moment — ⛔ a coincidence of timing, ⛔ NOT an
> identity. Two facts, two clocks: PARENT goes stale on every PUSH, TREE on every PROVEN BOOT.**
> 🔴 **TONIGHT'S PUSH (if any) WILL MOVE `origin/main` AND THEREFORE THE PARENT — ⛔ IT WILL NOT
> MOVE THE TREE. The TREE moves only at tomorrow's 08:15 PROVEN boot.**
>
> 🔴 **THE TREE'S CLOCK TICKED — §0.1 FIRED, and the advance record at the top of this file is it.**

## RB-1 — 🔬 what the hook actually does

`deploy/hooks/post-receive` (the **live** hook; its own header records that it is
byte-identical to the armed one):

```bash
git --work-tree="$TARGET" --git-dir="$GIT_DIR" checkout -f "$BRANCH"
#   TARGET=/home/ubuntu/systems/trading-system   GIT_DIR=/home/ubuntu/trading-system.git
```
then a guarded crontab install. ⭐ **There is NO `systemctl` anywhere in the hook — the
push does NOT start, stop or restart the service.**

⇒ 🔴 **The deployed tree is just a `checkout -f` target, so the fastest rollback does not
involve `origin/main` at all.**

## RB-2 — the git path, ⭐ SYNTAX-TESTED (⛔ object created locally, never pushed)

⛔ `git revert` over a range can conflict. The deterministic form builds a commit whose
**tree is exactly `75e637c`** (⭐ the last SHA proven to boot — see the box above; **advanced
`4568385` → `195436b` on 24-Aug 10:45, then `195436b` → `75e637c` on 26-Aug 19:2x**), parented on
**whatever `origin/main` is AT THAT MOMENT**:

```bash
# ── A. MEASURE the remote tip (no side effects) ──────────────────────────────
REMOTE=$(git ls-remote origin refs/heads/main | cut -f1)

# ── B. OBTAIN it. ls-remote returns a SHA; it does NOT fetch the object, and
#      commit-tree cannot use a parent the local clone does not have. The TREE
#      SHA must be local too. Skipping this is discovered MID-INCIDENT.
git fetch origin
PARENT=$(git rev-parse origin/main)
test "$PARENT" = "$REMOTE" || echo "STOP: remote moved between A and B"
git cat-file -e "${PARENT}^{commit}" || echo "STOP: parent object not local"
git cat-file -e '75e637c^{tree}'     || echo "STOP: tree object not local"

# ── C. CONSTRUCT with that exact parent ──────────────────────────────────────
RB=$(git commit-tree $(git rev-parse '75e637c^{tree}') -p "$PARENT" -m "revert: roll the deployed tree back to 75e637c")
#  ⚠️ 24-Aug 10:45: TREE ADVANCED 4568385 -> 195436b (the 08:15:20 boot is PROVEN).
#  ⚠️ 26-Aug 19:2x: TREE ADVANCED 195436b -> 75e637c (the 08:15 boot is PROVEN,
#     STARTUP row 3794 @ 08:15:29.559643 AFTER ExecMainStartTimestamp 08:15:18).
#  🔴 WHETHER THIS RB-2 IS A NO-OP DEPENDS ON WHAT HAS BEEN PUSHED SINCE 75e637c:
#     if nothing has, `git diff --stat <RB> HEAD` is EMPTY and there is nothing to
#     roll back. RB-2 becomes meaningful only once a LATER push exists.
#     ⭐ MEASURE IT — do not assume either way.
#     ⛔ Do not run it "to be safe" — check first, and prefer RB-3 in the window.

# ── D. PUSH that exact generated commit, immediately ─────────────────────────
git push origin "$RB":refs/heads/main
```

## 🔴 E — IF THE REMOTE ADVANCED IN BETWEEN, **STOP**

The push will be **rejected** (non-fast-forward). ⭐ That rejection is the control working.
⛔ **No `--force`** · ⛔ **no amend** · ⛔ **no silent rebuild against the changed tip** ·
⛔ **no blind `git push origin main`**.
⇒ ⭐ **A rejection is preferable to a rollback built on an unreviewed parent.** Go back to A,
re-measure, and look at *what* advanced before rebuilding — something else pushed during an
incident is itself information.

🔬 **Tested 23-Aug against the then-parent `742d9da`:** tree == `4568385`'s tree **YES** ·
ancestor **YES** (⇒ fast-forward, ⛔ no `--force`) · `git diff --stat <RB> 4568385` **empty**.
⚠️ **The SHAPE is what was tested; the PARENT is re-measured every time by step A.**
⚠️ It was hardcoded `-p 742d9da` until 23-Aug 22:5x — 🔴 three pushes later that would **no
longer fast-forward**, so this command would have been **rejected at 08:20**, during an
incident, with the book about to open. ⛔ Do not paste `195436b` in its place either: **derive it.**

⏱️ ~30–60 s **if he is at the PC**, plus network and the push discipline.

## RB-3 — 🔴 WHICH IS FASTER AT 08:20 · **RECOMMENDED: the VM tree checkout**

```bash
# ON THE VM — he is ALREADY at an ssh prompt (see §2: the morning script opens one)
sudo systemctl stop trading-system
git --work-tree=/home/ubuntu/systems/trading-system \
    --git-dir=/home/ubuntu/trading-system.git checkout -f 75e637c
#   ^^^^^^^ 75e637c — ADVANCED FROM 195436b ON 26-Aug 19:2x, and re-derived, not
#   left over: the 26-Aug 08:15 boot is PROVEN (STARTUP row 3794 @ 08:15:29.559643,
#   AFTER ExecMainStartTimestamp 08:15:18; NOT an earlier same-day row - the previous
#   STARTUP is 3791 on 25-Aug; V-2/V-3/V-4 all held). => 75e637c IS the last SHA
#   PROVEN TO BOOT.
#   🔴 THE OLD 195436b IS NOW WRONG: it PREDATES THE EOD LIFECYCLE UNIT, so pasting
#   it back would UNDO the very code that just proved it boots. ⛔ Do not paste it back.
#   🔴 THE OLDER 4568385 IS MORE WRONG STILL. ⛔ Do not paste that back either.
#   ⚠️ AT THE MOMENT OF THIS EDIT 75e637c == origin/main, so this checkout is a NO-OP.
#   That is the correct state, not a failure - RB-3 becomes meaningful again only
#   after a LATER push. ⭐ MEASURE origin/main at incident time; do not assume.
#   ⭐ HISTORICAL (23-Aug, kept): the target then was 4568385, because
#   trading-system had last started Fri 21-Aug 08:15:25 with origin/main = 4568385,
#   and 742d9da / 2f67bb8 / 195436b had never executed.
#   ⭐ HISTORICAL (24-Aug 10:45 -> 26-Aug 19:2x, kept): the target was 195436b.
#   🔁 RE-DERIVE THIS AFTER EVERY PROVEN BOOT — the TREE's clock, not the push clock.
# then re-run zerodha_morning.ps1 on the PC (or wait ~30s for token_watcher)
```

⏱️ **~10 seconds.** ⭐ **And it starts from exactly where the failure leaves him** — the
morning script's failure branch runs `Start-Process ssh trading-vm`, so a VM shell is
already open in front of him.

| | RB-3 (VM checkout) | RB-2 (revert commit) |
|---|---|---|
| time | **~10 s** | ~30–60 s + network |
| where he must be | ⭐ **already there** (auto-opened ssh) | back at the PC |
| touches `origin/main` | ⛔ no | ✅ yes |
| leaves divergence | ⚠️ **yes, WHENEVER THE TARGET IS BEHIND `origin/main`** — the tree goes to the last proven-boot SHA while `origin/main` stays at the newer tip. 🟢 **24-Aug: target == `origin/main` == `195436b`, so RB-3 leaves NO divergence today** — ⚠️ that changes the instant anything is pushed | ⛔ none |

⇒ ⭐ **Use RB-3 in the window; then reconcile with RB-2 — 🔴 THE **SAME DAY**, ⛔ NOT
*"at leisure"* (corrected in ADDENDUM §3). While diverged, the hook's `checkout -f main`
means the NEXT push of anything SILENTLY RESTORES what was rolled back.**
🔴 **AND A RULE THIS PROCEDURE JUST TAUGHT (23-Aug): A ROLLBACK PROCEDURE CARRYING A
STALE SHA IS A ROLLBACK THAT ROLLS BACK THE WRONG THING.** This file's header named
`origin/main = 4568385 / push HELD` **two deploys after that stopped being true**, and
⛔ nothing checks that a runbook's SHA still matches reality — it was found **by accident**
while adding an unrelated paragraph. ⭐ The target above survives re-derivation, ⚠️ but that
is **luck, ⛔ not a mechanism**: re-derive it after EVERY push, in the same breath as the push.

⚠️ RB-3 leaves the bare repo's `HEAD` detached at `4568385`; the **next** push self-heals
it, because the hook's own `checkout -f "$BRANCH"` re-attaches to `main`.

## RB-4 — 🔬 what rollback does to the CONFIG: **nothing can mismatch**

`config/system_config.yaml` is **tracked**, and `4568385..742d9da` changes it (**49 lines**).
`checkout -f` restores **code and tracked config from the SAME TREE** — ⭐ the property is
**VERSION ALIGNMENT**, ⛔ NOT filesystem atomicity/transactionality (a `checkout -f` is not a
transaction and can be interrupted). The conclusion below is unaffected.
⇒ ⭐ **The seven required keys and the schema that requires them move together in both
directions. A code/config version skew is not reachable via either path.**
⚠️ `checkout -f` touches **tracked files only** — `data_store/`, `.env`, `logs/` and the
session token are gitignored and are **not** disturbed. ⚠️ The one real risk is a
**hand-edited tracked file on the VM**, which is exactly what **V-7** (§3) exists to catch.

---

# §2 — 🔴 THE 08:15 BOOT IS **NOT UNATTENDED**. THE RECORD WAS WRONG.

⚠️ Three parties have called it *"unattended"* for two days. 🔬 **Measured today — it is
operator-initiated AND operator-verified.**

**`deploy/zerodha_morning.ps1`** — its own header: *"Run once each trading morning from
the project root."* Steps, verbatim from the file:

1. check the token exists / is not expired · 2. if expired → `zerodha_login.py` (**opens a
browser for TOTP**) · 3. **`scp` the token to the VM** · 4. **wait 15 s** · 5.
**`ssh trading-vm "systemctl is-active trading-system"`** · 6. report.

🔬 **`deploy/token_watcher.sh:14`: *"All start attempts also require a fresh token for
today."*** ⇒ **the service cannot start until Rama SCPs the token.**
🔬 `docs/first_day_live_runbook.md:31`: *"**08:00** — Run `zerodha_morning.bat` on PC,
token generated"*. 🔬 **No `schtasks` / `Register-ScheduledTask` registers it anywhere** —
the only Task Scheduler mentions are other, un-actioned items.

## ⇒ HE IS PRESENT, AND THE SCRIPT TELLS HIM — ~15 s after he sends the token

```powershell
$ServiceStatus = ssh trading-vm "systemctl is-active trading-system"
if ($ServiceStatus -ne "active") {
    "ERROR: trading-system service is NOT active on VM."   # red
    "       Check VM logs: ssh trading-vm `"journalctl -u trading-system -n 50`""
    Start-Process ssh -ArgumentList "trading-vm"           # opens a shell for him
    exit 1
}
```

⭐ **The risk framing improves materially and the record is corrected:** a config rejection
is caught by a human at his desk **within ~15 seconds**, with a VM shell already open —
⛔ not by a silent 08:15 machine.

## ⚠️ TWO THINGS THIS DOES **NOT** SOFTEN

1. **It is still an argument for OPS ② going first, just a different one.** The PS1 tests
   `-ne "active"`, and a crash-looping unit reports `activating` — so it *would* be caught.
   ⚠️ **But the 15 s sample lands inside a ~11 s restart cycle.** 🔴 **CORRECTED IN THE
   ADDENDUM §1 — it is ~9 %, ⛔ NOT a coin-flip; that word is withdrawn.** With OPS ② the unit sits in **`failed`**
   deterministically ⇒ the check is reliable instead of lucky. ⭐ **And nothing else in the
   day would ever tell him** — `token_watcher` reads `activating` as *"running — nothing to
   do"* and deletes the alert flags on every poll.
2. **"Be awake for it" still stands, but it is now a normal morning, not a vigil.** ⚠️ The
   exposure that remains is if he runs the script and **walks away** before step 5 prints.

---

# §3 — V-7 · ⚠️ `checkout -f` OVERWRITES — RE-MEASURE AT GATE TIME

🔬 22-Aug: the live `config/system_config.yaml` was byte-identical to `4568385`.
⛔ **That measurement is a day old and must not be carried.** Anything that edited a
**tracked** file on the VM since then is **silently clobbered** by the push.

> 🟢 **24-Aug 10:45 — V-7's COMPARAND IS THE *CURRENTLY DEPLOYED* SHA, so it advances too:
> `4568385` → `195436b`.** ⚠️ ⛔ **It is NOT the rollback target and must not be confused with
> it** — V-7 asks *"is the VM tree still exactly what was last deployed, or has someone
> hand-edited a tracked file that the next push would clobber?"*
> 🔬 **Measured today with the whole-tree form: drift = 0** ⇒ the deployed tree is exactly
> `195436b`; nothing on the VM would be clobbered. ⭐ **Re-measure at the NEXT gate — this
> reading expires the moment anyone touches the box.**

**V-7, run on the VM immediately before the push:**

```bash
cd /home/ubuntu/systems/trading-system
git --git-dir=/home/ubuntu/trading-system.git show 75e637c:config/system_config.yaml \
  | md5sum
#   ⚠️ 26-Aug 19:2x: comparand advanced 195436b -> 75e637c (the currently deployed SHA).
md5sum config/system_config.yaml
# the two md5s MUST match. If they differ by one byte -> STOP, report, do not push.
```

⭐ **Widen it to every tracked file, not just the config** — one command, and it states its
own width:

```bash
cd /home/ubuntu/systems/trading-system
GIT_INDEX_FILE=/tmp/v7.idx git --git-dir=/home/ubuntu/trading-system.git \
  --work-tree=. read-tree 75e637c
GIT_INDEX_FILE=/tmp/v7.idx git --git-dir=/home/ubuntu/trading-system.git \
  --work-tree=. update-index --refresh >/dev/null
GIT_INDEX_FILE=/tmp/v7.idx git --git-dir=/home/ubuntu/trading-system.git \
  --work-tree=. diff-index --name-status 75e637c
rm -f /tmp/v7.idx
# empty  => the deployed tree is exactly 75e637c; nothing on the VM will be clobbered
#   ⚠️ 24-Aug 10:45: comparand advanced 4568385 -> 195436b (the then-deployed SHA).
#   ⚠️ 26-Aug 19:2x: comparand advanced 195436b -> 75e637c (the currently deployed SHA).
#   ⭐ PREFER GIT_INDEX_FILE=$(mktemp) over a fixed /tmp/v7.idx — a fixed path collides
#     between two operators and survives a crash; either way it must NOT be the shared index.
```
🔴 **`update-index --refresh` is REQUIRED** — without it this recipe reports **1,309
phantom files**.
🔬 **Non-vacuity re-proven 24-Aug 10:44 WITHOUT ANY WRITE:** the same recipe run against
`4568385` (instead of the deployed SHA) reports **29 paths** — so the mechanism can report
non-empty, and the **0** it returns against `195436b` is a real zero. ⭐ *A green check is
evidence only if it could have been red.*

---

# §4 — 👤 MONDAY RUNBOOK — 🔴 **SUPERSEDED**

⛔ **DO NOT USE THIS VERSION.** It predates the addendum and is missing the RED-is-not-
proof-of-failure step and the reconciliation step. ⭐ **Use the six-line runbook at the
END of this document.** Kept only so the supersession is visible.

<details><summary>superseded four-line version</summary>

> **1 · GOOD.** `zerodha_morning.ps1` ends green: `DONE - System Ready` / `Service: ACTIVE`.
> Then a **Telegram SYSTEM START** alert. ~15 s after the token is sent.
>
> **2 · BAD.** Red: `ERROR: trading-system service is NOT active on VM.` — and an **SSH
> window opens by itself**. ⚠️ It will **NOT** name the bad config key (the boot-path alert
> wiring is still queued).
>
> **3 · THE KEY NAME** — in that SSH window:
> `grep -i "Config load failed" ~/systems/trading-system/logs/system_$(date +%F).log | tail -5`
> (⛔ not `journalctl` — it carries no INFO/CRITICAL app lines.)
>
> **4 · ROLLBACK** — same window, paste:
> 🔴 **⛔ DO NOT PASTE THE LINE BELOW — ITS SHA IS DEAD.** Kept verbatim only so the supersession
> is visible. As of **24-Aug 10:45** the target is **`195436b`**; `4568385` would revert
> **20 commits** when the right answer is ZERO. ⭐ **Use the FINAL runbook at the end of this file.**
> `sudo systemctl stop trading-system && git --work-tree=/home/ubuntu/systems/trading-system --git-dir=/home/ubuntu/trading-system.git checkout -f 4568385`
> then re-run `zerodha_morning.ps1`. ⛔ **REVERT — ⛔ never tune — if a VALID config is
> rejected.**

---

# STATUS

⛔ **NOT PUSHED · NOT DEPLOYED · `origin/main` = `45683859a0a05f466189ac5bc98f9a9f089f98d3`.**
Push HELD on 👤 Rama's confirmation that **OPS ②** landed with its three evidences
(DETECTION · ENFORCEMENT · GOVERNANCE — ⛔ never collapsed).
Gate-time sequence: **V-1 · V-2 · V-5 · V-6 · V-7** → then the full 40-char refspec.

</details>

---

# ADDENDUM — four additions, 23-Aug evening

## §1 — 🔬 THE PROBABILITY, MEASURED. ⛔ NOT A COIN-FLIP.

**Method:** time the work `main.py` does before it can `return 5` at `:1862`.
🔬 Measured on this PC, three runs: `import main` **0.358 s** + `load_all(config)`
**0.025 s** = **0.383 s**. ⚠️ That is a **FLOOR** — it excludes interpreter launch,
argparse, logger setup and the instance lock, and the VM is slower hardware.

| run time `T` | `P(sample lands on "active") = T / (T + RestartSec)` |
|---|---|
| 0.4 s (measured floor) | **≈ 3.8 %** |
| 1.0 s (realistic) | **≈ 9.1 %** |
| 1.5 s | **≈ 13 %** |

⇒ ⭐ **~9 %, ⛔ not 50 %. My "coin-flip" was wrong and is withdrawn.**
⚠️ **But it is still a real hole, and worse than 9 % sounds**, because a false green is
the **SILENT** direction: the script prints `DONE - System Ready` over a crash-looping
unit and ⛔ nothing later contradicts it.

## §2 — OPS ② DOES THREE THINGS. ⚠️ THE RECORD STATED ONLY THE THIRD.

1. ⭐ **It makes the morning check DETERMINISTIC** — the unit sits in `failed`, so
   `systemctl is-active` cannot return a lucky `active`. ⛔ No ~9 % false green.
2. 🔴 **IT CLOSES THE WALK-AWAY CASE — the one that actually matters.** If Rama sends the
   token and steps away before step 5 prints, **nothing today would EVER tell him**:
   `token_watcher` reads `activating` as *"running — nothing to do"* and wipes the
   once-per-day alert flags on every 30 s poll. ⭐ With OPS ② the unit reaches `failed`,
   the crash branch runs, and a **Telegram arrives in ~2 minutes**.
3. It bounds the unbounded restart loop — the original reason, and the least of the three.

## §3 — 🔴 RECONCILIATION IS **STEP 6**, ⛔ NOT "AT LEISURE"

⚠️ After **RB-3** the deployed tree is `4568385` while `origin/main` says **`195436b`**.
⇒ ⛔ **any reader — including a future session — would believe `195436b` is deployed.**
That is precisely the class this campaign keeps finding: **a record reporting a state that
is not the state.** 📄 My *"at leisure"* was wrong: *"at leisure"* is how the 08-Aug
allocation model stayed invisible for fifteen days.

⇒ ⭐ **After ANY RB-3, one of these happens the SAME DAY. ⛔ Neither is optional:**
**(a)** run **RB-2** so `origin/main` matches the tree, **or**
**(b)** write the divergence into the ledger **immediately**, with its reason.

### 🔴 THE SECOND-ORDER HAZARD — 🔬 VERIFIED IN THE HOOK

`deploy/hooks/post-receive` runs `git … checkout -f "$BRANCH"` — **`main`**, ⛔ not the
pushed SHA. ⇒ **while diverged, the NEXT push of ANYTHING — even an unrelated commit —
re-checkouts `main` and SILENTLY RESTORES the code he rolled back.**
⭐ **A rollback that is not reconciled undoes itself at the next deploy.**

## §4 — 🔴 THE CONFIG-HASH ALERT: **IT DOES NOT EXIST.** THE WORRY INVERTS.

🔬 Traced end to end:
- `utils/startup_checks.py:1579-1580` — `if config_hash_result.changed:
  warnings.append("config_hash_changed")`
- `check_config_hash` logs at **INFO** only:
  `"check_config_hash: %d file(s) changed: %s"` (`:623-626`)
- `StartupReport.warnings` is consumed at **exactly one** site repo-wide —
  `main.py:2141` — **and that line is inside `if args.dry_run:`**

⇒ 🔴 **In a normal boot the warning list is computed and DISCARDED.** No Telegram, no
WARNING-level log, not even a print. The only trace is **one INFO line** in
`logs/system_<date>.log` — ⚠️ and `journalctl` carries no INFO lines, so it is not there
either.

⇒ ⭐ **Rama will NOT get a config-change Telegram on Monday**, so no "expect this" line is
needed. ⚠️ **But the finding is worse in the other direction, and is recorded as such:** a
config change on the deployed path is **effectively invisible to the operator** — ⭐ the
`F11` shape (a control that runs and reports nothing). ⛔ Not fixed, ⛔ not in scope.
⚠️ `market_holiday` rides the same discarded list.

## 🔴 EN ROUTE — THE 15 s WAIT vs THE 30 s POLL. **THIS CHANGES HOW MONDAY'S RED IS READ.**

🔬 `deploy/token_watcher.sh:22` `SLEEP_SEC=30` (the poll loop, `sleep "$this_sleep"`).
🔬 `deploy/zerodha_morning.ps1:77` `Start-Sleep -Seconds 15`.

⇒ time from SCP to the watcher noticing is **uniform(0, 30) s, mean 15 s**, then the boot
itself. **The script samples at T+15 s.**
⇒ 💭 **on a PERFECTLY GOOD config, step 5 can report RED simply because the service has
not started yet** — roughly half the time by this arithmetic.

🔴 **CONSEQUENCE FOR MONDAY, and it must be in the runbook:** if Rama sees RED, the **first**
hypothesis is **the poll timing**, ⛔ not F1 rejecting his config. ⚠️ Without that line he
could roll back a perfectly good deploy on a false alarm. ⭐ **Distinguish them by the log,
⛔ never by the red line alone.**

---

# 👤 THE RUNBOOK — FINAL, PHONE-READABLE

> **1 · GOOD.** Green `DONE - System Ready` / `Service: ACTIVE`, ~15 s after the token,
> then a Telegram start alert.
>
> **2 · RED IS NOT PROOF OF FAILURE.** The watcher polls every **30 s**; the script waits
> only **15 s**. A good boot can show red simply because it had not started yet.
> **Wait 30 s and re-run `zerodha_morning.ps1` FIRST.** ⛔ Do not roll back on the red line
> alone.
>
> **3 · STILL RED ⇒ CHECK THE LOG.** An SSH window is already open. Run:
> `grep -i "Config load failed" ~/systems/trading-system/logs/system_$(date +%F).log | tail -5`
> ⛔ Not `journalctl` — it carries no INFO/CRITICAL app lines.
> ⚠️ **A matching entry CONFIRMS a config rejection. 🔴 NO matching entry does ⛔ NOT
> prove it was the timing** — it proves only that THAT signature is absent. Investigate the
> remaining startup failure. ⭐ Converting the absence of one log line into a diagnosis is the
> poisoned-gate error pointed the other way.
>
> **4 · THE ALERT WILL NOT NAME THE KEY.** The boot-path alert wiring is still queued.
> ⚠️ You will also get **nothing at all** about the config *hash* changing — that is
> expected, ⛔ not a fault.
>
> **5 · ROLLBACK** (only after step 3 confirms a real rejection):
> `sudo systemctl stop trading-system && git --work-tree=/home/ubuntu/systems/trading-system --git-dir=/home/ubuntu/trading-system.git checkout -f 75e637c`
> then re-run `zerodha_morning.ps1`. ⛔ **REVERT — ⛔ never tune — if a VALID config is
> rejected.**
>
> 🟢 **THE `75e637c` ABOVE IS CURRENT AS OF 26-Aug 19:2x — ADVANCED FROM `195436b` BECAUSE THE
> 26-Aug 08:15 BOOT IS *PROVEN*** (`STARTUP` row `3794` @ `08:15:29.559643`, AFTER
> `ExecMainStartTimestamp` `08:15:18`; ⛔ not an earlier same-day row — the previous is `3791` on
> 25-Aug; V-2/V-3/V-4 all held). 📄 `docs/audit/BOOT_PROOF_75e637c_26-Aug-2026.md`.
> ⚠️ **SUPERSEDED, RETAINED:** *"🟢 THE `195436b` ABOVE IS CURRENT AS OF 24-Aug 10:45 — ADVANCED
> FROM `4568385` BECAUSE THE 24-Aug 08:15:20 BOOT IS PROVEN (`STARTUP` row `3788` @
> `08:15:33.134646`). 📄 `docs/audit/BOOT_PROVEN_24-Aug-2026.md`."*
> 🔴 **⛔ DO NOT PASTE `195436b` — IT PREDATES THE EOD LIFECYCLE UNIT and would undo the very code
> that just proved it boots. ⛔ DO NOT PASTE `4568385` — worse still.**
> ⚠️ 🔬 **AT THE MOMENT OF THIS EDIT THIS COMMAND IS A NO-OP:** `75e637c` is already the deployed
> tree, so there is nothing to roll back. ⭐ That is the correct state, ⛔ not a failure. It becomes
> a real rollback again only after a LATER push — ⭐ and tonight's push is exactly such a push.
>
> ⭐ **THE RULE, ⛔ NOT THE VALUE, IS WHAT TO REMEMBER:** this SHA is *the last SHA **PROVEN TO
> BOOT***, ⛔ **NOT "the previous commit"** — and it moves on **every PROVEN BOOT** (step 5b),
> ⛔ never on a push. **RE-DERIVE IT IN THE SAME BREATH AS EACH PROVEN BOOT.**
> 🔴 **IF A BOOT IS NOT PROVEN, DO NOT ADVANCE IT** — a stale-but-true target is safe, ⛔ a fresh
> false one is not.
>
>
> **5b · WAS THE BOOT *PROVEN*?** (⭐ needed before §0.1 lets you advance the rollback target)
> `systemctl show trading-system -p ExecMainStartTimestamp --value`   ← the attempt's start
> `sqlite3 "file:/home/ubuntu/systems/trading-system/data_store/trading_system.db?mode=ro" "SELECT timestamp FROM system_events WHERE event_type='STARTUP' ORDER BY timestamp DESC LIMIT 1;"`
> ⭐ A row **AFTER** that start time = PROVEN. ⛔ An earlier same-day row is NOT.
> 🔴 **`no such table: system_events` MEANS YOU USED THE WRONG DATABASE — ⛔ NOT a failed boot.**
> ⚠️ Live DB is **`data_store/`**; `data/trading_system.db` is a **0-byte decoy**.
>
> **6 · AFTER ANY ROLLBACK — SAME DAY, ⛔ NOT OPTIONAL.** Either push the RB-2 revert
> commit, or write the divergence into the ledger. ⚠️ **Otherwise the next push of anything
> silently restores exactly what you rolled back.**

---

# 🔴 NEW ITEM · **THE MORNING CHECK CRIES WOLF ON ~HALF OF ALL HEALTHY DEPLOYS**

🏷️ `OPENED · ⛔ NOT DESIGNED · ⛔ NOT FIXED · 👤 RAMA'S CHOICE.`

**The two measurements, and they are the whole item:**

| 🔬 | value | site |
|---|---|---|
| watcher poll interval | **30 s** | `deploy/token_watcher.sh:22` `SLEEP_SEC="${SLEEP_SEC:-30}"` |
| morning script wait | **15 s** | `deploy/zerodha_morning.ps1:77` `Start-Sleep -Seconds 15` |

Time from `scp` to the watcher noticing is **uniform(0, 30) s**; the script samples once at
**T+15 s** and treats anything `-ne "active"` as a hard failure (red + auto-opened ssh +
`exit 1`). ⇒ 💭 **roughly half of all healthy mornings report RED.**

🔴 **WHY IT IS AN ITEM AND NOT A NUISANCE:** an operator who sees red on good mornings
**learns to ignore red**. ⭐ That is an alert-fatigue generator in its own right — the same
class as **NI-14** (a threshold that always fires). ⚠️ It nearly cost a rollback of a
healthy deploy: without the runbook's step 2, the first red on Monday reads as *"F1
rejected my config."*

**Candidate fixes, ⛔ neither chosen, ⛔ neither applied:**
(a) lengthen the script's wait past the poll interval (>30 s, or poll-and-retry until a
deadline); (b) shorten `SLEEP_SEC`. ⚠️ **(b) touches the boot path and would change restart
pacing** — ⛔ not a free knob. ⭐ (a) is confined to the PC script and changes nothing on the
VM. 👤 **Rama's call.**

---

# 🔴 NOTE ON F2 — **THE RE-ANCHOR IS NOW DUE. THIS SECTION WAS INVERTED.**

⚠️ **CORRECTED 23-Aug ~22:4x.** Everything this section previously asserted is now false. It is
recorded rather than quietly deleted, because *how* it went stale is the lesson.

📄 It read: *"the trigger has not fired: the push did NOT happen (OPS ② is not in force)
⇒ `origin/main` is still `4568385` … ✅ F2's line references remain VALID right now."*

| 📄 it said | 🔬 measured |
|---|---|
| OPS ② is not in force | ✅ **IN FORCE** — `RestartPreventExitStatus=3 4 5`, re-confirmed 24-Aug 10:45. 🔴 **BUT THE TIME WAS WRONG: it landed 23-Aug ~13:18, ⛔ NOT "~19:0x" — see the correction below** |
| the push did NOT happen | 🔴 **THREE pushes happened** — `742d9da` 13:22 · `2f67bb8` 21:43 · `195436b` **22:58** (⚠️ the third was recorded as "22:2x" — 🔬 corrected below) |
| `origin/main` is still `4568385` | 🔴 `origin/main` = **`195436bb6323c981d68f313ad7c39858cc0a4653`** |
| F2's line references remain VALID | 🔴 **EXPIRED** |

### 🔴 CORRECTION, 24-Aug 10:45 — **OPS ② LANDED AT 13:18, ⛔ NOT ~19:0x. THE ORDERING INVERTS.**

🔬 **Three independent measurements, all 13:18:** the unit-file mtime
(`/etc/systemd/system/trading-system.service`, **Aug 23 13:18**, md5 `a8ea94ba…`) · the journal
(`Aug 23 13:18:23 sudo[177744] … COMMAND=/usr/bin/systemctl daemon-reload` → `Reloading finished in
268 ms` — ⭐ **the ONLY reload on 23-Aug**) · the integrity control's own CRITICAL sentinel
`20260823_131831_2b42fc55` at **13:18:31.438** (*"trading-system.service content changed
(sha256 `7ad85a562ae2`→`faa2cac56bc2`)"*).
⇒ 🔬 Write, `daemon-reload` and alert **inside ~8 seconds at 13:18**; there is no later reload, so
*"landed ~19:0x"* cannot be rescued as *"edited early, took effect later"*.
🔴 **⇒ OPS ② was in force 4 minutes BEFORE the FIRST push, ⛔ not after the last one.** 👤 Rama had
exit-5 protection for **all three** pushes. ⭐ The substance was always right; the **ordering** this
table implied was inverted.
🔬 **And it was applied by editing the MAIN unit file, ⛔ NOT the prepared drop-in** — `DropInPaths`
holds only `watchman.conf`. ⭐ That was the *lucky* choice: the main unit file **is** integrity-watched
at CRITICAL and so it alerted; the drop-in directory is **not** watched, so the prepared
`exit5-no-restart.conf` would have landed **silently**.
📄 `docs/audit/BOOT_PROVEN_24-Aug-2026.md` §7 N-1 / N-2.

## ⇒ 🔴 F2's LINE REFERENCES ARE DEAD. RE-ANCHOR BEFORE ANY F2 DESIGN WORK USES ONE.

`F2_SEGMENT_CAPITAL_INVENTORY_22-Aug-2026.md` measured at **`4568385`**. Every file it depends on
has been touched since, repeatedly:

* **F1 + NI-1/2/4** — `position_sizer.py` · `config_loader.py` · `config_auditor.py` ·
  `system_config.yaml`
* **NI-5 · NI-9 · NI-17 · NI-18** — `position_sizer.py`, `risk_engine.py`, `config_auditor.py` again
* **NI-14** — `aggregator.py` · `severity.py`
* **NI-15 L1** — `security_monitor.py`

⭐ **`M3` stands and now BINDS: re-anchor F2's inventory at `195436b` before any F2 design work
uses one of its line numbers.** ⛔ Do not carry a single line number across that boundary.

---

# 🔴 THE CLASS THIS SECTION IS AN INSTANCE OF

> **A DOCUMENT CORRECTED IN THREE PLACES AND STALE IN A FOURTH IS HOW A CORRECTED DOCUMENT EARNS
> FALSE TRUST.**

⚠️ At 21:5x the header, §0 and RB-3's comment were corrected — and this section, at the very
bottom of the same file, was not. ⭐ A reader seeing three fresh corrections would reasonably
assume the whole file had been swept.
⇒ ⛔ **Correcting a document is a SWEEP, never a patch of the section you happened to be looking
at.**

⚠️ And it was the worst section to leave: it told whoever starts F2 that **re-anchoring was not
due** — the single instruction that would let dead line numbers into a capital-path design.

⚠️ **The same sweep found a second live defect the named section did not cover:** RB-2's revert
recipe was hardcoded `-p 742d9da`, which after three pushes would **no longer fast-forward** —
the emergency push would have been rejected at 08:20. The parent is now derived by measurement.

🔴 **Nothing checks that a document's SHA claims match reality.** That is an F11-shaped
**absence** — ⛔ there is no control there to ignore, so it is ⛔ **not** an F14 instance.
👤 Open, ⛔ not built.

---

🏷️ **STATE AT THE FOOT OF THIS FILE — `origin/main` = `195436bb6323c981d68f313ad7c39858cc0a4653`,
DEPLOYED, ⛔ NOT `VERIFIED LIVE`. Monday 24-Aug 08:15 exercises 20 commits in one run.**
