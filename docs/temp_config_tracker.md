# TEMP Config Values Tracker

**Last Updated:** 2026-06-03
**Tool:** `python scripts/revert_temp_config.py`

All values below were raised or changed during paper testing to avoid
false kill-switch triggers. They MUST be reverted to production values
before scaling beyond Rs 25K micro capital.

---

## Active TEMP Values

| # | File | Key | TEMP Value | Prod Value | Reason | Risk if Not Reverted |
|---|------|-----|-----------|-----------|--------|---------------------|
| 1 | system_config.yaml | capital.daily_loss_limit | 100000.0 | 1250.0 | Paper mode has no real P&L | Unlimited daily loss allowed |
| 2 | system_config.yaml | risk.max_consecutive_losses | 20 | 4 | Paper fills cause false loss streaks | No halt after repeated losses |
| 3 | system_config.yaml | risk.daily_loss_limit_pct | 1.00 | 0.05 | 100% to avoid paper kill switch | 100% capital loss in one day |
| 4 | system_config.yaml | order_reconciler.capital_drift_tolerance | 100000.0 | 50.0 | Paper mode drift is inherent | Capital mismatch not detected |
| 5 | system_config.yaml | drift_handler.log_only_threshold_rs | 100000.0 | 250.0 | Paper drift suppression | Drift warnings suppressed |
| 6 | system_config.yaml | drift_handler.soft_kill_threshold_rs | 200000.0 | 1000.0 | Paper drift suppression | Drift soft kill disabled |
| 7 | system_config.yaml | drift_handler.hard_kill_threshold_rs | 500000.0 | 2500.0 | Paper drift suppression | Drift hard kill disabled |
| 8 | strategies/gap_fade_long.yaml | min_score | 30 | 60 | More signals in paper | Low-quality signals accepted |

---

## How to Revert

```bash
# Dry-run (shows what would change)
python scripts/revert_temp_config.py

# Apply reverts
python scripts/revert_temp_config.py --apply --confirm
```

## Startup Check

Startup check #14 (`check_temp_config_values`) counts TEMP markers in all
config YAMLs. Currently a WARNING. When capital > Rs 25K, consider promoting
to a blocking failure.

---

## Status Legend

- **ACTIVE**: TEMP value in use, needs revert before production scaling
- **REVERTED**: Production value restored, TEMP marker removed
