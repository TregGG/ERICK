#!/usr/bin/env bash
# Switch the server between production (real SMS login via Twilio Verify) and development
# (login code shown on screen). Usage: erick-mode production | development | status
# Changes only the mode keys in backend/.env; restores the previous file if the server
# rejects the new settings or does not come up healthy.
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/common.sh"

usage() { echo "Usage: erick-mode production | development | status" >&2; exit 2; }

get_key() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//; s/[[:space:]]*$//'; }

# Replace KEY=... in .env (dropping any inline comment), or append it if missing.
set_key() {
  if grep -qE "^$1=" "$ENV_FILE"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
  else
    echo "$1=$2" >>"$ENV_FILE"
  fi
}

mask() { local v="$1"; [[ -z $v ]] && echo "(not set)" || echo "${v:0:4}…(${#v} chars)"; }

status() {
  echo "APP_ENV=$(get_key APP_ENV)"
  echo "OTP_DEV_MODE=$(get_key OTP_DEV_MODE)"
  echo "SMS_PROVIDER=$(get_key SMS_PROVIDER)"
  echo "TWILIO_ACCOUNT_SID=$(mask "$(get_key TWILIO_ACCOUNT_SID)")"
  echo "TWILIO_AUTH_TOKEN=$(mask "$(get_key TWILIO_AUTH_TOKEN)")"
  echo "TWILIO_VERIFY_SERVICE_SID=$(mask "$(get_key TWILIO_VERIFY_SERVICE_SID)")"
  echo "Running server: $(curl -fsS -m 3 "$HEALTH_URL" 2>/dev/null || echo 'not reachable')"
}

restore() {
  echo "Restoring previous settings..." >&2
  cp "$ENV_FILE.bak" "$ENV_FILE"
  restart_service
  wait_healthy >/dev/null || echo "WARNING: server is not healthy with the previous settings either" >&2
  exit 1
}

switch() {
  exec 9>"$DEPLOY_LOCK"
  flock -n 9 || fail "a deploy is running; try again in a minute"
  cp "$ENV_FILE" "$ENV_FILE.bak"
  if [[ $1 == production ]]; then
    set_key APP_ENV production
    set_key OTP_DEV_MODE false
    set_key SMS_PROVIDER twilio_verify
    set_key SIMULATION_ENABLED false
  else
    set_key APP_ENV development
    set_key OTP_DEV_MODE true
    set_key SMS_PROVIDER console
  fi

  # The backend's own startup guard: reports missing Twilio values, weak JWT_SECRET, etc.
  if ! (cd "$APP_DIR" && "$PY" -c "from backend.config import settings; settings.validate_for_runtime()"); then
    echo "The new settings were rejected (see above). Fix backend/.env and try again." >&2
    cp "$ENV_FILE.bak" "$ENV_FILE"
    exit 1
  fi

  restart_service
  local health
  health=$(wait_healthy) || { echo "Server did not come up healthy." >&2; restore; }
  echo "Now in $1 mode. Health: $health"

  if [[ $1 == production ]]; then
    if [[ $(get_key ROUTING_BASE_URL) == *router.project-osrm.org* ]]; then
      echo "Note: routing still uses the public OSRM demo server (rate limited, no SLA)."
    fi
  else
    echo "WARNING: development mode shows login codes on screen. Anyone can sign in as any phone number."
  fi
}

[[ -f $ENV_FILE ]] || fail "$ENV_FILE not found"
case "${1:-}" in
  production | development) switch "$1" ;;
  status) status ;;
  *) usage ;;
esac
