@echo off
REM setup_windows.bat — Stock Screener 2.0 setup for Windows
REM Usage: Double-click or run from Command Prompt

echo ==============================================
echo   Stock Screener 2.0 — Windows Setup
echo ==============================================

REM ── Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo X Python not found.
    echo   Download from: https://www.python.org/downloads/
    echo   Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo + Python found
python --version

REM ── Create virtual environment ────────────────────────────────────────────
IF NOT EXIST "venv" (
    echo - Creating virtual environment...
    python -m venv venv
    echo + Virtual environment created
) ELSE (
    echo + Virtual environment already exists
)

REM ── Activate and install ──────────────────────────────────────────────────
call venv\Scripts\activate.bat

echo - Installing dependencies (this may take a few minutes)...
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo + Dependencies installed

REM ── Initialize database ───────────────────────────────────────────────────
echo - Initializing database...
python run.py init
echo + Database initialized

REM ── Create logs directory ─────────────────────────────────────────────────
IF NOT EXIST "logs" mkdir logs

echo.
echo ==============================================
echo   Setup complete!
echo ==============================================
echo.
echo   Next steps:
echo   1. Add your NewsAPI key in config.py (optional)
echo   2. Add tickers:  python run.py add AAPL MSFT SPY
echo   3. Fetch data:   python run.py nightly
echo   4. Launch app:   python -m streamlit run dashboard.py
echo   5. Open:         http://localhost:8501
echo.
pause
