#!/bin/bash
set -euo pipefail
if [ "$(id -u)" != 0 ]; then echo 'Run with sudo.' >&2; exit 1; fi
cd /opt/jetson-bot
IMAGE='nousresearch/hermes-agent@sha256:41f1cfdd2486b38ca55453a28785910b498ee184e71eed4f2d7ee3f908aa7236'
id jetsonbot >/dev/null 2>&1 || useradd --create-home --shell /bin/bash jetsonbot
printf '%s\n' 'jetsonbot ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/jetsonbot
chmod 440 /etc/sudoers.d/jetsonbot
visudo -cf /etc/sudoers.d/jetsonbot
install -d -m 700 /etc/jetson-bot /var/lib/jetson-bot/budget /var/backups/jetson-bot
install -d -o jetsonbot -g jetsonbot /srv/jetson-bot/state /srv/jetson-bot/workspace
install -d -m 700 -o jetsonbot -g jetsonbot /srv/jetson-bot/state/home/.ssh
# OpenSSH reads the passwd home (/opt/data), rather than the shell's HOME.
if [ ! -e /srv/jetson-bot/state/.ssh ]; then
  ln -s home/.ssh /srv/jetson-bot/state/.ssh
  chown -h jetsonbot:jetsonbot /srv/jetson-bot/state/.ssh
fi
install -d -o jetsonbot -g jetsonbot /srv/jetson-bot/state/plugins/jetson-controls
cp deploy/control_plugin/*.py deploy/control_plugin/plugin.yaml /srv/jetson-bot/state/plugins/jetson-controls/
cp deploy/soul.md /srv/jetson-bot/state/SOUL.md
if ! test -f /etc/jetson-bot/deployment.env; then
  printf 'HERMES_IMAGE=%s\n' "$IMAGE" > /etc/jetson-bot/deployment.env
fi
cp deploy/discord.json /etc/jetson-bot/discord.json
cp deploy/discord.json /srv/jetson-bot/state/discord-channels.json
python3 deploy/prepare_config.py
if ! docker network inspect jetson-bot >/dev/null 2>&1; then
  docker network create --subnet 172.30.87.0/24 --gateway 172.30.87.1 jetson-bot
fi
task_key=/srv/jetson-bot/state/home/.ssh/jetson-host
if ! test -f "$task_key"; then
  ssh-keygen -q -t ed25519 -N '' -C jetson-bot-host-control -f "$task_key"
fi
install -d -m 700 -o jetsonbot -g jetsonbot /home/jetsonbot/.ssh
touch /home/jetsonbot/.ssh/authorized_keys
task_public_key=$(cat "$task_key.pub")
grep -qxF "$task_public_key" /home/jetsonbot/.ssh/authorized_keys || printf '%s\n' "$task_public_key" >> /home/jetsonbot/.ssh/authorized_keys
task_host_public=$(awk '{print $1" "$2}' /etc/ssh/ssh_host_ed25519_key.pub)
printf '172.30.87.1 %s\n' "$task_host_public" > /srv/jetson-bot/state/home/.ssh/known_hosts
cat > /srv/jetson-bot/state/home/.ssh/config <<'SSH'
Host jetson-host
  HostName 172.30.87.1
  User jetsonbot
  IdentityFile /opt/data/home/.ssh/jetson-host
  UserKnownHostsFile /opt/data/home/.ssh/known_hosts
  StrictHostKeyChecking yes
  BatchMode yes
SSH
chmod 600 /srv/jetson-bot/state/home/.ssh/* /home/jetsonbot/.ssh/authorized_keys
chown -R jetsonbot:jetsonbot /srv/jetson-bot/state /srv/jetson-bot/workspace /home/jetsonbot/.ssh
chmod +x deploy/run-*.sh deploy/jetsonctl.py deploy/clock_recover.py
ln -sfn /opt/jetson-bot/deploy/jetsonctl.py /usr/local/bin/jetsonctl
cp deploy/jetson-*.service deploy/jetson-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable jetson-clock.service jetson-clock.timer jetson-backup.timer jetson-budget.service jetson-bot.service jetson-discord-watch.service
systemctl start jetson-clock.service
systemctl start jetson-clock.timer jetson-backup.timer jetson-budget.service
docker run --rm --network jetson-bot --user "$(id -u jetsonbot):$(id -g jetsonbot)" \
  -v /opt/jetson-bot:/app:ro -v /srv/jetson-bot/state:/opt/data \
  -v /srv/jetson-bot/workspace:/workspace --entrypoint /bin/bash \
  "$IMAGE" /app/deploy/provision-documents.sh
echo 'Base services installed. Run sudo jetsonctl configure to enter credentials and activate Discord.'
