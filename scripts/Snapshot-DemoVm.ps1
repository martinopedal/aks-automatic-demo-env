#Requires -Version 7
<#
.SYNOPSIS
    Cleanly stop/deallocate the demo VM and save its specialized OS disk.
.DESCRIPTION
    Uses the same VM defaults as Connect-DemoVm.ps1. Does not run Sysprep,
    clean applications, start the VM, or delete resources. Leaves the VM
    deallocated. Requires Azure CLI login and VM/disk/snapshot write access.
    Prints phase/status messages and elapsed time. Add -Verbose for CLI
    timings and full resource IDs.
.PARAMETER PowerStateTimeoutSeconds
    Limit for each VM power-state wait, including shutdown and deallocation.
    Defaults to 1800 seconds (30 minutes). Accepts 60-86400 seconds.
    A timeout stops the script without forcing power-off.
.EXAMPLE
    .\scripts\Snapshot-DemoVm.ps1 -Subscription <subscription-id> -WhatIf
.EXAMPLE
    .\scripts\Snapshot-DemoVm.ps1 -Subscription <subscription-id>
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [string]$Subscription = $env:AZURE_SUBSCRIPTION_ID_ONLINE,
    [string]$ResourceGroup = 'rg-demo-vm-online',
    [string]$VmName = 'vm-demo-copilot',
    [string]$SnapshotName,
    [ValidateRange(60, 86400)][int]$PowerStateTimeoutSeconds = 1800
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'DemoVmDisk.Common.ps1')
$operationTimer = [Diagnostics.Stopwatch]::StartNew()
Write-DemoStatus 'Snapshot' "Preparing an OS-disk snapshot for '$VmName'..."

$context = Get-DemoDiskContext $Subscription $ResourceGroup $VmName $PowerStateTimeoutSeconds
if (-not $SnapshotName) { $SnapshotName = New-DemoDiskName 'snap-osdisk' $VmName }
Assert-DemoNameAvailable $context 'snapshot' $SnapshotName
$snapshotId = "$($context.Group['id'])/providers/Microsoft.Compute/snapshots/$SnapshotName"
Assert-DemoMutationAccess $context @($context.Vm['id'], $context.Disk['id'], $snapshotId) @(
    'Microsoft.Compute/virtualMachines/powerOff/action', 'Microsoft.Compute/virtualMachines/deallocate/action',
    'Microsoft.Compute/snapshots/write'
)
Write-Host ''
Write-Host 'Snapshot plan' -ForegroundColor Cyan
Write-Host "  VM:          $VmName"
Write-Host "  Source disk: $($context.Disk['name'])"
Write-Host "  Snapshot:    $SnapshotName"
Write-Host '  Final state: deallocated; keep the existing disk.'
Write-Verbose "Snapshot resource ID: $snapshotId"
$tags = Get-DemoProvenanceTags $context $context.Disk
if (-not $PSCmdlet.ShouldProcess("$VmName ($ResourceGroup)", "Cleanly shut down/deallocate and create snapshot '$SnapshotName'")) {
    Write-DemoStatus 'Preview' 'No changes made.'
    return
}

try {
    Assert-DemoUnchanged $context
    Stop-DemoVmCleanly $context
    Assert-DemoUnchanged $context -Deallocated
    Assert-DemoNameAvailable $context 'snapshot' $SnapshotName
    $tags['DemoCapturedUtc'] = [datetime]::UtcNow.ToString('o')
    Write-DemoStatus 'Create' "Creating full snapshot '$SnapshotName' from '$($context.Disk['name'])'..."
    Invoke-DemoAzure -Subscription $context.Subscription -NoResult -Arguments (@(
        'snapshot', 'create', '-g', $ResourceGroup, '-n', $SnapshotName,
        '--location', $context.Vm['location'], '--source', $context.Disk['id'],
        '--sku', 'Standard_LRS', '--incremental', 'false',
        '--network-access-policy', 'DenyAll', '--public-network-access', 'Disabled', '--no-wait', '--tags'
    ) + @(ConvertTo-DemoTagArguments $tags))
    $snapshot = Wait-DemoResource $context 'snapshot' $snapshotId
    Write-DemoStatus 'Verify' 'Checking snapshot source, OS/security metadata, export restrictions and VM state...'
    Assert-DemoCompatible $context $snapshot
    if ((Get-DemoValue $snapshot @('creationData', 'sourceResourceId')) -ne $context.Disk['id'] -or
        $snapshot['networkAccessPolicy'] -ne 'DenyAll' -or $snapshot['publicNetworkAccess'] -ne 'Disabled') {
        throw 'Snapshot source lineage or export restrictions do not match the request.'
    }
    Assert-DemoUnchanged $context -Deallocated
    $elapsed = $operationTimer.Elapsed.ToString('hh\:mm\:ss')
    Write-DemoStatus 'Complete' "Snapshot '$SnapshotName' is ready. VM remains deallocated. Total elapsed: $elapsed." -Success
    $result = [pscustomobject]@{
        SnapshotId = $snapshot['id']
        SnapshotName = $snapshot['name']
        SourceVmId = $context.Vm['id']
        SourceDiskId = $context.Disk['id']
        SourceDiskName = $context.Disk['name']
        CapturedUtc = $tags['DemoCapturedUtc']
        PowerState = 'PowerState/deallocated'
        Elapsed = $elapsed
    }
    Set-DemoResultDisplay $result @('SnapshotName', 'SourceDiskName', 'CapturedUtc', 'PowerState', 'Elapsed')
} catch {
    Write-DemoFailureState $context @($context.Disk['id'], $snapshotId)
    throw
}
