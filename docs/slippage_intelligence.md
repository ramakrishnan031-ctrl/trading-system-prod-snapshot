# Slippage / Execution Intelligence

A permanent **raw-data** layer for execution quality (schema **v31**, 2026-06-20).
Stores RAW FACTS only — every aggregate (band/strategy stats, tolerance
recommendations) is computed **on-demand via SQL** in reports, so nothing derived
ever goes stale. Recording is fully decoupled (async event subscribers) and
best-effort — it **can never block or fail trade execution**.

## Tables (`core/schema.sql`, main DB)
| Table | Grain | Written on |
|---|---|---|
| `order_execution_log` | one row per filled order/leg | `OrderFilled` event (async) |
| `trade_slippage_log` | one row per completed trade (roll-up + `rr_damage_pct`) | `PositionClosed` event (async) |
| `market_execution_context` | best-effort bid/ask/spread (Priority-2, all nullable) | `OrderFilled` event (async) |

Producer: `orders/slippage_recorder.py` (`SlippageRecorder`), wired in `main.py`.

## `rr_damage_pct` — the key metric
How much of the planned **risk budget** execution destroyed:
```
rr_damage_pct = (entry_adverse + sl_adverse − tgt_favourable) / planned_sl_distance × 100
```
Sign convention — **adverse = positive** (entry/SL), **favourable = positive** (TGT, reduces damage).
SL is fixed at the signal level (TGT recalcs from fill, FIX-013), so entry slippage directly inflates risk.

## Verification / Phase-2 report queries (compute on-demand — do NOT store)
```sql
-- Slippage + R:R damage by price band
SELECT price_band, COUNT(*) n,
       ROUND(AVG(entry_slippage_rs),3) avg_entry_slip_rs,
       ROUND(MAX(entry_slippage_rs),3) max_entry_slip_rs,
       ROUND(AVG(rr_damage_pct),2)     avg_rr_damage_pct
FROM trade_slippage_log GROUP BY price_band ORDER BY price_band;

-- Slippage + R:R damage by strategy
SELECT strategy_name, COUNT(*) n,
       ROUND(AVG(entry_slippage_pct),3) avg_entry_slip_pct,
       ROUND(AVG(rr_damage_pct),2)      avg_rr_damage_pct
FROM trade_slippage_log GROUP BY strategy_name ORDER BY avg_rr_damage_pct DESC;

-- Worst rr_damage trades (where execution hurt most)
SELECT trade_date, symbol, strategy_name, rr_damage_pct,
       entry_slippage_rs, sl_slippage_rs
FROM trade_slippage_log ORDER BY rr_damage_pct DESC LIMIT 20;

-- Spread vs entry slippage (where bid/ask was captured)
SELECT t.symbol, t.entry_slippage_rs, m.spread_rs, m.spread_pct
FROM trade_slippage_log t
JOIN market_execution_context m ON t.trade_id = m.trade_id AND m.leg = 'ENTRY'
WHERE m.spread_rs IS NOT NULL;

-- Per-leg fill detail for a trade
SELECT leg, order_type, intended_price, actual_price, slippage_rs, slippage_pct, filled_qty
FROM order_execution_log WHERE parent_trade_id = ? ORDER BY id;
```

## Roadmap
- **Phase 1 (done):** raw tables + recorder + `rr_damage_pct` + price bands.
- **Phase 2 (pending):** reports computing the above on-demand (band/strategy stats, calibration).
- **Phase 3 (pending):** adaptive tolerance engine (Global → Band → Strategy → Symbol hierarchy)
  feeding `entry_gate.slippage_control` (`tiered_slippage_abort`).

Config: `slippage_bands` in `system_config.yaml` (re-bandable). Parity: records paper + live.
