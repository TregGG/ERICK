#!/usr/bin/env bash
# Opens the passenger app in a separate, proxy-free browser profile (Linux counterpart of open-erick-edge.cmd).
URL="http://127.0.0.1:8091/passenger/"
PROFILE="${TMPDIR:-/tmp}/erick-local-browser"
for b in microsoft-edge microsoft-edge-stable google-chrome google-chrome-stable chromium brave; do
  if command -v "$b" >/dev/null; then
    "$b" --no-proxy-server --user-data-dir="$PROFILE" "$URL" >/dev/null 2>&1 &
    exit 0
  fi
done
xdg-open "$URL"
