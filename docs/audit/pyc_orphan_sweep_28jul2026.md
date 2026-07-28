# Orphaned `.pyc` — sweep, and the invariant question (28-Jul-2026)

**Trigger.** On 28-Jul, hunting the W8 backfill script on `main`, `find` returned
`scripts/__pycache__/backfill_closure_source_w8.cpython-311.pyc` while the `.py` was absent (it lived
on `check1-classify-27jul`). Filed as a search hazard. The follow-up question is the serious one:
**could an orphaned `.pyc` make a module importable that HEAD does not contain — silently breaking
deployed-tree-equals-HEAD?**

---

## 1. ⭐ MEASURED FIRST: is an orphaned `.pyc` importable?

Built a throwaway package, imported it, deleted the `.py`, and tried both layouts.

| layout | result |
|---|---|
| `pkg/__pycache__/orphan.cpython-311.pyc`, no `orphan.py` | ❌ **NOT importable** — `ModuleNotFoundError: No module named 'pkg.orphan'` |
| `pkg/orphan.pyc` (beside where the source was), no `orphan.py` | ✅ **IMPORTABLE** — executed the stale bytecode |

**This is PEP 3147 semantics, confirmed rather than assumed:** `__pycache__` is only a *cache keyed to
an existing source file*. Bytecode is only importable on its own in the **legacy sourceless layout**,
i.e. a `.pyc` sitting where the `.py` would be.

⇒ **Two hazard classes, and only one is dangerous:**
- **Type A** — orphan inside `__pycache__`. **Search hazard only.** Cannot execute.
- **Type B** — any `.pyc` *outside* `__pycache__`. **Import hazard.** Executes stale code.

## 2. The sweep

| tree | Type B (import hazard) | Type A (search hazard) |
|---|---|---|
| PC repo (`D:\Projects\trading-system`) | **0** | 15 |
| VM deployed tree (`~/systems/trading-system`) | **0** | 2 |

**Zero import hazards in either tree. The deployed-tree-equals-HEAD invariant is not silently
broken.**

The 15 PC orphans are mostly a *fingerprint of which unpushed branches have been checked out here*
(`test_no_outbound_network_from_tests`, `test_no_real_data_store_from_tests`,
`test_one_trade_per_symbol_direction`, `test_s4_webhook_degrade`, `test_check1_acceptance_rows`,
`test_alert_send_audit_trail`) plus a root `conftest` and some `ops_dashboard` leftovers. The 2 VM
orphans are both `test_daily_review` — see below.

## 3. A3 — does untracked bytecode survive the deploy? **YES, and production proves it**

The hook (`~/trading-system.git/hooks/post-receive`) deploys with:

```bash
git --work-tree="$TARGET" --git-dir="$GIT_DIR" checkout -f "$BRANCH"
```

`checkout -f` overwrites tracked files and removes files that left the ref, but it **never removes
untracked files** — and `__pycache__/` is git-ignored (`.gitignore:45`).

