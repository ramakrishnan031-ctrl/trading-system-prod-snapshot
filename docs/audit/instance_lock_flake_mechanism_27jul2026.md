# The `test_instance_lock` flake — the real mechanism

**27-Jul-2026, Monday.** §C of the Monday card. **Source reading only — nothing executed, no suite run**
(C3). Branch `hold-check1-w8-26jul` @ `d3fa5b8`, working tree otherwise untouched.

> ⛔ **This file is deliberately UNCOMMITTED today.** The Monday card asserts the branch is at **24
> commits**; adding a 25th would falsify the thing the card is used to check. Commit it after tonight's
> push, per D5.

---

## 0. The answer in one paragraph

`_holder_process()` spawns a real child process that takes the machine-global instance lock and then
**`time.sleep(120)`** — two full minutes. Nothing outside the spawning test's own `finally` ever kills
it: it is a bare `subprocess.Popen` with no job object, no `atexit`, no session fixture. So whenever a
pytest run ends without reaping one — an interrupt, a parallel suite, or `_holder_process`'s own
`assert` firing *before* `holder` is bound — **a live process holding `%TEMP%\trading-system.lock`
outlives the run by up to 120 seconds.** The next pytest invocation started inside that window finds
the lock held, and `test_p1` fails naming a PID that is **alive** and **different every run**.

That is the whole symptom Rama described. The racing parties are this test class's own children,
across overlapping invocations. **Not an external process. Not Windows. Not "PC-env".**

---

## 1. The chain, each link source-proven

| # | Fact | Where |
|---|---|---|
| L1 | The holder child sleeps **120 s** after acquiring | `tests/unit/test_instance_lock.py:50` |
| L2 | The child is a bare `Popen` — no job object, no `atexit`, no session fixture ⇒ **it is not killed when pytest dies** | `:52-55` |
| L3 | `assert line == "ACQUIRED"` sits **before `return proc`** ⇒ on the refusal path the caller never binds `holder`, so the `finally: _reap(holder)` **never runs** and that child is leaked | `:57` vs `:81-88`, `:126-132` |
| L4 | The lock path is **one machine-global constant** with zero per-test isolation | `utils/instance_lock.py:50-51` |
| L5 | The refusal message's PID comes from bytes 0–64 of the file and is **documented as informational** | `utils/instance_lock.py:120-126`, docstring `:22-24` |

**L1 + L2** give the orphan. **L3** gives a second, self-amplifying way to create one: once any run is
poisoned, the failing test leaks *another* child. **L4** guarantees every invocation on the box
contends for the same object. **L5** is why the recorded diagnosis went wrong (§3).

### Why the count wobbles 2 / 1 / 2 on an unchanged tree

Whether a previous run's 120-second orphan is still alive when the next run reaches `test_p1` depends
on nothing but **wall-clock spacing between invocations**. A 120-second hold, in a suite that takes
~13 minutes and is re-run several times per session, is close to a purpose-built flake generator.
There is also **no `pytest.ini`, `pyproject.toml`, `setup.cfg` or `tox.ini` in the repo**, so there is
no ordering pin and no xdist configuration — the interleaving is not stable run-to-run either.

---

## 2. Three further defects found on the way (all test-side, all real)

**2a. A hardcoded port collision between two modules — 59996.**

```
tests/unit/test_instance_lock.py:280     ok2, _ = check_port_available(59996)   # asserts AVAILABLE
tests/unit/test_phase17_batch3.py:67     test_port = 59996                       # BINDS + LISTENS
```

`test_instance_lock.py` explicitly reserved private ports for itself — *"Ports private to this module,
so a stray 5001 listener cannot colour results"* (`:34-36`, `_PORT_A=59321`, `_PORT_B=59322`) — and
then used bare `59996`–`59999` in its last two classes. `59996` is exactly the port the other module
binds.

**2b. `test_phase17_batch3.py::test_fix081_persistent_socket_lock` is a second holder with no
cleanup whatsoever.** It is a bare module-level function — the file has **no classes, no fixtures, no
teardown, no try/finally**. It acquires the machine-global lock at `:71` and `:80` and releases only
on the happy path (`:76`, `:84`). **Any raise between `:71` and `:84` leaks both the file lock and the
59996 socket for the remainder of the pytest process.** Every acquire site in `test_instance_lock.py`
is protected by a `teardown_method`; this one is protected by nothing.

**2c. The suite breaks the invariant it also asserts.** `utils/instance_lock.py:35-39` states the lock
file *"is never unlinked … unlinking the path while another process holds it would let the next start
create a fresh inode, lock that, and run alongside"*, and `test_release_leaves_the_file_in_place`
(`:208-218`) exists to assert exactly that. Two setups unlink it anyway:

```
tests/unit/test_instance_lock.py:181       _LOCK_FILE.unlink()          (TestAcquireInstanceLock.setup_method)
tests/unit/test_phase17_batch3.py:70       il._LOCK_FILE.unlink(missing_ok=True)
```

On Linux this is the *false-pass* direction — two holders on two inodes, the guard silently absent.
Note `missing_ok=True` suppresses only `FileNotFoundError`; on Windows an open holder makes this raise
`PermissionError`, which `test_fix081` does not catch.

