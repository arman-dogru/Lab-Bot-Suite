# Jetson Bot: daily use

Open the CLI from this PC and leave the window open while you work:

```powershell
.\deploy\jetson.ps1 Chat
```

The session runs on the Jetson over SSH. Give it a file path on the Nano for a document or image. `/jetson status`, `/jetson budget`, `/jetson pause`, `/jetson resume`, `/jetson jobs`, and `/jetson review all|auto|manual` do not call the model. Closing the window ends the chat. The agent service keeps schedules running.

Discord remains available until the CLI has taken a task. The channel is [jetson-bot](https://discord.com/channels/1565454238203519096/1565784164547035238). Attach source documents there, and use a separate thread for each project.

For a longer assignment, send `/goal --` followed by the objective and checkable completion criteria. For example: “Add project labels to the existing Project Hub, test them, preserve existing data, deploy locally, and return the viewing link.” The bot saves work on the Jetson and can install compatible tools, write/run code, build documents, maintain reusable skills, and administer the machine.

- `/goal status`, `/goal pause`, `/goal resume`: inspect or control the thread’s standing goal.
- `/stop`: interrupt this thread’s work and cancel its queued automatic recovery.
- `/jetson status`, `/jetson budget`: local status; no model credit needed.
- `/jetson pause`, `/jetson resume`: pause or allow paid model work.
- `/jetson jobs`: inspect saved work waiting for recovery or an identified blocker.

For schedules, ask in ordinary language and specify Toronto time. Example: “Every weekday at 9 AM, check this project and notify me only when something needs attention.” Terminal users can also use native `/cron` commands. New external integrations may need credentials from you.

Finished answers and files stay in task threads. #jetson-activity edits one task card with action counts; #jetson-status holds service/budget information and local controls. Private model reasoning is not published. Paid work is capped at US$20 per Toronto calendar day. Budget-paused requests can resume automatically; explicitly paused goals stay paused.

## Project Hub

Open [Project Hub](http://127.0.0.1:8787/) on this PC. It supports projects, tasks, notes with revisions, documents/artifacts, search, exports and a private authenticated local webhook. The bot built it and can modify it when asked.

The current viewing connection is already open. To reopen it, run this in PowerShell from the Windows project folder and leave it running:

```powershell
.\deploy\jetson.ps1 Tunnel
```

Enter the Jetson login password at its hidden prompt. Replace the USB address with the Jetson’s Wi-Fi address if needed. Closing this viewing connection does not stop the bot.

## Power and recovery

The Jetson now tries its saved Wi-Fi networks at boot without a desktop login.
For a new network, run `Connect-Jetson.cmd` on the connected Windows laptop and
choose direct Wi-Fi setup. It can also configure laptop internet sharing over
USB. See the [networking guide](docs/NETWORKING.md) or copy the portable
`artifacts/Jetson-Laptop-Setup.zip` to another Windows laptop.

The bot starts automatically when the Jetson is powered and its saved Wi-Fi is available. Its screen can sleep; no keyboard or desktop login is needed. A new Wi-Fi network must first be configured. Currently power comes from the connected PC over USB, so reliable independent operation requires the correct dedicated 5 V supply for this Nano board.

Daily private backups run at 3 AM, briefly stopping the bot and site for consistency. Local recovery commands on the Jetson are `sudo jetsonctl status`, `budget`, `pause`, `resume`, `stop`, `start`, and `backup`. Detailed paths, credential replacement, restore verification and image rollback are in [README.md](README.md).
