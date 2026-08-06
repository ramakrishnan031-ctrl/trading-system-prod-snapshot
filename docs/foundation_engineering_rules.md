# Foundation Engineering Rules v1.0

## Purpose

This document defines the foundational engineering rules and standards for building the trading system. The system must be runnable on both **Windows 11 (PC)** and **Ubuntu (Cloud)** via Git.

> \\\\\\\*\\\\\\\*Note:\\\\\\\*\\\\\\\* These rules should be followed as closely as possible. In exceptional circumstances, a rule may be skipped or ignored, but this document serves as the primary navigation for all engineering decisions.

\---

## 1\. Core Engineering Philosophy

### 1.1 Deterministic System

* Same input **must** always produce the same output.
* No hidden randomness.
* No hidden global state.

### 1.2 Idempotent Execution

* Running a script multiple times must not corrupt data.
* Re‑runs must be safe.
* No manual cleanup required between runs.

### 1.3 Single Responsibility Rule

* Each `.py` file may perform upto SIX responsibilities.
* One clear entry point per script.
* No mixed responsibilities.

### 1.4 Zero Hardcoding

* **No hardcoded** paths, dates, credentials, capital, symbols, settings, or modes.
* Everything must come from:

  * Configuration files
  * CLI arguments
  * Environment variables

### 1.5 No Circular Dependencies

* Modules must not depend on each other in circular chains.

### 1.6 Logs Over Prints

* No uncontrolled `print()` statements.
* All scripts must log:

  * Start time
  * Progress heartbeat
  * Output path
  * Errors
* **No silent failures.**

### 1.7 Resume‑Safe Design

* If interrupted (power cut / crash), the next run must continue safely.
* Same applicable to logs and other files: one file per day, continue appending to the existing same‑day file.
* No full restarts unless absolutely required.

### 1.8 Immutable Raw Data Rule

* Raw data must **NEVER** be modified or overwritten.
* Corrections must generate **new** files, not edits.

### 1.9 Configuration‑Driven System

* System behavior must be fully controlled via configuration.
* Changing behavior must **not** require code changes.

### 1.10 Boring Code Rule

* Avoid clever, complex, or fancy logic.
* Future‑you must understand the code easily after one year.

### 1.11 Traceable Reservation Lifecycle

* Every reservation must produce exactly **one** matching release, regardless of exit timing — same‑day, T+1, restart, or monitor replay.
* That release must be **linkable to its reservation by a key that exists on both rows**.
* **Any future ledger change must FAIL REVIEW if reservation creation and release cannot be linked end to end.**
* An untraceable release is half a release: it frees the money and destroys the audit path.
* A predicate error must never be able to strand capital permanently — a single release path gated on one live quantity is a design defect, not an implementation detail.

> **Origin (06‑Aug‑2026):** `RELEASE_USED` carries `reservation_id` on **0 of 220** rows while `RELEASE` carries it on **1189 of 1189** — the keys are perfectly disjoint. A reservation‑keyed query therefore returns a structurally guaranteed zero for any clean delivery exit. See `docs/design/f6_delivery_exit_predicate_design_06aug2026.md`.

### 1.12 Supported Operator Recovery Path

* **There must always exist ONE SUPPORTED OPERATOR RECOVERY PATH for every persistent business state.**
* If a state can be entered, a sanctioned way out must exist — a script, a command, or a documented procedure.
* Raw DB manipulation is **not** a recovery path. Neither is "restart and hope".
* A state with no supported exit is a **design defect**, not an operational inconvenience — and it must be found at design time, because by the time it is entered it is already too late.
* **The recovery path must ITSELF be regression‑tested or periodically validated.** ⛔ An untested recovery path is a claim, not a capability — and it will be discovered to be broken on the one day it is needed. *(Otherwise this rule becomes exactly what §1.11 was written against: a declared thing with no effect.)*

> **Origin (06‑Aug‑2026):** an `OPEN` delivery trade whose broker position is gone had **no supported closure path at all**. Width searched: all of `scripts/`, `system_manager.py`'s entire argument surface, every `CLOSED_MANUAL` reference outside the reconciler — every hit was a reader or a backfill of already‑closed rows. All three `OPEN → CLOSED` transitions were shut: CHECK1 by the delivery skip, `_finalize_gtt_exit` by its `held == 0` gate, EOD square‑off by CNC exemption. **Both doors were held by the two halves of one defect, and the `gtt_state` row keeping CHECK1's skip armed had been created by the defect itself.** The reservation was therefore re‑reserved at every 08:15 boot — ~21 % of the delivery bucket, daily, for a position that did not exist.

