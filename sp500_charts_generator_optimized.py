"""
S&P 500 Stock Price vs Moving Average Chart Generator (Optimized Version)

This optimized version includes:
- Parallel processing using multiprocessing (5-10x speedup)
- Optimized moving average calculation using NumPy
- Progress persistence to resume from failures
- Lazy loading HTML generation (charts render on scroll)
- Interactive search and filtering by sector
- Sort by company name or market cap

Estimated runtime: 20-40 minutes (down from 2-4 hours)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import blpapi
from blpapi import Event as EventType
import webbrowser
import warnings
import multiprocessing as mp
import os
import json
import argparse

warnings.filterwarnings('ignore')

# Constants
CHECKPOINT_FILE = "checkpoint.json"


def _normalize_ticker(t):
    """Normalize a ticker to Bloomberg format (e.g. 'AAPL' -> 'AAPL Equity')."""
    t = t.strip()
    if not t.upper().endswith('EQUITY'):
        t = f"{t} Equity"
    return t


def load_custom_tickers():
    """
    Load custom (non-S&P 500) tickers from custom_tickers.json in the script directory.
    Returns a set of Bloomberg-format tickers.
    If the file doesn't exist, returns an empty set.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, "custom_tickers.json")

    if not os.path.exists(path):
        return set()

    with open(path, 'r') as f:
        data = json.load(f)

    tickers = data if isinstance(data, list) else data.get('tickers', [])
    return {_normalize_ticker(t) for t in tickers}


def load_portfolio_config(path=None):
    """
    Load portfolio definitions from portfolios.json (used for filtering the view, not for
    determining which tickers to fetch).

    Returns:
    - portfolios: List of portfolio dicts (each with 'name' and 'tickers' in Bloomberg format)
    If the file doesn't exist, returns [].
    """
    if path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, "portfolios.json")

    if not os.path.exists(path):
        return []

    with open(path, 'r') as f:
        config = json.load(f)

    portfolios = []
    for p in config.get('portfolios', []):
        name = p['name']
        bloomberg_tickers = [_normalize_ticker(t) for t in p['tickers']]
        portfolios.append({'name': name, 'tickers': bloomberg_tickers})

    return portfolios


