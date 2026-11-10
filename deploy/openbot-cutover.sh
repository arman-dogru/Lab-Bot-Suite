#!/bin/bash
# Stop the Hermes Discord units. Budget, clock, and the Hermes files stay.
set -euo pipefail
systemctl stop jetson-bot.service jetson-discord-watch.service
systemctl is-active jetson-budget.service
systemctl is-active jetson-clock.service
test -f /opt/jetson-bot/deploy/run-hermes.sh
test -f /opt/jetson-bot/deploy/soul.md
