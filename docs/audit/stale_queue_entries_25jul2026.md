# §D — Two stale queue entries, settled

**25-Jul-2026. READ-ONLY. Both verified against source/production before being closed.**

---

## D1 — The 403 signal-count instrumentation item. **CLOSED. No build.**

**Verified against `docs/audit/refused_posts_403_investigation_23jul2026.md` rather than
taken on trust**, because the queue entry and the investigation disagreed.

The investigation's own "what done means" section records all four outcomes as satisfied:

- refusals established **correct**, with evidence (code + block structure + `system_events`);
- source **characterised** (Chartink 99.99%, measured, not assumed);
- **403-vs-401 choice checked** — correct;
- ⭐ **"Counting options *sized* — including the outcome that NO CODE CHANGE IS NEEDED (the
  count already exists at POST level; inner tally is estimable read-only)."**

⇒ **There is no hidden build item behind this line.** The POST-level count already exists;
the inner per-signal tally remains a **read-only query** if anyone ever wants it, not
instrumentation to be added. The framing correction from that report also stands and is
worth keeping attached: this is **not** "the one genuine blind spot", and roughly half the
refusals had been mis-attributed to the entry window.

**Action: struck from §G of the register (recorded there as correction C-43). Nothing to
build, nothing left open.**

---

## D2 — The four unmatched `predeploy-*` backups. **VERDICT: ACCIDENT, not a convention.**

### The files (measured on the VM tonight)

```
2026-07-17 02:00   357,146,624   predeploy-batch2-trading_system-2026-07-17_020026.db
2026-07-17 02:00    24,752,128   predeploy-batch2-analytics-2026-07-17_020026.db
2026-07-17 02:39   357,146,624   predeploy-sweep-trading_system-2026-07-17_023940.db
2026-07-17 02:39    24,752,128   predeploy-sweep-analytics-2026-07-17_023940.db
                   ───────────
                   ~764 MB total, all from ONE date, in two clusters 39 minutes apart
```

### Why they match nothing

`scripts/backup_retention.py:46` anchors on **`("pre_*.db", 20)`** — `pre` + **underscore**.
These are `predeploy-…` — `pre` + `deploy-`. **`pre_*` does not match `predeploy-*`.** They
fall outside every pattern and, by construction, always will.

### ⭐ The deciding evidence — deliberate or accident?

The question matters because the two answers have opposite implications. It is settled by
asking whether anything *generates* the name:

```
grep -rn "predeploy" --include=*.py --include=*.sh --include=*.yaml .   → NO code match
git log -S "predeploy" --all                                            → only TWO DOCS commits
```

**Nothing in the repo has ever created a `predeploy-*` file.** The only mention is a
narrative one: `docs/audit/sweep_done_17jul2026.md:32` — *"Fresh VM backup taken pre-deploy
(`predeploy-batch2-*`, `integrity_check: ok` on both DBs)"*.

⇒ **They were taken BY HAND during the 17-Jul sweep and named ad hoc.** Two runs, two
labels ("batch2", "sweep"), one night, never repeated. A convention recurs; this did not.

### What follows from it — and it is the opposite of the optimistic reading

**They are not protected by design. They are unreaped by accident.** That is the same
failure shape already recorded for the retention anchor: *"there is NO ANCHOR CONCEPT in the
retention logic — protection is RECENCY ONLY; the anchor was safe because it sits OUTSIDE
the script's scope, not because the script protects it."* This is a second instance of
protection-by-falling-outside-scope.

⚠️ **The trap for whoever tidies this up: renaming them to `pre_*` is a DELETE in disguise.**
Under `("pre_*.db", 20)` retention is keep-newest-20, and these are 9 days old — renaming
them in would make them among the oldest and therefore first out on the next nightly run.
*Do not "fix the typo" as a tidy-up.*

### Options (recorded, NOT acted — deleting backups is Rama's call)

| | | |
|---|---|---|
| **A. Delete them** | they are 17-Jul point-in-time copies, superseded by newer `pre_*` backups; frees ~764 MB | the honest default if no one wants a 17-Jul anchor |
| **B. Keep them, and WRITE IT DOWN** | add an explicit protected pattern (e.g. `("keep_*.db", None)`) to `backup_retention.py` and rename them into it | ⭐ the point of the exercise: **if a permanently-protected class is wanted, it must be an explicit rule in the script, not a one-character difference from `pre_`** |
| **C. Leave as-is** | costs ~764 MB indefinitely and leaves a class of file that silently escapes retention | the status quo, and the reason this keeps reappearing on the queue |

**No recommendation.** But B is the only option that makes the protection real rather than
incidental, and A is the only one that actually clears the queue entry.
