# D4 — THE MATCHER MEASUREMENT + THE DENY RULES — 04-Aug-2026

> # ⛔⛔ READ THIS BEFORE ANYTHING ELSE
> ## **THIS DENY PROTECTS AGAINST THE AGENT'S MISTAKES. IT IS *NOT* A SECURITY BOUNDARY.**
> The remote command inside `ssh host "…"` is a **quoted argument**, not a subcommand Claude Code
> parses ⇒ these patterns **string-match an opaque payload**. They catch the honest spelling; they
> would **not** catch a variable, a heredoc, or `scp`-then-run.
> ⭐ **Local compound commands ARE parsed** and do not evade them (§4) — that part is genuinely
> strong. **OS-level enforcement is SANDBOXING, not permission rules (§9).**
> ⛔ **A later reader who treats these rules as making the surface safe has re-created the exact
> declared-vs-effective gap this campaign exists to close.** That is why this sits at the top of
> the record and at the top of the rules block itself, not in a footnote.

**Status: `<BUILT — 16 DENY RULES WRITTEN, ⛔ NOT YET VERIFIED>`.** The `allow[]` array is
**untouched and byte-identical to its pre-edit snapshot** (533 entries, hash
`a9d274d4…`) — narrowing is step 2 and is deliberately not in this change.
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

---

# ⚖️ RULED 04-Aug-2026 (§G2) — **DENY BOTH.** THE RULES AS WRITTEN

**§2's two all-or-nothing patterns were re-decided on the measured facts, and the answer is still
deny** — for a reason the earlier framing missed:

⭐⭐ **The deny governs what the AGENT may do unprompted. It does not govern what RAMA can do.**
He restarts the service from his own shell whenever he likes; the deny removes only the *agent's*
ability to do it **with no human beat.** ⭐ That is not a new posture — it is **the campaign's
existing rule for its most dangerous action** (`§D3`: *the book is flattened MANUALLY; never build
an auto-flatten*), applied to the agent's authority instead of to the system's.

⭐ **And "you must edit the deny out" is not friction — it IS the human beat, made structural.** A
deliberate edit cannot happen by accident, cannot happen mid-unrelated-task, and cannot be
produced by an auto-filled prompt (§G1). For a webhook POST — **a fabricated trading signal
injected into a running system** — that is the correct cost.

**Measured against real need:**
- **live-service restart** — costs the deploy path **nothing**: per §D3 the service is already
  **down at push time by design**, and the deploy never restarts it (code loads at the next
  08:15). ⇒ this deny does not touch a single authorised workflow.
- **webhook POST** — a real diagnostic, so the cost is real but occasional. Against it: **15
  allow entries currently permit it with no prompt, on the signal path, registered nowhere.**

## 7. THE 16 RULES — 5 PATTERNS × 2 TOOL NAMESPACES

| # | pattern (written for **both** `Bash(…)` and `PowerShell(…)`) | stops | note |
|---|---|---|---|
| 1 | `*--cleanup-*` | `--cleanup-pending`, `--cleanup-orders` | no word boundary — the suffix is the point |
| 2 | `*--reset *` | `… --reset` / `… --reset foo` | ⭐ trailing ` *` **enforces a word boundary**, so `git commit --reset-author` is deliberately **NOT** caught |
| 3 | `*sed -i*.env*` | in-place edits of any `.env` | catches the `WEBHOOK_SECRET` comment-out |
| 4 | `*curl*webhook*` · `*wget*webhook*` | POSTing to the webhook | ⭐ scoped to the **transfer tool**, so `grep`/`journalctl` on "webhook" stay allowed — log diagnosis is untouched |
| 5 | `*systemctl restart*` · `*systemctl start *` · `*systemctl stop *` | live-service lifecycle | ⭐ `systemctl status` is **not** matched and stays allowed |

⛔ **Every pattern is written twice.** Tools are separate namespaces; omitting one reproduces the
half-narrowing R12 measured, on the deny side.
✅ **`allow[]` verified untouched** — 533 entries, hash identical to the snapshot.

## 8. 🔬 THE PROBE PLAN — ⛔ RUN IN A FRESH SESSION, NOT THIS ONE

Route **(b)** was ruled: **write → restart Claude Code → probe fresh**, where *"is the rule
loaded?"* is not in question. ⛔ These have **not** run; the rules are `<BUILT>`, not
`<VERIFIED>`.

⭐ **Every probe is an `echo` of the pattern — safe in BOTH outcomes by construction.** If a deny
silently fails to match, all that happens is a string is printed. ⛔ **Never probe a deny with a
command that would be destructive if the deny fails.**

| # | probe | expected |
|---|---|---|
| 1 | `echo --cleanup-pending` | **BLOCKED** |
| 2 | `echo x --reset y` | **BLOCKED** |
| 2b | `echo git commit --reset-author` | ✅ **ALLOWED** — proves the word boundary works and the rule is not over-broad |
| 3 | `echo sed -i s/a/b/ .env` | **BLOCKED** |
| 4 | `echo curl http://x/webhook/y` | **BLOCKED** |
| 4b | `echo grep webhook logfile` | ✅ **ALLOWED** — proves log diagnosis survives |
| 5 | `echo systemctl restart trading-system` | **BLOCKED** |
| 5b | `echo systemctl status trading-system` | ✅ **ALLOWED** |

⭐ **The negative probes (2b, 4b, 5b) matter as much as the positive ones** — a deny that blocks
everything is not evidence it blocks the *right* thing. Record per rule: **pattern · probe ·
observed result.**

⚠️ **If a positive probe is NOT blocked, there are three causes and they must be distinguished, not
guessed:** (i) rules not loaded, (ii) pattern wrong, (iii) `echo` short-circuited as a read-only
built-in before deny evaluation. Distinguish (iii) by re-probing with a non-read-only but harmless
command, e.g. `ssh --cleanup-pending` (invalid flag ⇒ ssh prints usage and exits).

## 9. 🔴 SANDBOXING — REGISTERED AS ITS OWN ITEM, ⛔ NOT FOLDED INTO D4

The docs name **sandboxing** as the OS-level layer: *"restricts the Bash tool's filesystem and
network access… applies only to Bash commands and their child processes"*, and *"prevent Bash
commands from reaching resources outside defined boundaries, even if a prompt injection bypasses
Claude's decision-making."*
⇒ **It is the answer to a different question than the one D4 asked** — D4 asks *what may the agent
be pre-authorised to do*; sandboxing asks *what can any subprocess reach at all*. **Not folded in.**

> ⚠️ **CALIBRATION CONFLICT, REPORTED NOT RESOLVED (per §2's standing guard).** The guard says
> *"this document does not license new register rows; the reconciled count (231) stands"*, and
> *"if this rule and the count ever conflict, REPORT it — never resolve it silently."* The ruling
> asks for sandboxing as **its own item**. ⛔ **I have NOT minted row 232.** It is recorded here
> and flagged; **whether it becomes a row is Rama's call.**

⛔ **HALT — the rules are `<BUILT>`, not `<VERIFIED>`.** ⭐ The matcher precondition **passed**;
what remains is §8, which needs a restart. Step 2 (narrow both channels together) and step 3
(stale sweep) are untouched and stay separate, in that order.
