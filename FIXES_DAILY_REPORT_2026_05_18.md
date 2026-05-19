# Daily Report Generator Fixes - 2026-05-18

## Summary
Fixed 4 critical issues in the daily report generator (`reports/daily_report.py`) affecting May 18 report quality.

## Issues Fixed

### Issue 1: Sheet 2_Orders - Header Merge showing "None"
**Root Cause**: Separator column styling (`FILL_SEPARATOR`) was applied to rows 1-49, which included the merged header cells in rows 1-2. This caused non-top-left cells in merged ranges to display "None".

**Fix**: Changed separator styling to start from row 3 instead of row 1.
```python
# Before: for r in range(1, 50)
# After:  for r in range(3, 50)
```
**Location**: Line 573

---

### Issue 2: Sheet 3_Capital - Strategy Column Empty
**Root Cause**: Trade records had empty `strategy` field, but signals contained the correct strategy name. No fallback logic existed to retrieve strategy from signal.

**Fix**: Added fallback logic to get strategy from signal if trade.strategy is empty.
```python
# Get strategy from trade, fallback to signal if empty
strategy = trade.get("strategy", "") or signal.get("strategy", "UNKNOWN")
```
**Location**: Lines 783-784, 837

---

### Issue 3: Sheet 4_Candles - Header Merge showing "None"
**Root Cause**: Same as Issue 1 - separator column styling interfered with merged headers.

**Fix**: Changed separator styling to start from row 3.
```python
# Before: for r in range(1, 100)
# After:  for r in range(3, 100)
```
**Location**: Line 945

---

### Issue 4: Sheet 6_Strategy_Analysis - Strategy Names Missing
**Root Cause**: Trades grouped by empty strategy key resulted in aggregation under empty string. No fallback to signal.strategy.

**Fix**: 
1. Added signal_map construction
2. Added fallback logic to get strategy from signal
```python
# Build signal map for strategy fallback
signal_map = {s.get("signal_id"): s for s in data.signals}

# Get strategy from trade, fallback to signal if empty
signal_id = trade.get("signal_id", "")
signal = signal_map.get(signal_id, {})
strat = trade.get("strategy", "") or signal.get("strategy", "UNKNOWN")
```
**Location**: Lines 1213-1220

---

## Test Coverage
Added 2 new tests to verify strategy fallback logic:
1. `test_build_sheet_3_capital_strategy_fallback` - Verifies Sheet 3 uses signal.strategy when trade.strategy is empty
2. `test_build_sheet_6_strategy_fallback_to_signal` - Verifies Sheet 6 aggregates correctly with fallback

**Total Tests**: 36/36 passing (34 existing + 2 new)

---

## Verification Steps
To verify the fixes on May 18 data:

```bash
# Regenerate May 18 report
python -m reports.daily_report --date 2026-05-18 --force

# Check output
# File: reports/output/daily_report_2026-05-18.xlsx
```

Expected results:
- ✅ Sheet 2: Headers display clean section names (no "None")
- ✅ Sheet 3: Strategy column shows strategy names (not empty)
- ✅ Sheet 4: Headers display clean section names (no "None")
- ✅ Sheet 6: Strategy-wise breakdown shows strategy names with aggregated metrics

---

## Related Files Modified
1. `reports/daily_report.py` - Main fixes
2. `tests/unit/test_daily_report.py` - Added test coverage

---

## Root Cause Analysis
**Why did trades have empty strategy?**
Possible causes:
1. Historical data before strategy field was enforced
2. Bug in trade creation where strategy wasn't copied from signal
3. Database migration issue

**Why did this work before?**
- The report generator assumed trade.strategy would always be populated (schema has `TEXT NOT NULL`)
- But empty strings ("") satisfy NOT NULL constraint
- No fallback logic existed for empty strings

**Long-term solution:**
- Audit trade creation in `orders/order_manager.py:insert_trade()` to ensure strategy is never empty
- Consider adding a CHECK constraint: `CHECK(strategy != '')` to schema
- Add validation at trade creation time to reject empty strategy

---

## Deployment Notes
- **Priority**: HIGH (affects daily operational analysis)
- **Testing**: All 36 unit tests pass
- **Breaking Changes**: None (backward compatible)
- **Rollback**: Safe - changes are additive fallback logic + styling fixes

---

*Generated: 2026-05-18*
*Fixes by: Claude Sonnet 4.5*
