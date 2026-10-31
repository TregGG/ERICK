#!/usr/bin/env bash
# Keep Campus Rick running: started every minute by erick-watchdog.timer (as root, see
# install-watchdog.sh). Starts any stopped service, and restarts the database, the app or Caddy
# when health checks fail twice in a row (one failure is often just a restart in progress).
# Logs to the journal: journalctl -t erick-watchdog
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/common.sh"

PG_UNIT="${PG_UNIT:-postgresql@16-main}"
PUBLIC_HOST="${PUBLIC_HOST:-campusrick.centralindia.cloudapp.azure.com}"
STATE_DIR="${STATE_DIR:-/run/erick-watchdog}"
mkdir -p "$STATE_DIR"

log() { logger -t erick-watchdog "$*"; echo "$*"; }

# Leave everything alone while a deploy or mode switch is restarting the app. The lock file
# is opened read-only (no create) because it belongs to the deploy user.
if [[ -e $DEPLOY_LOCK ]] && exec 8<"$DEPLOY_LOCK" && ! flock -n 8; then
  exit 0
fi

# failed NAME: count consecutive failures; true once there have been two.
failed() {
  local n=$(( $(cat "$STATE_DIR/$1" 2>/dev/null || echo 0) + 1 ))
  echo "$n" >"$STATE_DIR/$1"
  (( n >= 2 ))
}
ok() { rm -f "$STATE_DIR/$1"; }

# 1. Services that are not running at all: start them.
for unit in "$PG_UNIT" "$SERVICE" caddy; do
  if ! systemctl is-active --quiet "$unit"; then
    log "$unit is not running; starting it"
    systemctl start "$unit" || log "could not start $unit"
  fi
done

# 2. App and database, through the app's own health check.
if health=$(curl -fsS -m 5 "$HEALTH_URL" 2>/dev/null); then
  ok app
  if [[ $health == *'"database":"ok"'* ]]; then
    ok database
  elif failed database; then
    log "app reports the database unreachable; restarting $PG_UNIT and $SERVICE"
    systemctl restart "$PG_UNIT"
    systemctl restart "$SERVICE"
    ok database
  fi
  # Without the dispatch loop no ride is ever offered to a driver (unless deliberately disabled).
  if [[ $health != *'"dispatch_loop":false'* ]] || grep -qiE '^DISPATCH_ENABLED=(false|0)' "$ENV_FILE" 2>/dev/null; then
    ok dispatch
  elif failed dispatch; then
    log "dispatch loop is not running; restarting $SERVICE"
    systemctl restart "$SERVICE"
    ok dispatch
  fi
elif failed app; then
  log "app is not answering on $HEALTH_URL; restarting $SERVICE"
  systemctl restart "$SERVICE"
  ok app
fi

# 3. HTTPS through Caddy, checked locally so it does not depend on outside DNS or routing.
if curl -fsS -m 5 --resolve "$PUBLIC_HOST:443:127.0.0.1" -o /dev/null "https://$PUBLIC_HOST/api/health" 2>/dev/null; then
  ok caddy
elif [[ -z ${health:-} ]]; then
  : # the app itself is down; step 2 handles it
elif failed caddy; then
  log "https://$PUBLIC_HOST is not answering; restarting caddy"
  systemctl restart caddy
  ok caddy
fi
