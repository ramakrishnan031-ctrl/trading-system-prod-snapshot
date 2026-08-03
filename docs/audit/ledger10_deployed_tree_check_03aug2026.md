# LEDGER #10 — DEPLOYED-TREE-vs-HEAD VERIFICATION — `<BUILT — NOT DEPLOYED>`

03-Aug-2026. Closes the *verification* half of **IA-P10-01**. ⛔ Not the enforcement
half — see §7.

---

## 1. STEP 1 — WHAT ALREADY EXISTED (measured before writing anything)

| question | answer |
|---|---|
| Is there any deployed-tree-vs-HEAD check? | **Only a narrow slice.** Check 11 `stray_pyc_check` (shipped 28-Jul) catches sourceless `.pyc`. The audit's own words: *"the sole HEAD reference is the .pyc check"*. |
| How has the invariant been held so far? | **By ritual** — the audit names *"the ten manual SHA verifications this campaign performed as the de-facto mechanism"*. |
| Was a mechanism already designed? | **Yes, and described but ⛔ not built** — `integrity_audit_2026.md:3145-3149` + the IA-P10-01 recommendation: the two-liner as *"a 12th system_manager check (read-only, nightly artifact, no schema)"*. |

⇒ This item is **implementing an already-approved design**, not inventing one.

## 2. ⭐⭐ THE DESCRIBED TWO-LINER IS WRONG AS DESCRIBED — measured, not argued

The audit describes:

```
git --git-dir=~/trading-system.git --work-tree=$TARGET diff --stat HEAD
```

`git diff HEAD` **needs an index**, and the deployed work tree has no `.git` of its own —
the index lives in the bare repo. Two naive resolutions, both bad, **both measured**:

| form | measured result |
|---|---|
| `GIT_INDEX_FILE` → a **non-existent** path (a valid but EMPTY index) | on a provably **CLEAN** tree: **`1250 files changed, 344936 deletions(-)`** ⇒ the check would scream on its first healthy run |
| `GIT_INDEX_FILE` → a **zero-byte** file | not "empty" but **CORRUPT**: `fatal: index file smaller than expected` ⇒ a different failure with a different lesson |
| use the repo's **real** index | correct output, **but a monitoring job then writes the DEPLOY repo's index** |

**The form that is actually correct: COPY the real index to a temp file and diff against
the copy.** Measured on the real repo: clean tree → **empty output**; a planted one-line
change → **`README.md | 1 +`**; the real `.git/index` mtime **unchanged in both runs**.

⭐ Recorded because it is the whole value of Step 1: **a two-liner that had been described
for weeks would have false-positived catastrophically on its first night.** Both wrong
forms are pinned by a test so the copy step cannot be "simplified" away silently.

## 3. WHAT WAS BUILT

`scripts/system_manager.py` — **check 12, `deployed_tree_check(root, git_dir=None)`**,
wired into `run()` as the 12th spec + title. **No schema. No config. No new cron.**

- **Tracked drift:** `git diff --stat HEAD` against a **copy** of the index.
- **Untracked `.py`:** `git ls-files --others --exclude-standard -- '*.py'` — the other
  half of the question, because **`checkout -f` leaves untracked files in place** (KNOWN)
  and an untracked `.py` is importable while being in **no commit**. Check 11 covers only
  the `.pyc` slice of this.
- **Path resolution:** `~/trading-system.git` (the VM's bare repo — the work tree has no
  `.git`) → else `<root>/.git` (PC) → else **degrade**.

## 4. ⛔ THE SAFETY PROPERTY — it can never halt trading

`system_manager` **can trip tomorrow's SOFT_KILL**: `generate_full_report` collects
`CheckResult.soft_kill_reason` and `main()` passes them to `trigger_soft_kill`
(`:1144-1145`). That made this a kill-adjacent change, not a purely additive one.

