# R12 — ALLOWLIST PATH ENUMERATION — 04-Aug-2026

**Status: `<MEASURED — NOTHING NARROWED, NOTHING AUTHORISED>`.** This is the measurement **D4
said was owed before any narrowing**. Read-only: no allowlist entry added, removed or edited; the
VM was not contacted.

Subject: `.claude/settings.local.json` → `permissions.allow`, at `b594294`. The file is
**git-ignored** (`.gitignore:84`) and PC-local, so it has no history and no review.

> ⚠️ **THE ONE UNMEASURED PREMISE, STATED RATHER THAN ASSUMED.** Everything below reads each
> entry as a **literal prefix with a trailing `*` wildcard**, which is what the entries' own
> shape implies. ⛔ **I did not measure the matcher itself.** If matching is stricter than that,
> the reach figures shrink; if looser, they grow. **The conclusions about which surfaces are
> INDEPENDENT do not depend on it.**

---

## 1. THE SHAPE — 533 entries

| tool | entries |
|---|---|
| `PowerShell` | 379 |
| `Bash` | 150 |
| `Read` | 3 |
| `mcp__claude_ai_Google_Drive__create_file` | 1 |

**65** are prefix/wildcard rules ending in `*)`, i.e. an arbitrary suffix is permitted.

## 2. ⭐⭐ THE HEADLINE — D4's REMEDY NARROWS ONE OF **TWO INDEPENDENT CHANNELS**

The register frames R12 as *"21 `check_vm_state` entries … eleven point at `~/check_vm_state.py`,
a path Rama's own VM check proved absent"*, with the remedy being removal of the state-changing
ones. **Measured, that is half a remedy.**

**All 21 `check_vm_state` entries are on the `PowerShell` tool** — `Counter({'PowerShell': 21})`.
And there is **no `PowerShell(ssh *)` wildcard**: the only PowerShell prefix rules touching remote
access are `PowerShell(ssh-add *)` and `PowerShell(scp *)`.

⇒ **On the PowerShell channel, removal genuinely bites: 21 of 21 are NOT subsumed by any wildcard.**

⛔ **But the Bash channel is wide open, and it reaches the same VM:**

```
Bash(ssh *)                                          Bash(scp *)
Bash(ssh trading-vm *)                               Bash(/c/Windows/System32/OpenSSH/scp.exe *)
Bash(/c/Windows/System32/OpenSSH/ssh.exe *)          Bash(powershell *)
Bash(/c/Windows/System32/OpenSSH/ssh.exe trading-vm *)
Bash(C:/Windows/System32/OpenSSH/ssh.exe trading-vm *)
Bash(GIT_SSH_COMMAND="ssh -i …/trading_vm_secure" ssh *)
```

`Bash(ssh *)` alone pre-authorises
`ssh trading-vm "python3 scripts/check_vm_state.py --cleanup-pending"` — **the exact command D4
exists to stop.** Deleting all 21 PowerShell entries changes nothing about that.

⭐ **This is the SAME LESSON THE LEDGER ALREADY RECORDED ONCE, at a third layer.** It noted
*"removing `--reset` from the script did not narrow the allowlist — they are independent
surfaces."* There are **three**, not two:

