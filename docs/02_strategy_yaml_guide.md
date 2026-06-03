# Strategy YAML Configuration Guide

Each strategy is defined in `config/strategies/<name>.yaml` and mapped to
a Chartink scanner via `config/scan_webhook_map.yaml`.

## Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique strategy identifier (must match filename) |
| `display_name` | string | Human-readable name |
| `description` | string | One-line description |
| `direction` | LONG \| SHORT | Trade direction |
| `intent` | INTRADAY \| DELIVERY | Determines product type (MIS/CO vs CNC) |
| `order_protocol` | CO_PLUS_TGT \| LIMIT_TRIPLE | Order placement method |

## Entry Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `entry_method` | LIMIT \| MARKET | LIMIT | Order type for entry |
| `entry_offset_pct` | float | 0.001 | Offset from trigger price for limit orders |
| `entry_start_time` | HH:MM | 09:20 | Earliest entry time (IST) |
| `entry_end_time` | HH:MM | 15:00 | Latest entry time (IST) |
| `active_days` | list | MON-FRI | Days the strategy accepts signals |

## Stop Loss Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sl_method` | FIXED_PCT \| ATR | FIXED_PCT | How SL distance is calculated |
| `sl_pct` | float | 0.01 | Fixed % distance from entry (when FIXED_PCT) |
| `sl_atr_multiplier` | float | 1.5 | ATR multiplier (when ATR method) |
| `sl_min_pct` | float | 0.003 | Floor: SL never closer than this % |
| `sl_max_pct` | float | 0.05 | Ceiling: SL never farther than this % |
| `sl_gap_buffer_pct` | float | 0.0 | Extra SL width during 09:15-09:30 gap window |

## Target Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tgt_method` | RISK_REWARD \| FIXED_PCT \| ATR | RISK_REWARD | How target is calculated |
| `tgt_risk_reward` | float | 1.5 | R:R ratio (when RISK_REWARD) |
| `tgt_pct` | float | 0.0 | Fixed % from entry (when FIXED_PCT) |
| `tgt_atr_multiplier` | float | 2.5 | ATR multiplier (when ATR) |

## Smart Target (Trailing)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `smart_tgt_enabled` | bool | true | Enable trailing target logic |
| `smart_tgt_trail_trigger_pct` | float | 0.005 | Arm after price moves this % toward target |
| `smart_tgt_trail_step_pct` | float | 0.003 | Trail step size once armed |

## Pullback Gate

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pullback_wait_enabled` | bool | false | Wait for price pullback before entry |
| `pullback_wait_tolerance_pct` | float | 0.005 | Acceptable pullback depth |
| `pullback_wait_timeout_sec` | int | 180 | Max wait before abandoning |

## Scoring & Filtering

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min_score` | int | 0 | Minimum quality score (0-100) to accept signal |
| `min_volume_surge` | float | 1.3 | Min volume vs 20-day avg |
| `min_adr_pct` | float | 0.005 | Min average daily range % |
| `max_spread_pct` | float | 0.005 | Max bid-ask spread % |

## Risk & Sizing

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_risk_pct` | float | 0.01 | Max capital at risk per trade |
| `lot_size` | int | 1 | Lot size for position sizing |
| `max_concurrent_positions` | int | 3 | Max simultaneous positions for this strategy |

## Strategy Types

### Gap Fade (Long/Short)
Fades opening gaps. Enters when price reverses toward prior close.
- `pullback_wait_enabled: false` (timing-sensitive)
- `sl_gap_buffer_pct: 0.3` (wider SL during gap window)

### Gap Go (Long/Short)
Trades in the direction of the gap. Momentum continuation.
- `entry_start_time: "09:20"` (early entry)
- `tgt_risk_reward: 2.0` (higher R:R for momentum)

### First Pullback (Long/Short)
Enters on first pullback after a strong opening move.
- `pullback_wait_enabled: true`
- `pullback_wait_timeout_sec: 180`

### VWAP Bounce/Rejection (Long/Short)
Trades bounces off VWAP (long) or rejections at VWAP (short).
- `min_volume_surge: 1.5` (volume confirmation required)

### Range Breakout (Long/Short)
Enters on breakout from opening range.
- `entry_start_time: "09:45"` (after range forms)

### Open Low Breakout / Open High Breakdown
Trades break of opening price levels.
- `entry_start_time: "09:25"`

### Positional Strategies
Longer-hold strategies using CNC product type.
- `intent: DELIVERY`
- `entry_end_time: "14:00"` (earlier cutoff)
- `max_risk_pct: 0.005` (lower risk per trade)

## Example: Full Strategy YAML

```yaml
name: "gap_fade_long"
display_name: "Gap Fade Long"
description: "Long entry fading an opening gap-down"

direction: "LONG"
intent: "INTRADAY"
order_protocol: "CO_PLUS_TGT"

entry_method: "LIMIT"
entry_offset_pct: 0.001

sl_method: "FIXED_PCT"
sl_pct: 0.01
sl_atr_multiplier: 1.5
sl_min_pct: 0.003
sl_max_pct: 0.05
sl_gap_buffer_pct: 0.3

tgt_method: "RISK_REWARD"
tgt_risk_reward: 1.5

smart_tgt_enabled: true
smart_tgt_trail_trigger_pct: 0.005
smart_tgt_trail_step_pct: 0.003

pullback_wait_enabled: false

min_score: 60
min_volume_surge: 1.3
min_adr_pct: 0.005
max_spread_pct: 0.005

max_risk_pct: 0.01
lot_size: 1
max_concurrent_positions: 3

entry_start_time: "09:25"
entry_end_time: "15:00"
active_days: [MON, TUE, WED, THU, FRI]
```
