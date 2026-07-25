# F2 is DROPPED — and one dangerous belief corrected

**25-Jul-2026. Rama's decision: DROP.** Recorded as **dropped, not deferred and not
pending.** Nothing built.

---

## ⚠️⚠️ A2 — THE CORRECTION THAT MATTERS MORE THAN THE DECISION

Rama said he would use **`ssh trading-vm ./halt.sh`** or `systemctl stop`.

# `halt.sh` does not exist.

It was a **hypothetical I wrote in the F2 scoping report** — an illustration of what a
usable operator gesture *would* look like once a signed body made hand-signing impractical.
It was never built and was never proposed for building.

**VERIFIED, not assumed:**

```
repo:            find . -name "halt*"                  →  nothing
repo history:    git log --all -S "halt.sh"            →  ONE hit: 601b6f3, my own F2 report
VM:              find /home/ubuntu -maxdepth 3 -name "halt*"  →  nothing
VM PATH:         which halt.sh                         →  not found
VM:              /home/ubuntu/halt.sh, .../trading-system/halt.sh  →  neither exists
shell scripts calling soft_kill                        →  none
```

⭐ **And the asymmetry that makes the wrong belief easy to hold: `deploy/resume.sh` EXISTS**
(in the repo and on the VM, alongside `install_vm_services.sh` and `token_watcher.sh`).
There is a documented way to *resume* and no counterpart to *halt*. Anyone reasoning by
symmetry would assume one exists. **That is not a careless assumption; it is an invited
one.**

⛔ **If he reaches for `halt.sh` during an incident it will fail — at exactly the moment a
wrong belief costs the most.** Corrected in the register **and** in
`MONDAY_27-JUL_CARD.txt`, which is the document he will actually have open.

## THE ONLY HALT THAT WORKS TODAY

```bash
ssh trading-vm sudo systemctl stop trading-system
```

### A3 — And what it costs, stated plainly

**It stops everything: entries AND exit management.** There is **no way today to block new
entries while continuing to manage open positions** — which is precisely the capability F2
would have added, and the reason it looked worth building.

**If the service is stopped while holding an open position, that position is left to its
broker-side SL/TGT with no system monitoring.** Concretely, what is lost:

- `order_monitor` polling for fills/rejections on the exit legs
- `order_reconciler` broker-truth reconciliation
- the EOD square-off scheduler (15:17) — **the position will not be auto-squared**
- CHECK1's external-close backstop

**This is survivable, and the reason is worth knowing:** the SL and TGT legs are **real
resting orders at the broker**, not system-simulated ones (423/423 trades are LIMIT_TRIPLE,
with static legs already placed). So a stopped system does not leave a naked position — it
leaves an *unmonitored* one whose broker protection is intact.

⚠️ **But EOD square-off is the real gap:** stop the service mid-session with a position open
and nothing will flatten it at 15:17. It will sit until a broker-side leg fills, or until
the broker's own RMS auto-square (~15:20 for MIS). **Restart the service, or square manually,
before the close.**

**To resume:** `deploy/resume.sh` clears a persisted kill, then start the service.

---

## A1 — F2: DROPPED, with the cost record

**Reason: the ergonomic benefit evaporated once a signed body became mandatory.** Five
read-only investigations; **one item got smaller, four got bigger:**

| what was believed | what was found |
|---|---|
| auth is the blocker; C6 unblocks it | ✅ C6 delivered one named decision with a contract |
| a route on `:8080` inherits auth | ⚠️ the app has **no reference to the live `KillSwitch`** (it reads the DB) and `is_active()` returns `self._state` with no runtime re-read ⇒ **a DB write halts nothing** |
| the latency worry is the 8 s Telegram leg | ⚠️ **~38 s worst case**, dominated by a 30 s DB `busy_timeout` nobody had costed |
| `threads=1` may force a weaker 202 design | ✅ an unexamined default; raising it is safe — **the one item that shrank** |
| reuse `_authenticate` | ⚠️ **ruled out** — needs a separate secret **and** a signed body |
| "halt without SSH" is the benefit | ⚠️⚠️ a signed body forces a helper script ⇒ the real gesture is `ssh … ./halt.sh` — **the same gesture as `systemctl stop`** |

**The remaining gain was semantic (an audited SOFT_KILL that permits exits), never
ergonomic.** Rama chose to drop it and use SSH. Recorded.

---

## ⛔ A4 — WHY THE "CHEAP CLI" ALTERNATIVE IS NOT CHEAP

I suggested a VM-side CLI as the cheapest path if F2 were ever wanted. **On inspection that
suggestion has the same root problem as the web route, and it is written down here so
nobody revives it casually — including me.**

**A CLI is a SEPARATE PROCESS.** Kill state is **memory-authoritative**: `is_active()`
returns `self._state` from the running trading process, with no runtime re-read of the DB
(KS12, verified in source). So:

> **A CLI that writes `kill_switch_state` halts NOTHING — exactly like the web route it was
> proposed to avoid.** It would change what `/health` displays and what the next boot loads,
> while the running process continues placing orders.

**To actually halt the live process, a separate process must SIGNAL it** — and that is where
the cost is:

- A Python **signal handler runs in the main thread**, interrupting whatever it was doing.
- `KillSwitch` uses a **re-entrant lock (KS4 RLock)**. A handler firing while the main thread
  already holds that lock would **re-enter it** and could mutate kill state *mid-operation* —
  between a state test and its dependent write.
- `soft_kill` is explicitly built around persist-first atomicity (KS9). A re-entrant
  interruption is precisely the hazard that invariant exists to prevent.
- The existing async-flatten worker already documents this class of problem (M-C8, the
  dedicated `_flatten_lock`), so the codebase has met it before.

⇒ **"Skip the web surface" does not skip the hard part.** The hard part was never HTTP; it
is that **an out-of-process actor cannot safely mutate in-process authoritative state**
without a designed handoff. Any future F2 — web, CLI, or signal — has to solve that first.

**⛔ NOT BUILT. NOT DESIGNED. Recorded so the idea is re-examined, not re-adopted.**
