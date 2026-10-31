#!/usr/bin/env bash
# Kept for compatibility: starts the FastAPI backend.
exec "$(dirname "$(readlink -f "$0")")/start-erick.sh" "$@"
