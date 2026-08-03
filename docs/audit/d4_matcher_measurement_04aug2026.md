# D4 — THE MATCHER MEASUREMENT (deny-first precondition) — 04-Aug-2026

**Status: `<MEASURED — NO DENY RULE WRITTEN, THE ALLOWLIST IS UNCHANGED>`.**
Precondition step 1 of the re-scoped D4 (deny-first). Source: Claude Code's own documentation,
`https://code.claude.com/docs/en/permissions`, fetched 04-Aug-2026 ~03:0x IST. ⛔ Nothing below is
inferred from the entries' shape — that inference is what R12 flagged, and it is now retired.

✅ **Step 2 done anyway (it is safe and needed whenever the edit happens):** the git-ignored,
no-history `\.claude/settings.local.json` is snapshotted **outside the repo** at
`~/.claude/projects/D--Projects-trading-system/settings_backups/settings.local.json.2026-08-04_pre-D4-deny`,
**md5 `68ff918d47089573b6dfea949c3cc5de`, verified identical to the original.**

---

## 1. THE FOUR QUESTIONS — ALL ANSWERED FROM DOCUMENTED BEHAVIOUR

| # | question | answer |
|---|---|---|
| 1 | prefix, glob or exact? | **Glob.** *"Bash permission rules support wildcard matching with `*`. Wildcards can appear at any position."* |
| 1b | does `*` span spaces? | ✅ **Yes.** *"A single `*` matches any sequence of characters **including spaces**, so one wildcard can span multiple arguments. `Bash(git *)` matches `git log --oneline --all`."* |
| 1c | trailing ` *`? | **Word boundary.** *"`Bash(ls *)` matches `ls -la` but not `lsof`."* `Bash(ls:*)` is an equivalent spelling. |
| 2 | deny vs allow | **Deny wins.** *"Rules are evaluated in order: deny, then ask, then allow. The first match in that order determines the outcome, and rule specificity doesn't change the order."* |
| 3 | per-tool? | ✅ **Yes.** PowerShell rules *"use the same shape as Bash rules"* but are a **separate tool namespace**. |
| 4 | bypassable by chaining? | ⛔ **No** — see §4. |

### ⭐ THE R12 PREMISE IS NOW MEASURED, AND IT HOLDS
R12 flagged one unmeasured premise: entries read as literal-prefix-plus-`*`. **Confirmed.**
Because `*` spans spaces, **`Bash(ssh *)` does match**
`ssh trading-vm "python3 scripts/check_vm_state.py --cleanup-pending"`.
⇒ **R12's conclusion — D4's original mechanism narrows 1 of 3 surfaces — now rests on
measurement, not inference.** The premise was flagged, then closed, rather than carried.

## 2. ⛔⛔ THE FINDING THAT CHANGES THE DENY LIST — **A DENY CANNOT CARRY EXCEPTIONS**

> *"A broad deny rule like `Bash(aws *)` blocks every matching call, including calls that also
> match a narrower allow rule like `Bash(aws s3 ls)`, **so a deny rule can't carry allowlist
> exceptions.**"*

This splits the five ruled patterns into **two kinds**, and only one kind is safe to write as-is:

| ruled deny pattern | legitimate use? | verdict |
|---|---|---|
| `--cleanup-*` | none | ✅ **SAFE — deny outright** |
| `--reset` | none | ✅ **SAFE — deny outright** |
| `sed -i` on the production `.env` | none | ✅ **SAFE — deny outright** |
| **webhook POST** | ⚠️ **yes** — it is a real diagnostic, used repeatedly in this campaign | 🔴 **all-or-nothing** |
| **live-service restart** | ⚠️ **yes** — routine operator action | 🔴 **all-or-nothing** |

