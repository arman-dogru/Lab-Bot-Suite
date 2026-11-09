# Plug in and connect

The agent starts with the Jetson OS. It does not need a desktop login, laptop
script, monitor or keyboard when a saved Wi-Fi network is available.
Project Hub remains private at `http://127.0.0.1:8787/` through the connected PC's
SSH tunnel.

The Windows laptop helper is **Connect-Jetson.cmd** in the project folder.
Keep its `deploy` folder alongside it. It offers direct Wi-Fi setup, laptop
internet sharing, connection status and disabling sharing.

## Connect the Jetson directly to Wi-Fi

Connect its USB cable, run the helper and choose **1**. Alternatively:

```powershell
.\deploy\connect-jetson.ps1 -Mode Wifi
# Or provide the public network name:
.\deploy\connect-jetson.ps1 -Mode Wifi -SSID 'uOttawa-G'
```

Enter the Jetson login password at the SSH/sudo prompts. The terminal lists
nearby networks and asks for the SSID if needed. NetworkManager asks for the
Wi-Fi password through a hidden terminal prompt when a saved secret is not
already available. Passwords are not placed in command arguments, history or
laptop files. The saved Wi-Fi credential stays in the Jetson's root-private
NetworkManager profile so it can reconnect without a logged-in user.

The helper supports normal personal/open Wi-Fi. Enterprise Wi-Fi requiring
802.1X identity/certificates needs a suitable NetworkManager profile first;
saved profiles are then available for automatic use.

## Share a Windows laptop's internet over USB

Choose **2**, or run:

```powershell
.\deploy\connect-jetson.ps1 -Mode Share
# Select an upstream adapter explicitly if necessary:
.\deploy\connect-jetson.ps1 -Mode Share -UpstreamAdapter 'Wi-Fi'
```

Windows Internet Connection Sharing requires administrator approval. The helper
selects the internet-facing adapter and the Jetson RNDIS adapter, preserves any
unrelated sharing configuration, and checks for a conflicting sharing subnet.
It does not change the internet-facing adapter's address or expose Project Hub.
Organizational policy may prevent Windows sharing; the helper reports that
failure without changing policy.

The original USB management pair `192.168.55.100` / `192.168.55.1` remains usable.
Windows sharing also uses laptop `192.168.137.1` and Jetson `192.168.137.2`.
The helper can find the Jetson on either USB address. Saved direct Wi-Fi remains
preferred; USB supplies an internet fallback when the laptop DNS proxy is ready.
After opting in, the Jetson retries that fallback at boot and every minute.
If the laptop is absent, it removes its sharing route and continues trying Wi-Fi.

To turn sharing off, choose **4** or run:

```powershell
.\deploy\connect-jetson.ps1 -Mode DisableSharing
```

This disables the recorded Windows sharing pair and the Jetson's fallback.
The USB management address is retained. Choosing direct Wi-Fi also disables the
Jetson's sharing fallback; disable Windows sharing separately if it is no longer
wanted. Windows sharing should be configured on each laptop that will provide it.

Preview sharing adapter selection without changes:

```powershell
.\deploy\connect-jetson.ps1 -Mode Share -WhatIf
```

## Automatic startup and recovery

`jetson-network.service` makes saved Wi-Fi profiles available without a desktop
session and enables native NetworkManager autoconnection. A minute timer runs
an idempotent recovery check; healthy profiles are not rewritten or disconnected.
The most recently selected network has priority 100. Networks must be in range
and their saved credentials valid. Missing credentials or a captive portal can
still require input.

`jetson-clock` verifies time before the paid gateway becomes available.
`jetson-budget`, `jetson-bot`, the status/activity watcher and Project Hub are
enabled at boot and retain their existing restart policies. Owner access and
the US$20/day Toronto limit remain unchanged.

For status from the connected laptop:

```powershell
.\deploy\connect-jetson.ps1 -Mode Status
.\deploy\jetson.ps1 Diagnostics
```

Installer for a future deployment of these network files on the Jetson:
`sudo bash /opt/jetson-bot/deploy/install-network.sh`.
Original profiles were backed up privately under
`/var/lib/jetson-bot/network-backup-before-headless`; network recovery also keeps
a root-private profile snapshot in `/var/lib/jetson-bot/network/original-profiles`.

NetworkManager's [connection profile documentation](https://www.networkmanager.dev/docs/api/latest/settings-connection.html)
describes permissions, autoconnection and priorities. Windows sharing uses the
supported [EnableSharing interface](https://learn.microsoft.com/en-us/windows/win32/api/netcon/nf-netcon-inetsharingconfiguration-enablesharing).

## Validation

Direct Wi-Fi is the selected live mode. Network recovery tests cover headless
permissions, idempotence, literal SSIDs, sharing opt-in, subnet conflicts and an
absent laptop. Windows scripts were parsed and sharing adapter selection was
checked with `-WhatIf`; Windows sharing was not enabled on the current PC.
A real headless reboot passed on November 2: Wi-Fi reconnected automatically,
the clock verified, and Discord connected at 11:50 Toronto time with no desktop
login. All 73 support tests passed in the Jetson's pinned runtime. Project Hub's
local-only viewing tunnel was restored after reboot. Details are in `PROGRESS.md`.
