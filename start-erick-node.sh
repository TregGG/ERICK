#!/usr/bin/env bash
# Deprecated legacy Node host (see server.mjs header); does not implement the current API.
cd "$(dirname "$(readlink -f "$0")")"
ERICK_PORT=8091 exec node server.mjs