⚠️ **The ruling pre-accepted over-broad denies** (*"an annoyance, not a hazard — prefer it to an
under-broad one"*). ⭐ **But it made that trade-off before knowing an exception is IMPOSSIBLE, not
merely discouraged.** Once denied, the only way to run a legitimate restart through Claude Code is
to **edit the deny back out** — a narrower allow cannot reach it, and neither can a PreToolUse
hook: *"a matching deny rule blocks the call"* regardless of hook output.
⇒ 🔴 **Returned for confirmation on those two only. The three narrow ones are unaffected.**

## 3. EVERY PATTERN NEEDS **TWO** RULES

Tools are separate namespaces, so a `Bash(...)` deny does not cover a `PowerShell(...)` call —
which is the exact half-narrowing R12 measured, reappearing on the deny side. ⇒ each pattern is
written **twice**, `Bash(…)` and `PowerShell(…)`. Cheap and mechanical, but ⛔ **omitting one
reproduces the defect the ruling exists to fix.**

## 4. ⭐ COMPOUND COMMANDS DO **NOT** EVADE — BUT THE `ssh` PAYLOAD IS NOT PARSED

**Better than feared.** *"Claude Code is aware of shell operators, so a rule like
`Bash(safe-cmd *)` won't give it permission to run `safe-cmd && other-cmd`. The recognized command
separators are `&&`, `||`, `;`, `|`, `|&`, `&`, and newlines. **A rule must match each subcommand
independently.**"* PowerShell is **AST-parsed** with the same guarantee.

⛔ **The limit, and it is decisive for this surface:** that parsing applies to the **local** command
line. The remote command inside `ssh trading-vm "…"` is a **quoted argument**, not a subcommand
Claude Code parses. A deny there is **string-matching an opaque payload** — it catches the honest
spelling and would not catch a variable, a heredoc, a script file, or `scp`-then-run.

⇒ ⭐⭐ **STATE THE GUARANTEE HONESTLY: this deny protects against MY MISTAKES. It is not a security
boundary against a determined bypass.** The documentation says the same about the analogous case —
deny rules *"don't apply to arbitrary subprocesses… For OS-level enforcement… enable the
sandbox"* — and names **sandboxing** as the OS-level layer that *"applies only to Bash commands and
their child processes."*
⛔ **Do not let the deny be recorded as making the surface safe.** It makes it *harder to trip*.

## 5. ⛔ WHY STEP 3 (**PROVE THE DENY BITES**) CANNOT BE SATISFIED FROM THIS SESSION

The ruling requires each deny be proven with a probe that is **safe in both outcomes**. Correct —
and it cannot be interpreted here:

**Whether an edit to `settings.local.json` reaches an ALREADY-RUNNING session is not documented.**
The docs name `/permissions` as the in-session management path and call out *"live reload"*
explicitly for **skills**, but say nothing about re-reading a settings file mid-session.

⇒ If the running session has not re-read the file, a probe that **succeeds proves nothing**: the
pattern may be correct and simply not loaded. ⭐ **That is a check whose failure is
indistinguishable from the thing it is meant to detect** — the same class as the vacuous gate and
the unconditional `echo`, and the reason the ruling demanded a real probe in the first place.
⛔ **Writing a deny I cannot verify would produce exactly what §2 of R12 warns about: a rule that
looks identical in the file whether or not it works.**

## 6. 🔴 WHAT IS OWED — TWO ITEMS, BOTH SMALL

1. **Confirm the two all-or-nothing patterns** (webhook POST, live-service restart) now that
   §2 shows an exception is impossible. The other three are ready to write.
2. **A verification route for step 3.** Options, not ruled here: apply the rules via
   **`/permissions`** (documented in-session path, but user-driven — Rama would run it); or write
   the file and **restart Claude Code**, then probe in the fresh session; or accept the rules
   unverified — ⛔ **which the ruling already refused, correctly.**

⛔ **HALT. NO DENY RULE WRITTEN. THE ALLOWLIST IS BYTE-IDENTICAL TO ITS SNAPSHOT.**
⭐ **The matcher precondition PASSED — this halt is not step 1 failing.** It is step 3's proof
being uninterpretable from inside a running session, which the measurement only revealed by asking
how the rule would be *verified* rather than how it would be *written*.
