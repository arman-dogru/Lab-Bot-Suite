[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Menu', 'Wifi', 'Share', 'Status', 'DisableSharing')][string]$Mode = 'Menu',
    [string]$SSID,
    [string]$JetsonHost = '192.168.55.1',
    [string]$JetsonUser = 'armanberkay',
    [string]$UpstreamAdapter
)
$ErrorActionPreference = 'Stop'
$knownHosts = Join-Path $PSScriptRoot 'ssh_known_hosts'
if (-not (Test-Path -LiteralPath $knownHosts)) { throw 'Keep ssh_known_hosts beside this script.' }
$sshOptions = @('-o', 'HostKeyAlias=jetson-bot', '-o', "UserKnownHostsFile=$knownHosts",
    '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8')
$remoteHelper = 'sudo python3 /opt/jetson-bot/deploy/jetson-network.py'

function Invoke-Jetson([string]$Command) {
    & ssh @sshOptions -t "${JetsonUser}@${JetsonHost}" $Command
    if ($LASTEXITCODE) { throw "Jetson command failed (exit $LASTEXITCODE)." }
}

if ($Mode -eq 'Menu') {
    Write-Host '1. Connect Jetson directly to Wi-Fi (saved for future boots)'
    Write-Host '2. Share this laptop internet over USB (Windows administrator prompt)'
    Write-Host '3. Show Jetson connection status'
    Write-Host '4. Disable this laptop USB sharing'
    switch (Read-Host 'Choose 1, 2, 3 or 4') {
        '1' { $Mode = 'Wifi' }
        '2' { $Mode = 'Share' }
        '3' { $Mode = 'Status' }
        '4' { $Mode = 'DisableSharing' }
        default { throw 'No valid mode selected.' }
    }
}

if (-not $WhatIfPreference -and -not $PSBoundParameters.ContainsKey('JetsonHost')) {
    foreach ($candidate in @('192.168.55.1', '192.168.137.2')) {
        $client = New-Object Net.Sockets.TcpClient
        try {
            $pending = $client.BeginConnect($candidate, 22, $null, $null)
            if ($pending.AsyncWaitHandle.WaitOne(900) -and $client.Connected) {
                $client.EndConnect($pending)
                $JetsonHost = $candidate
                break
            }
        } catch { } finally { $client.Dispose() }
    }
}

if ($Mode -eq 'Status') {
    Invoke-Jetson 'nmcli device status; ip -4 route; systemctl is-active jetson-bot jetson-budget jetson-project-hub; curl -fsS --max-time 3 http://127.0.0.1:8870/health'
    return
}
if ($Mode -eq 'Wifi') {
    if (-not $PSCmdlet.ShouldProcess($JetsonHost, 'Save and connect a headless Wi-Fi profile')) { return }
    Invoke-Jetson "$remoteHelper scan"
    if (-not $SSID) { $SSID = Read-Host 'Wi-Fi network name (SSID)' }
    $bytes = [Text.Encoding]::UTF8.GetBytes($SSID)
    if ($bytes.Length -lt 1 -or $bytes.Length -gt 32 -or $SSID -match '[\x00\r\n]') { throw 'Invalid SSID.' }
    $encoded = [Convert]::ToBase64String($bytes)
    Write-Host 'SSH/sudo may ask for the Jetson login password; nmcli asks for the Wi-Fi password only when needed.'
    Invoke-Jetson "$remoteHelper wifi $encoded"
    Write-Host 'Saved. Jetson will try this network automatically without a desktop login.'
    return
}

$usb = @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -match 'Remote NDIS Compatible Device' })
if ($usb.Count -ne 1) { throw 'Connect one Jetson USB RNDIS adapter before using sharing.' }
$stateFolder = Join-Path (Split-Path $PSScriptRoot -Parent) '.local'
if ($Mode -eq 'DisableSharing' -and -not $UpstreamAdapter) {
    $manifestFile = Join-Path $stateFolder 'windows-sharing-state.json'
    if (-not (Test-Path -LiteralPath $manifestFile)) { throw 'No sharing setup was saved for this laptop.' }
    $savedSharing = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
    $UpstreamAdapter = (Get-NetAdapter | Where-Object InterfaceGuid -eq ([Guid]$savedSharing.publicGuid)).Name
}
if (-not $UpstreamAdapter) {
    $routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' |
        Where-Object { $_.InterfaceIndex -ne $usb[0].ifIndex } |
        Sort-Object @{Expression={ $_.RouteMetric + (Get-NetIPInterface -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4).InterfaceMetric }}
    if (-not $routes) { throw 'No internet-facing adapter found.' }
    $UpstreamAdapter = (Get-NetAdapter -InterfaceIndex $routes[0].InterfaceIndex).Name
}
$public = Get-NetAdapter -Name $UpstreamAdapter
if (($Mode -eq 'Share' -and $public.Status -ne 'Up') -or $public.ifIndex -eq $usb[0].ifIndex) { throw 'Choose a different, connected internet adapter.' }
if (-not $PSCmdlet.ShouldProcess("$($public.Name) -> $($usb[0].Name)", $Mode)) { return }
if ($Mode -eq 'Share') { Invoke-Jetson "$remoteHelper share-prepare" }

New-Item -ItemType Directory -Force -Path $stateFolder | Out-Null
$resultFile = Join-Path $stateFolder 'windows-sharing-result.json'
if (Test-Path -LiteralPath $resultFile) { Remove-Item -LiteralPath $resultFile }
$helper = Join-Path $PSScriptRoot 'windows-usb-sharing.ps1'
$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $helper),
    '-Action', $(if ($Mode -eq 'Share') { 'Enable' } else { 'Disable' }),
    '-PublicGuid', $public.InterfaceGuid.ToString(), '-PrivateGuid', $usb[0].InterfaceGuid.ToString(),
    '-ResultFile', ('"{0}"' -f $resultFile))
Write-Host 'Approve the Windows administrator prompt to configure USB internet sharing.'
$process = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList $arguments -PassThru
$process.WaitForExit()
if (-not (Test-Path -LiteralPath $resultFile)) { throw 'Sharing helper did not finish. Check Windows script policy or administrator permissions.' }
$result = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
if (-not $result.success) { throw $result.error }

# ICS uses a second subnet; SSH host identity stays pinned to this Jetson.
if ($Mode -eq 'Share') {
    $JetsonHost = '192.168.137.2'
    Invoke-Jetson "$remoteHelper share-enable"
    Write-Host 'USB sharing enabled. Saved Jetson Wi-Fi remains preferred when available.'
} else {
    $JetsonHost = '192.168.55.1'
    Invoke-Jetson "$remoteHelper share-disable"
    Write-Host 'USB internet sharing disabled; known Wi-Fi remains available.'
}
