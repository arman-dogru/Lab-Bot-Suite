#!/bin/bash
set -euo pipefail
source /etc/jetson-bot/deployment.env
if ! test -s /run/jetson-clock-verified; then
  echo 'Waiting for verified boot time before enabling paid requests.'
  until test -s /run/jetson-clock-verified; do sleep 2; done
fi
exec docker run --rm --name jetson-budget --network jetson-bot \
  --memory 256m --cpus 1 --log-opt max-size=5m --log-opt max-file=3 \
  -p 127.0.0.1:8870:8870 \
  -v /opt/jetson-bot:/app:ro -v /etc/jetson-bot:/etc/jetson-bot:ro \
  -v /var/lib/jetson-bot/budget:/budget \
  -w /app --entrypoint /opt/hermes/.venv/bin/python \
  "$HERMES_IMAGE" -m jetson_bot.budget
