# 05_deployment — Infrastructure Deployment

**Purpose:** Everything related to taking the built code and deploying it onto the VM + PC infrastructure. Covers the 4 steps of deployment done on Saturday 18-Apr-2026.

**Status:** ✅ COMPLETE. 1413/1413 tests green on both PC and VM.

---

## Step Sequence (chronological)

| File | Purpose | Status |
|------|---------|--------|
| `step2_cleanup_env_files.txt` | Prerequisite — reconcile 3 env files on PC before Step 2 | ✅ Done |
| `step2_vm_venv_and_first_run.txt` | VM venv setup, first manual run, holiday guard validation | ✅ Done |
| `step3_systemd_and_firewall.txt` | systemd units, firewall rules, cron, status.sh helper | ✅ Done |
| `step4a_systemd_restart_test.txt` | 4 restart scenarios proven, revert verified | ✅ Done |

Note: Step 1 (VM bootstrap, bare repo, Python 3.14, SSH config) and Step 4b/5 (Oracle port 5000 manual, requirements split) either predated this folder or were executed outside the numbered-step format. Step 4b requirements fix is captured in `fixes/fix4_requirements_split.txt`.

---

## Fixes (`fixes/` subfolder)

Bugs discovered during deployment, fixed before proceeding. Numbered by chronological discovery.

| File | Issue | Fixed Before |
|------|-------|--------------|
| `fix1_weekend_test_bug.txt` | 22 test failures on weekends due to non-deterministic date handling | Step 2 |
| `fix2_scan_webhook_schema.txt` | `ScanWebhookMapConfig` Pydantic schema mismatch | Step 3 |
| `fix3_holidays_schema_audit.txt` | `nse_holidays_2026.yaml` schema drift + full config audit | Step 3 |
| `fix4_requirements_split.txt` | Missing pytest, pytest-cov, python-dotenv in requirements.txt | Post Step 4a |

---

## What's Next After Deployment

Deployment hands off to **`../06_live_operations/`** — the daily operations playbooks for paper and live trading.

---

*Folder last updated: 18-Apr-2026*
