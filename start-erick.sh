#!/usr/bin/env bash
# Campus Rick: one-step local start on Linux.
# Starts PostGIS (Docker), applies migrations, loads campus data and demo accounts, then runs the server.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

fail() { echo "Startup failed: $*" >&2; exit 1; }

PY=.venv313/bin/python
if [ ! -x "$PY" ]; then
  if command -v python3.13 >/dev/null; then
    BASE_PY=python3.13
  else
    BASE_PY=python3
    echo "python3.13 not found; using $("$BASE_PY" --version). The project targets Python 3.13."
  fi
  echo "Creating Python virtual environment..."
  "$BASE_PY" -m venv .venv313 || fail "could not create .venv313"
  "$PY" -m pip install -r backend/requirements.txt || { rm -rf .venv313; fail "pip install failed"; }
fi

docker info >/dev/null 2>&1 || fail "Docker daemon is not running. Start it: sudo systemctl enable --now docker.socket docker.service"

export ERICK_PGDATA="${ERICK_PGDATA:-$HOME/.local/share/erick/pgdata}"
mkdir -p "$ERICK_PGDATA"
docker compose -f docker-compose.yml -f docker/compose.linux.yml up -d --wait database \
  || fail "PostGIS could not start. See: docker logs erick-postgis. Or set DATABASE_URL to SQLite in backend/.env."

"$PY" -m backend.manage bootstrap-dev || fail "bootstrap-dev failed"

echo
echo "  Passenger: http://127.0.0.1:8091/passenger/"
echo "  Driver:    http://127.0.0.1:8091/driver/"
echo "  Admin:     http://127.0.0.1:8091/admin/"
echo
exec "$PY" run-local.py