⭐ **The natural experiment, already sitting in production.** `tests/unit/test_daily_review.py` was
tracked on `main` and **deleted** in `01e07b7` ("Phase C cutover: DB-pure daily_trade_review replaces
daily_review"). On the VM today:

```
tests/unit/test_daily_review.py                              -> No such file       (deletion propagated ✓)
tests/unit/__pycache__/test_daily_review.cpython-312.pyc      57,287 B  Jun 15
tests/unit/__pycache__/test_daily_review.cpython-312-pytest-9.0.3.pyc  125,203 B  May 12
```

The source is correctly gone; **the bytecode has survived every deploy for over two months.**

⇒ **"A file deleted from git could keep executing on the VM indefinitely" is REFUTED for
`__pycache__` residue** — the residue is real, the execution is not. It would only be true for a
Type B `.pyc`, and nothing here produces one:

- **0** tracked `.pyc` in git (`git ls-files | grep -c '\.pyc$'` → 0)
- **no** `compileall`, `-B`, `PYTHONDONTWRITEBYTECODE` or `PYTHONPYCACHEPREFIX` anywhere in the repo
  or the deploy path ⇒ CPython default behaviour only, which writes **exclusively** into `__pycache__`

So Type B cannot arise by accident. It would take someone deliberately placing a `.pyc`.

## 4. ⛔ CORRECTED 28-Jul — **the "asymmetry" this section claimed DOES NOT EXIST**

> Recorded as a correction rather than silently edited, because the original claim was carried
> forward into a desk instruction (C6) that told the next person to preserve something that was
> never there.

**What this section originally said:** `.gitignore:45` is `__pycache__/`, there is *no bare `*.pyc`
rule*, so a Type B "shows as untracked in `git status` **on the PC**" — quietly protective — and the
residual gap is only that the VM has no `.git`.

**⛔ BOTH HALVES ARE FALSE.** Measured 28-Jul, not re-read:

```
$ sed -n '45,46p' .gitignore
__pycache__/
*.py[cod]

$ git check-ignore -v scripts/ghost.pyc
.gitignore:46:*.py[cod]     scripts/ghost.pyc
```

`*.py[cod]` matches `.pyc`. A Type B file is **ignored**, so `git status` shows **nothing** —
confirmed independently by planting one: `git check-ignore` named line 46 and `git status` stayed
silent.

⇒ **git is blind to this class EVERYWHERE, not only on the VM.** There is no asymmetry to protect;
the PC has no visibility either. The honest statement of the gap is **nothing detects a Type B on
either machine**, which is strictly worse than what was written here.

**This makes the §5 detector MORE justified, not less.** It was argued as closing a VM-only hole
that `git status` already covered on the PC. It does not close a VM-only hole — **it is the only
detector that exists anywhere.**

⭐ **And the C6 instruction built on this is VOID.** The desk file said: *"⛔ DO NOT ADD A BARE
`*.pyc` RULE TO `.gitignore` — its absence is quietly PROTECTIVE … put that reasoning in a comment
beside `.gitignore:45`."* **There is no absence — the rule is already there, at line 46.** So there
is nothing to preserve, nothing to add, and no comment to write. The instruction is retired here
instead of being acted on. *(Its underlying intent — don't blind a detector — is sound and is now
served by the check itself, which does not depend on git at all.)*

**Still true, and unchanged:** the deployed tree has no `.git` directory (verified: only
`.gitattributes` and `.gitignore`), so even a hypothetical `git status` route was never available
on the VM.

⭐ **How it got past me:** I read `.gitignore:45`, saw `__pycache__/`, and reported the absence of a
rule I had not looked one line further to find. **Same shape as the deployed-commit reflog error in
this same session** — an ABSENCE asserted from a check too narrow to see the thing. Both were caught
by re-deriving from measurement instead of re-reading the earlier note.

## 5. A4 — proposed fix (⛔ NOT APPLIED; the hook is not to be touched today)

Proportionate to a hazard that is currently absent and structurally hard to create. **Prefer the
guard over the mutation** — this project's rule is that removing a trap beats documenting it, and the
trap here is *undetectability on the VM*, not the inert residue.

**Recommended — a detector, ~3 lines, no deploy-path change.** Add to `scripts/system_manager.py`
(which already runs nightly and has a `_check(...)` idiom):

```python
stray = [p for p in ROOT.rglob("*.pyc")
         if p.parent.name != "__pycache__"
         and "venv" not in p.parts and "sats" not in p.parts]
# non-empty => a sourceless-import hazard in the deployed tree; CRITICAL.
```

It fails loudly on the only condition that can actually break the invariant. ⭐ **And per the §4
correction, there is no `git status` fallback to run "instead of" — not on the VM, and not on the PC
either. This is the only detector, on either machine.**

**Optional hygiene, low value — a `__pycache__` prune in the hook.** Buys no safety (Type A cannot
execute) and costs a recompile on next import. Would only remove the search hazard. If ever added:

```bash
find "$TARGET" -type d -name __pycache__ -prune -exec rm -rf {} +
```

⛔⛔ **`git clean -xdf` MUST NEVER be used in the deployed tree.** `data_store/` (the live database and
its backups), `logs/`, and `.env` are all untracked or ignored there. It would be catastrophic, and it
is the obvious-looking answer to this problem — which is exactly why it is written down here as a
prohibition.

**Not recommended:** `PYTHONDONTWRITEBYTECODE=1` in the unit/cron env — it slows every start and does
nothing about existing residue. `PYTHONPYCACHEPREFIX` is more elegant (moves all bytecode out of the
tree, making "tree == HEAD" literally checkable) but changes the runtime env of the live service for a
hazard that measures zero. Neither is worth a boot.

## 6. Also confirmed while here

✅ The hook's own sync invariant holds — `deploy/hooks/post-receive` and the armed
`~/trading-system.git/hooks/post-receive` are byte-identical (md5 `b7166732f1a4cbb70e7e1d2b984e3f88`).
The header warns that editing without re-arming silently recreates drift; it has not happened.

⚠️ **There is no deployed-tree-vs-HEAD integrity check at all** — nothing in `scripts/`, `deploy/` or
`ops/` compares them. The invariant that the 28-Jul cherry-pick decision was made to protect is held
by convention and verified by nothing. The §5 detector would be the first thing that ever checked any
property of the deployed tree against expectation. Recorded, not built.

Related: `docs/audit/capital_figure_sweep_28jul2026.md` · memory `verify-check-the-rc-not-the-output`