\---

## 2\. Python Coding Standards

|Rule|Requirement|
|-|-|
|**Naming Conventions**|`snake\\\\\\\_case` for functions<br>`PascalCase` for classes<br>`UPPER\\\\\\\_CASE` for constants|
|**File Size Limit**|If a file exceeds \~3000 lines, it must be refactored.|
|**Function Size Limit**|If a function exceeds 50‑60 lines, split it.|
|**No Hidden State**|Avoid global variables and mutable shared state.|
|**Explicit > Magic**|No dynamic imports, monkey patching, or runtime class modification.|
|**Strict Exception Handling**|Never use bare `except:` blocks.<br>All exceptions must log full traceback.<br>Stop process unless explicitly recoverable.|
|**Exit Code Discipline**|`exit(0)` → success<br>`exit(1)` → failure|
|**Folder \& File Naming**|Use **lowercase letters** only.|

\---

## 3\. File Processing \& Data Safety Rules

### 3.1 Atomic File Processing Rule

When processing multiple files:

1. Fully complete **file‑1**.
2. Validate **file‑1**.
3. Only then move to **file‑2**.

If any file fails:

* Stop immediately.
* Log error.
* Notify clearly.
* Exit with failure code.

**No silent continuation.**

### 3.2 Temporary Write Rule

Never write directly to the final filename.

Process:

1. Write to `filename.tmp`.
2. Validate completely.
3. Rename to final filename.

Prevents partial or corrupted files.

### 3.3 Duplicate Prevention Rule

Before adding new files or modifying existing files during production or debugging:

* Check whether any other files or folders exist with the same or intended requirement/logic.
* If they exist: **use it, correct it, or delete it first** (to avoid duplication or old placeholders).

### 3.4 Intelligent File Validation Rule

File existence does **NOT** mean file correctness.

**If file exists:**

* Open file.
* Validate structure.
* Validate logical correctness.
* Compare expected vs actual.
* Repair or update if needed.

**If file does not exist:**

* Create new.

**Validation Levels:**

|Level|Name|Checks|
|-|-|-|
|1|Basic Integrity|File readable, size > 0, valid structure, required columns present.|
|2|Structural Integrity|No duplicate rows, sorted dates, no mandatory null rows.|
|3|Logical Integrity|No future dates, no negative prices, correct row counts, last date matches expectation.|

**Update Philosophy:**

* Detect last valid date.
* Fetch only missing data.
* Append safely.
* Re‑validate.

**Never blindly skip existing files.**

### 3.5 No Auto‑Overwrite Rule

If a file exists:

* Validate first.
* Repair or update.
* Do **not** silently overwrite.

### 3.6 Lock File Rule

Before script execution:

* Create a `.lock` file.

If lock exists:

* Stop execution.
* Inform user: `"Already running"`.

Prevents double execution and race conditions.

### 3.7 Deterministic Ordering Rule

Always process files / symbols in **sorted order**.  
Never depend on OS directory ordering.

### 3.8 Single Source of Time Rule

* Use consistent timezone: **IST** throughout the project.
* Use **ISO format**: `YYYY-MM-DD` everywhere.
* Avoid inconsistent time handling.

\---

## 4\. Logging \& Monitoring Standards

1. **All modules must log.**
2. Log categories may include:

   * `broker`
   * `trades`
   * `scheduler`
   * `errors`
3. Each script must log:

   * Start time
   * Progress heartbeat
   * Output file location
   * Completion status
   * Full error trace (if any)
4. **Fail Fast Philosophy:**

   * Fail early
   * Fail loudly
   * Fail clearly
   * Never fail silently

\---

## 5\. Operational Discipline