class BloombergDataFetcher:
    """Handles Bloomberg Terminal data fetching"""

    def __init__(self):
        self.session = None
        self.refDataService = None

    def connect(self):
        """Connect to Bloomberg Terminal"""
        sessionOptions = blpapi.SessionOptions()
        sessionOptions.setServerHost("localhost")
        sessionOptions.setServerPort(8194)

        self.session = blpapi.Session(sessionOptions)

        if not self.session.start():
            raise Exception("Failed to start Bloomberg session")

        if not self.session.openService("//blp/refdata"):
            raise Exception("Failed to open //blp/refdata service")

        self.refDataService = self.session.getService("//blp/refdata")

    def disconnect(self):
        """Disconnect from Bloomberg Terminal"""
        if self.session:
            self.session.stop()

    def get_sp500_tickers_with_info(self):
        """Fetch S&P 500 tickers with market cap, company name, and GICS sector"""
        print("Fetching S&P 500 constituents...")

        # Create request for S&P 500 members
        request = self.refDataService.createRequest("ReferenceDataRequest")
        request.append("securities", "SPX Index")
        request.append("fields", "INDX_MEMBERS")

        self.session.sendRequest(request)

        tickers = []
        while True:
            ev = self.session.nextEvent(500)
            for msg in ev:
                if msg.hasElement("securityData"):
                    securityDataArray = msg.getElement("securityData")
                    for i in range(securityDataArray.numValues()):
                        securityData = securityDataArray.getValueAsElement(i)
                        if securityData.hasElement("fieldData"):
                            fieldData = securityData.getElement("fieldData")
                            if fieldData.hasElement("INDX_MEMBERS"):
                                members = fieldData.getElement("INDX_MEMBERS")
                                for j in range(members.numValues()):
                                    member = members.getValueAsElement(j)
                                    if member.hasElement("Member Ticker and Exchange Code"):
                                        ticker = member.getElementAsString("Member Ticker and Exchange Code")
                                        tickers.append(ticker)

            if ev.eventType() == EventType.RESPONSE:
                break

        # Convert to Bloomberg format
        print(f"Found {len(tickers)} S&P 500 stocks. Converting to Bloomberg format...")

        bloomberg_tickers = []
        for ticker in tickers:
            ticker_only = ticker.split()[0]
            bloomberg_ticker = f"{ticker_only} Equity"
            bloomberg_tickers.append(bloomberg_ticker)

        # Fetch market caps, company names, and GICS sectors
        print(f"Fetching company information for {len(bloomberg_tickers)} stocks...")
        sorted_stocks = self._fetch_reference_data(bloomberg_tickers)
        print(f"Successfully fetched information for {len(sorted_stocks)} stocks\n")
        return sorted_stocks

    def get_stock_info_for_tickers(self, tickers):
        """
        Fetch company info (market cap, name, GICS sector) for an arbitrary list of Bloomberg tickers.
        Reuses the same batch-fetch logic as get_sp500_tickers_with_info().
        """
        if not tickers:
            return []
        print(f"Fetching company information for {len(tickers)} non-S&P 500 portfolio tickers...")
        result = self._fetch_reference_data(list(tickers))
        print(f"Successfully fetched information for {len(result)} additional tickers\n")
        return result

    def _fetch_reference_data(self, bloomberg_tickers):
        """
        Fetch CUR_MKT_CAP, NAME, GICS_SECTOR_NAME for a list of Bloomberg-format tickers.
        Returns a list of dicts sorted by market cap descending.
        """
        stock_info = {}
        batch_size = 100

        for i in range(0, len(bloomberg_tickers), batch_size):
            batch = bloomberg_tickers[i:i+batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(bloomberg_tickers) + batch_size - 1) // batch_size
            print(f"  Batch {batch_num}/{total_batches} ({len(stock_info)} stocks processed so far)...")

            request = self.refDataService.createRequest("ReferenceDataRequest")

            for ticker in batch:
                request.append("securities", ticker)
            request.append("fields", "CUR_MKT_CAP")
            request.append("fields", "NAME")
            request.append("fields", "GICS_SECTOR_NAME")

            self.session.sendRequest(request)

            while True:
                ev = self.session.nextEvent(500)
                for msg in ev:
                    if msg.hasElement("securityData"):
                        securityDataArray = msg.getElement("securityData")
                        for j in range(securityDataArray.numValues()):
                            securityData = securityDataArray.getValueAsElement(j)
                            ticker = securityData.getElementAsString("security")

                            info = {
                                'ticker': ticker,
                                'market_cap': 0,
                                'company_name': ticker.replace(" Equity", ""),
                                'gics_sector': 'Unknown'
                            }

                            if securityData.hasElement("fieldData"):
                                fieldData = securityData.getElement("fieldData")
                                if fieldData.hasElement("CUR_MKT_CAP"):
                                    info['market_cap'] = fieldData.getElementAsFloat("CUR_MKT_CAP")
                                if fieldData.hasElement("NAME"):
                                    info['company_name'] = fieldData.getElementAsString("NAME")
                                if fieldData.hasElement("GICS_SECTOR_NAME"):
                                    info['gics_sector'] = fieldData.getElementAsString("GICS_SECTOR_NAME")

                            stock_info[ticker] = info

                if ev.eventType() == EventType.RESPONSE:
                    break

        sorted_stocks = sorted(stock_info.values(), key=lambda x: x['market_cap'], reverse=True)
        return sorted_stocks

    def get_historical_data(self, ticker, years=10):
        """Fetch historical data for a ticker"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=years*365)

        request = self.refDataService.createRequest("HistoricalDataRequest")
        request.append("securities", ticker)
        request.append("fields", "PX_LAST")
        request.append("fields", "CUR_MKT_CAP")
        request.append("fields", "EQY_FLOAT")
        request.append("fields", "PX_VOLUME")
        request.set("startDate", start_date.strftime("%Y%m%d"))
        request.set("endDate", end_date.strftime("%Y%m%d"))
        request.set("periodicitySelection", "DAILY")

        self.session.sendRequest(request)

        data = []
        while True:
            ev = self.session.nextEvent(500)
            for msg in ev:
                if msg.hasElement("securityData"):
                    securityData = msg.getElement("securityData")
                    if securityData.hasElement("fieldData"):
                        fieldDataArray = securityData.getElement("fieldData")
                        for i in range(fieldDataArray.numValues()):
                            fieldData = fieldDataArray.getValueAsElement(i)

                            date = fieldData.getElementAsDatetime("date")

                            row = {'Date': date}

                            if fieldData.hasElement("PX_LAST"):
                                row['Price'] = fieldData.getElementAsFloat("PX_LAST")
                            if fieldData.hasElement("CUR_MKT_CAP"):
                                row['MarketCap'] = fieldData.getElementAsFloat("CUR_MKT_CAP")
                            if fieldData.hasElement("EQY_FLOAT"):
                                row['Float'] = fieldData.getElementAsFloat("EQY_FLOAT")
                            if fieldData.hasElement("PX_VOLUME"):
                                row['Volume'] = fieldData.getElementAsFloat("PX_VOLUME")

                            data.append(row)

            if ev.eventType() == EventType.RESPONSE:
                break

        df = pd.DataFrame(data)
        df = df.sort_values('Date').reset_index(drop=True)

        # Convert units to millions
        df['Volume'] = df['Volume'] / 1_000_000
        # Float is already in millions

        # Forward fill missing values
        df = df.ffill()

        return df


class MovingAverageCalculator:
    """Calculates liquidity-based dynamic moving average (optimized)"""

    @staticmethod
    def calculate_dynamic_ma_optimized(df, ma_months=3):
        """
        Optimized version using NumPy vectorization

        Parameters:
        - df: DataFrame with Price, Float, Volume columns
        - ma_months: Number of months for the moving average period calculation

        Returns: DataFrame with additional columns for MA calculations
        """
        df = df.copy()

        # Calculate rolling average volume
        ma_days = ma_months * 20
        df['AvgVolume'] = df['Volume'].rolling(window=ma_days, min_periods=1).mean()

        # Calculate average days to turn over float
        df['AvgDaysTurnover'] = df['Float'] / df['AvgVolume']
        df['AvgDaysTurnover'] = df['AvgDaysTurnover'].replace([np.inf, -np.inf], np.nan)
        df['AvgDaysTurnover'] = df['AvgDaysTurnover'].ffill().fillna(30)

        # Optimized dynamic MA calculation using NumPy
        prices = df['Price'].values
        lookback_days = df['AvgDaysTurnover'].values.astype(int)
        n = len(prices)

        dynamic_ma = np.zeros(n)

        # Vectorized calculation where possible
        for i in range(n):
            lookback = max(1, min(int(lookback_days[i]), i + 1))
            start_idx = max(0, i - lookback + 1)
            dynamic_ma[i] = np.mean(prices[start_idx:i+1])

        df['DynamicMA'] = dynamic_ma

        # Calculate gain/loss vs MA
        df['GainLoss'] = (df['Price'] / df['DynamicMA'] - 1) * 100

        return df


class ChartDataExtractor:
    """Extracts chart data for lazy loading"""

    @staticmethod
    def extract_chart_data(df, ticker, company_name=""):
        """Extract chart data for lazy loading instead of generating Plotly HTML"""
        # Filter to last 5 years of data
        df = df.copy()
        df['Date'] = pd.to_datetime(df['Date'])

        # Get date 5 years ago from the most recent date
        most_recent_date = df['Date'].max()
        five_years_ago = most_recent_date - pd.DateOffset(years=5)
        df = df[df['Date'] >= five_years_ago].reset_index(drop=True)

        # Convert dates to m/d/yyyy format strings
        df['DateStr'] = df['Date'].apply(lambda x: f"{x.month}/{x.day}/{x.year}")

        # Calculate tick values for 6-month intervals (1/23 and 7/23 of each year)
        tick_dates = []
        current_year = most_recent_date.year

        potential_ticks = []
        for year in range(current_year, current_year - 6, -1):
            potential_ticks.append(pd.Timestamp(year=year, month=7, day=23))
            potential_ticks.append(pd.Timestamp(year=year, month=1, day=23))

        potential_ticks.sort()
        potential_ticks = [d for d in potential_ticks if five_years_ago <= d <= most_recent_date]

        # Find closest actual trading day
        available_dates = set(df['Date'].tolist())
        for target_date in potential_ticks:
            search_date = target_date
            for i in range(10):
                if search_date in available_dates:
                    tick_dates.append(search_date)
                    break
                search_date = search_date - pd.Timedelta(days=1)

        tick_dates.sort()
        tickvals = [f"{d.month}/{d.day}/{d.year}" for d in tick_dates]

        # Return data needed for chart rendering
        return {
            'dates': df['DateStr'].tolist(),
            'prices': df['Price'].tolist(),
            'ma': df['DynamicMA'].tolist(),
            'tickvals': tickvals
        }


def process_single_stock(args):
    """
    Process a single stock (for parallel execution)
    This function runs in a separate process
    """
    stock_info, idx, total = args
    ticker = stock_info['ticker']
    company_name = stock_info['company_name']
    gics_sector = stock_info['gics_sector']
    market_cap = stock_info['market_cap']

    try:
        # Each process needs its own Bloomberg connection
        bloomberg = BloombergDataFetcher()
        bloomberg.connect()

        try:
            # Fetch historical data
            df = bloomberg.get_historical_data(ticker, years=10)

            if df.empty or len(df) < 100:
                return {
                    'success': False,
                    'ticker': ticker,
                    'company_name': company_name,
                    'error': 'Insufficient data',
                    'idx': idx,
                    'total': total
                }

            # Calculate dynamic moving average (optimized version)
            df = MovingAverageCalculator.calculate_dynamic_ma_optimized(df, ma_months=3)

            # Extract chart data for lazy loading (instead of generating HTML)
            chart_data = ChartDataExtractor.extract_chart_data(df, ticker, company_name)

            # Get current market cap
            current_market_cap = df['MarketCap'].iloc[-1] if 'MarketCap' in df.columns else market_cap

            return {
                'success': True,
                'ticker': ticker,
                'company_name': company_name,
                'market_cap': current_market_cap,
                'gics_sector': gics_sector,
                'chart_data': chart_data,
                'idx': idx,
                'total': total
            }

        finally:
            bloomberg.disconnect()

    except Exception as e:
        return {
            'success': False,
            'ticker': ticker,
            'company_name': company_name,
            'error': str(e),
            'idx': idx,
            'total': total
        }


class HTMLGenerator:
    """Generates HTML output file with lazy loading charts"""

    @staticmethod
    def create_html(charts_data_list, stocks_info, portfolios=None, custom_tickers=None):
        """
        Create HTML file with lazy loading charts

        Parameters:
        - charts_data_list: List of tuples (ticker, company_name, info_dict with chart_data)
        - stocks_info: List of dicts with ticker, company_name, market_cap, gics_sector
        - portfolios: List of portfolio dicts with 'name' and 'tickers' (Bloomberg format), or None
        - custom_tickers: Set of Bloomberg-format tickers that were added beyond S&P 500, or None
        """
        if portfolios is None:
            portfolios = []
        if custom_tickers is None:
            custom_tickers = set()

        # Build a mapping: ticker -> list of portfolio names it belongs to
        ticker_to_portfolios = {}
        for p in portfolios:
            for t in p['tickers']:
                ticker_to_portfolios.setdefault(t, []).append(p['name'])

        html_template = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Stock Price vs Moving Average</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        :root {{
            --bg-primary: #f5f5f5;
            --bg-secondary: white;
            --text-primary: #333;
            --text-secondary: #666;
            --text-muted: #999;
            --text-header: #2c3e50;
            --border-color: #ddd;
            --shadow-color: rgba(0,0,0,0.1);
            --chart-bg: white;
            --chart-grid: lightgray;
        }}
        [data-theme="dark"] {{
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --text-primary: #eee;
            --text-secondary: #aaa;
            --text-muted: #777;
            --text-header: #e0e0e0;
            --border-color: #444;
            --shadow-color: rgba(0,0,0,0.3);
            --chart-bg: #e0e0e0;
            --chart-grid: #999;
        }}
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            transition: background-color 0.3s, color 0.3s;
        }}
        h1 {{
            text-align: center;
            color: var(--text-primary);
            margin-bottom: 10px;
        }}
        .subtitle {{
            text-align: center;
            color: var(--text-secondary);
            margin-bottom: 30px;
            font-size: 14px;
        }}
        .controls {{
            background-color: var(--bg-secondary);
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px var(--shadow-color);
            margin-bottom: 30px;
            transition: background-color 0.3s;
        }}
        .top-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }}
        .search-container {{
            flex-grow: 1;
            margin-right: 15px;
        }}
        .search-box {{
            width: 100%;
            padding: 12px;
            font-size: 16px;
            border: 2px solid var(--border-color);
            border-radius: 4px;
            box-sizing: border-box;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            transition: background-color 0.3s, border-color 0.3s, color 0.3s;
        }}
        .search-box:focus {{
            outline: none;
            border-color: #3498db;
        }}
        .theme-toggle {{
            display: flex;
            align-items: center;
            gap: 10px;
            white-space: nowrap;
        }}
        .theme-toggle-label {{
            font-size: 14px;
            color: var(--text-secondary);
        }}
        .toggle-switch {{
            position: relative;
            width: 50px;
            height: 26px;
        }}
        .toggle-switch input {{
            opacity: 0;
            width: 0;
            height: 0;
        }}
        .toggle-slider {{
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: #ccc;
            transition: 0.3s;
            border-radius: 26px;
        }}
        .toggle-slider:before {{
            position: absolute;
            content: "";
            height: 20px;
            width: 20px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: 0.3s;
            border-radius: 50%;
        }}
        input:checked + .toggle-slider {{
            background-color: #3498db;
        }}
        input:checked + .toggle-slider:before {{
            transform: translateX(24px);
        }}
        .button-group {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 10px;
        }}
        .filter-label {{
            font-size: 14px;
            font-weight: bold;
            color: var(--text-secondary);
            align-self: center;
            margin-right: 5px;
        }}
        .btn {{
            padding: 10px 20px;
            font-size: 14px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            transition: background-color 0.2s;
        }}
        .btn-sort {{
            background-color: #3498db;
            color: white;
        }}
        .btn-sort:hover {{
            background-color: #2980b9;
        }}
        .btn-filter {{
            background-color: #95a5a6;
            color: white;
        }}
        .btn-filter:hover {{
            background-color: #7f8c8d;
        }}
        .btn-filter.active {{
            background-color: #27ae60;
        }}
        .btn-portfolio {{
            background-color: #8e44ad;
            color: white;
        }}
        .btn-portfolio:hover {{
            background-color: #7d3c98;
        }}
        .btn-portfolio.active {{
            background-color: #6c3483;
            box-shadow: 0 0 0 3px rgba(142, 68, 173, 0.4);
        }}
        .btn-user-portfolio {{
            background-color: #2980b9;
            color: white;
        }}
        .btn-user-portfolio:hover {{
            background-color: #2471a3;
        }}
        .btn-user-portfolio.active {{
            background-color: #1a5276;
            box-shadow: 0 0 0 3px rgba(41, 128, 185, 0.4);
        }}
        .btn-trend {{
            background-color: #95a5a6;
            color: white;
        }}
        .btn-trend:hover {{
            background-color: #7f8c8d;
        }}
        .btn-trend.active-up {{
            background-color: #27ae60;
        }}
        .btn-trend.active-down {{
            background-color: #e74c3c;
        }}
        .btn-manage {{
            background-color: transparent;
            color: var(--text-secondary);
            border: 2px dashed var(--border-color);
            padding: 8px 16px;
        }}
        .btn-manage:hover {{
            border-color: var(--text-secondary);
            color: var(--text-primary);
        }}
        /* Modal styles */
        .modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }}
        .modal-overlay.active {{
            display: flex;
        }}
        .modal {{
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 30px;
            width: 500px;
            max-width: 90vw;
            max-height: 80vh;
            overflow-y: auto;
            box-shadow: 0 10px 40px rgba(0,0,0,0.3);
        }}
        .modal h2 {{
            margin-top: 0;
            color: var(--text-primary);
        }}
        .modal label {{
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: var(--text-secondary);
            font-size: 14px;
        }}
        .modal input[type="text"] {{
            width: 100%;
            padding: 10px;
            font-size: 14px;
            border: 2px solid var(--border-color);
            border-radius: 4px;
            box-sizing: border-box;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            margin-bottom: 15px;
        }}
        .modal input[type="text"]:focus {{
            outline: none;
            border-color: #3498db;
        }}
        .ticker-input-row {{
            display: flex;
            gap: 8px;
            margin-bottom: 10px;
        }}
        .ticker-input-row input {{
            flex: 1;
            margin-bottom: 0 !important;
        }}
        .ticker-input-row button {{
            padding: 10px 16px;
            white-space: nowrap;
        }}
        .ticker-tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 15px;
            min-height: 30px;
        }}
        .ticker-tag {{
            display: inline-flex;
            align-items: center;
            gap: 4px;
            background: #8e44ad;
            color: white;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 13px;
        }}
        .ticker-tag .remove-tag {{
            cursor: pointer;
            font-weight: bold;
            margin-left: 2px;
            opacity: 0.8;
        }}
        .ticker-tag .remove-tag:hover {{
            opacity: 1;
        }}
        .ticker-tag.custom {{
            background: #e67e22;
        }}
        .ticker-universe-list {{
            max-height: 300px;
            overflow-y: auto;
            border: 1px solid var(--border-color);
            border-radius: 4px;
            padding: 10px;
            margin-bottom: 15px;
        }}
        .ticker-universe-list .ticker-tags {{
            margin-bottom: 0;
        }}
        .section-header {{
            font-size: 13px;
            font-weight: bold;
            color: var(--text-secondary);
            margin-bottom: 8px;
        }}
        .section-header span {{
            font-weight: normal;
            color: var(--text-muted);
        }}
        .btn-save-tickers {{
            background: #27ae60;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }}
        .btn-save-tickers:hover {{
            background: #229954;
        }}
        .ticker-error {{
            color: #e74c3c;
            font-size: 13px;
            margin-bottom: 10px;
            display: none;
        }}
        .modal-buttons {{
            display: flex;
            gap: 10px;
            justify-content: flex-end;
            margin-top: 20px;
        }}
        .modal-buttons button {{
            padding: 10px 24px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }}
        .modal-btn-save {{
            background: #27ae60;
            color: white;
        }}
        .modal-btn-save:hover {{
            background: #229954;
        }}
        .modal-btn-cancel {{
            background: #95a5a6;
            color: white;
        }}
        .modal-btn-cancel:hover {{
            background: #7f8c8d;
        }}
        .modal-btn-delete {{
            background: #e74c3c;
            color: white;
            margin-right: auto;
        }}
        .modal-btn-delete:hover {{
            background: #c0392b;
        }}
        .portfolio-list {{
            margin-bottom: 20px;
        }}
        .portfolio-list-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px;
            border: 1px solid var(--border-color);
            border-radius: 4px;
            margin-bottom: 8px;
        }}
        .portfolio-list-item .name {{
            font-weight: bold;
            color: var(--text-primary);
        }}
        .portfolio-list-item .count {{
            font-size: 13px;
            color: var(--text-muted);
        }}
        .portfolio-list-item .actions {{
            display: flex;
            gap: 6px;
        }}
        .portfolio-list-item .actions button {{
            padding: 4px 10px;
            font-size: 12px;
            border: none;
            border-radius: 3px;
            cursor: pointer;
        }}
        .portfolio-list-item.drag-over {{
            border-color: #8e44ad;
            background: rgba(142, 68, 173, 0.1);
        }}
        .drag-handle {{
            cursor: grab;
            color: var(--text-muted);
            font-size: 16px;
            user-select: none;
        }}
        .drag-handle:active {{
            cursor: grabbing;
        }}
        .chart-container {{
            background-color: var(--bg-secondary);
            margin-bottom: 30px;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px var(--shadow-color);
            transition: background-color 0.3s;
            overflow: visible;
        }}
        .company-header {{
            font-size: 24px;
            font-weight: bold;
            color: var(--text-header);
            margin-bottom: 5px;
        }}
        .market-cap {{
            font-size: 14px;
            color: var(--text-muted);
            margin-bottom: 15px;
        }}
        .chart-placeholder {{
            height: 500px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
            background-size: 200% 100%;
            animation: shimmer 1.5s infinite;
            border-radius: 4px;
            color: var(--text-muted);
            font-size: 16px;
        }}
        [data-theme="dark"] .chart-placeholder {{
            background: linear-gradient(90deg, #2a2a4e 25%, #1a1a3e 50%, #2a2a4e 75%);
            background-size: 200% 100%;
        }}
        @keyframes shimmer {{
            0% {{ background-position: 200% 0; }}
            100% {{ background-position: -200% 0; }}
        }}
        .chart-loaded .chart-placeholder {{
            display: none;
        }}
        .chart-area {{
            width: 100%;
            display: none;
        }}
        .chart-loaded .chart-area {{
            display: block;
        }}
        .footer {{
            text-align: center;
            color: var(--text-muted);
            margin-top: 50px;
            padding: 20px;
            font-size: 12px;
        }}
        .no-results {{
            text-align: center;
            padding: 40px;
            color: var(--text-muted);
            font-size: 18px;
        }}
        .stats {{
            text-align: center;
            color: var(--text-secondary);
            margin-bottom: 15px;
            font-size: 14px;
        }}
        .back-to-top {{
            display: block;
            text-align: center;
            margin-top: 10px;
            font-size: 14px;
            color: #3498db;
            text-decoration: none;
        }}
        .back-to-top:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div id="top"></div>
    <h1>Stock Price vs Moving Average</h1>
    <div class="subtitle">
        Generated on {generation_date}
    </div>

    <div class="controls">
        <div class="top-bar">
            <div class="search-container">
                <input type="text" class="search-box" id="searchBox" placeholder="Search by ticker or company name...">
            </div>
            <div class="theme-toggle">
                <span class="theme-toggle-label">Dark Mode</span>
                <label class="toggle-switch">
                    <input type="checkbox" id="themeToggle" onchange="toggleTheme()">
                    <span class="toggle-slider"></span>
                </label>
            </div>
        </div>

        <div class="button-group">
            <button class="btn btn-sort" onclick="sortAZ()">Sort A-Z</button>
            <button class="btn btn-sort" onclick="sortMarketCap()">Sort by Market Cap</button>
            <button class="btn btn-manage" onclick="openTickerModal()">Manage Tickers</button>
        </div>

        <div class="button-group" id="sectorButtons">
            <span class="filter-label">Sectors:</span>
            <button class="btn btn-filter active" onclick="filterSector('all')">All Sectors</button>
            {sector_buttons}
        </div>

        <div class="button-group" id="portfolioButtons">
            <span class="filter-label">Portfolios:</span>
            <!-- Portfolio buttons rendered dynamically by JS -->
        </div>

        <div class="button-group">
            <span class="filter-label">Trend:</span>
            <button class="btn btn-trend" id="btnUptrend" onclick="filterTrend('uptrend')">Uptrend</button>
            <button class="btn btn-trend" id="btnDowntrend" onclick="filterTrend('downtrend')">Downtrend</button>
        </div>

        <div class="stats" id="statsDisplay">
            Showing {num_stocks} stocks
        </div>
    </div>

    <div id="chartsContainer">
        {charts}
    </div>

    <div class="no-results" id="noResults" style="display: none;">
        No stocks match your search or filter criteria.
    </div>

    <!-- Ticker Universe Modal -->
    <div class="modal-overlay" id="tickerModal">
        <div class="modal">
            <h2>Manage Ticker Universe</h2>
            <p style="font-size:13px;color:var(--text-muted);margin-top:-10px;">S&amp;P 500 stocks are included automatically. Add custom tickers below to include them on the next run.</p>

            <div class="section-header">S&amp;P 500 Tickers <span style="color:#95a5a6;">(gray)</span> &amp; Custom Tickers <span style="color:#e67e22;">(orange)</span></div>
            <div class="ticker-universe-list" id="sp500TickerList"></div>

            <div class="section-header">Add Custom Tickers</div>
            <div class="ticker-input-row">
                <input type="text" id="customTickerInput" placeholder="e.g. ET, HEI, MRP" style="margin-bottom:0 !important;">
                <button class="btn btn-sort" onclick="addCustomTicker()">Add</button>
            </div>
            <div class="ticker-error" id="customTickerError"></div>

            <div class="modal-buttons">
                <button class="modal-btn-cancel" onclick="closeTickerModal()">Cancel</button>
                <button class="btn-save-tickers" onclick="saveCustomTickers()">Save Custom Tickers</button>
            </div>
        </div>
    </div>

    <!-- Portfolio Management Modal -->
    <div class="modal-overlay" id="portfolioModal">
        <div class="modal">
            <div id="modalManageView">
                <h2>Manage Portfolios</h2>
                <div class="portfolio-list" id="portfolioList"></div>
                <div class="modal-buttons">
                    <button class="modal-btn-save" onclick="showEditPortfolio(null)">+ New Portfolio</button>
                    <button class="modal-btn-cancel" onclick="closeModal()">Close</button>
                </div>
            </div>
            <div id="modalEditView" style="display:none;">
                <h2 id="modalEditTitle">New Portfolio</h2>
                <label for="portfolioNameInput">Portfolio Name</label>
                <input type="text" id="portfolioNameInput" placeholder="e.g. My Tech Picks">
                <label>Tickers (must already be in the page)</label>
                <div class="ticker-input-row">
                    <input type="text" id="tickerInput" placeholder="e.g. AAPL">
                    <button class="btn btn-sort" onclick="addTickerFromInput()">Add</button>
                </div>
                <div class="ticker-error" id="tickerError"></div>
                <div class="ticker-tags" id="tickerTags"></div>
                <div class="modal-buttons">
                    <button class="modal-btn-delete" id="deletePortfolioBtn" onclick="deleteEditingPortfolio()" style="display:none;">Delete</button>
                    <button class="modal-btn-cancel" onclick="showManageView()">Back</button>
                    <button class="modal-btn-save" onclick="savePortfolio()">Save</button>
                </div>
            </div>
        </div>
    </div>

    <div class="footer">
        Data source: Bloomberg Terminal | Moving Average: Liquidity-based dynamic calculation
    </div>

    <script>
        // Store all chart data for lazy loading
        const chartsData = {charts_data_json};

        // Pre-configured portfolios from Python (read-only)
        const preConfiguredPortfolios = {pre_configured_portfolios_json};

        // Script directory path (for save dialog)
        const scriptDir = {script_dir_json};

        // S&P 500 tickers (auto-included, read-only)
        const sp500Tickers = {sp500_tickers_json};

        // Custom tickers currently saved in custom_tickers.json
        let customTickers = {custom_tickers_json};

        // Current active sector filters (Set for multiple selections)
        let activeSectors = new Set(['all']);

        // Active portfolio name (null = no portfolio filter; mutually exclusive with sectors)
        let activePortfolio = null;

        // Active trend filter (null, 'uptrend', or 'downtrend')
        let activeTrend = null;

        // All portfolios from localStorage (includes pre-configured on first load)
        let userPortfolios = JSON.parse(localStorage.getItem('userPortfolios') || '[]');

        // Migrate old "US Equity" format to "Equity" in localStorage portfolios
        (function migrateTickerFormat() {{
            let changed = false;
            userPortfolios.forEach(p => {{
                p.tickers = p.tickers.map(t => {{
                    if (t.endsWith(' US Equity')) {{
                        changed = true;
                        return t.replace(' US Equity', ' Equity');
                    }}
                    return t;
                }});
            }});
            if (changed) {{
                localStorage.setItem('userPortfolios', JSON.stringify(userPortfolios));
            }}
        }})();

        // Track portfolios the user explicitly deleted so they don't get re-added
        let dismissedPortfolios = JSON.parse(localStorage.getItem('dismissedPortfolios') || '[]');

        // Merge pre-configured portfolios that aren't already in userPortfolios
        (function mergePreConfigured() {{
            const existingNames = new Set(userPortfolios.map(p => p.name));
            let added = false;
            preConfiguredPortfolios.forEach(p => {{
                if (!existingNames.has(p.name) && !dismissedPortfolios.includes(p.name)) {{
                    userPortfolios.unshift(p);
                    added = true;
                }}
            }});
            if (added) {{
                localStorage.setItem('userPortfolios', JSON.stringify(userPortfolios));
            }}
        }})();

        // Currently editing portfolio index (null = new)
        let editingPortfolioIdx = null;
        // Tickers being edited in the modal
        let editingTickers = [];

        // Track loaded charts
        let loadedCharts = 0;
        const totalCharts = {num_stocks};

        // Render a chart using Plotly
        function renderChart(ticker) {{
            const containerDiv = document.getElementById('container-' + ticker);
            const chartDiv = document.getElementById('chart-' + ticker);

            if (containerDiv.dataset.loaded === 'true') return;

            // Mark as loaded first so the chart-area becomes visible
            containerDiv.classList.add('chart-loaded');
            containerDiv.dataset.loaded = 'true';

            // Use requestAnimationFrame to ensure DOM has updated
            requestAnimationFrame(() => {{
                const data = chartsData[ticker];
                if (!data) return;

                const isDark = document.documentElement.getAttribute('data-theme') === 'dark';

                const traces = [
                    {{
                        x: data.dates,
                        y: data.prices,
                        type: 'scatter',
                        mode: 'lines',
                        name: 'Stock Price',
                        line: {{ color: 'blue', width: 2 }},
                        showlegend: false
                    }},
                    {{
                        x: data.dates,
                        y: data.ma,
                        type: 'scatter',
                        mode: 'lines',
                        name: 'Dynamic Moving Average',
                        line: {{ color: 'red', width: 2 }},
                        showlegend: false
                    }}
                ];

                const layout = {{
                    autosize: true,
                    height: 500,
                    margin: {{ l: 60, r: 50, t: 20, b: 80 }},
                    showlegend: false,
                    hovermode: 'x unified',
                    xaxis: {{
                        title: '',
                        tickmode: 'array',
                        tickvals: data.tickvals,
                        ticktext: data.tickvals,
                        tickangle: -90,
                        showgrid: true,
                        gridcolor: isDark ? '#999' : 'lightgray',
                        gridwidth: 1,
                        tickfont: {{ color: isDark ? '#333' : '#333' }}
                    }},
                    yaxis: {{
                        title: '',
                        tickformat: '$,.0f',
                        showgrid: true,
                        gridcolor: isDark ? '#999' : 'lightgray',
                        gridwidth: 0.5,
                        tickfont: {{ color: isDark ? '#333' : '#333' }}
                    }},
                    paper_bgcolor: isDark ? '#e0e0e0' : 'white',
                    plot_bgcolor: isDark ? '#e0e0e0' : 'white'
                }};

                const config = {{
                    responsive: true,
                    displayModeBar: true
                }};

                Plotly.newPlot(chartDiv, traces, layout, config).then(function() {{
                    Plotly.Plots.resize(chartDiv);
                }});

                loadedCharts++;
            }});
        }}

        // Theme toggle functionality
        function toggleTheme() {{
            const isDark = document.getElementById('themeToggle').checked;
            document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
            updateChartTheme(isDark);
        }}

        function updateChartTheme(isDark) {{
            const bgColor = isDark ? '#e0e0e0' : 'white';
            const gridColor = isDark ? '#999' : 'lightgray';
            const fontColor = isDark ? '#333' : '#333';

            document.querySelectorAll('.js-plotly-plot').forEach(chart => {{
                Plotly.relayout(chart, {{
                    'paper_bgcolor': bgColor,
                    'plot_bgcolor': bgColor,
                    'xaxis.gridcolor': gridColor,
                    'yaxis.gridcolor': gridColor,
                    'xaxis.tickfont.color': fontColor,
                    'yaxis.tickfont.color': fontColor
                }});
            }});
        }}

        function initTheme() {{
            const savedTheme = localStorage.getItem('theme') || 'light';
            const isDark = savedTheme === 'dark';
            document.documentElement.setAttribute('data-theme', savedTheme);
            document.getElementById('themeToggle').checked = isDark;
            if (isDark) {{
                setTimeout(() => updateChartTheme(true), 100);
            }}
        }}

        // ============ Portfolio Logic ============

        // Get all portfolios
        function getAllPortfolios() {{
            return userPortfolios.map(p => ({{ ...p, type: 'user' }}));
        }}

        // Find portfolio tickers by name (returns Set of tickers)
        function getPortfolioTickers(name) {{
            const all = getAllPortfolios();
            const p = all.find(x => x.name === name);
            return p ? new Set(p.tickers) : new Set();
        }}

        // Render portfolio buttons into the #portfolioButtons div
        function renderPortfolioButtons() {{
            const container = document.getElementById('portfolioButtons');
            // Keep the label
            let html = '<span class="filter-label">Portfolios:</span>';

            userPortfolios.forEach(p => {{
                const activeClass = activePortfolio === p.name ? ' active' : '';
                const escapedName = p.name.replace(/'/g, "\\\\'");
                html += `<button class="btn btn-portfolio${{activeClass}}" onclick="filterPortfolio('${{escapedName}}')" title="Shift+click to edit">${{p.name}}</button>`;
            }});

            html += '<button class="btn btn-manage" onclick="openModal()">+ Manage Portfolios</button>';
            container.innerHTML = html;
        }}

        // Toggle portfolio filter (mutually exclusive with sector filters)
        function filterPortfolio(name) {{
            if (activePortfolio === name) {{
                // Deactivate - reset to all
                activePortfolio = null;
                activeSectors.clear();
                activeSectors.add('all');
                // Re-activate "All Sectors" button
                const allBtn = document.querySelector('#sectorButtons .btn-filter[onclick*="all"]');
                if (allBtn) allBtn.classList.add('active');
            }} else {{
                // Activate this portfolio
                activePortfolio = name;
                // Deactivate all sector buttons
                activeSectors.clear();
                document.querySelectorAll('#sectorButtons .btn-filter').forEach(btn => btn.classList.remove('active'));
            }}
            renderPortfolioButtons();
            filterStocks();
        }}

        // ============ Trend Logic ============

        function getTrend(ticker) {{
            const data = chartsData[ticker];
            if (!data || !data.prices.length || !data.ma.length) return null;
            const lastPrice = data.prices[data.prices.length - 1];
            const lastMA = data.ma[data.ma.length - 1];
            return lastPrice >= lastMA ? 'uptrend' : 'downtrend';
        }}

        function filterTrend(trend) {{
            if (activeTrend === trend) {{
                activeTrend = null;
            }} else {{
                activeTrend = trend;
            }}
            // Update button styles
            document.getElementById('btnUptrend').className = 'btn btn-trend' + (activeTrend === 'uptrend' ? ' active-up' : '');
            document.getElementById('btnDowntrend').className = 'btn btn-trend' + (activeTrend === 'downtrend' ? ' active-down' : '');
            filterStocks();
        }}

        // Search functionality
        document.getElementById('searchBox').addEventListener('input', filterStocks);

        function filterStocks() {{
            const searchTerm = document.getElementById('searchBox').value.toLowerCase();
            const containers = document.querySelectorAll('.chart-container');
            let visibleCount = 0;

            // If portfolio is active, get its ticker set
            const portfolioTickers = activePortfolio ? getPortfolioTickers(activePortfolio) : null;

            containers.forEach(container => {{
                const ticker = container.dataset.ticker;
                const tickerLower = ticker.toLowerCase();
                const company = container.dataset.company.toLowerCase();
                const sector = container.dataset.sector;

                const matchesSearch = tickerLower.includes(searchTerm) || company.includes(searchTerm);

                let matchesFilter;
                if (portfolioTickers) {{
                    // Portfolio filter active
                    matchesFilter = portfolioTickers.has(ticker);
                }} else {{
                    // Sector filter active
                    matchesFilter = activeSectors.has('all') || activeSectors.has(sector);
                }}

                const matchesTrend = !activeTrend || getTrend(ticker) === activeTrend;

                if (matchesSearch && matchesFilter && matchesTrend) {{
                    container.style.display = 'block';
                    visibleCount++;
                    if (container.dataset.loaded === 'false') {{
                        observer.observe(container);
                    }}
                }} else {{
                    container.style.display = 'none';
                }}
            }});

            updateStats(visibleCount);
        }}

        function updateStats(count) {{
            let parts = [];
            if (activePortfolio) {{
                parts.push(`Portfolio: ${{activePortfolio}}`);
            }} else if (!activeSectors.has('all')) {{
                parts.push(`${{activeSectors.size}} sector${{activeSectors.size > 1 ? 's' : ''}}`);
            }}
            if (activeTrend) {{
                parts.push(activeTrend === 'uptrend' ? 'Uptrend' : 'Downtrend');
            }}
            const contextText = parts.length > 0 ? ` (${{parts.join(' | ')}})` : '';
            document.getElementById('statsDisplay').textContent = `Showing ${{count}} of {num_stocks} stocks${{contextText}}`;
            document.getElementById('noResults').style.display = count === 0 ? 'block' : 'none';
        }}

        function sortAZ() {{
            const container = document.getElementById('chartsContainer');
            const charts = Array.from(container.children);
            charts.sort((a, b) => a.dataset.company.toLowerCase().localeCompare(b.dataset.company.toLowerCase()));
            charts.forEach(chart => container.appendChild(chart));
        }}

        function sortMarketCap() {{
            const container = document.getElementById('chartsContainer');
            const charts = Array.from(container.children);
            charts.sort((a, b) => parseFloat(b.dataset.marketcap) - parseFloat(a.dataset.marketcap));
            charts.forEach(chart => container.appendChild(chart));
        }}

        function filterSector(sector) {{
            // Clear portfolio filter when clicking sectors
            if (activePortfolio) {{
                activePortfolio = null;
                renderPortfolioButtons();
            }}

            if (sector === 'all') {{
                activeSectors.clear();
                activeSectors.add('all');
                document.querySelectorAll('#sectorButtons .btn-filter').forEach(btn => btn.classList.remove('active'));
                event.target.classList.add('active');
            }} else {{
                if (activeSectors.has(sector)) {{
                    activeSectors.delete(sector);
                    event.target.classList.remove('active');
                    if (activeSectors.size === 0) {{
                        activeSectors.add('all');
                        document.querySelector('#sectorButtons .btn-filter[onclick*="all"]').classList.add('active');
                    }}
                }} else {{
                    activeSectors.delete('all');
                    activeSectors.add(sector);
                    event.target.classList.add('active');
                    document.querySelector('#sectorButtons .btn-filter[onclick*="all"]').classList.remove('active');
                }}
            }}
            filterStocks();
        }}

        // ============ Modal Logic ============

        function openModal() {{
            document.getElementById('portfolioModal').classList.add('active');
            showManageView();
        }}

        function closeModal() {{
            document.getElementById('portfolioModal').classList.remove('active');
        }}

        // Close modal when clicking overlay
        document.getElementById('portfolioModal').addEventListener('click', function(e) {{
            if (e.target === this) closeModal();
        }});

        function showManageView() {{
            document.getElementById('modalManageView').style.display = 'block';
            document.getElementById('modalEditView').style.display = 'none';
            renderPortfolioList();
        }}

        function renderPortfolioList() {{
            const list = document.getElementById('portfolioList');
            let html = '';

            if (userPortfolios.length > 0) {{
                html += '<div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">Drag to reorder</div>';
                userPortfolios.forEach((p, idx) => {{
                    html += `<div class="portfolio-list-item" draggable="true" data-idx="${{idx}}">
                        <div style="display:flex;align-items:center;gap:8px;">
                            <span class="drag-handle">&#x2630;</span>
                            <span class="name">${{p.name}}</span> <span class="count">${{p.tickers.length}} tickers</span>
                        </div>
                        <div class="actions">
                            <button class="btn btn-sort" onclick="showEditPortfolio(${{idx}})">Edit</button>
                            <button class="btn" style="background:#e74c3c;color:white;" onclick="deleteUserPortfolio(${{idx}})">Delete</button>
                        </div>
                    </div>`;
                }});
            }} else {{
                html = '<div style="text-align:center;color:var(--text-muted);padding:20px;">No portfolios yet. Create one!</div>';
            }}

            list.innerHTML = html;
            initDragAndDrop();
        }}

        function initDragAndDrop() {{
            let dragIdx = null;
            const items = document.querySelectorAll('#portfolioList .portfolio-list-item[draggable]');
            items.forEach(item => {{
                item.addEventListener('dragstart', (e) => {{
                    dragIdx = parseInt(item.dataset.idx);
                    item.style.opacity = '0.4';
                    e.dataTransfer.effectAllowed = 'move';
                }});
                item.addEventListener('dragend', () => {{
                    item.style.opacity = '1';
                    document.querySelectorAll('#portfolioList .portfolio-list-item').forEach(el => el.classList.remove('drag-over'));
                }});
                item.addEventListener('dragover', (e) => {{
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    item.classList.add('drag-over');
                }});
                item.addEventListener('dragleave', () => {{
                    item.classList.remove('drag-over');
                }});
                item.addEventListener('drop', (e) => {{
                    e.preventDefault();
                    const dropIdx = parseInt(item.dataset.idx);
                    if (dragIdx !== null && dragIdx !== dropIdx) {{
                        const moved = userPortfolios.splice(dragIdx, 1)[0];
                        userPortfolios.splice(dropIdx, 0, moved);
                        localStorage.setItem('userPortfolios', JSON.stringify(userPortfolios));
                        renderPortfolioList();
                        renderPortfolioButtons();
                    }}
                }});
            }});
        }}

        function showEditPortfolio(idx) {{
            document.getElementById('modalManageView').style.display = 'none';
            document.getElementById('modalEditView').style.display = 'block';

            editingPortfolioIdx = idx;

            if (idx !== null) {{
                // Editing existing
                const p = userPortfolios[idx];
                document.getElementById('portfolioNameInput').value = p.name;
                editingTickers = [...p.tickers];
                document.getElementById('modalEditTitle').textContent = 'Edit Portfolio';
                document.getElementById('deletePortfolioBtn').style.display = 'block';
            }} else {{
                // New
                document.getElementById('portfolioNameInput').value = '';
                editingTickers = [];
                document.getElementById('modalEditTitle').textContent = 'New Portfolio';
                document.getElementById('deletePortfolioBtn').style.display = 'none';
            }}

            document.getElementById('tickerError').style.display = 'none';
            renderTickerTags();
        }}

        function renderTickerTags() {{
            const container = document.getElementById('tickerTags');
            container.innerHTML = editingTickers.map((t, i) =>
                `<span class="ticker-tag">${{t}} <span class="remove-tag" onclick="removeEditingTicker(${{i}})">&times;</span></span>`
            ).join('');
        }}

        function removeEditingTicker(idx) {{
            editingTickers.splice(idx, 1);
            renderTickerTags();
        }}

        function addTickerFromInput() {{
            const input = document.getElementById('tickerInput');
            let val = input.value.trim().toUpperCase();
            if (!val) return;

            // Normalize to Bloomberg format if needed
            if (!val.endsWith('EQUITY')) {{
                val = val + ' Equity';
            }}

            // Validate: ticker must exist in page data
            if (!chartsData[val]) {{
                const errEl = document.getElementById('tickerError');
                errEl.textContent = `"${{val}}" is not in this report. Use Manage Tickers to add it, then re-run the script.`;
                errEl.style.display = 'block';
                return;
            }}

            // Check for duplicates
            if (editingTickers.includes(val)) {{
                const errEl = document.getElementById('tickerError');
                errEl.textContent = `"${{val}}" is already in this portfolio.`;
                errEl.style.display = 'block';
                return;
            }}

            document.getElementById('tickerError').style.display = 'none';
            editingTickers.push(val);
            renderTickerTags();
            input.value = '';
            input.focus();
        }}

        // Allow Enter key to add ticker
        document.getElementById('tickerInput').addEventListener('keydown', function(e) {{
            if (e.key === 'Enter') {{
                e.preventDefault();
                addTickerFromInput();
            }}
        }});

        function savePortfolio() {{
            const name = document.getElementById('portfolioNameInput').value.trim();
            if (!name) {{
                alert('Please enter a portfolio name.');
                return;
            }}
            if (editingTickers.length === 0) {{
                alert('Please add at least one ticker.');
                return;
            }}

            // Check name uniqueness
            const allNames = userPortfolios.map((p, i) => i === editingPortfolioIdx ? null : p.name).filter(Boolean);
            if (allNames.includes(name)) {{
                alert('A portfolio with this name already exists. Choose a different name.');
                return;
            }}

            const portfolio = {{ name: name, tickers: [...editingTickers] }};

            if (editingPortfolioIdx !== null) {{
                // Check if the active portfolio was the one being renamed
                const oldName = userPortfolios[editingPortfolioIdx].name;
                userPortfolios[editingPortfolioIdx] = portfolio;
                if (activePortfolio === oldName) {{
                    activePortfolio = name;
                }}
            }} else {{
                userPortfolios.push(portfolio);
            }}

            localStorage.setItem('userPortfolios', JSON.stringify(userPortfolios));
            renderPortfolioButtons();
            showManageView();
        }}

        function dismissPortfolioName(name) {{
            if (!dismissedPortfolios.includes(name)) {{
                dismissedPortfolios.push(name);
                localStorage.setItem('dismissedPortfolios', JSON.stringify(dismissedPortfolios));
            }}
        }}

        function deleteEditingPortfolio() {{
            if (editingPortfolioIdx === null) return;
            const name = userPortfolios[editingPortfolioIdx].name;
            if (!confirm(`Delete portfolio "${{name}}"?`)) return;

            if (activePortfolio === name) {{
                activePortfolio = null;
                activeSectors.clear();
                activeSectors.add('all');
                const allBtn = document.querySelector('#sectorButtons .btn-filter[onclick*="all"]');
                if (allBtn) allBtn.classList.add('active');
            }}

            dismissPortfolioName(name);
            userPortfolios.splice(editingPortfolioIdx, 1);
            localStorage.setItem('userPortfolios', JSON.stringify(userPortfolios));
            renderPortfolioButtons();
            filterStocks();
            showManageView();
        }}

        function deleteUserPortfolio(idx) {{
            const name = userPortfolios[idx].name;
            if (!confirm(`Delete portfolio "${{name}}"?`)) return;

            if (activePortfolio === name) {{
                activePortfolio = null;
                activeSectors.clear();
                activeSectors.add('all');
                const allBtn = document.querySelector('#sectorButtons .btn-filter[onclick*="all"]');
                if (allBtn) allBtn.classList.add('active');
            }}

            dismissPortfolioName(name);
            userPortfolios.splice(idx, 1);
            localStorage.setItem('userPortfolios', JSON.stringify(userPortfolios));
            renderPortfolioButtons();
            filterStocks();
            renderPortfolioList();
        }}

        // ============ Custom Ticker Management ============

        function openTickerModal() {{
            document.getElementById('tickerModal').classList.add('active');
            renderSP500List();
            renderCustomTickerTags();
            document.getElementById('customTickerError').style.display = 'none';
            document.getElementById('customTickerInput').value = '';
        }}

        function closeTickerModal() {{
            document.getElementById('tickerModal').classList.remove('active');
        }}

        // Close ticker modal when clicking overlay
        document.getElementById('tickerModal').addEventListener('click', function(e) {{
            if (e.target === this) closeTickerModal();
        }});

        function renderSP500List() {{
            const container = document.getElementById('sp500TickerList');
            const sp500Html = sp500Tickers.map(t => `<span class="ticker-tag" style="background:#95a5a6;cursor:default;">${{t.replace(' Equity', '')}}</span>`).join('');
            const customHtml = customTickers.map((t, i) =>
                `<span class="ticker-tag custom">${{t.replace(' Equity', '')}} <span class="remove-tag" onclick="removeCustomTicker(${{i}})">&times;</span></span>`
            ).join('');
            container.innerHTML = '<div class="ticker-tags">' + sp500Html + customHtml + '</div>';
        }}

        function renderCustomTickerTags() {{
            renderSP500List();
        }}

        function addCustomTicker() {{
            const input = document.getElementById('customTickerInput');
            const errEl = document.getElementById('customTickerError');
            // Support comma-separated input
            const raw = input.value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
            if (raw.length === 0) return;

            const errors = [];
            for (let val of raw) {{
                if (!val.endsWith('EQUITY')) {{
                    val = val + ' Equity';
                }}
                if (sp500Tickers.includes(val)) {{
                    errors.push(`${{val.replace(' Equity', '')}} is already in S&P 500`);
                    continue;
                }}
                if (customTickers.includes(val)) {{
                    errors.push(`${{val.replace(' Equity', '')}} is already added`);
                    continue;
                }}
                customTickers.push(val);
            }}

            if (errors.length > 0) {{
                errEl.textContent = errors.join('; ');
                errEl.style.display = 'block';
            }} else {{
                errEl.style.display = 'none';
            }}

            renderCustomTickerTags();
            input.value = '';
            input.focus();
        }}

        function removeCustomTicker(idx) {{
            customTickers.splice(idx, 1);
            renderCustomTickerTags();
        }}

        // Enter key to add custom ticker
        document.getElementById('customTickerInput').addEventListener('keydown', function(e) {{
            if (e.key === 'Enter') {{
                e.preventDefault();
                addCustomTicker();
            }}
        }});

        async function saveCustomTickers() {{
            // Convert to short form for the JSON file
            const tickers = customTickers.map(t => t.replace(/ Equity$/i, ''));
            const jsonStr = JSON.stringify({{ tickers: tickers }}, null, 2);

            // Use File System Access API (Chrome/Edge) to let user pick save location
            if (window.showSaveFilePicker) {{
                try {{
                    const handle = await window.showSaveFilePicker({{
                        suggestedName: 'custom_tickers.json',
                        types: [{{
                            description: 'JSON File',
                            accept: {{ 'application/json': ['.json'] }}
                        }}]
                    }});
                    const writable = await handle.createWritable();
                    await writable.write(jsonStr);
                    await writable.close();
                    closeTickerModal();
                    alert('Saved! Re-run the Python script to fetch data for the new tickers.');
                    return;
                }} catch (err) {{
                    if (err.name === 'AbortError') return;
                }}
            }}

            // Fallback: download file
            const blob = new Blob([jsonStr], {{ type: 'application/json' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'custom_tickers.json';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            closeTickerModal();

            alert(`Saved custom_tickers.json to your Downloads folder.\\nMove it to:\\n${{scriptDir}}\\nThen re-run the script to fetch data for the new tickers.`);
        }}

        // ============ Intersection Observer ============

        const observer = new IntersectionObserver((entries) => {{
            entries.forEach(entry => {{
                if (entry.isIntersecting) {{
                    const ticker = entry.target.dataset.ticker;
                    renderChart(ticker);
                    observer.unobserve(entry.target);
                }}
            }});
        }}, {{
            root: null,
            rootMargin: '200px',
            threshold: 0.01
        }});

        // Start observing all chart containers
        document.querySelectorAll('.chart-container').forEach(container => {{
            observer.observe(container);
        }});

        // Initialize theme, portfolio buttons, and custom tickers summary on page load
        document.addEventListener('DOMContentLoaded', function() {{
            initTheme();
            renderPortfolioButtons();
        }});
    </script>
</body>
</html>
"""

        # Get unique sectors and create buttons
        sectors = sorted(set(info['gics_sector'] for _, _, info in charts_data_list))
        sector_buttons = []
        for sector in sectors:
            sector_buttons.append(f'<button class="btn btn-filter" onclick="filterSector(\'{sector}\')">{sector}</button>')
        sector_buttons_html = '\n            '.join(sector_buttons)

        # Build pre-configured portfolios JSON for embedding in JS
        pre_configured_portfolios_json = json.dumps(portfolios)

        # Embed script directory path so the save dialog can guide the user
        script_dir_json = json.dumps(os.path.dirname(os.path.abspath(__file__)))

        # Build S&P 500 ticker list and custom tickers list for the ticker modal
        all_chart_tickers = {t for t, _, _ in charts_data_list}
        sp500_tickers_json = json.dumps(sorted(all_chart_tickers - custom_tickers))
        custom_tickers_json = json.dumps(sorted(custom_tickers))
        num_stocks_sp500 = len(all_chart_tickers - custom_tickers)

        # Generate charts HTML containers (placeholders only, no chart content)
        charts_html = []
        charts_data_dict = {}

        for ticker, company_name, info in charts_data_list:
            market_cap = info['market_cap']
            gics_sector = info['gics_sector']
            chart_data = info['chart_data']

            # Store chart data for JavaScript
            charts_data_dict[ticker] = chart_data

            # Determine which portfolios this ticker belongs to
            portfolio_names = ticker_to_portfolios.get(ticker, [])
            portfolios_attr = ','.join(portfolio_names)

            # Create container with placeholder
            chart_section = f"""
    <div class="chart-container"
         id="container-{ticker}"
         data-ticker="{ticker}"
         data-company="{company_name}"
         data-sector="{gics_sector}"
         data-marketcap="{market_cap}"
         data-portfolios="{portfolios_attr}"
         data-loaded="false">
        <div class="company-header">{company_name}</div>
        <div class="market-cap">Market Cap: ${market_cap:,.0f}M</div>
        <div class="chart-placeholder">Loading chart...</div>
        <div class="chart-area" id="chart-{ticker}"></div>
        <a href="#top" class="back-to-top">Back to Top</a>
    </div>
"""
            charts_html.append(chart_section)

        # Convert charts data to JSON
        charts_data_json = json.dumps(charts_data_dict)

        final_html = html_template.format(
            generation_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            num_stocks=len(charts_data_list),
            num_stocks_sp500=num_stocks_sp500,
            sector_buttons=sector_buttons_html,
            charts='\n'.join(charts_html),
            charts_data_json=charts_data_json,
            pre_configured_portfolios_json=pre_configured_portfolios_json,
            script_dir_json=script_dir_json,
            sp500_tickers_json=sp500_tickers_json,
            custom_tickers_json=custom_tickers_json
        )

        return final_html


