#!/usr/bin/env sh
# Dev mode: API on :8000, React on :5173 (open http://localhost:5173)
cd "$(dirname "$0")"
(cd backend && .venv/bin/python -m uvicorn main:app --reload --port 8000) &
API_PID=$!
trap 'kill $API_PID' EXIT
cd frontend && npm run dev
