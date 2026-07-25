# §B — The two backup items, decide-ready

**25-Jul-2026. READ-ONLY: nothing built, nothing scheduled, nothing deleted.**

---

## B1 — `predeploy-*`: the one fact that decides it

**Question: is anything in those four files not already in a surviving backup?**

**⭐ ANSWER: NO. They are pure duplication.** MEASURED on the VM tonight:

```
oldest recoverable point, ALL backups ....... 2026-07-12   (5 days BEFORE the predeploy files)
daily backup dates present .................. 12,13,14,15,16,17,18,19-Jul — unbroken
surviving pre_*.db set ...................... 20 files, spanning 17-Jul → 22-Jul
  incl. pre_deploy_p1_health_hmac_20260717_211747.db   (17-Jul 21:17)
  incl. pre_deploy_regime_p0_20260717_225742.db        (17-Jul 22:57)
the predeploy-* files ....................... 17-Jul 02:00 and 02:39
```

The 17-Jul state is **bracketed on both sides** — by a 16-Jul daily backup before it and by
two surviving `pre_*` backups later the same day — inside an unbroken daily series running
from 12-Jul. The only thing the `predeploy-*` pair adds is an **intra-day point at 02:00 on
a date already covered three times over.**

⇒ **Deleting them loses no recoverable state.** ~764 MB freed.

⚠️ **CARRY THE TRAP (unchanged): renaming them to `pre_*` is a DELETE IN DISGUISE.**
Retention is `("pre_*.db", 20)` keep-newest-20, and at 17-Jul-02:00 these would be the
**oldest** in that set — so "fixing the typo" hands them to the next nightly run. If the
intent is to keep them, they must go into an **explicitly protected pattern**, not into
`pre_*`.

**Options unchanged (Rama's call — deleting backups is not mine):**
**A.** delete (~764 MB, loses nothing per the above) · **B.** keep, and add a real protected
pattern to `backup_retention.py` so the protection is a rule rather than a one-character
accident · **C.** leave as-is (costs 764 MB and leaves a class that silently escapes
retention).

---

## B2 — Off-disk backup: the runbook

**The exposure, measured:** `/dev/sda1` is the only volume (96 G, 21 G used). The live DB
and `data_store/backups` are on the **same device** (`stat %d` = 2049 for both). No
`~/.aws`, no `~/.config/rclone`, **0 offsite cron jobs**. One disk failure — or one bad
`rm -rf` under `data_store/` — takes the live database *and* every backup together.

**⭐ It is a 176 MB job, not a 9.4 GB one.** The 9.4 GB of backups are **derived artefacts**
of the live DB. What is irreplaceable:

```
trading_system.db   123 MB
analytics.db         52 MB
                   ───────
                   ~176 MB per night
```

### ⚠️⚠️ THE WAL WARNING — READ BEFORE COPYING ANYTHING

**A plain `cp`/`scp` of a live SQLite WAL database can copy a torn page.** The `.db` file
alone is not self-consistent while writes are in flight: committed data may still live in
the `-wal` sidecar, and a naive copy can land mid-checkpoint. The result is a backup that
*looks* fine and fails `integrity_check` — or worse, restores with silent gaps.

**Two safe forms. Use one of them, never a bare `cp`:**

| form | when | why it is safe |
|---|---|---|
| ⭐ **`sqlite3 SRC ".backup DEST"`** | **any time, including while running** | the online-backup API takes a consistent snapshot across WAL + main, under SQLite's own locking |
| **plain copy** | **only while the service is DOWN and checkpointed** | no writer, nothing in flight. Still verify. |

The service self-exits at 17:35 and starts 08:15, so **an evening pull qualifies for the
second form** — but `.backup` is safe either way and costs nothing extra, so prefer it.

### The runbook (PULL from the PC — do NOT push)

⭐ **Pull, not push, is the security point:** a compromised or failing VM cannot reach the
copy, and **no new credential lands on the box being protected.** Cloud object storage would
put a write key *on the VM*, which is the thing at risk.

**Step 1 — on the VM, make a consistent snapshot (safe even if running):**

```bash
ssh trading-vm 'cd ~/systems/trading-system/data_store && \
  sqlite3 trading_system.db ".backup /tmp/ts_snap.db" && \
  sqlite3 analytics.db      ".backup /tmp/an_snap.db" && \
  sqlite3 /tmp/ts_snap.db "PRAGMA integrity_check;" && \
  sqlite3 /tmp/an_snap.db "PRAGMA integrity_check;"'
```
**Both `integrity_check` must print `ok`. If either does not, STOP — do not keep that copy.**

**Step 2 — pull to the PC:**

```powershell
$d = "D:\backups\vm\$(Get-Date -f yyyy-MM-dd)"
New-Item -ItemType Directory -Force $d | Out-Null
scp trading-vm:/tmp/ts_snap.db "$d\trading_system.db"
scp trading-vm:/tmp/an_snap.db "$d\analytics.db"
ssh trading-vm 'rm -f /tmp/ts_snap.db /tmp/an_snap.db'
```

**Step 3 — verify the copy that landed (this is the step people skip):**

```powershell
# size sanity + the only check that actually proves the file is usable
Get-ChildItem $d | Select-Object Name,Length
sqlite3 "$d\trading_system.db" "PRAGMA integrity_check; select count(*) from trades;"
sqlite3 "$d\analytics.db"      "PRAGMA integrity_check; select count(*) from candles;"
```
**A good copy: `integrity_check` = `ok`, and the row counts are non-zero and close to the
VM's (trades 423, candles 275,129 as of 25-Jul).** A copy that opens but reports `0 rows`
is a failed copy, not an empty database.

**Retention on the PC:** keep 7 daily + 1 weekly; ~1.2 GB steady-state. No script needed —
delete by folder date.

⛔ **NOT BUILT AND NOT SCHEDULED** per §B2. This is a runbook for Rama to run manually, or
to hand to Task Scheduler when he chooses.
