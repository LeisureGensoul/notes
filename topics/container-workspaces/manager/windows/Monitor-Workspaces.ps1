param(
    [string]$User,
    [double]$Interval = 1,
    [switch]$Once,
    [string]$Disk = '/run/desktop/mnt/host/wsl/AOSP'
)

$ErrorActionPreference = 'Stop'
$managerRoot = Split-Path -Parent $PSScriptRoot
$sampleDir = Join-Path ([IO.Path]::GetTempPath()) ('workspace-monitor-' + [guid]::NewGuid())
$sampleWindows = {
    param($directory, $repeat)
    $sample = Join-Path $directory 'host'
    $draft = Join-Path $directory 'host.tmp'
    do {
        try {
            $cpu = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -Property PercentProcessorTime -ErrorAction Stop
            if ($null -eq $cpu.PercentProcessorTime) { throw 'Missing CPU sample' }
            $value = "$($cpu.PercentProcessorTime) $([Environment]::ProcessorCount)"
        } catch { $value = 'N/A' }
        try {
            $memory = Get-CimInstance Win32_OperatingSystem -Property TotalVisibleMemorySize,FreePhysicalMemory -ErrorAction Stop
            if (!$memory.TotalVisibleMemorySize -or $null -eq $memory.FreePhysicalMemory) { throw 'Missing RAM sample' }
            $ram = "$($memory.TotalVisibleMemorySize) $($memory.FreePhysicalMemory)"
        } catch { $ram = 'N/A' }
        [IO.File]::WriteAllLines($draft, [string[]]@($value, $ram))
        Move-Item -LiteralPath $draft -Destination $sample -Force -ErrorAction Stop
        if ($repeat) { Start-Sleep -Seconds 1 }
    } while ($repeat)
}
$dockerArgs = @('run', '--rm', '--init', '--pull=never')
if (Test-Path Env:NO_COLOR) { $dockerArgs += @('--env', 'NO_COLOR') }
if (-not $Once -and -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) {
    $dockerArgs += '-it'
}
$dockerArgs += @(
    '--mount', "type=bind,src=$managerRoot/monitor.py,dst=/manager/monitor.py,readonly",
    '--mount', "type=bind,src=$managerRoot/codex/status.py,dst=/manager/codex/status.py,readonly",
    '--mount', "type=bind,src=$sampleDir,dst=/run/windows-host,readonly",
    '--mount', "type=bind,src=$Disk,dst=/mnt/wsl/AOSP,readonly",
    '--mount', 'type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
    '--mount', 'type=bind,src=/run/desktop/mnt/host/wsl/docker-desktop/cli-tools/usr/bin/docker,dst=/usr/local/bin/docker,readonly',
    'builder:local', 'python3', '-B', '/manager/monitor.py',
    '--disk', '/mnt/wsl/AOSP',
    '--windows-host', '/run/windows-host/host',
    '--interval', $Interval.ToString([Globalization.CultureInfo]::InvariantCulture)
)
if ($User) { $dockerArgs += @('--user', $User) }
if ($Once) { $dockerArgs += '--once' }
$null = [IO.Directory]::CreateDirectory($sampleDir)
$sampler = $null
try {
    if ($Once) { & $sampleWindows $sampleDir $false }
    else { $sampler = Start-Job -ScriptBlock $sampleWindows -ArgumentList $sampleDir, $true }
    & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Monitor exited with code $LASTEXITCODE." }
} finally {
    if ($sampler) { Remove-Job -Job $sampler -Force }
    [IO.File]::Delete((Join-Path $sampleDir 'host.tmp'))
    [IO.File]::Delete((Join-Path $sampleDir 'host'))
    [IO.Directory]::Delete($sampleDir)
}
