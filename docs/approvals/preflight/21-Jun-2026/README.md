# Pre-flight — Phase A LOOK preview (draft, 21-Jun-2026)

Early visual-sign-off samples of the pre-flight report, rendered from the CURRENT
Phase A check-set (**31 checks / 7 groups**). This is a DRAFT preview of the *look*
only — Broker (Group 4) + config-rest + Phase B/C are still being built, and the
data here is representative (not a live VM run). The authentic Sunday dry-run sample
(Step 5, real VM data) will replace these before the cron reinstall.

Open in a browser:
- `preflight_phase_a_clean.html` — a clean morning (READY, 30 pass / 1 skip)
- `preflight_phase_a_critical.html` — a bad morning (2 critical, 3 warnings, 1 auto-fixed)
- `preflight_telegram_and_subjects.txt` — Telegram MarkdownV2 + email subjects

Cosmetics reuse the approved Cron Officer language (banner, stat cards, readiness
bar, status pills, per-check table). ALERT-ONLY: a CRITICAL never blocks trading.
