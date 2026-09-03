#!/usr/bin/env bash
# Flood Alerts LK -- run the backend on a laptop. No Docker, no PostgreSQL.
#
#   ./run_local.sh              start (seeds the database on first run)
#   ./run_local.sh --reset      wipe the database and re-seed, then start
#   ./run_local.sh --no-seed    start against whatever is already there
#   ./run_local.sh --live       enable live ingestion from the Irrigation Dept
#
# macOS and Linux. Windows: use run_local.bat.

set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
RESET=0
SEED=1
LIVE=0

for arg in "$@"; do
  case "$arg" in
    --reset)   RESET=1 ;;
    --no-seed) SEED=0 ;;
    --live)    LIVE=1 ;;
    *) echo "unknown option: $arg"; exit 1 ;;
  esac
done

# --- python ----------------------------------------------------------------

PY=""
for candidate in python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "ERROR: Python 3.10 or newer is required and was not found."
  echo "Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi
echo "Using $($PY --version)"

# --- virtual environment ---------------------------------------------------

if [ ! -d .venv ]; then
  echo "Creating virtual environment (.venv)..."
  "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -f .venv/.installed ]; then
  echo "Installing dependencies (once, about a minute)..."
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements-local.txt
  touch .venv/.installed
fi

# --- configuration ---------------------------------------------------------
# Written into the environment rather than a .env file so that running this
# script never silently overwrites a config you edited by hand.

export DATABASE_URL="${DATABASE_URL:-sqlite:///./floodwatch.db}"
export JWT_SECRET="${JWT_SECRET:-local-demo-secret-not-for-deployment}"
export ADMIN_TOKEN="${ADMIN_TOKEN:-demo-admin}"
export ENVIRONMENT="${ENVIRONMENT:-development}"
export PORT="$PORT"

if [ "$LIVE" = "1" ]; then
  export INGEST_ENABLED=true
  echo "Live ingestion ON -- the server will poll the Irrigation Department every 10 minutes."
else
  # Off by default for a presentation: no dependence on the room's wifi, and no
  # error noise in the terminal you are projecting. The dashboard's "Pull
  # readings" button fetches live data on demand if the network is there.
  export INGEST_ENABLED=false
fi

# --- database --------------------------------------------------------------

DB_FILE="floodwatch.db"

if [ "$RESET" = "1" ]; then
  echo "Resetting the database..."
  rm -f "$DB_FILE"
fi

if [ "$SEED" = "1" ] && [ ! -f "$DB_FILE" ]; then
  echo "Seeding demo data (18 participants, 3 days of pings, one river in flood)..."
  python -m scripts.seed_demo --flood
fi

# --- go --------------------------------------------------------------------

echo
echo "Admin token: $ADMIN_TOKEN"
echo "Starting on port $PORT.  Press Ctrl-C to stop."

# 0.0.0.0 so a physical phone on the same wifi can reach it too. The emulator
# does not need this, but it costs nothing and saves you if you switch.
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