| # | surface | narrowed by |
|---|---|---|
| 1 | the **script** (`check_vm_state.py`'s own flags) | `38f8ab9` |
| 2 | the **PowerShell** allowlist channel | ⛔ not yet |
| 3 | the **Bash** allowlist channel | ⛔ **not yet, and not named by D4** |

⛔ **Narrowing 1 and 2 while leaving 3 produces a "read-only scope" that still permits everything.**
That is precisely the outcome D4's *"do NOT narrow on assumption"* was written to prevent.

## 3. ⛔⛔ THE DESTRUCTIVE SURFACE IS MUCH WIDER THAN `check_vm_state`

`--reset` (×2) and `--cleanup-pending` (×1) are **three entries**. Measured across all 533:

| pre-authorised capability | entries |
|---|---|
| restart / start / stop the **live service** | **26** |
| **POST to the LIVE webhook** | **15** |
| local git history rewrite (`reset`/`revert`/`checkout`/`rm`/`stash`/`merge`…) | 13 |
| push / deploy (incl. `.\deploy_audit_fixes.ps1 -Force`) | 6 |
| arbitrary file write (`Out-File`, `New-Item`, `tee`, `scp`) | 6 |
| mutate the **production venv** (`/home/ubuntu/systems/venv/bin/pip install …`) | 5 |
| mutate **deployed code** (`git pull origin main` on the VM) | 3 |
| **edit the production `.env`** via `sed -i` | 2 |
| external egress (Google Drive `create_file`) | 2 |

⭐⭐ **THE ONE THE REGISTER NEVER NAMES, AND IT IS ON THE SIGNAL PATH: 15 entries POST a
hand-written payload to the live webhook**, e.g.

```
ssh … 'curl -s -X POST http://localhost:5000/webhook/positional_swing_long \
        -d "{\"stocks\":\"FABTECH\",\"trigger_prices\":\"180.52\", …}"'
```

⇒ **a fabricated trading signal injected into the running system, with no prompt.** Against that,
`--reset` is not obviously the worst thing on this list.

⚠️ **And one entry disables an auth control:**
`sed -i 's/^WEBHOOK_SECRET=/#WEBHOOK_SECRET=/' .env` — it **comments out the webhook secret.**

## 4. LIVE vs STALE — ⭐ FOUR VM IDENTITIES, AND THE REGISTER COUNTED THE WRONG ELEVEN

| how the VM is addressed | entries | verdict |
|---|---|---|
| `trading-vm` (ssh alias) | **149** | ✅ **LIVE** — `~/.ssh/config` resolves it to `161.118.187.249`, `User ubuntu` |
| `ubuntu@80.225.198.195` | 51 | ⛔ **STALE** — old IP |
| `ubuntu@161.118.188.171` | 12 | ⛔ **STALE** — see below |
| `opc@80.225.198.195` | 3 | ⛔ **STALE** — old IP *and* old user (`opc`, not `ubuntu`) |

⚠️ **`161.118.188.171` is NOT the live VM either, despite looking current.** The tracked record
carries **`161.118.187.249` ×19** against `161.118.188.171` ×1, and the ssh alias agrees with the
19. **⇒ 66 entries (51+12+3) name a VM address that is not the live one — not 11.**

**Remote path generations:** `~/trading-system/…` **69** (older layout) · `~/check_vm_state.py`
**11** (home-dir script, the register's eleven) · `~/systems/trading-system/…` **7** (current
layout, per `/home/ubuntu/systems/`). ⇒ the `~/trading-system/` generation is **also** stale and is
**6× larger** than the one the register flagged.

⛔ **Staleness is not safety.** A stale entry is inert only until the path or host returns; and
the wildcard channel in §2 does not depend on any of these paths being correct.

## 5. FRAMING — WHAT THIS SURFACE ACTUALLY IS

Per the scope correction on record: on the VM the assistant work is **agy (Google/Gemini)**;
**Claude has no production role there.** ⇒ this allowlist governs an **interactive dev-time agent
during a session Rama is driving** — ⛔ **not** an autonomous agent holding production keys. That
bounds the severity, and it is stated here so the numbers above are not read as worse than they are.

⭐ **It does not dissolve the finding.** Pre-authorisation's whole effect is **removing the prompt**,
so the correct question is *"what can happen with no human beat?"* — and §3 answers it.

## 6. ⛔ WHAT IS STILL OPEN — NONE OF IT DECIDED HERE

1. **The Bash channel (§2 surface 3)** — the only genuinely unsolved piece; D4 does not mention it.
2. **Whether the 15 webhook-POST entries are acceptable** — signal path, never registered.
3. **Whether to delete the 66 stale-identity entries** — ⚠️ deletion is cosmetic while §2 stands.
4. **Whether `permissions.deny` should carry the guarantee instead** — an allowlist cannot express
   *"never `--cleanup-*`"* while a broad channel exists; a deny rule can. ⭐ The precedent already
   exists in this system: `~/tools/claude/.claude/settings.json` carries a **14-rule
   `permissions.deny`**.

⛔ **HALT. NOTHING NARROWED.** ⭐ **D4 as written would have produced a "read-only scope" that
still permits every destructive operation, via `Bash(ssh *)`.** That is exactly the outcome the
ruling's own *"do NOT narrow on assumption"* was protecting against — and it is why the
measurement was owed first.
