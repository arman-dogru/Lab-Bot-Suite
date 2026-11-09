#!/bin/bash
# Open the pinned Hermes CLI inside the running agent container.
# One container and one Hermes home; this does not start a second copy.
set -euo pipefail
if ! sudo docker inspect -f '{{.State.Running}}' jetson-hermes 2>/dev/null | grep -q true; then
  echo 'The Jetson agent is not running. Start it, then open Chat again.' >&2
  exit 1
fi
uid=$(id -u jetsonbot)
gid=$(id -g jetsonbot)
bin=$(sudo docker exec --user "$uid:$gid" jetson-hermes sh -c 'command -v hermes || command -v /opt/hermes/.venv/bin/hermes' || true)
if [ -z "$bin" ]; then
  echo 'The pinned Hermes image has no chat CLI on PATH.' >&2
  exit 1
fi
exec sudo docker exec -it --user "$uid:$gid" -w /workspace \
  -e HERMES_HOME=/opt/data \
  -e TERM="${TERM:-xterm-256color}" \
  jetson-hermes "$bin" chat