⇒ **Check 12 never sets `soft_kill_reason`.** `soft_kill_reason` is **opt-in per check**
(only set when `violation()` is called with it), so the property holds **by
construction** — and is asserted by a test that fails if anyone adds one.

**Why report-don't-block is right here:** the failure class (partial checkout, stray
untracked `.py`, hook drift) is **environment-caused**, and the standing discriminator is
*BLOCK when the failing condition needs a deliberate act; DEGRADE+ALARM when the
environment can cause it.* Escalation stays the operator's call.

**Every failure path degrades to a warning, never a violation:** no git dir · no index
yet · `git diff` non-zero rc · timeout (30 s) · `OSError`/`ValueError`. A check that
cannot run must not look like a check that found something.

## 5. VALIDATION

| check | result |
|---|---|
| New tests | **11**, all green (`tests/unit/test_system_manager_deployed_tree.py`) |
| **RED-on-old** | **11 FAILED** in a base worktree at `f1972ec`; test file **md5-identical both sides** (`068f0dc708eec476b62e19c70647f146`); `config/instruments.csv` copied in (V3) |
| Existing system_manager tests | 16 green, unchanged (27 total in the pair) |
| **Composition smoke test on the REAL repo** | ran against the live PC tree with its own uncommitted work and correctly reported **2 violations**: `scripts/system_manager.py` tracked drift **and** the new untracked test file. `soft_kill_reason=None`. `.git/index` mtime **unchanged**. |
| Full regression | see §6 |

⭐ **The tests build REAL git repos in `tmp_path` rather than mocking `subprocess`.** A
mocked git seam would assert only what we told it to say — the exact failure practices
**§V4** was re-earned on this morning (a stubbed seam made 8 tests vacuous). The defect
class here lives in git's real behaviour, so the tests use git's real behaviour.

## 6. REGRESSION STAMP

**Run:** `pytest tests/unit tests/integration -q` (campaign invocation), main tree,
11:0x → 11:2x IST, no midnight crossing.

**Result: 9 failed · 5,515 passed · 4 skipped.**

- **NEW-FAILURE SET = EMPTY.** The 9 are the same 9 as this morning's gate (c): the **7
  named standing failures** + the **2 proven interpreter artifacts**
  (`test_instance_lock` ×2 — the venv launcher stub, see `kill_drill_03aug2026.md` §5c).
- **Arithmetic closes:** passes **5,504 → 5,515 = +11**, exactly the 11 new tests;
  collection **5,517 → 5,528 = +11**. Nothing else moved.

## 7. LABEL, SCOPE AND WHAT IS NOT CLOSED

**`<BUILT — NOT DEPLOYED>`.** ⛔ **Not `<VERIFIED LIVE>`:** its first real execution is on
the VM, against the real bare-repo layout, and no PC test can stand in for that. It
reaches `<DEPLOYED>` when tonight's push lands and `<VERIFIED LIVE>` only when a real EOD
report carries its line.

⛔ **This closes VERIFICATION, not ENFORCEMENT.** IA-P10-01 names both; nothing here
prevents a divergent tree, it only makes divergence *visible nightly*. Enforcement (e.g.
a boot-time gate) remains open and is **not** proposed here.

⚠️ **Known limits, stated rather than discovered later:**
1. **First VM run is unrehearsed.** Mitigated by construction — every failure path is a
   warning, the resolver falls back, and it cannot soft-kill — but it is still the first
   real run.
2. **It answers "does the tree match HEAD", not "is the running PROCESS that code".** A
   service started before a push keeps its old code in memory; that is a different
   question and this check does not claim it.
3. **Untracked non-`.py` is ignored** by design (reports/logs/data land in the tree
   constantly); only `.py` is importable.
4. **Output is bounded** (5 diff lines, 10 untracked paths) because this check runs LAST
   and is therefore the first casualty of Telegram's 4096-char truncation — on exactly
   the night it matters. The full list always survives in
   `reports/system_manager/<day>.txt` and the email sentinel.

⛔ No new register row — **231 stands**.