|Rule|Requirement|
|-|-|
|**Version Freeze**|Freeze Python version. Pin all dependency versions. Generate `requirements.txt` with exact versions (commented sections).|
|**No Direct Production Testing**|Use sandbox / staging before production.|
|**Read‑Only Raw Data Permissions**|`raw/` → read‑only<br>`processed/` → write allowed<br>`logs/` → append only|
|**Deterministic Restart**|After reboot, system must detect unfinished tasks, resume safely, and avoid duplication.|
|**Zero Manual Intervention**|No manual file moves, renames, or triggers. Everything automated and verified.|
|**Backup Strategy**|Weekly backup of config, processed data, and logs. Store on separate disk/partition.|
|**Minimal External Dependencies**|Use mature, stable libraries only. Avoid experimental or unstable packages.|
|**Performance Is Secondary**|Correctness > Speed<br>Stability > Optimization|
|**Virtual Environment**|Always create a `venv` inside the project.|
|**Single Source of Truth**|Maintain a single source of truth everywhere possible.|
|**Ask Questions**|Ask the user for clarification whenever required (do not assume).|
|**Uploaded Code as Input Only**|If any uploaded files contain code, treat it as an idea. Modify suitably if needed; do not stick to it blindly.|

### 5.1 Verification Discipline — what counts as proof

> **A clean `git diff` is NOT proof that nothing changed.**

This project infers "the file is back the way it was" from a clean `git diff` constantly —
after planting a defect to prove a test goes red, after a temporary edit, after a restore.
That inference is unsound, and it failed silently on 26-Jul-2026.

**How it fails.** Rewriting a file through a text API that normalises newlines (on Windows,
`pathlib.Path.write_text` converts every `\n` to `\r\n`) changes **every line of the file**.
`git diff` shows nothing, because git normalises line endings on the way in. The file on
disk is a different file. Any check that then compares content — an md5 restore-check, a
hash-based artifact baseline — fails, and it fails for a reason that has nothing to do with
what was being tested. Time is then spent debugging the wrong thing.

**The rules that follow from it:**

1. **Verify a restore by `md5` AND byte count, never by `git diff`.** Capture both before
   the edit and compare after. Those are the only two checks that see a line-ending rewrite.
2. **Never use a whole-file text write to plant or restore a source file.** Use `git stash` /
   `git checkout -- <path>` for restores, and a byte-preserving targeted edit for plants.
3. **State which check you actually ran.** "`git diff` is clean" and "md5 matches" are
   different claims with different strengths. Do not report the weaker one as the stronger.

**The general form, of which the above is one instance:**

> **A green check is evidence ONLY if it could have been red.**

Before trusting any check, name the mechanism by which it would have failed. If you cannot,
the check is vacuous and proves nothing. Two corollaries earned the hard way:

* **A replacement that cannot fail is worse than the number it replaced, because it looks
  rigorous.** Swapping a brittle constant (`assert len(x) == 69`) for something that *reads*
  like a property but can never be false is a regression in disguise — the brittle number at
  least failed loudly. Prove the new assertion can go red before keeping it.
* **Control the input; do not disable the check.** When a test depends on ambient state — the
  wall clock, the day of the week, the environment — pin the input so the real assertion
  still runs. Skipping or loosening the assertion removes the coverage; pinning the clock
  keeps it and makes it deterministic. A suite whose answer depends on *when* it runs cannot
  serve as a baseline, and baseline comparability is what the whole gate rests on.

\---

## 6\. CSV \& Script Governance Rules

### 6.1 CSV Naming Convention

* Must contain date in filename.
* Format: `name\\\\\\\_yyyy-mm-dd.csv`

### 6.2 Mandatory Script Header Comment Block

Every `.py` file **must** begin with a structured header comment block containing:

* Purpose of the script
* Whether any manual input is required (Yes/No + explanation)
* How the script works (high‑level step explanation)
* Inputs (files, configs, CLI args, environment variables)
* Outputs (files generated, logs, reports)
* Main functions / entry point

> The script must \\\\\\\*\\\\\\\*NOT\\\\\\\*\\\\\\\* start directly with code.  
> The header comment block must appear at the very top before any imports.

### 6.3 All `.py` Files Must:

* Be resume‑safe.
* Provide minimal heartbeat output.
* Print output storage location if generating reports.

\---

## 7\. Cloud Runnability

The system must be movable and runnable in cloud‑based environments (AWS, Oracle, etc.):

* **No hardcoded system paths.**
* Any other cloud‑compatibility issues must be resolved.

\---

## Final Principle

The system must be:

* Deterministic
* Self‑validating
* Self‑healing
* Resume‑safe
* Config‑driven
* Non‑fragile
* Fully auditable
* Cloud‑run friendly

**Foundation Engineering Rules v1.0 — Finalized.**

