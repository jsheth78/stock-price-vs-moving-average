@echo off
REM S&P 500 Stock Analysis Runner
REM Double-click this file to run the analysis

echo ================================================================================
echo S^&P 500 Stock Price vs Moving Average Analysis
echo ================================================================================
echo.
echo Starting analysis...
echo.
echo The program will:
echo   1. Connect to Bloomberg Terminal
echo   2. Fetch S^&P 500 Index data
echo   3. Calculate dynamic moving averages
echo   4. Generate interactive HTML charts
echo   5. Automatically open the results in your browser
echo.
REM Change to the script directory
cd /d "%~dp0"

REM Run the Python script
python sp500_charts_generator_optimized.py

REM Check if there was an error
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ================================================================================
    echo ERROR: The analysis encountered an error
    echo ================================================================================
    echo.
    echo Please check the error messages above for details.
)


