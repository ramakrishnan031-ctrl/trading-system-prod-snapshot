# PB-01 and the V3-chain gate — scoping pass

**READ-ONLY.** 2026-07-24 (IST). Deployed HEAD: `bc75406`. DB `mode=ro&immutable=1`; VM logs/JSONL read-only. **No evidence producer was run** (`forward_shadow_record.py`, the V3 chain runner, `pb01_runner.py` — none invoked; §1.1). No config/flag change; signal path untouched; service not restarted. **No performance numbers, no recommendation** (§1.3, §5.4).

Companion to `forward_shadow_scoping_2026-07-24.md`.

---

## VERDICT (the three questions, in order)

> **1. Why does PB-01 emit nothing?** Its strategy is a **deliberate registration stub** — `config/strategies/pb01_breakout_retest.yaml:4,30`: *"REGISTRATION STUB — SHADOW / NON-TRADING,"* `enabled: false` ("never trades until the spec-13 promotion gate") — AND its upstream Chartink scanner **has never fired: 0 `pb01_breakout_retest` signals ever**, so `pb01_watchlist` is **empty (0 rows)** and there is nothing to confirm. **Classification: DELIBERATE staged default-off** (like the structure-exit manager), not never-wired and not dead-config. **"No live twin" is load-bearing:** PB-01 never trades, so a would-be record has no live outcome to compare against — it could **not be evaluated as a would-be shadow even if switched on** (it would need actual trading or a dedicated outcome-sim).
> **2. What is the V3 population, and does RR reject on merit or missing data?** The 281 records are **not a sample of live signals** — `observe()` sits at `signal_processor.py:909`, immediately after `sizing.success`, so they are signals that had **already passed scoring, risk, AND sizing** (the live system's near-admission, about-to-trade set, ~40/day). It rejects **98.6%** of them, and **neither half is "merit":** **46% (103/223) have no S&R zone to evaluate** (`v3_rr` null — mostly no resistance above a breakout entry: `nearest_resistance` null on 88), and the **54% (120) with a computed RR sit structurally far below the 2.0 floor** (median **0.33R**, max **1.78R** — not a calibration near-miss). The S&R-RR gate is **geometrically incompatible** with the current momentum/breakout entries, not selective about quality.
> **3. Can either path produce evidence at a useful rate? No — neither, in any reasonable time.** V3: ~0.6 would-pass/day (itself an upper bound on would-trade), against a **structural, not tunable** ~99% rejection. PB-01: nothing at all (0 signals ever), and structurally un-evaluable as a shadow. **The current entry approach cannot validate a new entry thesis through these shadows.**

---

## §2 — Why PB-01 produces nothing (measured)

- **§2.1 (constructed?):** Unlike the breakeven manager, PB-01 **is** in the boot path — `git log -S "pb01_runner" -- main.py` → `5a71fb6` ("Step 10b … all default-OFF SHADOW/NO-ORDER"); `main.py:1253-1255,1334-1343,3080` wire the capture worker + entry stage under `watchlist.enabled` (currently `true`, line 434).
- **§2.2/2.3 (what gates it → classification):** the strategy `pb01_breakout_retest` is `enabled: false` (`pb01_breakout_retest.yaml:30`; `scan_webhook_map.yaml:79` "enabled:false + v3_playbook:true → FAIL-CLOSED"). The YAML header states the intent verbatim: a **registration stub** so a Chartink EOD scanner can be *received/authenticated/routed* and the V3 chain can produce *would-be records*, but it "does NOT trade real capital … never trades until the spec-13 promotion gate." ⇒ **DELIBERATE staged default-off** — honour it as a decision (fail-closed, proven by the G-NO-ORDER test), not an oversight.
- **The proximate cause of zero output is one level up: 0 `pb01_breakout_retest` signals have ever arrived** (measured: `signals`=0, `screener_results`=0; it is absent from the 13-strategy signal census). No Chartink EOD webhook for that scanner has been received, so the capture worker (even though constructed) has nothing to persist → `pb01_watchlist`=0 rows → the entry stage has nothing to confirm → `pb01_would_be.jsonl` is never created.
- **§2.4 ("no live twin" — settle it):** PB-01 candidates originate from the EOD watchlist → next-morning 5-min retest, **not** from live signals, and `enabled:false` means the live system never trades them (`pb01_runner.py:207,225`: *"no live twin (live_* mirror the would-be placement)"*). So there is **no live order or outcome to compare a PB-01 would-be against**. The other two shadows borrow the live decision/outcome of the same signal; PB-01 has none. **Consequence:** even if the scanner fired and PB-01 emitted would-be records, a would-be shadow alone could describe its *decisions* but not *evaluate* them — that needs either promotion to live trading or a dedicated outcome simulation (as `fs-v1` does with `sim_R`). This is why "fixing PB-01" is not a one-flag question.
- **§2.5 (what it would need to emit a record — described, not proposed):** (1) a Chartink EOD `pb01_breakout_retest` webhook (currently 0 ever) captured to `pb01_watchlist`; (2) next-morning in 09:20–11:00, the entry stage's `gate_confirm` (a 5-min close above the level with a decisive body and ≥ volume_mult × baseline) **and** `gate_pullback` (touched-within-proximity **and** held) both pass (`hard_gate.py:362-459`). It currently stops at (1).

## §3 — Why the V3 chain rejects almost everything

### §3.1 — the population (this gates everything after it)

`v3_chain.observe()` is called at **`signal_processor.py:909`**, *after* `self._sizer.calculate` and the `if not sizing.success: raise _PipelineReject` at `:900-901`. So the V3 chain sees **only signals that have already cleared the score gate, the risk engine, and sizing** — the live system's **near-admission, about-to-trade** population (~40/day, vs ~1,000+ scored/day). Measured corroboration: **80 of the 281 observed signals carry a `trade_id`** (the live system took them into order flow). ⇒ **the 98.6% V3 rejection is applied to the signals the live system actually accepted, not to raw/junk arrivals.** The rate is not "V3 catching what the live path missed."

### §3.2 — the RR gate: merit vs missing data (the hypothesis, tested)

`gate_rr` (`hard_gate.py:233-287`) is a **must-have**: if `sl_zone_edge` or `tgt_zone_edge` is None → **FAIL "missing S&R zone,"** and on that path **no `v3_rr` is recorded** (null). Only when both zones exist does it compute `R:R = reward/risk` and PASS iff `≥ rr_floor` (config `rr_floor: 2.0`, `sl_buffer_atr_mult: 0.20`). Of the 223 RR rejects:

| | count | share | meaning |
|---|---:|---:|---|
| `v3_rr` **null** (no S&R zone) | 103 | 46% | **rejected on MISSING DATA** — no safe SL/TGT zone (mostly no resistance above a breakout entry: `nearest_resistance` null ×88) |
| `v3_rr` **numeric** | 120 | 54% | computed R:R below floor |

The 120 computed values: **min 0.02 · p25 0.16 · median 0.33 · p75 0.62 · max 1.78** — **all far below the 2.0 floor; not clustered just beneath it.** That is **structural, not calibration**: the S&R-derived geometry (nearest resistance as TGT, nearest support as SL) yields a systematically tiny reward-to-risk for momentum/breakout entries that enter mid-move or above resistance. And `sr_sync_hit = 69.8%`, so this is **not** a cold-fetch failure — the zones are being built; the geometry is simply wrong for these entries. **Net: the RR gate is neither cleanly "on merit" nor a simple bug — it is a structural mismatch between the S&R-RR criterion and the current entry style.** (§3 for HTF: 54 rejects — the fail-open blocker fires only on a strong 1h contradiction, `hard_gate.py:290-330`; a minority, distinct from the RR mass.)

### §3.3 — the four that passed (characterised, not scored)

All four are LONG, both S&R zones present, on 22–23-Jul: M&MFIN (`gap_go_long`, v3_rr 3.60), M&MFIN (`vwap_bounce_long`, 2.49), ORIENTELEC (`first_pullback_long`, 2.61), WAKEFIT (`gap_go_long`, 2.18). Each is a case where a support **and** a resistance bracket the entry with `R:R ≥ 2.0`; notably the V3 `v3_rr` (2.18–3.60) exceeds the live fixed `1.5` — i.e. V3 would set a wider target. **No P&L reported** (four is noise). **Passing V3 ≠ would-trade:** `observe()` is post-sizing but **pre-throttle/pre-placement**, so four would-pass is an **upper bound** — the ~20s throttle (which discards a large majority) and placement gates cut it further. The real would-trade rate is lower still.

## §4 — Can either path produce evidence at a useful rate? (plainly, first)

**No. Neither path can produce a useful sample in any reasonable time — and for the V3 chain the barrier is structural, not a matter of accumulating days.**

- **V3 chain:** ~0.6 would-pass/day (4 over 7 OOS days), an upper bound on would-trade. To judge whether V3's selection is *better* needs outcomes: only ~45–80 of the observed signals have live outcomes, and V3 would reject ~277 of them. But the decisive point is that the ~99% rejection is **structural** — 46% unevaluable (no zone) and the rest at a median 0.33R against a 2.0 floor — so more trading days would not move the verdict; the S&R-RR gate is incompatible with the current entries by construction.
- **PB-01:** produces **nothing** (0 signals, 0 watchlist rows, 0 records) and, being enabled:false with **no live twin**, cannot be evaluated as a would-be shadow even if its scanner fired — it would need promotion to live trading or a separate outcome-sim first. Its evidence rate is zero and structurally blocked.
- **§4.4 erosion:** every service-down day is a lost OOS day (17-Jul already cost one, `forward_shadow_scoping_2026-07-24.md`); factor that into any projection rather than assuming clean days.

**Implication (not a recommendation):** the two entry-evidence shadows that exist do not, at current rates and by their current construction, provide a path to validating an entry thesis. The fs-v1 score shadow (working) tests the score threshold, not a new entry thesis. This is a statement about the evidence machinery, not about what to do next.

## Evidence appendix (reproduce; all read-only)

- Code (`bc75406`): `signals/signal_processor.py:900-916` (observe after sizing.success); `v3_chain/runner.py:95-256` (population/gates/persist); `screening/hard_gate.py:233-287` (gate_rr, `sl/tgt_zone_edge None → FAIL, no v3_rr`), `:290-330` (gate_htf fail-open), `:362-459` (PB-01 confirm/pullback); `config/strategies/pb01_breakout_retest.yaml:4,30`; `config/scan_webhook_map.yaml:79`; `config/system_config.yaml:388` (`v3_chain_mode: shadow`), `:391` (`rr_floor: 2.0`), `:433-445` (watchlist/pb01).
- Git: `git log -S "pb01_runner" -- main.py` → `5a71fb6`; `V3ChainRunner` → `474e10b`.
- Data (`immutable=1` / read-only JSONL): `would_be.jsonl` 281 recs (223 RR / 54 HTF / 4 PASS; RR: 103 null-v3_rr, 120 numeric median 0.33R; sr_sync_hit 69.8%; 80/281 traded); `signals` pb01_breakout_retest = 0; `pb01_watchlist` = 0 rows; `pb01_would_be.jsonl` absent.
- No evidence producer run.
