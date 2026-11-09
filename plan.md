# Plan: OpenBot on the operator PC, the Nano as the computer

The agent workspace is [CopilotKit OpenBot](https://github.com/CopilotKit/OpenBot), MIT. It is the control plane: chat, policy, audit, routines, and human takeover. The Jetson Nano is the one computer those agents use. We do not build a second orchestrator in `jetson_bot/loop.py`, and we do not run the OpenBot stack on the Nano.

People talk to the agents from outside the Nano. Two doors, with different jobs:

- A chat site on the operator's own network is where you work and where you take over a browser.
- Discord is the pocket channel. It can start a task, report progress, and collect a yes or no. It cannot drive the screen.

The model stays Grok through the existing budget gateway. Purchases stay denied. Hermes stays installed until one OpenBot task has read a file on the Nano and the audit shows the allow or the deny.

## 1. Why the pieces sit where they do

OpenBot is a React app, a Hono API, PostgreSQL, a routine worker, and CopilotKit Intelligence. A full bot computer in that project is a container with Chromium, a workspace, and a browser profile. The published desktop installers are Mac and Windows x64. Source on Windows is supported inside WSL 2. None of that is a Jetson image.

The Nano has 4 GB of RAM, Ubuntu 18.04, and one heavy job at a time. One Chromium is already most of the free memory. PostgreSQL, Bun, a React dev server, and a browser per bot do not fit beside Hermes.

So:

| Piece | Where it runs | Reached how |
| --- | --- | --- |
| OpenBot app and API | This Windows PC, in WSL 2 | Browser on the LAN, or Discord |
| Intelligence | Same PC, free local license, or the managed free project | Only the OpenBot server talks to it |
| PostgreSQL | Docker on that PC | Not published off the machine |
| Budget gateway | Nano, `127.0.0.1:8870` | SSH tunnel from the PC |
| Files, shell, documents, one browser | Nano | OpenBot calls them through a tunnel. They do not listen on campus Wi-Fi |
| Hermes and Discord bot | Nano, until cutover | Existing service |

The Nano's campus address is not the chat address. The chat address is the operator PC. The USB link `192.168.55.1` is how that PC reaches the board.

## 2. Talking to an agent from outside the Nano

### Door A, the network chat

This is the real console. OpenBot already serves it.

- App at `http://<operator-lan>:3010` while developing, or port `3001` if the single Docker image is used.
- A channel at `/channel/:id` is one conversation with one coworker. `/bot` is the direct chat.
- The same page shows the live computer screen and the activity list for this conversation.
- Sign-in is required before the port is reachable by anything except localhost. `OPENBOT_SINGLE_USER=true` is only for a first local trial. It makes whoever can open the port an administrator.

Bind the published port to the operator LAN interface, not to every interface on a shared campus network. Phones and laptops on that LAN open the chat. A phone off the LAN uses Tailscale to the operator PC, still not a port forward onto the Nano.

What you can do there:

- Send the task.
- Watch the screen.
- Approve or deny a held action.
- Take the browser, solve a login or a check, and hand it back.
- Read the audit at `/admin/audit`.

### Door B, Discord

Discord stays a text reach, because that is the channel that already works away from the desk. It is not the computer view.

A small bridge on the operator PC, not a second agent:

1. An owner message in the existing private Discord channel becomes a user message on that bot's OpenBot channel.
2. The bot's final text, and a one-line tool summary, is posted back to Discord.
3. A held action posts the tool name and redacted arguments, plus Allow and Deny. Those buttons call OpenBot's approval route. They do not run the tool themselves.
4. A help request posts: what page it stopped on, and the LAN or Tailscale URL of that channel. The message says the screen is only in that page.

The bridge uses the Discord bot token the project already has. It checks the owner user id before it forwards. Other users are ignored. Message content from Discord is untrusted user text, not a policy override.

Routine runs may post a result to Discord. They may not take a browser. If a routine needs a person, it pauses and sends the same help message.

### What neither door does

- Discord does not receive a video of the browser and does not click the page.
- The Nano does not host the chat and does not accept browser websocket clients on its campus address.
- A yes in Discord during an active takeover does not run a computer action. Takeover refuses bot actions until you release the screen.

## 3. Takeover

OpenBot's rule is the one we use. The computer does not decide. The server asks for help, you drive, and the bot is locked out until you let go.

### When it happens

The bot asks for a person when:

- The page is a login, a second factor, or a "verify you are human" check.
- A tool is risk `hold` and the review mode is `auto` or `manual`.
- A secret must be typed into a site. The bot never types the password. You do, in the screen. The audit records that a secret was supplied and how long it was, not the characters.
- You press Take control yourself, even if the bot did not ask.

Purchases never get this far. The policy denies them before a person is asked.

### The sequence

1. The bot's computer tool returns "need a person" and the page URL. The run status becomes `WAITING`.
2. The server writes `computer.help_requested` with that URL. If the computer later restarts, it reopens that page rather than a blank one.
3. Discord, if the bridge is up, posts the help line and the chat URL. The network chat shows the same wait on the channel.
4. You open the channel in a browser on the LAN or over Tailscale. The screen is a websocket from the OpenBot server to that bot's computer. The socket is authorized as you, the same check as the rest of that bot's routes.
5. You take control. The server writes `computer.control_taken`. From this moment every bot action on that computer is refused, not queued.
6. You sign in or pass the check in the live screen.
7. You release control. The server writes `computer.control_released`. The run resumes. The bot sees that the page is past the wall, not your password.
8. If you do nothing for 30 minutes, the wait ends as denied, the run stops, and Discord gets that line. A routine does not sit forever.

### Approvals that are not a screen

A file write, a `git push`, an install, or a GitHub write does not need the pixels. Discord Allow and Deny are enough, and so is the button on the channel. Both call the same approval row. The first answer wins. A second answer is recorded and ignored.

The arguments shown in Discord are redacted. Paths outside the workspace are not offered as an approval. They are already denied.

### Who was watching

Every audit row keeps OpenBot's initiator:

| Kind | Meaning for us |
| --- | --- |
| `person` | You sent the message from the network chat or from Discord, and you are the one who can approve. |
| `routine` | The schedule started it. Nobody is at the screen. A hold or a login wall pauses and notifies. It does not act. |
| `handoff` | Another bot asked. Still your authority, still your approval. |

A row must not say `person` for a run that started from cron.

## 4. The Nano computer

OpenBot's supervisor wants one Docker computer per bot, with its own Chromium. We do not enable that. There is one computer, the Nano, shared by the bots we actually run. Isolated extra containers stay off.

What the Nano exposes, on localhost only:

- Workspace `/srv/jetson-bot/workspace`.
- The document reader, fetch, image resizer, and the argv shell already planned around `jetson_bot`.
- One Chromium, started for a page that has no API, killed when the run ends or when you release a takeover that is done.
- The budget gateway.

The operator PC reaches those ports through the SSH tunnel that `deploy/jetson.ps1` already knows how to open. OpenBot's computer URL is the tunneled address, not `10.206.x` and not a public name.

The shell on the Nano still has no sudo, an executable allowlist, a timeout, and an output cap. Outbound network from the browser goes through a deny-by-default list once that list exists. Until it exists, the browser tool is off.

Free memory is checked before Chromium starts. Under 700 MB free, the tool returns a refusal and the run continues with `fetch_url` or stops if the page truly needs a browser.

## 5. Model and policy

- OpenBot's model setting is the budget gateway, via the tunnel: `http://127.0.0.1:8870/v1` on the Nano, seen from the PC through the tunnel. The raw xAI key is not pasted into OpenBot.
- The gateway stays function-tools only, four images, $20 Toronto cap, streaming off until a streamed bill can be settled.
- Review modes remain `all`, `auto`, and `manual`. `auto` allows workspace reads and ordinary writes. It holds push, install, send, and deploy. `manual` holds those too. `all` still denies purchases and secret reads.
- OpenBot's CEL policy is the record of those rules in the product UI. The Nano tools enforce the same denies even if a rule in the UI is loosened by mistake. Two checks, the stricter one wins.
- Persona text cannot authorize a denied tool.

## 6. What we stop building

- `jetson_bot/loop.py` as a from-scratch agent. OpenBot is the loop.
- A chat website of our own. OpenBot's channel page is the chat.
- A browser on the Nano that Discord somehow drives.
- Per-bot computers, voice, Imagine, and a public MCP catalogue. GitHub and Kaggle come later, as held or read tools with tokens injected on the Nano.

Hermes is unchanged until Phase 3. Then its Discord connection is replaced by the bridge, and its gateway can stop.

## 7. Phases

### Phase 1. OpenBot on this PC, local only

Run the template in WSL 2 from the OpenBot source, or the single Docker image, bound to localhost. Complete the Intelligence login and a model setting aimed at a fake or at the tunneled gateway. Create one coworker. Confirm a localhost channel answers.

No Discord bridge. No Nano tools yet. Single-user mode is acceptable only while the port is localhost.

Exit: you send a message in `/channel/:id` on this PC and get a reply. The audit shows the turn.

### Phase 2. The Nano is the computer

Open the SSH tunnel. Point the bot's file and shell tools at the Nano workspace. Run one read of a file that exists only on the board. Run one command that the policy denies, and see the deny in `/admin/audit` before anything runs.

Exit: the file contents in the reply match the Nano file. A purchase tool never executes. The ledger on the Nano moves only if a real model call was made.

### Phase 3. The two doors

Bind the chat to the operator LAN, with sign-in on. Add the Discord bridge for owner text, tool summaries, Allow and Deny, and help links.

Exit: a message from Discord creates a channel turn, and the reply comes back to Discord. A phone on the LAN opens the same channel and sees the transcript. A phone not on the LAN cannot open it.

### Phase 4. Takeover

Enable one Chromium on the Nano. Use a fixture page that asks for a login. Confirm the run waits, Discord receives the URL and not a password field, the LAN page shows the screen, control locks out the bot, release resumes the run, and the three audit names are present: `computer.help_requested`, `computer.control_taken`, `computer.control_released`.

Also confirm a non-visual hold can be allowed from Discord without opening the screen, and that Allow during an active takeover does not run a computer action.

### Phase 5. Cutover

Stop the Hermes Discord gateway after Phase 4. Keep the budget service, the clock check, and the Nano tools. Leave Hermes's files in place for one release so a rollback is a service start, not a restore from backup.

## 8. Done when

From a phone or laptop that is not the Nano, you can give the agent a task in Discord or in the LAN chat. You can leave. A later approval reaches you in Discord. If the agent is stuck on a login, Discord tells you, you open the channel, you see the page, you finish the check, and the agent continues without having seen the secret. The audit names who started the run and whether anyone had the screen. The Nano never accepts that chat connection on its own network address.
