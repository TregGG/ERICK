#!/usr/bin/env bash
# One-time setup on the server (safe to run again): sudo /opt/erick/deploy/install-watchdog.sh
# - systemd restarts PostgreSQL, Caddy and the app whenever they crash, and never gives up;
# - deploy/watchdog.sh runs every minute to catch hangs and anything systemd misses.
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/common.sh"
PG_UNIT="${PG_UNIT:-postgresql@16-main}"
[[ $EUID -eq 0 ]] || fail "run with sudo"

for unit in "$PG_UNIT" caddy "$SERVICE"; do
  mkdir -p "/etc/systemd/system/$unit.service.d"
  cat >"/etc/systemd/system/$unit.service.d/restart.conf" <<'EOF'
# Added by Campus Rick deploy/install-watchdog.sh
[Unit]
StartLimitIntervalSec=0

[Service]
Restart=always
RestartSec=5
EOF
done

cat >/etc/systemd/system/erick-watchdog.service <<EOF
[Unit]
Description=Campus Rick watchdog (restarts stopped or unhealthy services)

[Service]
Type=oneshot
ExecStart=$APP_DIR/deploy/watchdog.sh
EOF

cat >/etc/systemd/system/erick-watchdog.timer <<'EOF'
[Unit]
Description=Run the Campus Rick watchdog every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now erick-watchdog.timer
systemctl start erick-watchdog.service
echo "Watchdog installed. Next run: $(systemctl list-timers erick-watchdog.timer --no-legend | awk '{print $1, $2, $3}')"
echo "Its log: journalctl -t erick-watchdog"
