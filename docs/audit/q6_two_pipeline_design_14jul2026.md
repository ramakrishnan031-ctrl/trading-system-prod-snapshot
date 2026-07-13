# Q6 — TWO-PIPELINE PLUMBING: intended model, current state, deferred gap

**Date (IST):** 14-Jul-2026 · **Status:** DESIGN NOTE — **document + DEFER to Slice 2.5 (delivery activation). DO NOT BUILD now.** · **Decision (Rama + Web Claude + ChatGPT):** the two-pipeline model is already ratified by the V3 03.05 design; the substrate is substantially built; do not reopen the capital path for a pipeline that does not run.

---

## 1 · The intended model (ratified, V3 03.05)
| Cap / control | Scope |
|---|---|
| **Concentration (per-symbol) · Sector exposure** | **SHARED** across pipelines — a symbol's / sector's total exposure is one number regardless of pipeline. |
| **Capital bucket · max open positions · max daily orders · long/short balance** | **PER-PIPELINE** — INTRADAY (MIS/CO) and DELIVERY (CNC) each get their own. |

Rationale: concentration/sector risk is a property of the *underlying*, so it must aggregate across pipelines; capacity/turnover is a property of the *strategy family*, so it is sized per-pipeline.

## 2 · What is ALREADY built (no work needed)
- **Per-pipeline caps (count):** `capital/risk_engine.py` — `max_open_delivery_positions` (3) / `max_daily_delivery_trades` (5), enforced ONLY for a delivery entry (`is_delivery_entry = sizing_result.bucket == "positional"`), via `count_open_delivery_positions` / `count_daily_delivery_trades` (Slice 2.5 Phase-3 A). INTRADAY keeps `max_open_positions` (5) / `max_daily_trades` (10).
- **Per-pipeline capital buckets:** `intraday_bucket_pct` 0.70 / `positional_bucket_pct` 0.30 (fund_manager).
- **Routing discriminator:** delivery = the positional/CNC bucket; intraday = MIS/CO. `force_intraday_only` triple-locks delivery OFF today.
- **Shared concentration/sector:** `max_concentration_pct` (0.10) / `max_sector_exposure_pct` (0.40) are per-symbol / per-sector, pipeline-agnostic — already SHARED per the model. (gate-8's TOCTOU hardening — Q4 — folds reserved-not-placed into the SHARED sector number; consistent.)
- **Display:** `pipeline` / `horizon` taxonomy (side-task A) already surfaced in `strategy_status` (Telegram / HTML / plaintext Category) + EOD `daily_report` sheet-6. **Q6's "surface pipeline/horizon" is DONE.**

## 3 · The one DEFERRED gap (do not build now)
**Asymmetric coupling (A5):** an INTRADAY entry counts delivery positions toward its `max_open_positions`, but a DELIVERY entry does NOT count intraday positions toward its `max_open_delivery_positions` (`risk_engine.py` ~L424-431). The fully-independent model (§1) would make each pipeline's position/order cap count ONLY its own pipeline.

Why DEFER, not fix:
- It is **fail-safe** — the asymmetry makes INTRADAY *more* conservative (it over-counts), never less. It can only reject, never over-admit.
- It is **moot today** — delivery is OFF (`force_intraday_only=true`, `delivery_enabled=false`), so `is_delivery_entry` is always False and the delivery caps never bind.
- It is a **capital-path change** — reopening `approve()`'s counting for a pipeline that does not run adds risk for zero live benefit.

**→ Fold the symmetry fix into Slice 2.5 (delivery activation), where it will be built + parity-proven alongside CNC entry, DDPI, and the delivery sizing knobs — the point at which it first has any effect.**

## 4 · Q6 verdict
Two-pipeline plumbing is **substantially complete**. Shared-vs-per-pipeline is settled and mostly implemented; the only gap (cap symmetry) is fail-safe and inert while delivery is off. **Q6 = documented + deferred. No capital-path code written.**
