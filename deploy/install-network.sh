#!/bin/bash
set -euo pipefail
test "$(id -u)" = 0 || { echo 'Run with sudo.' >&2; exit 1; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install -m 644 "$root/jetson-network.service" /etc/systemd/system/jetson-network.service
install -m 644 "$root/jetson-network.timer" /etc/systemd/system/jetson-network.timer
systemctl daemon-reload
systemctl enable --now NetworkManager.service jetson-network.service jetson-network.timer
systemctl enable jetson-clock.service jetson-clock.timer jetson-budget.service jetson-bot.service \
  jetson-discord-watch.service jetson-project-hub.service jetson-backup.timer
echo 'Headless network recovery and agent boot services enabled.'
