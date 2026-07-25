# §C — The four operator security items, made executable (or closed)

**25-Jul-2026 Saturday ~20:2x. READ-ONLY: nothing built, nothing changed, nothing run
against production beyond read-only probes.** Every "nothing uses it" below was VERIFIED
before being written, because a wrong one costs an outage.

---

## C2 — Disable `rpcbind`. ✅ VERIFIED SAFE. One command. Do it any time.

**Evidence (all measured on the VM tonight), in the order that matters:**

```
rpcinfo -p 127.0.0.1   →  ONLY portmapper itself registered (100000 v2/v3/v4, tcp+udp)
                          no NFS, no NIS/ypbind, no quotad, nothing.
grep -c nfs /etc/fstab  →  0            (no NFS mounts, so nothing needs it at boot)
running services        →  rpcbind.service and NOTHING that consumes RPC
ss -lntup               →  0.0.0.0:111 tcp AND udp, plus [::]:111  ← exposed on all ifaces
```

⇒ **It is listening on every interface and serving nothing but itself.** Safe to disable.

⚠️ **THE TRAP — disabling only the service does not work.** `rpcbind.socket` is active too
and will socket-activate the service straight back. Both must go:

```bash
sudo systemctl disable --now rpcbind.socket rpcbind.service
# verify — expect no output at all:
ss -lntup | grep :111
```

**Rollback** (if anything ever needs it): `sudo systemctl enable --now rpcbind.socket`.
**No trading impact, no restart, any time.**

---

## C1 — Rotate the Telegram bot token. ⭐ THE OLDEST LIVE SECURITY ITEM.

### Where it lives, and who reads it

| | |
|---|---|
| **Stored** | `/home/ubuntu/systems/trading-system/.env`, **mode 600 ubuntu:ubuntu** ✅ (correct) |
| **Not stored** | `.env` is gitignored; `.env.example` carries a placeholder only (scrubbed by the C-1 fix) — **no repo-history exposure** |
| **Read by (in-process, at BOOT)** | `main.py:225` (required-secret check) + `:264`; `alerts/telegram_notifier.py:288` |
| **Read by (per-invocation, from cron)** | `scripts/cron_officer.py:568`; `scripts/liveness_probe.py`; monitoring_canary — these `set -a && . ./.env` on every run |

### ⭐ The timing rule falls straight out of that split

- **Cron jobs re-read `.env` on every invocation** → they pick up a new token immediately.
- **The trading service reads it ONCE at boot and holds it in memory** → it keeps using the
  **old** token until the next start.

⇒ **Revoking the old token while the service is RUNNING silently breaks its alerts for the
rest of the day** (the sends fail; for CRITICAL the sentinel still lands, so alert_watcher
still covers you — but Telegram goes quiet). **So: rotate while the service is DOWN.**

**✅ SAFE WINDOW = any evening after ~17:35, or any weekend.** The service self-exits at
17:35 and next starts 08:15, so **rotating tonight or tomorrow needs no restart at all** —
the 08:15 boot picks up the new token as part of its normal start. **It never requires a
mid-week restart.** That was the constraint; it is satisfied for free by the daily cycle.

### The steps

```bash
# 1. In Telegram, talk to @BotFather:  /mybots → @Trade_sysbot → API Token → Revoke
#    BotFather issues the new token in the same reply. Copy it.
# 2. On the VM (service must be inactive — check first):
systemctl is-active trading-system          # expect: inactive
cp -a ~/systems/trading-system/.env ~/systems/trading-system/.env.bak-$(date +%F)
nano ~/systems/trading-system/.env          # replace the TELEGRAM_BOT_TOKEN= value
chmod 600 ~/systems/trading-system/.env     # confirm perms survived the edit
# 3. VERIFY WITHOUT SENDING ANYTHING (getMe validates the token, sends no message):
set -a && . ~/systems/trading-system/.env && set +a
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" | head -c 200
#    expect {"ok":true,...,"username":"Trade_sysbot"}   —  {"ok":false} = wrong token
# 4. Shred the backup once verified:  shred -u ~/systems/trading-system/.env.bak-*
```

⚠️ **Verify with `getMe`, not by sending a test alert** — the standing rule is no real
alerts on non-trading days, and `getMe` proves the credential without emitting anything.
**Confirmation that it works end-to-end arrives free** on Monday's first real heartbeat.

---

## C3 — The 2FA seed move. ⚠️ WINDOW RESOLVED: **SUNDAY IS NOT VALID.**

**The register said "this weekend is a valid window". That is now half wrong, and the
runbook itself says so** (`docs/decisions/RUNBOOK_2fa_seed_vm_only.md`, which already
exists and is complete — nothing needed writing):

> **"FRIDAY EVENING or SATURDAY only. NEVER the night before a trading day."**