---

## 3. Ledger corrections (§C2)

Historical audit files are dated records and are **not rewritten** — this section supersedes them.

| Recorded | Verdict |
|---|---|
| `live_seed_mc1_wired_18jul2026.md:286` — *"Stale lock naming **PID 8708, which is not running**; the refusal names a different PID than the spawned one"* | ❌ **Reads a field documented as meaningless.** `_read_holder_pid` returns whatever bytes 0–64 hold; the docstring says the PID *"is now informational only … nothing branches on whether it is alive."* A dead PID there is **by design** — `test_succeeds_when_stale_lock_dead_pid` writes `999999999` on purpose. "Names a different PID than the spawned one" is **the specification**, not a symptom. |
| same — *"✅ PC-only — all `test_instance_lock` tests PASS on the VM"* | ❌ **Not a platform property.** Both contenders exist on both platforms; the VM simply did not overlap two invocations. Labelling it PC-only is what closed the investigation for nine days. |
| `p1_health_require_hmac_done_17jul2026.md:117` — *"Windows concurrent-process / file-lock flake"* | ❌ **Wrong attribution.** It is an orphaned child process plus a shared fixture, neither of which is Windows-specific. |
| `capital_snapshot_redirect_25jul2026.md:128` — *"known PC-env instance-lock flake … untouched by anything here"* | ⚠️ **Conclusion stands, label wrong.** It genuinely was not attributable to that change. "PC-env" is not why. |
| `clock_dependency_class_26jul2026.md:204-209` — *"Three suites running in parallel, contending for the machine-global `%TEMP%` lock"* + rule *"never run suites in parallel on this machine"* | ✅ **Closest to right, and the rule is sound — but incomplete.** It covers only deliberate parallel invocation. The 120-second orphan makes **sequential** runs contend too, when they are merely *close together*. Following the rule alone will not stop the flake. |
| `MONDAY_27-JUL_CARD.txt:116` — *"machine-global … flake (2/1/2 on an unchanged tree)"* | ✅ **Correct**, and §1 now explains the wobble. |

### A hypothesis checked and refuted

`test_main.py` was the obvious suspect — it is the largest baseline-failing file and it imports the
lock. **It is not a contender:** `tests/unit/test_main.py:312-313` replaces both
`acquire_instance_lock` and `release_instance_lock` with `MagicMock`. It never touches the real lock.

---

## 4. One production-code finding — LATENT, not live

`utils/instance_lock.py:176` assigns `_lock_fd = fd` **without closing the previous fd**. A leaked open
fd still holds the kernel lock, so a second successful acquire in one process would strand the first
lock until process exit. `release_instance_lock()` would then release only the second.

**Reachability:** `main.py:1801` calls `acquire_instance_lock()` **exactly once**, inside a
`try/finally` that releases at `:1808`. There is no second call anywhere in production.
⇒ **LATENT** per the live-vs-latent rule: document and pin, do not stop for it. It becomes reachable
only if the path is unlinked between two acquires — which is precisely what §2c's test code does, and
is one reason the test-side unlink is worth removing.

---

## 5. Proposed fix — contained, and it weakens nothing (§C4)

**Not applied today.** It is test-only, but it cannot be verified without running the suite, and C3
defers execution to the evening. Landing an unverified fix on a single-variable observation day is the
thing §B exists to prevent.

1. **Kill the orphan at the root — make the child die with its parent.** Replace `time.sleep(120)`
   with a blocking read on stdin; the child then exits the moment pytest closes the pipe, on both
   platforms. It still holds the lock for exactly as long as the test needs it, so **P1 and P2 are
   tested identically** — the child's lifetime stops being a wall-clock guess and becomes a
   consequence of the parent still being alive.
2. **Reap on the refusal path too** — spawn, then `try:` around the `assert`, so L3's leak closes.
3. **Isolate the lock file per process.** Both modules already `import utils.instance_lock as il`, so a
   session-scoped autouse fixture can monkeypatch `il._LOCK_FILE` to a `tmp_path` file. The properties
   under test are *contention on whatever path is configured* — redirecting the path removes the
   machine-global surface without changing one line of `utils/instance_lock.py`. `tests/conftest.py`
   already does exactly this for sentinels (`_isolate_real_sentinels`), so the precedent is in-tree.
4. **Give `test_fix081` its own port and a `try/finally`**, and drop both test-side `unlink()` calls —
   they contradict the invariant `test_release_leaves_the_file_in_place` asserts.

⛔ **What must not be done:** weakening `test_p1` (a second concurrent instance is refused) or the two
`test_p2` restart tests. Two instances trading one book double every order; a guard that wrongly
refuses a start is its own outage. The flake is in the *fixture*, never in the property.

---

## 6. What is proven, and what is not

**Proven by source, no execution required:** L1–L5; the 59996 collision; `test_fix081`'s missing
cleanup; both `unlink()` sites; `test_main.py`'s mocking; the absence of any pytest config file;
`main.py`'s single call site.

**Not proven, and honestly labelled:** which contender wins on any particular run, and the exact
Windows handle-teardown timing after `TerminateProcess`. Neither is needed for the conclusion — the
120-second orphan is sufficient on its own — but neither should be asserted without measurement.
