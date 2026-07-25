# Why has the trailing stop never fired?

**Investigation — READ-ONLY.** Date of work: 2026-07-24 (IST). Deployed HEAD read against: `bc75406`.
Data: live VM DB opened `mode=ro&immutable=1` (zero-trace; no `-wal`/`-shm` present, service `inactive`). No code, config, deploy, or restart. **No fix, no config change, no design** (per instruction §E5).

Supersedes §A of `VSCODE_MONDAY_INVESTIGATIONS_27-Jul-2026.txt`.

---

## VERDICT (the four explanations)

> **1. Explanation 2 — UNREACHABLE BY CONSTRUCTION. The deciding number: `order_protocol` = LIMIT_TRIPLE on 423 / 423 trades (measured).**
> **2. The only SL-trailing engine (`SmartTgtManager`) registers a trade only when its protocol is `CO_PLUS_TGT` (`order_placer.py:2193`); the router assigns the LIMIT_TRIPLE default unconditionally (`order_placer.py:950`), discarding the 13/16 strategies that declare `CO_PLUS_TGT`. So no trade is ever registered → `smart_tgt_state` = 0 rows → nothing to trail.**
> **3. It is NOT explanation 1 (the strategies + global config are switched ON), NOT 3 (65% of trades reach the 0.5% arm threshold), and NOT 4 (39% of SL losers had room; winners' true MFE ≈ 3.1R in-sample / ≈ 2.2–2.4R out-of-sample — both well beyond the 1.5R exit).**

The evidence **does** separate the four explanations — this is not a "cannot distinguish" case. One qualifier: the *net P&L consequence* of making the trail reachable is a separate, unresolved question (see §D.5 and §E), because it needs an ordered intra-path simulation the persisted data cannot supply.

Fact vs. explanation (instruction §H.1): the **fact** is `sl_trail_count` = 0 / 423. The **cause** traced here — LIMIT_TRIPLE ⇒ `SmartTgtManager` never registers — is evidenced end-to-end (the intermediate facts `smart_tgt_state` = 0, CO legs = 0, `superseded_by` = 0 are each measured), not assumed.

---

## §B — Does anything ever modify an SL leg? (measured)

The trailing SL is a broker-side modification. Two mechanisms could record one; **both read zero**, and a third counter that the system itself maintains reads zero:

| Signal | Meaning | Measured (423 trades / 713 orders) |
|---|---|---|
| `orders.leg = 'CO'` | Cover-Order bracket entries (the only kind the trail can modify) | **0** |
| `orders.superseded_by IS NOT NULL` | cancel-and-replace SL chain | **0 / 713** |
| `trades.sl_trail_count > 0` | in-place CO-trigger modify confirmed by broker | **0 / 423** |
| `smart_tgt_state` rows | trades ever registered with the trail engine | **0** |

- **§B1 count = ZERO by every record.** The in-place `modify_order` path (used by the CO trail) leaves no new order row; the system's own proxy for it is `trail_count`→`sl_trail_count`, incremented **only after the broker confirms a modify** (`smart_tgt_manager.py:609-618`). Zero increments ⇒ zero confirmed modifies. The broker-side order history is not reachable now (service down, no token), but the local record is internally consistent and is the system's authoritative persisted record.
- **§B2 — the modify path EXISTS in code (grep-verified, `.modify_order(` call sites).** This is "wired but never triggered," *not* "never written":
  - `smart_tgt_manager.py:588` — trailing SL on a **CO** trigger_price. Gate: `order_protocol == CO_PLUS_TGT` → never met.
  - `breakeven_manager.py:289` — breakeven SL advance on **LIMIT_TRIPLE**. Gate: `strategy.trailing_sl_enabled` (`order_placer.py:2163-2168`); schema default `False` (`strategies/schema.py:105`) and **no strategy YAML sets it** → never met.
  - `structure_exit_manager.py:569` — structure-aware SL. Gate: `structure_exit_enabled` = **`false`** (`system_config.yaml:448`; the manager is not even constructed) → never met.
  - `broker/zerodha_adapter.py:950`, `broker/angelone_adapter.py:54` — the adapter capability itself is fully built.
- **§B3 does not fire:** the count is not non-zero, so there is no contradiction with `sl_trail_count` = 0.

---

## §C — The mechanics (from source at `bc75406`)

- **C1 — where `sl_trail_count` is set.** `SmartTgtManager` increments `info["trail_count"] += 1` on a confirmed broker modify (`smart_tgt_manager.py:618`); `OrderPlacer` copies `smart_tgt_state.trail_count` → `trades.sl_trail_count` at close (`order_placer.py:2477-2491`), and only for `CO_PLUS_TGT`. Both sit on the CO path nothing reaches — decisive on its own.
- **C2 — the trigger condition + live value.** `_compute_target_sl` (`smart_tgt_manager.py:503-507`): arms when `distance_pct = |best_price − entry| / entry ≥ trigger_pct`, then steps every `step_pct`. Live values (`system_config.yaml:457-458`): **`trigger_pct = 0.005` (0.5% of entry), `step_pct = 0.003` (0.3%)**. Quoted from code, not a variable name. The 0.5% arm is a *small* move — relevant to §D.
- **C3 — enabled or disabled in live config?** Config says **ENABLED**: global `smart_tgt.enabled: true` (`system_config.yaml:456`) and 13/16 strategies carry `smart_tgt_enabled: true`. But this flag is inert to the actual trail in the deployed wiring — a nuance worth stating plainly:
  - `main.py:2471` constructs `SmartTgtManager(enabled=True)` **hardcoded** (it does not read `smart_tgt.enabled`).
  - The registration gate `order_placer.py:2193-2197` checks **only** the protocol, not `.enabled`.
  - `smart_tgt.enabled` is read at exactly one site — `order_placer.py:1738` — to pick the cosmetic "Smart TGT monitoring: enabled/disabled" line in the ORDER-PLACED Telegram body (`:845-855`).
  - So `config_loader.py:1313`'s docstring ("if False, OrderPlacer skips register_trade") does **not** match the deployed code. The outcome is unchanged either way, because the protocol gate blocks registration regardless.
- **C4 — price-update → trail path; is it reachable?** `main.py:2456-2463` feeds live ticks into `candle_store`; on each minute close `candle_store` fires `SmartTgtManager._on_candle_close` (`:353`), which filters `self._tracked` by `instrument_token` (`:361-365`) and calls `_process_candle` → `_compute_target_sl` → `_modify_co_sl`. The path is **code-reachable but data-starved**: `_tracked` is empty because nothing ever registers (`smart_tgt_state` = 0). Register-the-work happens only for CO fills, which never occur. *(Instruction §C4 warned the register's assessment here was wrong once this week — this is verified from source + the empty-state fact, not cited.)*
- **C5 — does the system receive the price data the trail needs?** Yes. The tick→candle feed is live and independent of the trail; `on_candle_close` fires for every tracked instrument. The trail is starved of **registrations**, not of ticks. (`volume` is always 0 per LF11, but `volume_dependent_trails: false`, so that path is irrelevant.)

**Why every trade is LIMIT_TRIPLE (the root).** `order_placer.py:949-950` is `# OP9: choose protocol` immediately followed by `order_protocol = self._default_protocol` — *unconditional*, no per-strategy/per-signal branch. `signal_processor.py` never references `order_protocol` (grep: no matches), so the strategy's declared value is never plumbed to the decision. `self._default_protocol` = the constructor default `"LIMIT_TRIPLE"` (`order_placer.py:570`), and `main.py` never passes `default_order_protocol` (grep: it appears only inside `order_placer.py` + tests). The 13/16 strategies' `order_protocol: "CO_PLUS_TGT"` (and their per-strategy `smart_tgt_trail_*` values) are **dead config** — read into `StrategyConfig`, never consulted. The DB confirms the outcome empirically: **423/423 LIMIT_TRIPLE**, so no code subtlety could have produced a CO trade. *(The 05_d4 decision doc cited `order_placer.py:855` for this; the line has moved to `:950` at `bc75406` but says the same thing — source wins, claim re-verified.)*

---

## §D — Did the trades have room to trail? (the part that matters most)

Scope: 181 trades entered (`qty_filled>0`: 141 CLOSED + 40 CLOSED_MANUAL); 242 never filled (FAILED 188 / REJECTED 44 / CANCELLED 9 / one 0-qty manual). `trade_excursions` covers **135** of the 181 entered (candles begin 2026-06-19; the other 46 are early/sub-minute — a coverage caveat, not a silent drop).

**Measurement note (instruction §D3, §H.5).** `trade_excursions.mfe_pct` is computed over `entry_time … exit_time` (`state_store.py:1963`, `entry_dt <= cdt <= exit_dt`) — i.e. **exit-censored**. This is the *correct* measure for the trail's **arming** question (the trail arms on favourable excursion *during the holding period*), and **useless** for the "runners past TGT" question (a TGT_HIT trade's window ends at the TGT fill). For the latter I ran a separate **uncapped** reconstruction (entry → 15:20 on the entry date) directly from the `candles` table. Both are labelled below.

### D.1 — how many trades reached the 0.5% arm threshold? (capped MFE — the correct measure)

| Group | n | armed (MFE ≥ 0.5%) | never green (MFE ≤ 0) |
|---|---:|---:|---:|
| SL_HIT | 64 | **25 (39.1%)** | 9 (14.1%) |
| TGT_HIT | 45 | **43 (95.6%)** | 1 (2.2%) |
| MANUAL | 26 | **20 (76.9%)** | 1 (3.8%) |
| **ALL** | **135** | **88 (65.2%)** | 11 (8.1%) |

**→ Explanation 3 (threshold never reached / decoration) is refuted.** The 0.5% arm is reached by ~two-thirds of trades; it sits well inside where real trades go, not beyond them (§D.2: "many met it").

### D.4 — anti-vacuity: did the SL losers have any favourable excursion before reversing?

Of the 64 SL_HIT trades (capped MFE, i.e. the max favourable move actually seen while held):

| bucket | n | % |
|---|---:|---:|
| never green (MFE ≤ 0) | 9 | 14.1% |
| 0 – 0.25% | 16 | 25.0% |
| 0.25 – 0.5% | 14 | 21.9% |
| **0.5 – 1.0% (armed)** | 15 | 23.4% |
| **≥ 1.0% (armed)** | 10 | 15.6% |

**→ Explanation 4 (trades never went far enough) is refuted as a blanket claim.** Only 14% of losers went straight adverse with nothing to trail; **39% (25/64) reached the 0.5% arm** before reversing to their stop. For those 25, a trailing SL that locked in the arm level would have exited at a **profit** instead of the full loss.

- Their capped `mfe_R` (favourable reach in R): median **+0.86R** (max +1.47R).
- **Benefit-only counterfactual (upper bound):** if the trailed SL had held on reversal, median exit **+0.67R** vs actual **−1.12R** → median swing **+1.84R/trade**, **sum +46.7R** across the 25.
- **This is an UPPER bound, not the net effect** (see D.5). It counts only rescued losers and ignores the offsetting harm on winners; it assumes the trailed SL is hit cleanly on the reversal (reasonable — these trades reversed *through* that level to hit the lower original stop) and ignores tick-rounding / slippage on the trailed exit.

### D.3 — the winners, specifically: re-deriving the "~2.93R"

The inherited "~2.93R median true MFE against exits at 1.5R" was described as underpowered (the 19-Jul OOS run had n=9 winners → 1.44R). **Re-derived here from candles over the whole book (n=45 winners), uncapped entry→EOD:**

| measure | TGT_HIT winners (n=45) |
|---|---|
| **uncapped `mfe_R` median** | **+2.96R** (mean +3.66, p75 +4.47, max +14.68) |
| capped `mfe_R` median (censored at TGT) | +1.59R |
| median "room left on the table" (uncapped − capped) | **+1.36R** |
| reached ≥ 1.5R uncapped | 42/45 (93%) |
| reached ≥ 2.0R uncapped | 35/45 (78%) |
| reached ≥ 2.93R uncapped | 24/45 (53%) |

**In-sample vs out-of-sample split (this is the honest test — the whole-book +2.96R blends both).** The original 2.93R was an in-sample (≤13-Jul) discovery figure; the 19-Jul forward test found only 1.44R (n=9) and called it "cannot distinguish." Re-splitting my n=45 at the OOS boundary:

| winners' uncapped median `mfe_R` | in-sample | out-of-sample |
|---|---|---|
| boundary 2026-07-13 | +3.06R (n=28) | **+2.41R (n=17)** |
| boundary 2026-07-14 | +3.11R (n=31) | **+2.20R (n=14)** |

**→ The room-beyond-TGT holds forward, at attenuated magnitude and modest power.** In-sample reproduces ~3.1R (near-circular — largely the original population). **Out-of-sample winners median ~2.2–2.4R (n=14–17)** — below in-sample but clearly above the 1.5R exit, and **materially higher than the 19-Jul n=9 = 1.44R** (which was flagged unstable; the +5–8 winners accumulated since moved it up). So the winners genuinely have room past the fixed 1.5R TGT out-of-sample too, ~0.7–0.9R median — with wide error bars at n≈15. (Method note: my uncapped window is entry→15:20 max-favourable from candles; I cannot fully reconcile the exact 19-Jul method, so I read this as "updates, at more data" rather than "refutes.")

### D.5 — what the persisted data can and cannot settle

- **Settles (counts, robust):** the arm threshold is reached (D.1), losers had room (D.4), winners had large room beyond TGT (D.3). Explanations 3 and 4 are refuted.
- **Does NOT settle (net P&L of enabling a trail):** the trailing SL rescues some losers **but** 43/45 winners (96%) also arm — a trailed SL can scratch a winner that dips after arming and before reaching TGT. Netting benefit against that harm needs an **ordered intra-path** simulation (the 13-Jul exit study did this on 115 trades but its per-trade sims were never persisted). The inherited figure — config-only BreakevenMgr 60/80 = **−0.117R, worse than doing nothing** (`05_d4_exit_policy.md`) — is not reproducible at adequate power and is cited here only as prior, not as fact.

---

## §E — Implication for the exits-versus-entries question (implication, not recommendation)

Clearly marked and separate from the finding above. The sequencing is Rama's.

1. **"Exits are the largest unmeasured lever" is supported, not contradicted.** Explanation 4 is refuted: winners' true MFE runs **~3.1R in-sample and ~2.2–2.4R out-of-sample** vs a 1.5R exit ⇒ a median **~0.7–0.9R per winner left uncaptured out-of-sample** (~1.4R in-sample). This is a real exit lever that persists forward, though attenuated and at modest power (n≈15 OOS winners) — no longer the near-nothing the 19-Jul n=9 (1.44R) suggested.

2. **But the lever is the fixed TGT ceiling — which the trailing *stop* never touches.** `SmartTgtManager` trails the **SL** only; the TGT LIMIT stays at 1.5R. Capturing the winners' upside is a *raise/trail-the-target* change, i.e. a **different** mechanism from the one this investigation is about. Making the never-fired trailing **stop** reachable would tighten downside, not capture the +1.36R upside.

3. **The trailing stop's own value is downside-only and net-ambiguous.** It could convert ~39% of SL losses into small wins (a real, sized effect) while risking scratching the 96% of winners that arm. Its net sign is unproven from persisted data; the one inherited estimate for the config-only breakeven variant was negative.

4. **Net implication:** the exit lever that the data points to is the **1.5R target ceiling on winners**, not the absent SL trail. If exits are pursued, they route to the target policy; the trailing stop is a separate, downside-risk change whose net effect is unmeasured. Whether to pursue exits at all versus entry quality remains the open D4 decision, and this evidence re-frames it toward the target ceiling rather than resolves it.

---

## Evidence appendix (reproduce)

All DB reads: `sqlite3 "file:/home/ubuntu/systems/trading-system/data_store/{trading_system,analytics}.db?immutable=1"` on the VM, 2026-07-24.

- Distributions: `SELECT order_protocol|entry_mode|sl_trail_count, count(*) FROM trades GROUP BY 1` → LIMIT_TRIPLE 423, FULL 423, sl_trail_count 0 = 423. `SELECT count(*) FROM smart_tgt_state` → 0.
- Orders: `SELECT leg, count(*) FROM orders GROUP BY 1` (CO = 0); `sum(superseded_by IS NOT NULL)` = 0 / 713.
- Excursions (capped): `trades ⋈ trade_excursions` on `qty_filled>0` → `scratchpad/excursions_135.csv` → `analyze_capped.py`.
- Excursions (uncapped): `trades ⋈ ana.candles` with `c.ts` in `[entry, entry-date 15:20]` → `scratchpad/uncapped_mfe.csv` → `analyze_uncapped.py` (0 sanity violations: uncapped ≥ capped on all 135).

Code anchors (`bc75406`): trail engine `orders/smart_tgt_manager.py` (trigger `:503-507`, increment `:618`, modify `:588`); registration `orders/order_placer.py` (protocol `:950`, CO-gate `:2193-2197`, sl_trail_count copy `:2489`); wiring `main.py:2465-2481, 2553-2564`; config `config/system_config.yaml:455-460`, `config/strategies/*.yaml`; schema `strategies/schema.py:60,99,105`; other engines `orders/breakeven_manager.py:289`, `orders/structure_exit_manager.py:569`.
