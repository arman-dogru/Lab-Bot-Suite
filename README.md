# Jetson Bot

An owner-only Discord assistant running on a dedicated Jetson Nano. Hermes runs
in its official pinned ARM64 image; model requests pass through a local budget
gateway before reaching xAI.

Start with the [operating guide](OPERATING_GUIDE.md). The bot-built
[Project Hub](http://127.0.0.1:8787/) is available through the private SSH tunnel.
See [acceptance evidence and practical limits](ACCEPTANCE.md).

For the recovered Windows workspace, source provenance, development setup and
current connectivity status, see [Windows setup](docs/WINDOWS_SETUP.md).
For plug-in startup, saved Wi-Fi and the laptop connection helper, see
[networking](docs/NETWORKING.md).

## Current setup

- Host OS and NVIDIA drivers preserved.
- Headless boot, automatic Wi-Fi, sleep prevention, and independent HTTPS clock recovery.
- Persistent projects, documents, skills, memory, and tool environments.
- US$20 per Toronto calendar day, with durable request reservations and actual billed-cost settlement.
- Local status/pause/resume controls that do not call a model.
- Daily private backups, with isolated configuration/ledger restoration verification.
- Dedicated account `jetsonbot`, passwordless host administration, and pinned SSH host keys.
- Document tools in a persistent environment; complete dependency freeze stored on the Jetson.

Live Discord/Grok operation is active. Coding, document attachments, saved skills,
memory, compaction, goals, schedules, headless reboot, automatic budget recovery,
backup restoration, and a replacement-image rollback have passed live checks.
See [PROGRESS.md](PROGRESS.md) for the last verified state and remaining work.

## Credentials and onboarding

Onboarding is complete. Use the following only when replacing credentials.

With the USB connection attached, run this from the Windows project folder:

```powershell
.\deploy\jetson.ps1 Configure
```

The script connects to the Jetson at `192.168.55.1` over USB using its saved host
identity. Add `-JetsonHost <address>` to use a different address. The public Discord IDs are
already saved; setup prompts for the bot token and Grok API key with hidden input.
Credentials are validated without paid inference.
The token is saved with Hermes; the real Grok key is saved only with the budget
service. Do not put either credential into this repository or a Discord message.

## Discord channels

- **#jetson-bot**: your requests, task threads, finished answers, and files.
- **#jetson-activity**: one task card, edited with observed tool operations, main model calls, output-token counts, and elapsed time. No prompts, command arguments, outputs, narration, or private reasoning are forwarded. Helper-model spending is included in the budget panel.
- **#jetson-status**: one changing service/budget dashboard, plus local controls and service notices. Ordinary text here is handled without a model call.

Tool previews, routine model narration, reasoning display, and memory-change notices are disabled in chat. Activity and status cards suppress notifications and edit in place; updates are coalesced and Discord rate limits are respected. There is no model heartbeat cost for either panel.

Automatic goal work cycles share one activity card. When the native goal checker
requests another cycle, its routine reply remains in the saved task transcript.
The final answer, failures, and requests for input remain visible. Completion
notices go to status. Repeated missing verification evidence pauses the goal
after three continuation attempts. The pinned-release delivery shim must be
checked again when upgrading Hermes.

## Private Project Hub

Open [Project Hub](http://127.0.0.1:8787/) on this PC while the SSH tunnel is open.
The site runs on the Jetson and provides projects, task boards with priorities and
due dates, Markdown notes with revision history, document uploads/downloads,
search, JSON/Markdown exports, and an authenticated local task webhook.

To reopen the private connection later, run in PowerShell and leave it running:

```powershell
.\deploy\jetson.ps1 Tunnel
```

Enter your Jetson login password at SSH's hidden prompt. Replace the address with
the Jetson's Wi-Fi address when USB networking is unavailable. No router forwarding
is needed. Discord continues working when this viewing tunnel is closed.

The independent `jetson-project-hub` service uses the tested pinned image, runs
as `project-hub`, and listens only on host `127.0.0.1:8787`. Live code is
`/opt/project-hub`; the database/uploads are `/var/lib/project-hub/data`. Source
and its test suite are `/srv/jetson-bot/workspace/project-hub`. The private app-only
webhook token is `/var/lib/project-hub/secrets/webhook.token`; it is separate from
bot/provider credentials. Ask the bot to use it locally without printing it.

The normal browser forms, task movement, note revisions, search, archive/reopen,
exports, file bytes, webhook authentication and restart persistence were checked.
The automated browser file picker needs the extension's file-URL permission;
the deployed upload/download service was verified directly with browser-style HTTP.

## Use it

Message the bot in its configured channel or DM it. Use separate task threads
for separate projects. Example requests:

- "Create and test a Python script that organizes these files."
- "Read this PDF and create a Word report with the findings."
- "Install the tools needed for this project and save a reusable skill."
- "Work toward this goal until the tests pass."

Use `/goal` for a persistent objective and `/stop` to interrupt current work.
For a new objective, use `/goal -- Build and test ...`; the `--` prevents words
such as "continue" from being interpreted as control commands. Use `/goal status`,
`/goal pause`, and `/goal resume` to inspect or control that objective.
To schedule work in Discord, ask in plain language, for example "Every weekday at
9 AM Toronto time, check this project and notify me only if something changed."
The pinned release exposes `/cron` subcommands in the terminal; Discord scheduling
uses the scheduling tool through a normal request. `/jetson status`, `/jetson budget`, `/jetson pause`, and
`/jetson resume` use local control logic and remain available without model credit.
`/jetson jobs` lists saved work waiting for budget or blocked recovery.
Script-only schedules use `/opt/data/scripts`; project outputs stay in `/workspace`.

## Host controls

Run these on the Jetson using SSH:

```sh
sudo jetsonctl status
sudo jetsonctl budget
sudo jetsonctl pause
sudo jetsonctl resume
sudo jetsonctl stop
sudo jetsonctl start
sudo jetsonctl backup
```

Pause/resume controls the paid model gateway. Stop/start controls the Discord
agent service. Existing requests cannot be made unbilled by pausing them.
Requests interrupted by the spending limit are saved and retried when sufficient
budget returns. `/stop` cancels queued recovery for that conversation. Explicitly
paused goals remain paused. Repeated recovery failures are reported as blockers.
Fresh, confirmed interrupted goals also resume after a service restart, with
owner checks and a three-interruption breaker. Explicit stops, paused goals,
unknown execution outcomes and Hermes's restart-loop guard remain authoritative.
Native goal waits also have durable wakeups. If history compression fails and
Hermes resets a conversation, an active goal can resume from saved project files;
three repeated resets pause that goal for inspection. Explicit stops and pauses
take precedence. Chat and scheduled work share Hermes's native execution-capacity
registry, so a scheduled worker waits without model calls while the slot is busy.

Projects and output files are in `/srv/jetson-bot/workspace` on the host and
`/workspace` inside the agent. Hermes state is `/srv/jetson-bot/state` on the host
and `/opt/data` inside the agent. Run document scripts with
`/opt/data/home/envs/documents/bin/python` inside the agent container.

## Budget behavior

Before forwarding a request, the gateway reserves a conservative uncached input
bound plus the permitted maximum output cost. It refunds the difference only
after xAI reports the actual bill. Timeouts and missing usage retain reservations.
Retries are separate physical requests and need their own admission. Unknown
models, server-hosted paid tools, streaming, and unpriced request fields are
rejected in v1. The output cap is 8,192 tokens.

The ledger and operator pause survive restarts. Daily allowance changes at
America/Toronto midnight, including DST. A provider bill above its reservation
pauses all further paid requests for operator review. Full host administration
means the agent could deliberately alter its own operational safeguards; its
instructions prohibit bypassing the gateway or obtaining the provider key.

Long coding sessions use Hermes's supported `legacy` retained-history mode with
a 3,200-token recent-history budget and automatic summaries at 32,000 tokens. This avoids the
default 10,000-token minimum tail competing with the bounded request size.
Project milestones should also be saved in files so work can resume after a reset.

## Recovery and updates

Services are `jetson-budget`, `jetson-bot`, and `jetson-clock`. Inspect their state
with `sudo systemctl status NAME`; inspect logs with `sudo journalctl -u NAME`.
The bot remains inactive until `/etc/jetson-bot/ready` exists after onboarding.

Backups are kept in `/var/backups/jetson-bot` with root-only access. They include
credentials and should remain private. Seven daily archives are retained. Test a
backup with `sudo python3 /opt/jetson-bot/deploy/verify_backup.py`.
Backups stop the bot, activity watcher and running Project Hub for consistency,
then restart only the services that were running. They include deployed site code,
data/uploads, its private token, service definitions and a consistent budget ledger.
Restore verification uses an isolated directory and checks database integrity and
uploaded-file hashes. These archives preserve the application setup and state;
they are not a disk image of NVIDIA's operating system.

For an image upgrade, take a backup, stop the agent, record the current digest in
`/etc/jetson-bot/deployment.env`, and smoke-test the replacement official ARM64
image before changing the pin. Recreate services from the same persistent mounts.
If validation fails, restore the prior digest and its pre-upgrade configuration
before restarting. Do not run two gateway containers on the same state directory.
Replacement and rollback were tested with a distinct image built from the same
pinned release. A future Hermes version still needs its own migration checks.

The clock is verified before the paid gateway starts. If networking is unavailable
at boot, time recovery retries automatically. Wi-Fi reconnects without a desktop
login. Powering on with the configured Wi-Fi available starts the bot automatically.
The monitor may sleep and can be disconnected. The current Mac USB power arrangement
depends on the Mac remaining powered and supplying adequate current; reliable
independent operation needs the correct dedicated 5 V supply for this Nano board.

## Verification

Local budget tests:

```sh
python3 -m unittest discover -s tests -v
```

On-device document/host-control checks are in `deploy/offline_checks.py`. Run them
through the normal image entrypoint with `HERMES_UID`/`HERMES_GID` set so the
container's runtime user is correctly remapped. Bypassing initialization with an
arbitrary numeric user makes OpenSSH unable to resolve that user.

No paid model call is required for the offline checks. Live coding, attachments,
memory/skill persistence, compaction and goal completion have passed. Native owner
admission was checked with simulated unauthorized events. A scheduled task ran
after reboot. Both ordinary requests and scheduled tasks resumed after a budget
pause; cancellation prevented a queued task from running. The project-hub build
passed the larger practical acceptance test. A running goal was interrupted,
then resumed without a new prompt and reused its checkpoint exactly once.
The support suite contains 65 checks;
see the latest checkpoint for actual on-device and recovery evidence.
