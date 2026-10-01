#!/usr/bin/env sh
# One-time setup: Python virtualenv + backend deps, and frontend npm packages.
set -e
cd "$(dirname "$0")/backend"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
cd ../frontend
npm install
echo "Setup done. Run ./start.sh to launch Seekr."
