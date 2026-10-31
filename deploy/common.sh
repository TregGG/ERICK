# Shared settings and helpers for deploy.sh and erick-mode.sh (sourced, not run directly).
# Defaults match docs/DEPLOY_AZURE.md; override with environment variables if the server differs.
APP_DIR="${APP_DIR:-/opt/erick}"
SERVICE="${ERICK_SERVICE:-erick}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8091/api/health}"
PY="$APP_DIR/.venv/bin/python"
ENV_FILE="$APP_DIR/backend/.env"

fail() { echo "ERROR: $*" >&2; exit 1; }

restart_service() { sudo systemctl restart "$SERVICE"; }

# Wait up to 30 s for /api/health to report status ok. Prints the response on success.
wait_healthy() {
  local body
  for _ in $(seq 30); do
    if body=$(curl -fsS -m 3 "$HEALTH_URL" 2>/dev/null) && [[ $body == *'"status":"ok"'* ]]; then
      echo "$body"
      return 0
    fi
    sleep 1
  done
  return 1
}
