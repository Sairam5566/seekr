@echo off
REM Dev mode: API on :8000, React on :5173 (open http://localhost:5173)
start "Seekr API" cmd /k "cd /d %~dp0backend && .venv\Scripts\python -m uvicorn main:app --reload --port 8000"
start "Seekr web" cmd /k "cd /d %~dp0frontend && npm run dev"
