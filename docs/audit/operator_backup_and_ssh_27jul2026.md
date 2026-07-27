# Two operator items — offsite backup (done) · SSH lockdown (refused, for now)

**Date:** 27-Jul-2026 Monday, ~20:00 IST · **Everything below was measured, not assumed.**

> Relocated from `Downloads/OPERATOR_backup_and_ssh_27-JUL.txt` on 27-Jul under the standing
> rule that **all reports live in `docs/audit/*.md`**; Downloads carries only the CARDS and the
> REGISTER — the artifacts Rama opens under pressure.

---

## D1. Offsite backup — ⭐ the first one now exists

**The exposure, re-measured:** `/dev/sda1` is the **only** volume (96 G, 21 % used). The live
database *and every backup* sit on it. One disk failure — or one bad `rm -rf` under `data_store/`
— takes the data and all its copies together.

**It is a ~200 MB job, not a 9.3 GB one.** The 9.3 GB under `data_store/` is mostly derived
artefacts. What is irreplaceable is small.

### ⭐⭐ The runbook had a gap, and it was the worst possible one

It backed up `trading_system.db` and `analytics.db` — and **not**
`data_store/v3/forward_shadow_fs-v1.jsonl`.

That file is the out-of-sample evidence: 12.2 MB / 21,149 lines, and **the one artifact in the
system that genuinely cannot be regenerated.** The databases could be substantially rebuilt from
broker records; the forward-shadow record could not be rebuilt at all — and its producer must
never be re-run by hand. It was in **no backup anywhere**. It is now included below.

### Two other inaccuracies found and fixed

- The verify step used `sqlite3` **on the PC**. `sqlite3` is **not on this PC's PATH** — so the
  step the runbook itself calls *"the one people skip"* could not be run even by someone who
  wanted to. Rewritten to use `python`.
- It checked against *"trades 423"*. It is **430** now. A hardcoded count as a pass criterion
  drifts into a false alarm. Rewritten to compare against the VM's **own** current count, so it
  cannot go stale.

### ✅ Already done — `D:\backups\vm\2026-07-27\`

| file | check | result |
|---|---|---|
| `trading_system.db` | `integrity_check` | `ok`, trades = 430 (VM said 430 — match) |
| `analytics.db` | `integrity_check` | `ok`, candles = 310,168 |
| `forward_shadow_fs-v1.jsonl` | line count | 21,149 — **first ever copy** |
| `would_be.jsonl` | size | 599 KB |

**~199 MB total.**

### The single command, for next time

Git Bash, from `D:\Projects\trading-system`.

⚠️ Run it in the **evening**, after the 17:35 self-exit. It is safe while running too — `.backup`
uses SQLite's online-backup API, which takes a consistent snapshot across the WAL.
⛔ **Never use a plain `cp`/`scp` on a live `.db`**: it can copy a torn page and produce a backup
that looks fine and restores with gaps.

```bash
d="D:/backups/vm/$(date +%F)"; mkdir -p "$d" && \
ssh trading-vm 'cd ~/systems/trading-system/data_store && \
  sqlite3 trading_system.db ".backup /tmp/ts_snap.db" && \
  sqlite3 analytics.db ".backup /tmp/an_snap.db" && \
  sqlite3 /tmp/ts_snap.db "PRAGMA integrity_check;" && \
  sqlite3 /tmp/an_snap.db "PRAGMA integrity_check;" && \
  sqlite3 /tmp/ts_snap.db "select count(*) from trades;"' && \
scp -q trading-vm:/tmp/ts_snap.db "$d/trading_system.db" && \
scp -q trading-vm:/tmp/an_snap.db "$d/analytics.db" && \
scp -q trading-vm:'~/systems/trading-system/data_store/v3/*.jsonl' "$d/" && \
ssh trading-vm 'rm -f /tmp/ts_snap.db /tmp/an_snap.db' && \
python -c "import sqlite3,sys,os; d=sys.argv[1]; [print(' %-20s %s rows=%d'%(n,sqlite3.connect(os.path.join(d,n)).execute('PRAGMA integrity_check;').fetchone()[0],sqlite3.connect(os.path.join(d,n)).execute('select count(*) from '+t).fetchone()[0])) for n,t in (('trading_system.db','trades'),('analytics.db','candles'))]" "$d"
```

