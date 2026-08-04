# LEDGER #3b — STEP 1: ARE *RELEASE-THE-RESERVATION* AND *DISOWN-THE-POSITION* SEPARABLE?

**Status: `<MEASURED — NOT IMPLEMENTED, NOT AUTHORISED>`.** Measured at **`4ae55a6`**.
Read-only; **0 `.py` changed.** #3's design registered this as *the direction to TEST,
explicitly not a settled mechanism*.

## 1. THE ANSWER: **SEPARABLE — and already separate. But the weld is somewhere else.**

The hypothesis to test was *"one status field carrying both meanings, one writer, one
consumer contract."* **Measured, that is FALSE on all three counts.**

| axis | *disown the position* | *release the reservation* | welded? |
|---|---|---|---|
| **key** | `trade_id` → `trades.status` | `reservation_id` (`fund_manager.release:606`) — or `symbol` + `trade_id` (`release_used:1173`) | ⛔ **different keys** |
| **store** | `trades` table | `fund_manager._reservations` + `fm_ledger` | ⛔ **different stores** |
| **writer** | `_update_trade_status` | `fm.release` / `fm.release_used` | ⛔ **different writers** |

⭐ **They are not merely separable in principle — they ALREADY diverge in production.**
`order_reconciler.py:2638-2654` (orphan auto-close) executes them as **two consecutive
statements with two independent `try/except` blocks**:

```
except Exception as exc:
    log.error("FIX-B: update trade status FAILED: %s", exc)   # <- continues anyway
...
try:
    self._fm.release(reservation_id, ...)
except Exception as exc:
    log.error("FIX-B: capital release failed: %s", exc)        # <- continues anyway
```

⇒ **Either can fail while the other proceeds.** A failed status write leaves a position
un-disowned while its capital IS released; a failed release leaves capital held on a
disowned position. **Neither is atomic with the other, and nothing reconciles them.** ⇒ the
"weld" #3b was written to break **does not exist**.

## 2. ⭐⭐ THE REAL WELD, AND #7 IS WHAT MADE IT VISIBLE

**"Release the reservation" is not ONE operation.** There are two verbs with different
semantics, and a third family for adoption:

| verb | key | applies when | side effects |
|---|---|---|---|
| `release(reservation_id)` `:606` | reservation_id | **pre-fill** — cancel / reject | returns **reserved** margin |
| `release_used(symbol, …, trade_id)` `:1173` | symbol + trade_id | **post-fill** — position close | returns **used** margin **+ updates `daily_realized_pnl` (FM7)** |
| `release_adopted_reservation` / `restore_adopted_reservation` | trade row | adoption path | — |

⛔⛔ **CALLING THE WRONG ONE IS NOT A NO-OP: `release_used` also writes realised P&L.**

⇒ **The question every caller must answer before releasing is: DID THIS TRADE FILL?** — and
that is **exactly one of the three facts `trades.status` was measured (ledger #7 §2b) to be
carrying**: *"does this trade hold a position?"* (`EXITING` yes, `PENDING_FILL` no).

⭐ **So the weld is real but it is one layer over from where #3b looked.** It is not
*release ↔ disown*; it is **release-VERB-SELECTION ↔ the position-held-ness fact inside the
overloaded `trades.status` column.** ⇒ **#3b's design changes shape:** the separable thing
is **that fact**, not the two operations — which are already separate.

## 3. ⛔ WHAT THIS MEANS FOR #3b, STATED SO IT IS NOT MISREAD

- ✅ **The INTENT of #3b stands**: something here genuinely conflates two concepts.
- ⛔ **The MECHANISM as registered — "split release from disown" — is refuted by
  measurement**: they are already split, to the point of being able to diverge silently.
- ⇒ **Returned for re-scoping, per §G5** (mechanism refuted, intent intact). ⛔ **Never
  "#3b was wrong."** ⭐ This is §G5's **fifth** instance in the campaign.
- 🔴 **The re-scoped question:** *can the position-held-ness fact be lifted out of
  `trades.status` so the release verb is selected on it explicitly, rather than each caller
  re-deriving it from a status literal?* ⛔ **Not answered here; not designed here.** It is a
  money-path/schema change and a CAREFUL-LOOP item by any reading.

## 4. ⚠️ A SEPARATE FINDING FALLING OUT OF THIS, RECORDED NOT ACTED ON

The orphan auto-close's **non-atomicity is itself a defect**, independent of #3b: two
money-path mutations with no compensating action if one half fails, and **only an
`log.error` to show for it** — no CRITICAL, no escalation, no reconciliation pass that would
detect the mismatch. ⚠️ **LATENT vs LIVE not established** — I did not measure whether either
`except` has ever fired in production. ⛔ **Do not report it as live until that is measured.**

⛔ **HALT.** No code, no re-scoping decision, no register edit beyond this record.
