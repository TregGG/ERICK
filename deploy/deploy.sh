#!/usr/bin/env bash
# Deploy the latest origin/master on the server: pull, install changed dependencies, migrate,
# restart and health-check. Rolls the code back if the new version does not come up healthy.
# Run by GitHub Actions over SSH (.github/workflows/deploy.yml), or by hand on the server.
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/common.sh"

# Everything lives in main() so bash has read the whole script before git replaces this file.
main() {
  exec 9>"$DEPLOY_LOCK"
  flock -n 9 || fail "another deploy is already running"
  cd "$APP_DIR"

  local old new
  old=$(git rev-parse HEAD)
  git fetch --quiet origin master
  new=$(git rev-parse origin/master)
  if [[ $old == "$new" ]]; then
    echo "Already at $(git log -1 --format='%h %s')"
  fi
  git reset --quiet --hard "$new"

  if ! git diff --quiet "$old" "$new" -- backend/requirements.txt; then
    echo "requirements.txt changed: installing dependencies..."
    ~/.local/bin/uv pip install --python "$PY" -r backend/requirements.txt
  fi

  "$PY" -m backend.manage migrate
  restart_service

  local health
  if ! health=$(wait_healthy); then
    echo "New version is not healthy. Recent log:" >&2
    sudo journalctl -u "$SERVICE" -n 30 --no-pager >&2 || true
    echo "Rolling back code to $(git log -1 --format=%h "$old")..." >&2
    git reset --quiet --hard "$old"
    restart_service
    wait_healthy >/dev/null || echo "WARNING: previous version is not healthy either" >&2
    echo "Note: database migrations from the failed version were NOT rolled back." >&2
    exit 1
  fi

  echo "Deployed $(git log -1 --format='%h %s')"
  echo "Health: $health"
}

main "$@"
exit