Its stated reason: if the VM ends up holding a seed that does not authenticate, **the 08:15
TOTP refresh fails and the system boots unable to trade**. Fri/Sat buys ~2 days to detect
and recover; Sunday buys none. (And the token cron does not run at weekends, so weekend
detection is manual.)

**⇒ VERDICT: tonight (Saturday, now 20:2x) is the LAST valid slot this weekend. Sunday is
EXPLICITLY EXCLUDED. If tonight is missed, the next window is FRIDAY 31-JUL evening or
SATURDAY 1-AUG.** Do not carry "this weekend is available" into Monday — by then it is
simply false.

**⭐ BUT THE TIMING RULE ONLY BINDS PATH B, AND PATH A IS THE ZERO-RISK ONE.**
The runbook offers two paths, and the failure mode the timing rule exists for — *the VM
holding a seed that does not authenticate* — **cannot occur under Path A**, which does not
touch the VM's seed at all:

| Path | What it does | Time-constrained? |
|---|---|---|
| **A — VM-only, NO rotation** | delete the **PC's** copy of the current, known-working seed; the VM keeps the seed it already proves daily | **No.** Nothing on the VM changes; there is no boot to break. A local file edit. |
| **B — rotate + VM-only** | re-enrol → new seed → VM-only → verify. **ONE-WAY, no rollback.** | **Yes — Fri/Sat only.** |

**Path A achieves the actual goal** ("both factors are no longer on the dev box") at zero
credential-chain risk. *Recorded as a reading, not a decision: the runbook states its
timing rule globally, so if Rama wants Path A on Sunday he should do so knowingly.*

### 🔴 One thing that is unambiguously safe and still undone — do it tonight either way

**The PC `.env` is STILL world-readable and still holds the seed** (measured tonight:
`644 rama`, 5 `ZERODHA_TOTP` matches). The runbook flagged this on 20-Jul as
*"tighten the PC perms regardless of this change"* and it is still open. On Windows this
is an ACL change, not `chmod`:

```powershell
icacls "D:\Projects\trading-system\.env" /inheritance:r /grant:r "$env:USERNAME:(R,W)"
```

That removes inherited access for other principals. **Independent of A vs B, and reversible.**

---

## C4 — Off-disk backup target. ⭐ It is a 176 MB problem, not a 9.4 GB one.

**Measured tonight:**

```
/dev/sda1   96G, 21G used, 76G avail (22%)     ← ONE volume for everything
live DB and data_store/backups are on the SAME DEVICE (stat %d = 2049 for both)
backups dir = 9.4 GB
no ~/.aws, no ~/.config/rclone, 0 offsite cron jobs
```

⇒ The exposure is real and single-point: **one disk failure, or one bad `rm -rf` under
`data_store/`, takes the live DB and every backup together.**

**⭐ THE SCOPING INSIGHT: you do not need the 9.4 GB offsite.** The backups are *derived
artefacts* of the live DB. What is irreplaceable is:

```
trading_system.db   123 MB
analytics.db         52 MB
                    ─────
                    ~176 MB   ← a nightly copy of this is the whole job
```

**Cheapest real fix — PULL from the PC over the SSH that already works:**

```powershell
# on the PC, nightly (Task Scheduler), no new credential anywhere on the VM:
scp trading-vm:~/systems/trading-system/data_store/{trading_system,analytics}.db `
    "D:\backups\vm\$(Get-Date -f yyyy-MM-dd)\"
```

⭐ **PULL, not push, is the security point:** a compromised or failing VM cannot reach the
copy, and no new credential is added to the box being protected. Cost: £0, ~176 MB/night.

**The alternatives, for completeness — not recommended:**
- **Cloud object storage (S3/B2) with a write-only key** — survives losing the PC too, but
  puts a *new credential on the VM*, which is the thing being protected. Ongoing cost.
- **A second volume / provider snapshot** — protects against filesystem corruption, **not**
  against provider-account loss, and is the same blast radius for an accidental delete.

⚠️ **Whatever is chosen, copy the DB while the service is DOWN, or use
`?mode=ro&immutable=1`/`sqlite3 .backup`** — a plain `cp` of a live WAL database can copy a
torn page. **REPORTED, NOT BUILT** per §C5.

---

## Summary — what Rama can do, and when

| item | status | when |
|---|---|---|
| **C2 rpcbind** | ✅ verified safe, one command (both `.socket` AND `.service`) | any time |
| **C1 Telegram rotation** | ✅ steps written, verify with `getMe` not a test alert | **any evening after 17:35 or any weekend — never needs a mid-week restart** |
| **C3 2FA seed** | ⚠️ **Sunday INVALID**; tonight is the last slot, else Fri 31-Jul / Sat 1-Aug. Path A is zero-risk and arguably unconstrained | tonight, or next Fri/Sat |
| **C3b PC `.env` perms** | 🔴 still 644 and still holds the seed — unambiguously safe to fix | **tonight, independent of everything** |
| **C4 off-disk backup** | scoped: ~176 MB/night, PULL from PC | reported, not built |
