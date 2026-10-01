@echo off
REM One-time setup: Python virtualenv + backend deps, and frontend npm packages.
cd /d "%~dp0backend"
python -m venv .venv || goto :error
call .venv\Scripts\python -m pip install -r requirements.txt || goto :error
if not exist .env copy .env.example .env >nul
cd /d "%~dp0frontend"
call npm install || goto :error
echo.
echo Setup done. Run start.bat to launch Seekr.
goto :eof
:error
echo Setup failed. Check that Python 3.10+ and Node 18+ are installed.
exit /b 1
