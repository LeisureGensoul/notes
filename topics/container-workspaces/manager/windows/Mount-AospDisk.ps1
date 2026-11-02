#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$env:WSL_UTF8 = '1'
$uuid = '9fa88778-a01d-458f-932b-f59d3673e77d'
$partitionId = '06706d9c-8111-41bb-972f-745d52f1b96b'

function Get-MountedUuid {
    $value = wsl.exe --system --user root --exec findmnt -n -o UUID --mountpoint /mnt/wsl/AOSP
    if ($LASTEXITCODE -eq 0) { return "$value".Trim() }
}

$mounted = Get-MountedUuid
if ($mounted -eq $uuid) { return }
if ($mounted) { throw '/mnt/wsl/AOSP is occupied by another disk.' }

$partition = @(Get-Partition | Where-Object { $_.Guid -and [guid]$_.Guid -eq [guid]$partitionId })
if ($partition.Count -ne 1) { throw 'AOSP partition not uniquely found.' }
$disk = Get-Disk -Number $partition[0].DiskNumber
if ($disk.IsBoot -or $disk.IsSystem -or ($disk.SerialNumber -replace '[^a-zA-Z0-9]', '') -ne 'E8238FA6BF530001001B448B4AE5F2BA') {
    throw 'Unexpected physical disk.'
}

wsl.exe --mount "\\.\PHYSICALDRIVE$($disk.Number)" --partition $partition[0].PartitionNumber --type ext4 --name AOSP
if ($LASTEXITCODE -ne 0 -or (Get-MountedUuid) -ne $uuid) { throw 'AOSP disk mount failed.' }
Write-Output 'AOSP mounted at /mnt/wsl/AOSP.'
