# Windows workspace

This project continues the existing Jetson deployment. Its source was recovered
from `/opt/jetson-bot` on November 2, 2026; Project Hub source was recovered from
`/opt/project-hub` into `artifacts/project-hub-source`.
The deployment had no `.git` directory, and the handover contained no project
remote URL. This workspace has a new local `main` repository with no remote and
no commits. Original Git history has not been recovered. The user confirmed
there is no remote repository to pull; the recovered files are the local source.

Runtime credentials, chat history, app databases, uploads and private backups
remain on the Jetson. The imported tree was checked for provider tokens,
Discord tokens and private SSH keys. Its original file hashes are recorded in
the ignored `.local/source-manifest.json`.

## Development environment

The prepared interpreter is `D:\Projects\Jetson\.venv\Scripts\python.exe`
(Python 3.12). Recreate it from PowerShell in the project folder:

```powershell
.\deploy\prepare-windows.ps1
```

Support-service code uses the standard library. Windows additionally requires
`tzdata` for the Toronto budget calendar. Hermes itself runs in its existing
pinned ARM64 Linux container on the Jetson; installing it on Windows is not
required for this workspace.

## Connect and operate

USB SSH is `armanberkay@192.168.55.1`. The helper checks the public SSH host key
saved in `deploy/ssh_known_hosts`. SSH and sudo prompt interactively for the
Jetson login password; the helper does not store it.

```powershell
.\deploy\jetson.ps1 Status
.\deploy\jetson.ps1 Budget
.\deploy\jetson.ps1 Start
.\deploy\jetson.ps1 Diagnostics
.\deploy\jetson.ps1 Test
.\deploy\jetson.ps1 Tunnel
```

The tunnel exposes Project Hub only at `http://127.0.0.1:8787/`. Leave its terminal
open. A tunnel was also opened during this handover; stop that process before
opening another tunnel on the same port. Its PID is in the ignored
`.local/hub-tunnel.json`. Closing a viewing tunnel does not stop the Jetson.
For Wi-Fi SSH, add `-JetsonHost <Jetson-Wi-Fi-IP>`.

## Verification on November 2

- All 65 support checks passed in the existing pinned image on the Jetson,
  without network access or model calls.
- The recovered Project Hub integration suite passed on Windows:
  `.\.venv\Scripts\python.exe artifacts\project-hub-source\test_hub.py`.
- The live Project Hub returned HTTP 200 through the private USB SSH tunnel.
- Windows support checks have two platform incompatibilities: Linux mode-0600
  assertions and replacement of a mode-0600 file. Use the Jetson's Linux test
  command above for authoritative validation; the checks were not weakened.
- The recovered deployment services and backup/clock timers remain enabled.

## Startup network requirement

The Jetson boots without a desktop login. Discord and the paid-model gateway
need working internet. At the initial inspection, Wi-Fi was disconnected and
the clock-recovery service could not verify the current date. The paid gateway
correctly waited for `/run/jetson-clock-verified`.

The user chose Wi-Fi for connectivity. The saved `uOttawa-G` profile connected
at `10.206.5.149`, and automatic reconnection is enabled. The clock then recovered
through the existing independent HTTPS checks, and the budget gateway became
ready with the November 2 allowance of US$20 and no paid requests used.
USB internet sharing is not enabled. The catch-up backup completed successfully
at 10:38 Toronto time, saving the root-private
`/var/backups/jetson-bot/20261102-103552.tar.gz` archive (707 MiB). No restore test
was performed on this new archive.

Final startup verification at 10:40 Toronto time confirmed:

- A fresh Discord login as `Jetson-Bot-Lab#4166` at 10:40:12 and a running gateway.
- Bot, budget, status/activity watcher, Project Hub and both timers active and
  enabled; all three containers running without an OOM flag.
- Budget health `ok=true`, `configured=true`; Grok credentials and configured
  model availability verified by a free model-list request.
- US$0 used today, US$20 available, no outstanding reservations, operator resumed.
- Project Hub health `ok=true` and the private viewing tunnel still available.

The original owner access rules and US$20/day budget remain in effect. No paid
inference or new Discord task was used for this startup check.

The later networking continuation fixed a desktop-user-only Wi-Fi profile and
added autonomous recovery. A real headless reboot passed at 11:50, with automatic
Wi-Fi and Discord reconnection and all 73 support tests passing. Use
`Connect-Jetson.cmd` for laptop networking; see [Networking](NETWORKING.md).
