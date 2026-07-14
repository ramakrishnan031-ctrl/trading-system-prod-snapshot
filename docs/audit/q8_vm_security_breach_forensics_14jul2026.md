# Q8 — VM Security: Breach Investigation (Read-Only Forensics)

**Date:** 14-Jul-2026 (off-market, post-deploy `2dc69d5`)
**Scope:** READ-ONLY forensics of the trading VM (`161.118.187.249`, Oracle Cloud, user `ubuntu`).
**Mandate:** Change nothing. Every control is PROPOSE-not-apply. Production trading config untouched.
**Trigger:** Concern that a *foreign-IP SSH session occurred inside the 18:00–08:00 "time-lock" window*, raising the possibility that `.env` (Zerodha API key + secret) was read.

> **Recovery pre-check (done first):** `main == VM-bare == 2dc69d5`, working tree clean (last checkout Jul 14 19:03, matching the PB‑01 shadow deploy). VM clock correct (IST/UTC agree). The deploy completed before the disconnect. ✔

---

## 1. WERE THE BROKER CREDENTIALS EXPOSED? — **NO (high confidence).**

**The Zerodha API key + secret in `/home/ubuntu/systems/trading-system/.env` were not exposed.** No rotation is *compelled* by the evidence. (If you want zero residual doubt it is a low‑cost precaution — see §6 — but nothing here forces it.)

**Why this is a defensible "NO," not a hand-waved "we found nothing":** credential exposure requires an *unauthorized party to gain `ubuntu`/`root` on the box and read the file*. That single event would have to appear in at least one of **five independent, mutually-corroborating, intact records** — and it appears in none:

| Record | What it would show | What it shows |
|---|---|---|
| `auth.log` (back to Jun 21) + `wtmp` (back to May 3) | any successful login | **2,246 successful logins — 100% `publickey`, 100% user `ubuntu`, 100% from Bharti Airtel India dynamic IPs.** Zero password, zero keyboard-interactive, zero root. |
| `authorized_keys` | any non-Rama key | one key (`BRi6…` "Trading VM"); the Jul-13 change was Rama's own (§2). opc/root keys are Rama's, OCI-neutered. |
| `security_state.json` (security-watcher's own independent state) | new key / new IP / file change | corroborates auth.log; its `known_login_ips` list is entirely Airtel. |
| CRITICAL sentinels + `bash_history` | operator actions | coherent operator narrative; **no `scp`/`rsync`/`sftp`/`nc`/`base64`/`curl -T`/`cat .env`** anywhere. |
| `copy_audit.log` + auditd `copy_attempt` | any VM→PC file copy | **zero copy attempts since the Jun-20 activation test.** |

`.env` itself: perms `0600 ubuntu:ubuntu`, **mtime + ctime = Jul 5 22:22 (unchanged since)**, and auditd's `env_change` watch shows **0 write/attribute events** — the file was never modified or re-permissioned.

**Honest limits of the evidence (so the "NO" is earned, not naive):**
- auditd watches `.env` with `-p wa` (**write/attribute only — it does NOT log reads**). So there is no positive "read audit" proving every reader was legitimate. The "NO" rests instead on *bounding the set of possible readers*: only someone with `ubuntu`/`root` could read it, and every access record shows that set was exclusively Rama.
- `atime` (Jul 13 22:23) is under `relatime` and reflects a *legitimate* read (systemd `EnvironmentFile` / a cron `. ./.env`); it is not a reliable exfiltration tracer either way.
- The one theoretical read-vector the copy-gate cannot block — a **PC-initiated pull** (`scp ubuntu@vm:.env`) — still requires an authenticated SSH session first, and **no unauthorized session ever existed** to initiate one.
- The logs are **intact and coherent** (no truncation/rotation gaps in the relevant window; five independent sources agree). Hiding a breach would require tampering all of them, plus root escalation, plus leaving no key/binary/cron trace — none of which is present. This is why "we would have found it" is a fair claim.

**What the "foreign IP" actually was.** The only non-India address that ever touched the box is `45.198.224.120` (**Stockholm, SE — Vpsvault.host**), which appears in the security-watcher's `geoip_cache` and is the most likely origin of the "foreign IP" alarm. Its full record: **1,402 failed `root` attempts, 482 failed `ubuntu`, 327 btmp failures, and 0 successful logins.** It is a brute-force bot, defeated by key-only auth. It never got a shell.

---

## 2. WHAT ELSE WAS ACCESSED OR TAKEN — **Nothing provable; and the "unexpected key" was Rama's own rotation.**

- **Database (`trading_system.db`, `analytics.db`) — no evidence of copy.** No `scp`/`rsync`/`sftp` in history; `copy_audit.log` empty since Jun 20; auditd `copy_attempt` = 0. (Same read-audit caveat as §1: no unauthorized session existed to copy it.)
- **Code (the IP) — no evidence of copy.** Same channels, same result. (Note: the code also lives in the bare git repo and is pushed over SSH by Rama; a `git clone` by an authenticated session is not separately audited — but again, no unauthorized session existed.)
- **The authorized_keys change (the thing that most looks like an intrusion) was Rama.** On **Jul 13 09:16** the sole `ubuntu` key was changed to `BRi6…` ("Trading VM"). Proof it was Rama, not an attacker:
  - His `bash_history` (intact, 767 lines, ending exactly at **Jul 13 09:17**) shows repeated `nano ~/.ssh/authorized_keys` + `chmod 600 ~/.ssh/authorized_keys` — a manual key edit.
  - `wtmp` shows his interactive session from Airtel IP `157.51.61.99` spanning **09:14–09:17**, bracketing the edit.
  - The new key `BRi6…` has since authenticated **only** from Rama's Airtel IPs (`157.51.54.166`, `.73.138`, `.61.99`, `.79.188`, `.54.117`, `110.224.80.202`). An attacker-installed key would authenticate from the attacker's infra; this never happens.
  - All **eight** ED25519 fingerprints that have ever authenticated (`uDRN8` ×900, `DrHT9` ×788, `vsjJ8` ×184, `BRi6` ×165, `WcC0=rama@DESKTOP-029USHU` ×95, `XrYwY` ×67, `9xTid` ×42, `wPqEm` ×5) authenticated **exclusively from Airtel India** — successive rotations of Rama's own keys.
- **The security-watcher CAUGHT it and this is almost certainly what started this investigation.** `security_state.json` contains `alerted: { "authkeys:unexpected:SHA256:BRi6…" }`, and CRITICAL sentinels were **generated and delivered** at `Jul 13 09:15:58 / 09:15:59 / 09:16:00 / 09:17:01`. An "unexpected authorized_keys fingerprint" alert — layered on top of the mistaken belief that the "time-lock" should have blocked the evening SSH session (§4) — reads exactly like "someone put a foreign key on my box inside the locked window." It was Rama's un-baselined rotation.

---

## 3. IS THE BOX CLEAN TODAY? — **Yes, on every check performed. (One caveat: no formal rootkit scanner is installed — proposed, not run.)**

| Persistence vector | Result |
|---|---|
| `authorized_keys` (all users) | `ubuntu`: only `BRi6…` (Rama's). `opc` + `root`: Rama's `rama@DESKTOP-029USHU` key wrapped in the standard **OCI `exit 142` forced-command** (neutered — prints "login as ubuntu" and exits). Passwords **Locked** for opc/ubuntu/root; `PermitRootLogin no`; `opc` **not** in sudo. Only real login path = `ubuntu` via key. |
| Cron (all users + `/etc/cron*`) | 100% the documented trading-system jobs (backups, token refresh, preflight, EOD reports, `forward_shadow_record`@18:15, gemini ops…). **No injected entries.** root/opc: no crontab. |
| systemd units/timers | **None** created or modified in the last 30 days. |
| Listening ports | `sshd:22`, `rpcbind:111` (pre-existing hardening item, §5), loopback `:8500` (GUI), tailnet-only `:443/:42423` (Tailscale). **No foreign/reverse-shell listener.** |
| Processes | Every long-running process is a recognized system / OCI (oracle-cloud-agent, unified-monitoring-agent) / trading (`token_watcher.sh`) daemon. Nothing anomalous. |
| SUID binaries (changed <40d) | Only `/usr/lib/openssh/ssh-keysign` (Jul 9 — a legitimate `openssh` unattended-upgrade). |
| Binary integrity | `dpkg --verify openssh-server coreutils libpam-modules libpam-runtime sudo` → **clean** (no trojaned ssh/PAM/coreutils). |
| sudoers | Only OCI + cloud-init defaults (incl. the standard `ubuntu ALL=(ALL) NOPASSWD:ALL` cloud-image grant). No injected sudoers file. |
| `/etc` changed (<7d) | `ld.so.cache`, `timezone`, OCI monitoring configs — all benign. **No `/etc/passwd`/`/etc/shadow` change** (no user added). |
| Outbound connections | None suspicious at inspection time (point-in-time). |

**Rootkit/malware scan — NOT run (tooling absent).** `rkhunter`, `chkrootkit`, `debsums`, `clamav`, `aide`, `unhide` are **all absent**, and installing one is a state change outside the read-only mandate. The manual persistence + integrity sweep above (processes, ports, SUID, `dpkg --verify`, cron, systemd, `/etc` mtimes) covers much of the same ground and is clean. A formal scan is **proposed** in §5/§6.

> **Meta note (so you don't misread tonight's alerts):** the flurry of "Non-whitelisted sudo command" INFO alerts firing right now (`sudo passwd/wc/crontab/find/zgrep/sshd`) are **my own forensic commands** tripping the security-watcher in real time — which incidentally confirms the monitor is alive and watching. They are not an intruder.

---

## 4. WHY THE "TIME-LOCK" FAILED — **It didn't. It was never an SSH control. (Category error.)**

The "18:00–08:00 time-lock" is **`scripts/copy_gate.py`**, whose job is to gate **VM→PC file *copying*** (`scp`/`sftp`/`rsync`, via `/usr/local/bin` shims). SYSTEM_MAP states it plainly: *"git push (ssh) + interactive ssh are NOT wrapped."* It was **never designed to block an SSH login**, so an evening SSH session inside that window is not a lock failure — it's the documented, intended behaviour. (`copy_protection_enabled: true`; the Jun-20 activation test in `copy_audit.log` shows it correctly denying no-token and allowing with-token.)

**Confirmed: there is NO SSH access-time restriction of any kind.** Effective sshd config: `PermitRootLogin no` (`99-trading-security.conf`) + `PasswordAuthentication no` (`60-cloudimg-settings.conf`). **No `Match` block, no `pam_time` in the sshd PAM stack.** SSH is reachable **24/7 on `0.0.0.0:22` from any IP**, protected by key-only auth + fail2ban (35,319 total failed / 450 banned / 0 succeeded — working as intended against the bots).

So the real situation is not "a lock failed," it's **"the imagined lock never existed on this surface."** The genuine gap is that SSH has no network/time restriction at all. **And critically — do not naively "fix" it with an 18:00–08:00 SSH lock: that window is exactly when Rama does his off-market deploys. A time-locked SSH would lock the operator out of his own deploy window.** (This is why §5 ranks *network-scoping* and *key-hardening* above time-locking.)

---

## 5. PROPOSED CONTROLS (ranked; PROPOSE — do not apply). Each with lockout risk + recovery path.

> Universal recovery path for all SSH-affecting changes: **Oracle Cloud web console → Instance → Console Connection (serial/VNC)** gives out-of-band access that does not depend on port 22. Verify it works *before* touching SSH. `fail2ban` is retained for brute-force noise but **is not a control against a stolen key** — say so plainly so it stops being mistaken for one.

**C1 — Re-baseline the SSH key (`python scripts/approve_ssh_keys.py --apply`). [do first; near-zero risk]**
The live key `BRi6…` doesn't match the baseline (`uDRN8…`), so the security-watcher has been in a *permanent* "unexpected key" alert state since Jul 13. That's harmful two ways: it's alert-fatiguing, and it means a **real** future intrusion key would blend into an already-firing alert. Re-baselining resets the tripwire so the *next* unexpected key stands out. *Lockout risk: none (updates the monitor's baseline; does not touch sshd). Recovery: n/a.*

**C2 — Scope SSH to Tailscale-only (close `0.0.0.0:22`). [highest security value; moderate lockout risk]**
Rama already runs Tailscale (the GUI is tailnet-only). Binding sshd to the tailnet interface and closing 22 in the **OCI Security List / NSG** + host firewall eliminates the *entire* brute-force surface and makes a stolen key useless without also being on the tailnet. *Lockout risk: real if Tailscale is down on his client. Recovery: OCI serial console; and stage it by adding the Tailscale rule and confirming SSH-over-tailnet works **before** removing the public rule.*

**C3 — Passphrase + ssh-agent on the private key(s). [highest value vs the actual residual risk; low lockout risk]**
The real residual threat is **key theft from Rama's PC**, not the server. His PC key (`trading_vm_secure`) is documented as having **no passphrase** → the key *file* alone is enough. Adding a passphrase (kept in ssh-agent) makes a stolen file useless. *Lockout risk: low (he controls his own client). Recovery: keep a second enrolled key.*

**C4 — Telegram alert on every successful SSH login (IP + user + time). [low risk]**
Partially exists (the watcher does "new IP" INFO alerts). Extending to *every* accepted login gives seconds-to-know visibility. *Lockout risk: none (alert-only).*

**C5 — Close/curtail `rpcbind` (`0.0.0.0:111`). [low risk]**
Unused portmapper exposed to the internet (a standing SYSTEM_MAP finding). `systemctl disable --now rpcbind` (or firewall it) removes needless surface. *Lockout risk: none for this workload (nothing here uses RPC/NFS). Recovery: re-enable if some OCI feature needs it — verify first.*

**C6 — Restrict the app's exposure to `.env` — but note it buys little *here*. [caution]**
The generic advice "make `.env`/`data_store` root-only" assumes a multi-user box. **This box's trust boundary *is* `ubuntu`** — it's the only login user *and* it has `NOPASSWD:ALL` sudo, so `ubuntu ≡ root`; anyone who is `ubuntu` can `sudo cat .env` regardless of file mode. To make C6 meaningful you would *also* have to strip `ubuntu`'s passwordless sudo and split secrets so only the systemd `EnvironmentFile` (root-read) holds the broker keys — a large operational change that would break the ~40 cron jobs that source `. ./.env` as `ubuntu`. **Recommended framing: the higher-leverage move is keeping unauthorized parties off `ubuntu` entirely (C2/C3), not tightening a file mode that `ubuntu`-sudo already defeats.** *Lockout/operational risk: high if done bluntly. Not recommended as stated.*

**C7 — Formal read-only rootkit/integrity scan. [propose; not run — tooling absent]**
`sudo apt-get install chkrootkit rkhunter debsums` then a **read-only** `chkrootkit -q`, `rkhunter --check --sk`, `debsums -s`. Reports only; remediate nothing. (Installing the tools is the only state change — hence proposed, not done tonight.) *Lockout risk: none.*

**C8 — Hardware key (FIDO2/YubiKey `sk-ssh-ed25519`). [strongest; needs hardware]**
The key cannot be copied at all. Best-in-class, but requires a device and enrollment. *Lockout risk: loss of the device → keep a backup key enrolled. Recovery: OCI console.*

**Explicitly NOT recommended:** a time-locked SSH (C-style) on 18:00–08:00 — it targets the exact window Rama works in (§4), and it defends against nothing the evidence shows.

---

## 6. WHAT RAMA MUST DO HIMSELF — TODAY

1. **Confirm the `BRi6…` key is yours.** It is `ssh-ed25519 …Jg/J "Trading VM"`, fingerprint `SHA256:BRi6UmV8Out1HWj2Bvl8ahPooIUTZp8kcOPCdirM4OY`, and it matches your Jul-13 09:16 manual rotation. If yes (expected) → run **`python scripts/approve_ssh_keys.py --apply`** to re-baseline and silence the standing "unexpected key" alert (C1). If you do **not** recognize it → treat as live intrusion and rotate the key immediately.
2. **Broker API key/secret rotation — your call, NOT an emergency.** The evidence says they were not exposed. If you want zero residual doubt, regenerate the Zerodha app key+secret on the Kite developer console and update `.env` (+ re-run the TOTP flow) — a low-cost precaution, not compelled.
3. **Decide on C2 (SSH → Tailscale-only) and C3 (key passphrase).** These are the two controls that actually address your real threat (a stolen key), and you already have Tailscale. Do them off-market with the OCI serial console confirmed as your recovery path.
4. **Nothing operational is at risk for tomorrow.** This was read-only; no production/trading/config/SSH change was made. The 15-Jul supervised session proceeds as planned (see the ledger's STOP conditions).

---

## Appendix — evidence index (all read-only)

- **Logins:** `last -Fai` (wtmp→May 3); `sshd Accepted` across `auth.log{,.1,.2-.4.gz}` (→Jun 21): 2,246 accepted, all `publickey`/`ubuntu`/Airtel-IN; 0 password, 0 root. 8 ED25519 fps, all Airtel-sourced.
- **Foreign IP:** `45.198.224.120` (Stockholm, Vpsvault.host) — 0 accepted, 1,402 failed root / 482 failed ubuntu / 327 btmp. `fail2ban`: 35,319 failed / 450 banned / 0 success.
- **`.env`:** `0600 ubuntu:ubuntu`, mtime/ctime Jul 5 22:22; auditd `env_change (-p wa)` = 0 events (reads not audited).
- **Key change:** `authorized_keys` mtime Jul 13 09:16; sole key `BRi6…`; baseline/`security.yaml` still `uDRN8…` (mismatch = un-baselined rotation); `bash_history` nano-edits end 09:17; watcher alerted + delivered sentinels 09:15:58–09:17:01.
- **Persistence:** cron = documented jobs only; 0 systemd changes/30d; listeners = 22/111/8500(lo)/443(ts); `dpkg --verify` openssh/coreutils/pam/sudo clean; sudoers = OCI+cloud-init only; opc/root keys OCI-neutered, passwords Locked.
- **Egress:** `copy_audit.log` empty since Jun 20; auditd `copy_attempt` = 0.
- **Detection health:** security-watcher `Type=simple`+`Restart=always` periodic model, live (flagging this investigation's own sudo calls); alert→sentinel→`.delivered` email chain intact.

**Verdict:** No breach. The "foreign SSH session inside the time-lock" is a composite of (a) a brute-force bot that never got in, (b) Rama's own un-baselined key rotation that the monitor correctly alerted on, and (c) the misconception that the copy-gate time-lock is an SSH control. The genuine, actionable gaps are: SSH exposed 24/7 to the world (C2), no key passphrase (C3), a standing un-reset key-baseline (C1), and `rpcbind` exposure (C5).
