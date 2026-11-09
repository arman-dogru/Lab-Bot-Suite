# Persistent Hermes web search

DDGS is installed through Hermes's native package manager. Its environment lives
under `/opt/data/installs`, which is persistent across container replacement.
The gateway selects that environment automatically. A plain `docker exec ...
python` instead uses the image's base environment and may not find DDGS.

To reproduce the extra after initializing a replacement container:

```sh
sudo docker exec --user 1001:1002 -w /opt/hermes jetson-hermes \
  /opt/hermes/.venv/bin/python -m pm.cli install --extra ddgs
sudo systemctl restart jetson-bot
```

Wait for the Discord connection before sending requests. The working dependency
freeze is `/workspace/requirements-hermes-runtime.lock.txt` (149 packages when
recorded on October 30). The base image digest is recorded in
`/etc/jetson-bot/deployment.env`. Preserve the state backup before upgrades.

Verified after container restart: the selected runtime imported DDGS and returned
two search results, including NVIDIA's Jetson Nano guide. Search and text-based
page access remain subject to the target website's availability.
