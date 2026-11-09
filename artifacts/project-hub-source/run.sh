#!/bin/sh
# Launch Project Hub without the Hermes image entrypoint.
# Accept either PROJECT_HUB_* (app) or the shorter HUB_* aliases.
set -eu
cd "$(dirname "$0")"
export PROJECT_HUB_HOST="${PROJECT_HUB_HOST:-${HUB_HOST:-127.0.0.1}}"
export PROJECT_HUB_PORT="${PROJECT_HUB_PORT:-${HUB_PORT:-8787}}"
export PROJECT_HUB_DATA="${PROJECT_HUB_DATA:-${HUB_DATA:-$(pwd)/data}}"
if [ -n "${HUB_SECRET:-}" ] && [ -z "${PROJECT_HUB_SECRET:-}" ]; then
    export PROJECT_HUB_SECRET="$HUB_SECRET"
fi
exec python3 app.py
