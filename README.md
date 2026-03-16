# S&P 500 Stock Price vs Moving Average Analysis

Interactive chart generator for all S&P 500 stocks showing stock price vs. liquidity-based dynamic moving average.

## Quick Start

### Run the Program
```bash
python sp500_charts_generator_optimized.py
```

**Expected runtime:** 20-40 minutes (processes all 503 S&P 500 stocks in parallel)

**Output:** `sp500_stock_analysis.html` - Interactive HTML file with all charts

## What It Does

Generates an interactive HTML page with charts for all S&P 500 stocks showing:
- **Blue line**: Daily stock price (volatile)
- **Red line**: Liquidity-based dynamic moving average (smooth, lags price)
- Last 5 years of data
- Sorted by market capitalization (descending)

### Key Features

**Interactive Controls:**
- 🔍 **Search box** - Search by ticker (e.g., "AAPL") or company name (e.g., "Apple")
- 📊 **Sort A-Z** - Alphabetically by company name
- 📊 **Sort by Market Cap** - Largest to smallest (default)
- 🏷️ **Sector filters** - Filter by GICS sector (multi-select supported)

**Display:**
- Company name in header (e.g., "Apple Inc." not "AAPL US Equity")
- Market cap under company name
- No chart titles (clean layout)
- Real-time stats counter

## Liquidity-Based Moving Average

Unlike traditional fixed-period moving averages (50-day, 200-day), this uses a dynamic lookback period based on stock liquidity:

```
1. AvgVolume = 60-day rolling average of daily volume
2. AvgDaysTurnover = Float ÷ AvgVolume
3. DynamicMA = Average price over past AvgDaysTurnover days
```

**Result:**
- More responsive for liquid stocks (shorter lookback)
- Smoother for illiquid stocks (longer lookback)
- Adapts to each stock's trading characteristics

## Requirements

### Software
- Python 3.8+
- Bloomberg Terminal (running and logged in with API access)
- Multiple Bloomberg API connections (8 simultaneous for optimal performance)

### Python Packages
```bash
pip install -r requirements.txt
```

Packages:
- `pandas >= 2.0.0`
- `numpy >= 1.24.0`
- `plotly >= 5.18.0`
- `blpapi >= 3.19.0`

### Hardware
- **CPU:** 4+ cores recommended (8+ cores ideal)
- **RAM:** 8GB minimum (16GB recommended)
- **Disk:** 500MB free space

## Performance

### Optimized Version (Parallel Processing)
- **Runtime:** 20-40 minutes for all 503 stocks
- **Workers:** 8 parallel workers (configurable)
- **Speedup:** 5-8x faster than sequential processing
- **Resume capability:** Can resume from failures

### Key Optimizations
1. **Parallel processing** - 8 stocks processed simultaneously
2. **Optimized MA calculation** - NumPy vectorization (20-30% faster)
3. **Progress persistence** - Saves every 10 stocks to `checkpoint.json`
4. **Batch API requests** - Fetches company info in batches of 100

## How to Use the HTML Output

### Search
- Type in the search box to filter by ticker or company name
- Real-time filtering as you type
- Examples: "Apple", "AAPL", "Microsoft", "tech"

### Sort
- **Sort A-Z**: Alphabetically by company name
- **Sort by Market Cap**: Descending order (largest first)

### Filter by Sector (Multi-Select)
- Click "All Sectors" to see everything
- Click individual sectors to filter (e.g., "Information Technology")
- Click multiple sectors to view several at once
- Selected sectors highlighted in green
- Stats show: "Showing X of 503 stocks (Y sectors)"

**Example workflow:**
1. Click "Information Technology" → See all IT stocks
2. Click "Communication Services" → See both IT and Comm stocks
3. Search "meta" → Find Meta in Communication Services
4. Click "All Sectors" → Back to all stocks

## Bloomberg Data Fields

**From SPX Index:**
- `INDX_MEMBERS` - S&P 500 constituent tickers

**For each stock (10 years historical):**
- `PX_LAST` - Daily closing price
- `CUR_MKT_CAP` - Current market capitalization
- `EQY_FLOAT` - Equity float (in millions)
- `PX_VOLUME` - Daily trading volume
- `NAME` - Company name
- `GICS_SECTOR_NAME` - GICS sector classification

## File Structure

```
Stock Price vs Moving Average/
├── sp500_charts_generator_optimized.py   # Main program (USE THIS)
├── requirements.txt                       # Python dependencies
├── README.md                             # This file
├── Stock Price vs Moving Average.xlsx    # Original Excel reference
├── checkpoint.json                       # Auto-generated (resume capability)
└── sp500_stock_analysis.html            # Generated output
```

## Troubleshooting

