#!/bin/bash
set -euo pipefail
source /etc/jetson-bot/deployment.env
task_uid=$(id -u jetsonbot)
task_gid=$(id -g jetsonbot)
task_cpus=$(nproc)
if [ "$task_cpus" -gt 3 ]; then task_cpus=3; fi
for task_attempt in {1..30}; do
  if curl -fsS --max-time 2 http://127.0.0.1:8870/health >/dev/null; then break; fi
  if [ "$task_attempt" = 30 ]; then echo 'Budget gateway did not become ready.' >&2; exit 1; fi
  sleep 1
done
exec docker run --rm --name jetson-hermes --network jetson-bot \
  --memory 2500m --memory-swap 3500m --cpus "$task_cpus" \
  --log-opt max-size=10m --log-opt max-file=3 \
  --env-file /srv/jetson-bot/state/runtime.env \
  -e HERMES_UID="$task_uid" -e HERMES_GID="$task_gid" \
  -e API_SERVER_ENABLED=false -e HERMES_DASHBOARD=0 \
  -e HERMES_WRITE_SAFE_ROOT=/opt/data:/workspace \
  -e HERMES_TIMEZONE=America/Toronto -e TZ=America/Toronto \
  -v /srv/jetson-bot/state:/opt/data \
  -v /srv/jetson-bot/workspace:/workspace \
  -v /opt/jetson-bot:/opt/jetson-bot:ro \
  -w /workspace "$HERMES_IMAGE" gateway run
