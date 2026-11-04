param(
    [ValidatePattern('^[a-z][a-z0-9_-]{0,31}$')]
    [string]$User,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$disk = '/run/desktop/mnt/host/wsl/AOSP'
$linuxRoot = '/run/desktop/mnt/host/' + $root.Substring(0, 1).ToLowerInvariant() + $root.Substring(2).Replace('\', '/')

& (Join-Path $PSScriptRoot 'Mount-AospDisk.ps1')
wsl.exe --system --user root --exec mount --make-shared /mnt/wsl/AOSP
if ($LASTEXITCODE -ne 0) { throw 'Cannot enable mount propagation on the SSD.' }
docker desktop start --timeout 120
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop did not start.' }

$argsDocker = @(
    'run', '--rm', '--privileged', '--pull=never',
    '--mount', "type=bind,src=$disk,dst=$disk,bind-propagation=rshared",
    '--mount', "type=bind,src=$root,dst=$linuxRoot,readonly",
    '--mount', 'type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
    '--mount', 'type=bind,src=/run/desktop/mnt/host/wsl/docker-desktop/cli-tools/usr/bin/docker,dst=/usr/local/bin/docker,readonly',
    '-w', $linuxRoot, 'builder:local', 'python3', '-B', 'start.py', '--restore-only', $disk
)
if ($User) { $argsDocker += @('--user', $User) }
docker @argsDocker
if ($LASTEXITCODE -ne 0) { throw 'Workspace restore failed.' }

& (Join-Path $PSScriptRoot 'Monitor-Workspaces.ps1') -User $User -Once:$Once
