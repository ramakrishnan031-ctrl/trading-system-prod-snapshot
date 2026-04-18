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

---

## EF-3 — FundManager.release_used PnL formula was LONG-biased  [RESOLVED]

File: capital/fund_manager.py::release_used
Impact: pre-fix formula `pnl = (exit_price - entry_price) * exit_qty - costs`
        silently produced sign-flipped PnL for every SHORT position. BL-7 had
        kept _on_order_filled from ever running exits, so the bug was dormant —
        A.3.d would have been the first caller to exercise it with real SHORT
        prices. Six of 15 Chartink strategies are SHORT-only; 30-50% of fills
        on any paper day would have posted inverted realized PnL to the daily
        loss limit + reports.
Severity: CRITICAL (mechanism defined, wiring missing; activates on first SHORT exit)
Fix: SUB-STEP 0.5 of A.3.d.
     - release_used now takes a required `direction: str` param (LONG|SHORT);
       ValueError on anything else. `_VALID_DIRECTIONS: frozenset` constant added.
     - Branch: LONG → (exit-entry)*qty; SHORT → (entry-exit)*qty; both minus costs.
     - Docstring documents the sign convention explicitly.
     - Caller updates: order_reconciler._check1_manual_close reads direction from
       the trade row (sqlite3.Row indexing via try/except, with LONG fallback and
       WARNING log). order_placer._handle_exit_fill passes direction from the
       trade row (authoritative) or the cached fill_entry.direction (fallback).
Tests added:
  - test_fund_manager.py: test_release_used_short_profit, test_release_used_short_loss,
    test_release_used_rejects_invalid_direction (+3)
  - test_order_reconciler.py: direction kwarg asserted in MANUAL_CLOSE path
  - test_order_placer.py::TestBl7dExitFillHandling::test_short_tgt_fill_gross_pnl_direction_correct
    (regression guard)
Status: RESOLVED in commit containing BL-7d+BL-10a
Discovered: Phase A, A.3.d pre-work (verification grep of release_used callers)

---

## EF-4 — paper_capital is not a declared SystemConfig field

File: main.py (getattr(app_config.system, "paper_capital", 500_000.0))
     core/config_loader.py::SystemConfig (field missing)
Impact: paper_capital is fetched from SystemConfig via getattr() with a
        500_000.0 default. A typo or missing YAML key silently falls back
        to 500k with no Pydantic validation and no CONFIG_DIFF audit trail.
        Every other SystemConfig value is declared as a typed Pydantic field
        with extra="forbid"; this one slipped through.
Severity: MEDIUM (operational footgun, not capital-corruption). Paper-only
          so live PnL is not affected. Still: a configuration key the operator
          cannot actually misspell into a visible error is an anti-pattern
          for this system.
Fix size: small -- add `paper_capital: float` to SystemConfig (or fold into
         PaperConfig alongside auto_fill_delay_sec). Remove the getattr in
         main.py. Add to system_config.yaml paper: block.
Status: DEFERRED to Phase E. Tempting to bundle with H-20 (A.3.f) since we
        are already adding PaperConfig, but out of scope: A.3.f's PaperConfig
        holds only auto_fill_delay_sec. Expanding scope here would delay
        Phase A closeout.
Discovered: Phase A, A.3.f pre-work (grep of paper_mode/is_paper in main.py)