def load_checkpoint():
    """Load checkpoint data if it exists"""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r') as f:
            return json.load(f)
    return {'completed': [], 'charts_data': [], 'stocks_info': []}


def save_checkpoint(checkpoint_data):
    """Save checkpoint data"""
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(checkpoint_data, f)


def main():
    """Main execution function with parallel processing"""

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="S&P 500 Stock Price vs Moving Average Chart Generator")
    parser.add_argument('--portfolios', type=str, default=None,
                        help="Path to portfolios.json config file (default: looks in script directory)")
    args = parser.parse_args()

    print("="*80)
    print("S&P 500 Stock Price vs Moving Average Chart Generator (OPTIMIZED)")
    print("="*80)
    print()

    # Load portfolio definitions (for view filtering only)
    portfolios = load_portfolio_config(args.portfolios)
    if portfolios:
        print(f"Loaded {len(portfolios)} portfolio filter(s)")
        for p in portfolios:
            print(f"  - {p['name']}: {len(p['tickers'])} tickers")
        print()

    # Load custom tickers (additional stocks beyond S&P 500)
    custom_tickers = load_custom_tickers()
    if custom_tickers:
        print(f"Loaded {len(custom_tickers)} custom ticker(s): {', '.join(sorted(custom_tickers))}")
        print()

    # Initialize Bloomberg connection (main process)
    bloomberg = BloombergDataFetcher()

    try:
        bloomberg.connect()

        # Get S&P 500 tickers with company info
        stocks_info = bloomberg.get_sp500_tickers_with_info()

        # Identify non-S&P 500 tickers from custom list and portfolios
        sp500_ticker_set = {s['ticker'] for s in stocks_info}
        all_extra_tickers = set(custom_tickers)
        for p in portfolios:
            all_extra_tickers.update(p['tickers'])
        non_sp500_tickers = all_extra_tickers - sp500_ticker_set

        if non_sp500_tickers:
            extra_info = bloomberg.get_stock_info_for_tickers(non_sp500_tickers)
            stocks_info.extend(extra_info)

        print(f"Processing {len(stocks_info)} stocks...")
        print("="*80)
        print()

        # Load checkpoint
        checkpoint = load_checkpoint()
        completed_tickers = set(checkpoint['completed'])
        charts_data = checkpoint.get('charts_data', [])

        # Filter out already completed tickers
        remaining_stocks = [s for s in stocks_info if s['ticker'] not in completed_tickers]

        if len(remaining_stocks) < len(stocks_info):
            print(f"Resuming from checkpoint: {len(completed_tickers)} stocks already completed")
            print(f"Remaining: {len(remaining_stocks)} stocks")
            print()

        # Disconnect main Bloomberg session before forking
        bloomberg.disconnect()

        # Prepare arguments for parallel processing
        total_stocks = len(stocks_info)
        args_list = []
        for stock_info in remaining_stocks:
            idx = next(i for i, s in enumerate(stocks_info, 1) if s['ticker'] == stock_info['ticker'])
            args_list.append((stock_info, idx, total_stocks))

        # Determine number of workers
        num_workers = min(8, os.cpu_count() or 4)
        print(f"Using {num_workers} parallel workers")
        print("="*80)
        print()

        # Process stocks in parallel
        with mp.Pool(num_workers) as pool:
            for result in pool.imap_unordered(process_single_stock, args_list):
                if result['success']:
                    print(f"[{result['idx']}/{result['total']}] OK {result['company_name']}")

                    charts_data.append((
                        result['ticker'],
                        result['company_name'],
                        {
                            'chart_data': result['chart_data'],
                            'market_cap': result['market_cap'],
                            'gics_sector': result['gics_sector']
                        }
                    ))

                    completed_tickers.add(result['ticker'])

                    # Save checkpoint every 10 stocks
                    if len(completed_tickers) % 10 == 0:
                        checkpoint = {
                            'completed': list(completed_tickers),
                            'charts_data': charts_data
                        }
                        save_checkpoint(checkpoint)
                else:
                    print(f"[{result['idx']}/{result['total']}] FAIL {result['company_name']}: {result['error']}")

        print()
        print("="*80)
        print("Generating HTML output...")

        # Sort charts_data by original market cap order
        ticker_order = {s['ticker']: i for i, s in enumerate(stocks_info)}
        charts_data.sort(key=lambda x: ticker_order.get(x[0], float('inf')))

        # Generate final HTML
        html_output = HTMLGenerator.create_html(charts_data, stocks_info, portfolios, custom_tickers)

        # Save to file in the same directory as this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_file = os.path.join(script_dir, "sp500_stock_analysis.html")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_output)

        print(f"[OK] Successfully generated {output_file}")
        print(f"[OK] Total charts created: {len(charts_data)}")
        print("="*80)

        # Clean up checkpoint file
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
            print("Checkpoint file cleaned up")

        # Open the HTML file in the default browser
        print("\nOpening HTML file in browser...")
        webbrowser.open('file://' + os.path.abspath(output_file))
        print("[OK] HTML file opened in browser")
        print("\n" + "="*80)
        print("ANALYSIS COMPLETE!")
        print("="*80)

    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()

        # Save checkpoint on error
        checkpoint = {
            'completed': list(completed_tickers) if 'completed_tickers' in locals() else [],
            'charts_data': charts_data if 'charts_data' in locals() else []
        }
        save_checkpoint(checkpoint)
        print(f"\nProgress saved to {CHECKPOINT_FILE}")
        print("Run the script again to resume from this point")

        raise


if __name__ == "__main__":
    # Required for Windows multiprocessing
    mp.freeze_support()
    main()
