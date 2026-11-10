[CmdletBinding()]
param(
    [ValidateSet('Status', 'Budget', 'Start', 'Stop', 'Configure', 'Tunnel', 'Test', 'Diagnostics', 'Chat')]
    [string]$Action = 'Status',
    [string]$JetsonHost = '192.168.55.1',
    [string]$JetsonUser = 'armanberkay'
)
$ErrorActionPreference = 'Stop'
$knownHosts = Join-Path $PSScriptRoot 'ssh_known_hosts'
if (-not (Test-Path -LiteralPath $knownHosts)) { throw 'Pinned Jetson SSH identity is missing.' }
$sshOptions = @('-o', 'HostKeyAlias=jetson-bot', '-o', "UserKnownHostsFile=$knownHosts",
    '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
    '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3')
$destination = "${JetsonUser}@${JetsonHost}"
switch ($Action) {
    'Chat' {
        Write-Host 'Opening the Jetson CLI. Approval prompts use this terminal.'
        & ssh @sshOptions -t $destination 'bash /opt/jetson-bot/deploy/run-chat.sh'
    }
    'Tunnel' {
        Write-Host 'Project Hub: http://127.0.0.1:8787/ (leave this terminal open)'
        & ssh @sshOptions -o ExitOnForwardFailure=yes -N -L '127.0.0.1:8787:127.0.0.1:8787' -L '127.0.0.1:8870:127.0.0.1:8870' -L '127.0.0.1:8871:127.0.0.1:8871' $destination
    }
    'Diagnostics' {
        & ssh @sshOptions $destination 'date -Is; uptime; ip -4 route; systemctl is-active jetson-budget jetson-bot jetson-project-hub jetson-discord-watch jetson-clock.timer jetson-backup.timer; curl -fsS --max-time 3 http://127.0.0.1:8870/health'
    }
    'Test' {
        $testCommand = 'sudo bash -c ''source /etc/jetson-bot/deployment.env; docker run --rm --network none --memory 512m -v /opt/jetson-bot:/app:ro -w /app --entrypoint /opt/hermes/.venv/bin/python "$HERMES_IMAGE" -m unittest discover -s tests -q'''
        & ssh @sshOptions -t $destination $testCommand
    }
    default {
        & ssh @sshOptions -t $destination "sudo jetsonctl $($Action.ToLowerInvariant())"
    }
}
exit $LASTEXITCODE