- ✅ **GOOD:** both `integrity_check` print `ok`, **and** the trades count printed by the VM
  (step 1) **equals** the trades count printed by the PC (last step).
- ⛔ If either `integrity_check` is not `ok`, **stop** and do not keep that copy.
- ⛔ A copy that opens but reports **0 rows is a FAILED copy**, not an empty database.

**Retention:** keep 7 daily + 1 weekly, delete by folder date. ~1.4 GB steady state.
**Frequency:** weekly is enough for the DBs. ⭐ But the forward-shadow file only ever *grows* and
cannot be rebuilt — so never let it go more than a week uncopied.

> **Snapshot directories are dated, therefore frozen.** `D:\backups\vm\2026-07-27\` is not
> something to keep updated; the next run writes a new dated folder beside it. Verified 27-Jul
> 20:5x: the folder holds all four files at 19:48, and a fresh `.backup` taken at 20:42 returned a
> byte-identical `trading_system.db` (133,984,256 B) — the service being inactive, nothing had
> moved.

---

## D2. SSH → Tailscale-only — ⛔ do NOT do this yet. There is a blocker.

Scoped before recommending, and **the scoping changed the answer**.

### What is already true (measured)

- Tailscale **is** installed and running on the VM: `100.74.84.44`
- The PC **is** on the tailnet and online: `100.88.112.125`
- **Port 22 is open to the WORLD**: `iptables` shows `ACCEPT tcp dpt:22` from `0.0.0.0/0`.
  (There is no `ufw`; the rules are raw iptables. Note the **webhook port is already correctly
  restricted to a single source IP** — so the pattern is understood and in use.)

### What breaks if port 22 closes to the world today

| path | effect |
|---|---|
| **cron** | ✅ nothing. Measured: **zero** crontab lines use `ssh`, `scp` or `rsync`. |
| **the deploy path** | ⚠️ breaks, but trivially fixable. `git push` goes to `trading-vm:~/trading-system.git`, and `~/.ssh/config` resolves `trading-vm` to the **public** IP `161.118.187.249`, not the Tailscale one. One line changes it to `100.74.84.44`. The PC is already on the tailnet, so this would just work. |
| **the phone fallback** | ⛔⛔ **breaks — and this is the blocker.** |

### ⛔ The blocker, stated plainly

> `tailscale status` shows **`moto-g96-5g` OFFLINE, LAST SEEN 23 DAYS AGO.**

The phone has not been on the tailnet for over three weeks. The document
`TONIGHT_IF_PC_IS_DOWN_27-JUL.txt` tells Rama to SSH **from the phone** when the PC is dead — and
that path works **today only because port 22 is open to the world**.

**Closing port 22 now would silently invalidate the emergency runbook he was told to rely on, at
exactly the moment he would need it.**

⭐ Tonight is the proof that the phone path is not hypothetical: the PC *did* go down, and that
document was written for it.

### The order to do it in — each step verifiable before the next

1. **Bring the phone back onto the tailnet, and PROVE it:** from the phone, run one of tonight's
   check commands (e.g. `ssh trading-vm "systemctl is-active trading-system"`) over Tailscale,
   **with mobile data and WiFi OFF**. ⭐ WiFi off is the point — otherwise you may be testing your
   home network, not the tailnet.
2. **Repoint the PC:** in `C:\Users\rama\.ssh\config`, change
   `HostName 161.118.187.249` → `HostName 100.74.84.44`. Then prove the **deploy path** still works
   end to end — push a docs-only branch and confirm `post-receive` runs. ⛔ Do not test this with a
   real deploy.
3. **Only then** restrict port 22 to the tailnet, keeping the existing single-IP pattern already
   used for the webhook port.
4. ⭐ **Keep a written way back in.** A cloud-console serial/VNC session is the recovery path if
   Tailscale itself fails — confirm you can reach it **before** step 3, not after.

⏰ **Not this week.** Wed is the T2 arm and Thu the close; a network change that can lock you out
has no business landing beside a real overnight position. The natural slot is alongside the **2FA
seed move already deferred to Fri 7 / Sat 8-Aug** — both are credential/access work and belong on
the same quiet day.

---

Both items are Rama's to execute. **D1's command is tested — it was run on 27-Jul.**
**D2 is a documented REFUSAL for now, with the one blocker named and its fix.**