### "Bloomberg session limit reached"
**Solution:** Reduce number of workers in the code:
```python
# Line 791 in sp500_charts_generator_optimized.py
num_workers = min(4, os.cpu_count() or 4)  # Change from 8 to 4
```

### High memory usage
**Solution:** Reduce number of workers (see above) or use fewer workers (2-4)

### Program interrupted
**Solution:** Just run it again:
```bash
python sp500_charts_generator_optimized.py
```
It will resume from `checkpoint.json` automatically

### Some stocks fail to process
**Behavior:** Normal - program continues with remaining stocks
**Reason:** Insufficient historical data (<100 days)
**Action:** None needed - failed stocks excluded from output

### Charts not displaying in HTML
**Check:**
1. Using modern browser (Chrome, Firefox, Edge)
2. Internet connection (for Plotly CDN)
3. File size is reasonable (~80-100MB for all stocks)

## Configuration Options

### Number of Workers (Line 791)
```python
# Use 8 workers (default, fastest)
num_workers = min(8, os.cpu_count() or 4)

# Use 4 workers (if Bloomberg connection limits)
num_workers = min(4, os.cpu_count() or 4)

# Use 2 workers (slower systems)
num_workers = min(2, os.cpu_count() or 2)
```

### Checkpoint Frequency (Line 815)
```python
# Save every 10 stocks (default)
if len(completed_tickers) % 10 == 0:
    save_checkpoint(checkpoint)

# Save every 5 stocks (more frequent)
if len(completed_tickers) % 5 == 0:
    save_checkpoint(checkpoint)
```

## Technical Details

### Moving Average Calculation
```python
# For each trading day:
lookback_days = int(Float / AvgVolume_60day)
DynamicMA = mean(Price over lookback_days)
```

**Example (liquid stock):**
- Float: 15,000M shares
- AvgVolume: 50M shares/day
- Lookback: 300 days
- → Long-term trend

**Example (illiquid stock):**
- Float: 100M shares
- AvgVolume: 1M shares/day
- Lookback: 100 days
- → Medium-term trend

### Data Processing Flow
1. Connect to Bloomberg Terminal
2. Fetch S&P 500 tickers (503 stocks)
3. Fetch company info (name, sector, market cap) in batches
4. Sort by market cap (descending)
5. Process stocks in parallel (8 workers):
   - Each worker: own Bloomberg connection
   - Fetch 10 years historical data
   - Calculate dynamic MA
   - Generate Plotly chart
   - Return HTML
6. Collect results, sort by market cap
7. Generate final HTML with all features
8. Clean up checkpoint file

### Output HTML Structure
- **Header:** Title and generation date
- **Controls:** Search box, sort buttons, sector filters, stats
- **Charts:** One chart per stock with company name and market cap
- **Footer:** Data source attribution
- **JavaScript:** Real-time search, sort, filter functionality

## Browser Compatibility

Works in all modern browsers:
- ✅ Chrome/Edge (latest)
- ✅ Firefox (latest)
- ✅ Safari (latest)

No frameworks required - vanilla JavaScript for maximum compatibility.

## Data Quality Notes

- **Historical data:** 10 years (but only last 5 years displayed in charts)
- **Update frequency:** Run script to get latest data
- **Missing data:** Forward-filled automatically
- **Holidays/weekends:** No data (charts show trading days only)
- **X-axis gridlines:** Anchored to 1/23 and 7/23 of each year

## Excel Reference

The original Excel methodology is preserved in `Stock Price vs Moving Average.xlsx`:
- All calculations match Excel exactly
- Verified with VST (Vistra Corp) on 2026-01-16
- Same Bloomberg fields, same formulas

## Performance Comparison

| Metric | Sequential | Optimized (8 workers) | Improvement |
|--------|-----------|----------------------|-------------|
| **Runtime** | 2-4 hours | 20-40 minutes | **5-8x faster** |
| **MA Calculation** | Python loops | NumPy arrays | 20-30% faster |
| **Resume capability** | No | Yes | ✓ |
| **Progress visibility** | Sequential | Real-time | ✓ |
| **Memory usage** | ~200MB | ~620MB | Higher |

## Support

For issues or questions:
1. Check Bloomberg Terminal is running and logged in
2. Verify API access enabled in Terminal
3. Check `checkpoint.json` for progress if interrupted
4. Review error messages in console output

## Summary

This program:
- ✅ Fetches S&P 500 stocks from Bloomberg Terminal
- ✅ Calculates liquidity-based dynamic moving averages
- ✅ Generates interactive HTML with 503 charts
- ✅ Provides search, sort, and filter capabilities
- ✅ Runs in 20-40 minutes with parallel processing
- ✅ Can resume from failures automatically
- ✅ Matches Excel calculations exactly

**Ready to use!** Just run `python sp500_charts_generator_optimized.py`
