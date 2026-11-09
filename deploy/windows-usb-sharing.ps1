param(
    [ValidateSet('Enable', 'Disable')][string]$Action,
    [Guid]$PublicGuid,
    [Guid]$PrivateGuid,
    [string]$ResultFile
)
$ErrorActionPreference = 'Stop'
$publicConfig = $null
$privateConfig = $null
$enabledHere = $false
try {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) { throw 'Administrator access is required.' }
    $public = Get-NetAdapter | Where-Object InterfaceGuid -eq $PublicGuid
    $private = Get-NetAdapter | Where-Object InterfaceGuid -eq $PrivateGuid
    if (-not $public -or -not $private -or $public.ifIndex -eq $private.ifIndex -or
        $private.InterfaceDescription -notmatch 'Remote NDIS Compatible Device') { throw 'Sharing adapter identity check failed.' }
    $manager = New-Object -ComObject HNetCfg.HNetShare
    $publicConnection = $null
    $privateConnection = $null
    foreach ($connection in $manager.EnumEveryConnection) {
        $properties = $manager.NetConnectionProps($connection)
        $config = $manager.INetSharingConfigurationForINetConnection($connection)
        $guid = [Guid]$properties.Guid
        if ($config.SharingEnabled -and $guid -notin @($PublicGuid, $PrivateGuid)) {
            throw 'Another internet-sharing configuration is active; it was preserved.'
        }
        if ($guid -eq $PublicGuid) { $publicConnection = $connection }
        if ($guid -eq $PrivateGuid) { $privateConnection = $connection }
    }
    if (-not $publicConnection -or -not $privateConnection) { throw 'Sharing connection lookup failed.' }
    $publicConfig = $manager.INetSharingConfigurationForINetConnection($publicConnection)
    $privateConfig = $manager.INetSharingConfigurationForINetConnection($privateConnection)
    $manifestFile = Join-Path (Split-Path $ResultFile -Parent) 'windows-sharing-state.json'
    if ($Action -eq 'Disable') {
        if (-not (Test-Path -LiteralPath $manifestFile)) { throw 'No saved sharing setup for this laptop; nothing was changed.' }
        $manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
        if ([Guid]$manifest.publicGuid -ne $PublicGuid -or [Guid]$manifest.privateGuid -ne $PrivateGuid) {
            throw 'Saved sharing setup uses different adapters; nothing was changed.'
        }
        if ($privateConfig.SharingEnabled) { $privateConfig.DisableSharing() }
        if ($publicConfig.SharingEnabled) { $publicConfig.DisableSharing() }
        $icsAddress = Get-NetIPAddress -InterfaceIndex $private.ifIndex -IPAddress '192.168.137.1' -ErrorAction SilentlyContinue
        if ($icsAddress) { $icsAddress | Remove-NetIPAddress -Confirm:$false }
        Remove-Item -LiteralPath $manifestFile
    } else {
        $conflicts = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
            $_.IPAddress -like '192.168.137.*' -and $_.InterfaceIndex -ne $private.ifIndex
        }
        if ($conflicts) { throw 'The Windows sharing subnet is already used on another adapter.' }
        if (-not $publicConfig.SharingEnabled -and -not $privateConfig.SharingEnabled) {
            @{ publicGuid=$PublicGuid.ToString(); privateGuid=$PrivateGuid.ToString();
               dhcp=(Get-NetIPInterface -InterfaceIndex $private.ifIndex -AddressFamily IPv4).Dhcp.ToString() } |
                ConvertTo-Json | Set-Content -LiteralPath $manifestFile
            $publicConfig.EnableSharing(0)
            $enabledHere = $true
            $privateConfig.EnableSharing(1)
            Start-Sleep -Seconds 3
        } elseif (-not ($publicConfig.SharingEnabled -and $privateConfig.SharingEnabled -and
                         $publicConfig.SharingConnectionType -eq 0 -and $privateConfig.SharingConnectionType -eq 1)) {
            throw 'Existing sharing roles differ from the requested configuration; nothing was changed.'
        }
        # Keep the original USB management connection alongside the ICS subnet.
        if (-not (Get-NetIPAddress -InterfaceIndex $private.ifIndex -IPAddress '192.168.55.100' -ErrorAction SilentlyContinue)) {
            New-NetIPAddress -InterfaceIndex $private.ifIndex -IPAddress '192.168.55.100' -PrefixLength 24 | Out-Null
        }
        if (-not (Get-NetIPAddress -InterfaceIndex $private.ifIndex -IPAddress '192.168.137.1' -ErrorAction SilentlyContinue)) {
            throw 'Windows did not create the expected sharing gateway.'
        }
    }
    if (-not (Get-NetIPAddress -InterfaceIndex $private.ifIndex -IPAddress '192.168.55.100' -ErrorAction SilentlyContinue)) {
        New-NetIPAddress -InterfaceIndex $private.ifIndex -IPAddress '192.168.55.100' -PrefixLength 24 | Out-Null
    }
    @{success=$true} | ConvertTo-Json | Set-Content -LiteralPath $ResultFile
} catch {
    if ($enabledHere) {
        if ($privateConfig -and $privateConfig.SharingEnabled) { $privateConfig.DisableSharing() }
        if ($publicConfig -and $publicConfig.SharingEnabled) { $publicConfig.DisableSharing() }
    }
    @{success=$false; error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $ResultFile
    exit 1
}
