# Extra Findings (EF) — Issues discovered during audit remediation

Findings surfaced while executing Web Claude's audit plan that were NOT in
the original 54-item audit. Logged here so they don't derail the phase
cadence but aren't forgotten.

Severity legend: CRITICAL > HIGH > MED > LOW (same scale as the main audit).

---

## EF-1 — WebhookReceiver reads flat config path, main.py passes AppConfig

File: signals/webhook_receiver.py (self._config.signal_queue.capacity access)
     main.py:~1072 (passes full AppConfig, signal_queue lives at .system.signal_queue)
Impact: AttributeError on first /health endpoint hit in production
Severity: HIGH (new; not in Web Claude's audit)
Fix size: ~5 lines, same shape-tolerant pattern as BL-18
Status: deferred to Phase E (grouped with H-findings) unless it surfaces earlier
Discovered: Phase 0, during BL-18 shape-resolution work
