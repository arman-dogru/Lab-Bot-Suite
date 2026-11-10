# Plan: OpenBot on the Jetson, used from the local network

The agents live on the Jetson Nano. People manage them from another machine on the local network, in a browser. The page they see is the OpenBot web UI itself, the MIT app from [CopilotKit/OpenBot](https://github.com/CopilotKit/OpenBot), not a lookalike we draw. That UI is where agents are created, where a person talks to an agent, and where agent-to-agent chats are visible.

The Nano is also the only computer. There is not one Chromium container per agent. One browser may start, then it exits. The model remains Grok through the budget gateway on the same board. Purchases stay denied.

## 1. What you can do from another machine

On a laptop or phone that is on the lab network, open:

```text
http://<jetson-lan-address>:3010
```

Sign in. From that session:

- Create, rename, and disable agents.
- Open an agent's channel and send work.
- Read another channel where two agents handed work to each other. Those transcripts stay in OpenBot. They are not a private side log.
- Approve or deny a held action.
- Take the one shared screen when an agent is stopped on a login or a check, then hand it back.
- Read the audit.

The Jetson does not need a monitor for any of this. The HDMI port can stay disconnected.

The address is a local one: the USB link `192.168.55.1`, or the lab Wi-Fi address the board already uses. The port is not forwarded to the public internet. Sign-in is required as soon as the port is reachable by anything except localhost. `OPENBOT_SINGLE_USER=true` is only for a first boot while the bind is still `127.0.0.1`.

There is no Discord door. Tasks, approvals, agent management, inter-agent transcripts, and takeover all happen in this UI.

## 2. What runs on the Nano

One OpenBot deployment, built for ARM64 and started on the board:

| Process | Role | Bind |
| --- | --- | --- |
| OpenBot app | The real React UI: agents, channels, screen, admin | LAN address, port 3010, sign-in on |
| OpenBot API | Policy, audit, coworkers, handoff, routines | LAN address, port 3001, same sign-in |
| PostgreSQL | Agents, channels, messages, audit, grants | `127.0.0.1` only |
| One worker | Claims a single run at a time | localhost |
| Budget gateway | Already on the board, `127.0.0.1:8870` | localhost |
| Computer tools | Workspace, shell, documents, fetch, one browser | `127.0.0.1:8871` |

CopilotKit Intelligence is used if its local license fits in memory. Threads and inter-agent transcripts must still be stored on the Nano if Intelligence is too heavy. The UI reads them from the OpenBot API either way. A second copy of the chat is not built.

Agents are rows in that database, not extra machines. Each agent has a name, a persona, a memory namespace, and a tool allowlist. They share one workspace, `/srv/jetson-bot/workspace`, and one browser. Two agents do not get two Chromium processes.

The model URL inside OpenBot is `http://127.0.0.1:8870/v1`. The raw xAI key is not copied into the OpenBot environment. The gateway keeps function tools only, four images, the $20 Toronto cap, and streaming off.

## 3. Inter-agent chat

When one agent hands work to another, OpenBot already records that as a handoff with initiator `handoff`. The plan uses that, with three limits that fit this board:

- Depth at most 2. A child may not start another child.
- At most 4 child tasks for one parent run.
- One model call on the device at a time. The parent waits. The UI still shows both sides of the channel while the run is queued.

The channel list is the place you see those conversations. A routine is marked `routine`, not `person`. A handoff is marked `handoff`. A message you typed is marked `person`.

## 4. Memory budget

The board has 4 GB. The UI and the database stay up. The browser does not.

| Piece | Target |
| --- | --- |
| OS and network | 700–1000 MB |
| OpenBot API and built UI | under 400 MB |
| PostgreSQL | under 200 MB |
| One agent worker | under 250 MB |
| Budget gateway | about 50 MB |
| One Chromium, only while a page is open | 300–600 MB, then gone |
| Free margin | 500 MB |

Before Chromium starts, free memory must be at least 700 MB and a deny-by-default outbound list must exist. Otherwise the tool refuses and the run continues with `fetch_url` or stops. Hermes is stopped before this stack is measured. Its files stay on disk so it can be started again.

If the measured set goes into swap, the browser stays off and agent count stays at the number that fit. Swap is not the plan.

## 5. Takeover from the LAN page

The screen socket is served by the OpenBot process on the Nano and is authorized as the signed-in user. You do not SSH in to click.

1. The agent hits a login, a second factor, or a human check. The run becomes `WAITING`. The audit records `computer.help_requested` and the page URL without a query string.
2. The LAN page for that channel shows the wait. Nothing is posted to Discord.
3. You open the channel from another machine on the LAN and take the screen. The audit records `computer.control_taken`. Bot actions are refused until you release.
4. You type the secret in the screen. The audit stores that a secret was supplied and its length, not the characters.
5. You release. The audit records `computer.control_released`. The browser is closed. The run resumes.
6. If nobody takes the screen for 30 minutes, the wait is denied and the run stops. The channel shows that.

A file write, a push, or an install does not need the screen. Allow or Deny on the channel calls the approval row. The first answer wins.

Purchases and secret-file reads are denied before anyone is asked. A path outside `/srv/jetson-bot/workspace` is denied, not offered. Persona text cannot change that.

## 6. Build order

### Phase 1. UI on the board, localhost only

Cross-build or build on the Nano the OpenBot web app and API for ARM64. Bind them to `127.0.0.1`. Create two agents. Send a message to one. Hand a task from that agent to the other. From an SSH tunnel, open the UI and read both channels.

Exit: the page is the OpenBot UI. Both transcripts are visible. No second chat app exists in this repo.

### Phase 2. LAN access

Bind the UI and API to the Jetson's LAN address. Turn sign-in on. From another machine on that network, sign in, open the agent list, and open an inter-agent channel. From a machine not on that network, the port does not answer.

Exit: `http://192.168.55.1:3010` or the lab Wi-Fi address loads the OpenBot UI after sign-in. The campus port is not published beyond the lab network.

### Phase 3. One computer, one worker

Point tools at the workspace, the document reader, fetch, the image resizer, and the argv shell on `127.0.0.1`. Shell has no sudo, an allowlist, a timeout, and an output cap. Model calls go through the budget gateway. One worker claims one run.

Exit: a file that exists only on the board is quoted correctly. A purchase never runs. The ledger moves only for a real completion. Two agents do not run two model calls at once.

### Phase 4. Takeover

Enable one Chromium under the memory and outbound rules. Prove help, take, lockout, release, and the 30-minute deny from a browser on another LAN machine.

### Phase 5. Remove Discord

After Phase 3 has passed, stop `jetson-bot.service` and `jetson-discord-watch.service`. Do not add a Discord bridge. Keep the budget service, the clock check, and the tool files. Leave the Hermes tree on disk so a rollback is a service start, not a restore.

## 7. Testing

Two layers. A unit test on this PC never counts as the LAN page. A live check never counts unless it hits the board.

### On this PC, before anything is called done

Run twice, from a clean start:

```text
python -m unittest tests.test_openbot_pivot -v
```

Both runs must pass. The suite calls the shipped functions. It must show:

- The model URL is `http://127.0.0.1:8870/v1`. The OpenBot environment has no raw xAI key. Streaming is rejected. A non-function tool is rejected. A fifth image is rejected. The daily cap is the $20 Toronto ledger.
- `OPENBOT_SINGLE_USER=true` is present only when the bind is `127.0.0.1`. A signed-in bind to `192.168.55.1` or `10.206.5.149` is allowed. `0.0.0.0` is refused. A client off that subnet is refused. PostgreSQL is specified as `127.0.0.1` only.
- A workspace read returns the file text. A purchase, a secret path, and a path outside the workspace are denied before any read, in `all`, `auto`, and `manual`. Persona text does not change a deny. The shell rejects `sudo`, a purchase word, and an absolute or `..` path hidden inside an argument, including `//` and backslash spellings. `print(1)` and a path under `/srv/jetson-bot/workspace` still run. Output is capped and the timeout is passed through.
- Chromium does not start without an outbound list, or when free memory is under 700 MB. It is marked stopped when the run ends.
- A person message is labeled `person`, a handoff `handoff`, a cron start `routine`. A second model call is refused while one is in flight. A child cannot start another child. The fifth child of one parent is refused. No Discord body is produced.
- Takeover writes `computer.help_requested`, `computer.control_taken`, and `computer.control_released`. A secret is stored as a length only. An Allow during takeover is refused. After 30 minutes with nobody at the screen, release leaves the run `DENIED`.
- Cutover stops `jetson-bot.service` and `jetson-discord-watch.service` only. The budget service, the clock, and the Hermes files stay. There is no Discord bridge.
- This repo has no `jetson_bot/loop.py` and no second chat page.

A Windows-only file-mode failure in an older recovery test is recorded separately. It does not replace a failed OpenBot test.

### On the board, from the laptop

Do these only after the unit suite is green. Save the raw response. Do not invent a pass.

| Check | How | Pass |
| --- | --- | --- |
| UI identity | Fetch `http://192.168.55.1:3010` twice | Both succeed. Both are the upstream OpenBot document, title `OpenBot`, with `openbot-theme` or the upstream bundle. The body is not a file from this repo. |
| API | Fetch port 3001 on the same host | The API answers. An unsigned request is not the agent list. |
| Listeners | `ss` on the board | 3010 and 3001 are on `192.168.55.1` and/or `10.206.5.149` only. PostgreSQL is on `127.0.0.1` only. Those ports are not on `0.0.0.0` or `::`. |
| Sign-in | Environment on the board, secrets redacted | `OPENBOT_SINGLE_USER` is not `true`. The model URL is `http://127.0.0.1:8870/v1`. The credential is not the raw xAI key. |
| Agents | Signed-in browser on the laptop | Two agents are listed. One person message is visible. The handoff channel is readable in OpenBot and labeled `handoff`. |
| Tools | One workspace file, one outside path, one purchase | The workspace text matches the file. The outside path is denied and not read. The purchase is denied. The ledger billed total does not move for those denials. |
| One worker | Queue while a run is active | A second model call does not start. |
| Browser gate | Free memory and outbound list | Chromium starts only when both pass. Otherwise the tool refuses and the run continues without a browser. One browser, not one per agent. |
| Takeover | Login fixture from the laptop browser | Help, take, lockout, secret length, release, and the 30-minute deny match the audit names above. Do not wait a real 30 minutes if the shipped clock test already passed. Do not fake a screen. |
| Discord gone | `systemctl` and process list | Both Discord units are inactive. Budget and clock are active. `run-hermes.sh` and `soul.md` are still on disk. No Discord bridge process. |

If SSH or the page cannot be reached, write the connection error and leave that live check failed. A green unit test does not fill it in.

## 8. Done when

From a laptop on the local network, not from the Nano's own screen, you open the OpenBot UI, see the agents, talk to one, and read the channel where that agent talked to another. A login wall is finished in that same UI. The board is the only place the agents and the page are served.
