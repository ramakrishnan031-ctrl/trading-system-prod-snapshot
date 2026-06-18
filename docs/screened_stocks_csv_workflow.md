# Screened Stocks CSV — Post-Trade Analysis Workflow

**Purpose:** Generate daily CSV of ALL screened stocks (traded + rejected) for candle analysis and strategy tuning.

---

## Workflow

### 1. Daily CSV Generation (Automated)

The cron job runs daily at **16:01 IST** (Mon-Fri) on the VM:

```bash
1 16 * * 1-5 cd /home/ubuntu/systems/trading-system && \
    /home/ubuntu/systems/venv/bin/python scripts/generate_screened_stocks_csv.py >> \
    logs/cron-screened-stocks.log 2>&1
```

Output: `reports/daily_review/screened_stocks_YYYY-MM-DD.csv`

### 2. Download CSV from VM

After market close, download the CSV from the VM:

```bash
scp ubuntu@161.118.187.249:/home/ubuntu/systems/trading-system/reports/daily_review/screened_stocks_2026-05-09.csv \
    ./reports/daily_review/
```

Or use WinSCP/FileZilla to download via SFTP.

### 3. Fetch 1-Minute Candles (Future Tool)

**Note:** `candle_fetcher.py` is not yet implemented. When built, it will:

1. Read the CSV file
2. Extract all symbols (TRADED + NON_TRADED columns)
3. Download 1-minute Zerodha candles for each symbol
4. Save to `data_store/candles/SYMBOL_YYYY-MM-DD.csv`

Planned usage:

```bash
python scripts/candle_fetcher.py 2026-05-09
```

### 4. Analyze Rejections

Compare actual price action against our entry/SL/TGT levels:

- **TRADED symbols**: Did price action validate the trade? Hit SL or TGT?
- **NON_TRADED symbols**: Were rejections correct?
  - Score too low → Did price move significantly? (would we have made money?)
  - Quote unavailable → Was it a transient API issue?
  - Outside entry window → Would earlier entry have been better?

### 5. Tune Strategy Parameters

Based on candle analysis:

- Adjust quality score thresholds
- Tune SL/TGT percentages
- Modify entry window timings
- Refine pullback parameters

---

## CSV Format

3-column vertical layout:

| TRADED | NON_TRADED | REJECTION_REASON |
|--------|-----------|------------------|
| ABLBL | WESTLIFE | Score too low (48/100) |
| AEROFLEX | MANKIND | Duplicate symbol (active position/order exists) |
| BANDHANBNK | SIGMAADV | Quote unavailable (API failure) |
| COFORGE | TVSSCS | Outside entry window (after HH:MM) |
| ... | ... | ... |

**Notes:**
- Columns are padded to same length (empty strings for shorter lists)
- TRADED symbols are deduplicated (set)
- NON_TRADED can have duplicates if same symbol rejected multiple times
- Rejection reasons are expanded from log codes for readability

---

## Manual Run (Testing/Backfill)

To generate CSV for a specific date manually:

```bash
# On VM or PC (requires log file)
python scripts/generate_screened_stocks_csv.py 2026-05-08
```

Default: generates CSV for today if no date argument provided.

---

## Troubleshooting

### Empty CSV (no symbols)

Check:
1. Log file exists: `logs/system_YYYY-MM-DD.log`
2. Log contains signal processor entries
3. Cron job has correct date format (runs post-market)

### Missing symbols

Verify log patterns match:
- `ORDER PLACED — SYMBOL`
- `rejected at CHECK: reason`

Run with debug to see parsed results:

```python
traded, non_traded = parse_log_file(log_path)
print(f"Traded: {traded}")
print(f"Non-traded: {non_traded}")
```

### Wrong rejection reasons

Check `EXACT_MAP` in `scripts/generate_screened_stocks_csv.py` — add new rejection codes as they appear in production logs.

---

## Files

- **Script**: `scripts/generate_screened_stocks_csv.py`
- **Tests**: `tests/test_generate_screened_stocks_csv.py`
- **Cron**: `deploy/cron/trading-system.cron` (line 19-20)
- **Logs**: `logs/cron-screened-stocks.log` (cron execution log)
- **Output**: `reports/daily_review/screened_stocks_YYYY-MM-DD.csv`

---

**Paper/Live Parity:** UNIFIED — works for both modes (mode prefix in log messages ignored).

---

*Last updated: 2026-05-09*
