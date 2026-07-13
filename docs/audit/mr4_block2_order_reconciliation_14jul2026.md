# M-R4 — daily-review Block-2 "ORDER" is a tautology: report + options (Rama decides)

**Date (IST):** 14-Jul-2026 · **Status:** REPORT — not fixed (a wrong redesign risks a false "capital corruption" alarm). Same family as "a test that cannot fail": *a reconciliation block must compare an internal number to an INDEPENDENT external source, never to another internal derivation of the same thing.*

---

## (a) What Block-2 CURRENTLY compares (`reports/daily_trade_review.py::build_reconciliation`)
```python
qualified         = len(trade_records)                                   # LHS
placed_ok         = sum(1 for t in trade_records if t.get("broker_order_id"))
placement_failed  = qualified - placed_ok                                # DERIVED from LHS
# block: lhs = qualified ; rhs = placed_ok + placement_failed
```
Because `placement_failed ≡ qualified − placed_ok`, the RHS is `placed_ok + (qualified − placed_ok) = qualified = LHS` **identically**. The block can NEVER FAIL — it compares the trade list to itself. It presents "PASS / verified" while verifying nothing (a false-PASS). VERIFIED still present at HEAD (M-R3 fix touched Block-4 only).

## (b) What INDEPENDENT sources exist
The **P1 broker-reconcile feeder** (`scripts/eod_broker_reconcile.py`, `BrokerState`) already pulls EXTERNAL broker facts and reconciles some of them:
- `open_order_count` (broker) vs `local.pending_order_count` — **already an external order check** (Block "2. ORDERS" in the P1 verdict).
- `day_realized` (broker P&L) vs local realized — external P&L check.
- `positions` (broker) vs local open positions.

**Gap:** the P1 feeder's `open_order_count` is orders *open at EOD*, not *total orders placed during the day*, and P1 is currently SHADOW (`authoritative:false`). So it is a real external source, but it answers a slightly different question than Block-2's "every qualified signal → a placed order or a placement failure."

## (c) Options for Rama
1. **Delete Block-2 as a "reconciliation" block.** It cannot compare to an external source at the signal→order granularity. Rely on **Block-3 (TRADE)** — which already has a genuine partition with an `unmapped → FAIL` check (schema-drift catch) — and **Block-5 (BROKER)**, fed by P1. Cleanest; removes the false-PASS.
2. **Re-point Block-2 to an INDEPENDENT internal cross-table check:** `qualified` (signals with status TRADED) vs the count of *distinct trade rows* vs the count of *ENTRY orders in the `orders` table*. Cross-table (signals vs trades vs orders) can actually FAIL if the pipeline drops a signal between tables — meaningful, still internal, no broker dependency.
3. **Fold order reconciliation into Block-5 (BROKER)** once P1 goes `authoritative:true`, comparing system placed-order count to the broker's full-day order count (needs P1 to capture full-day orders, not just EOD-open). Most correct, but gated on the P1 soak + an authoritative flip.

**Recommendation:** Option 1 now (remove the false-PASS — a block that manufactures false confidence is worse than no block), and Option 3 later (real external order reconciliation) when P1 is promoted. Option 2 is a reasonable interim if you want to keep a Block-2 slot filled with a check that can actually fail. **Awaiting your call — not built.**
