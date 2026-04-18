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

---

## EF-2 — Track-after-persist race in OrderPlacer.place()

File: orders/order_placer.py (post _persist_entry_orders() block, BL-7c)
Impact: if `order_monitor.track()` raises between `_persist_entry_orders()` and
        the `_fill_map` write (e.g., duplicate internal_id ValueError), the
        orders table has rows but the monitor has no coverage for those
        internal_ids. Those orders will never publish `OrderFilled`, capital
        never commits from the reservation, and the trade row sits in
        PENDING_FILL until the reconciler's orphan-detection path picks it up
        (or EOD square-off forces an exit).
Severity: HIGH latent
Fix size: medium — needs a rollback pathway (cancel broker orders via adapter
         + delete orders rows + release reservation + mark trade FAILED).
         That rollback is its own design exercise because partial-cancel
         semantics differ per broker and the adapter doesn't yet expose a
         bulk-cancel primitive.
Status: deferred to Phase E or later (post paper-trial). A.3.c (BL-7c) added
        the 3-leg track() wiring that makes this race reachable for SL+TGT
        legs too, not just ENTRY — but the gap itself is pre-existing
        (ENTRY leg had the same race before BL-7c).
Discovered: Phase A, A.3.c pre-work grep review (finding S1)
