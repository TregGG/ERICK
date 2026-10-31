#!/usr/bin/env bash
# Kept for compatibility: starts the FastAPI backend (the old backend/dev_server.py was removed).
exec "$(dirname "$(readlink -f "$0")")/start-erick.sh" "$@"
