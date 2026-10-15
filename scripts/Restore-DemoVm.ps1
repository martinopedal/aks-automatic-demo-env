#Requires -Version 7
<#
.SYNOPSIS
    Restore the demo VM from a snapshot or retained compatible OS disk.
.DESCRIPTION
    With no source, offers a numbered picker of this VM's snapshots and
    unattached OS disks. New disks default to the currently attached OS
    disk's SKU unless -DiskSku is supplied. Existing disks keep their
    tier. Keeps the previous disk and leaves the VM
    deallocated. Do not run Terraform apply/recreate/destroy until the
    local OS-disk change is manually reconciled.
    Prints phase/status messages and elapsed time. Add -Verbose for CLI
    timings and full resource IDs.
.PARAMETER PowerStateTimeoutSeconds
    Limit for each VM power-state wait, including shutdown and deallocation.
    Defaults to 1800 seconds (30 minutes). Accepts 60-86400 seconds.
    A timeout stops the script without forcing power-off.
.EXAMPLE
    .\scripts\Restore-DemoVm.ps1 -Subscription <subscription-id>
.EXAMPLE
    .\scripts\Restore-DemoVm.ps1 -Subscription <subscription-id> -SnapshotName <name> -WhatIf
.EXAMPLE
    .\scripts\Restore-DemoVm.ps1 -Subscription <subscription-id> -OsDiskId <retained-disk-id>
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High', DefaultParameterSetName = 'Picker')]
param(
    [string]$Subscription = $env:AZURE_SUBSCRIPTION_ID_ONLINE,
    [string]$ResourceGroup = 'rg-demo-vm-online',
    [string]$VmName = 'vm-demo-copilot',
    [Parameter(Mandatory, ParameterSetName = 'Snapshot')][string]$SnapshotName,
    [Parameter(Mandatory, ParameterSetName = 'Disk')][string]$OsDiskId,
    [Parameter(ParameterSetName = 'Snapshot')]
    [Parameter(ParameterSetName = 'Picker')][string]$DiskName,
    [Parameter(ParameterSetName = 'Snapshot')]
    [Parameter(ParameterSetName = 'Picker')]
    [ValidateSet('Standard_LRS', 'StandardSSD_LRS', 'Premium_LRS')][string]$DiskSku,
    [ValidateRange(60, 86400)][int]$PowerStateTimeoutSeconds = 1800
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'DemoVmDisk.Common.ps1')
$operationTimer = [Diagnostics.Stopwatch]::StartNew()
Write-DemoStatus 'Restore' "Preparing OS-disk restore for '$VmName'..."

$context = Get-DemoDiskContext $Subscription $ResourceGroup $VmName $PowerStateTimeoutSeconds
if ($PSCmdlet.ParameterSetName -ne 'Disk') {
    if (-not $PSBoundParameters.ContainsKey('DiskSku')) {
        $DiskSku = $context.Disk['sku']['name']
        Write-DemoStatus 'Disk SKU' "New-disk default: $DiskSku (currently attached OS disk)."
    } else {
        Write-DemoStatus 'Disk SKU' "New-disk tier: $DiskSku (explicit override)."
    }
}
$snapshot = $null
if ($SnapshotName) {
    Assert-DemoResourceName $SnapshotName
    $snapshot = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('snapshot', 'show', '-g', $ResourceGroup, '-n', $SnapshotName)
} elseif ($OsDiskId) {
    if ($OsDiskId -notmatch "^/subscriptions/$($context.Subscription)/resourceGroups/[^/]+/providers/Microsoft.Compute/disks/[^/]+$") {
        throw 'OsDiskId must be a full managed-disk resource ID in the specified subscription.'
    }
} else {
    $selection = Select-DemoRestoreSource $context $DiskSku
    if ($selection.Kind -eq 'Snapshot') { $snapshot = $selection.Resource }
    else {
        if ($PSBoundParameters.ContainsKey('DiskName') -or $PSBoundParameters.ContainsKey('DiskSku')) {
            throw 'DiskName/DiskSku only apply when creating a disk from a snapshot; the selected existing disk is unchanged.'
        }
        $OsDiskId = $selection.Resource['id']
    }
}
$snapshotId = $null
if ($snapshot) {
    $snapshotId = $snapshot['id']
    Assert-DemoCompatible $context $snapshot
    Assert-DemoSku $context $DiskSku
    if (-not $DiskName) { $DiskName = New-DemoDiskName 'osdisk' $VmName }
    Assert-DemoNameAvailable $context 'disk' $DiskName
    $replacementId = "$($context.Group['id'])/providers/Microsoft.Compute/disks/$DiskName"
    Show-DemoRestoreSource $snapshot 'Snapshot' $DiskSku
    $actions = @('Microsoft.Compute/disks/write')
} else {
    $replacementId = $OsDiskId
    $disk = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('disk', 'show', '--ids', $replacementId)
    Assert-DemoCompatible $context $disk -Disk
    Show-DemoRestoreSource $disk 'Disk'
    $actions = @()
}
Assert-DemoMutationAccess $context @($context.Vm['id'], $context.Disk['id'], $replacementId) ($actions + @(
    'Microsoft.Compute/virtualMachines/powerOff/action', 'Microsoft.Compute/virtualMachines/deallocate/action',
    'Microsoft.Compute/virtualMachines/write', 'Microsoft.Compute/disks/write'
))
Write-Warning 'Local OS-disk changes create Terraform drift. Do not run apply, recreate-vm or destroy until manually reconciled. This is a procedural restriction, not a workflow lock.'
Write-Host ''
Write-Host 'Restore plan' -ForegroundColor Cyan
Write-Host "  VM:           $VmName"
Write-Host "  Current disk: $($context.Disk['name']) (retain for rollback)"
Write-Host "  Replacement:  $(($replacementId -split '/')[-1])"
Write-Host '  Final state:  deallocated; no automatic startup or deletion.'
Write-Verbose "Replacement resource ID: $replacementId"
Write-Verbose "Previous OS disk ID: $($context.Disk['id'])"
$previousTags = Get-DemoProvenanceTags $context $context.Disk
if ($snapshot) { $tags = Get-DemoProvenanceTags $context $snapshot $snapshotId }
if (-not $PSCmdlet.ShouldProcess("$VmName ($ResourceGroup)", "Replace OS disk with '$(($replacementId -split '/')[-1])', retaining '$($context.Disk['name'])'")) {
    Write-DemoStatus 'Preview' 'No changes made.'
    return
}

try {
    Assert-DemoUnchanged $context
    if ($snapshot) {
        $snapshot = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('snapshot', 'show', '--ids', $snapshotId)
        Assert-DemoCompatible $context $snapshot
        Assert-DemoNameAvailable $context 'disk' $DiskName
        Write-DemoStatus 'Create' "Creating '$DiskName' from '$($snapshot['name'])': $DiskSku, $($snapshot['diskSizeGB']) GiB..."
        # Snapshot copies inherit OS/generation/security; explicitly setting TrustedLaunch is rejected for Copy.
        $arguments = @(
            'disk', 'create', '-g', $ResourceGroup, '-n', $DiskName, '--location', $context.Vm['location'],
            '--source', $snapshotId, '--sku', $DiskSku, '--size-gb', "$($snapshot['diskSizeGB'])",
            '--network-access-policy', 'DenyAll', '--public-network-access', 'Disabled', '--no-wait'
        )
        if ($context.Disk['zones']) { $arguments += @('--zone', ($context.Disk['zones'] -join ',')) }
        Invoke-DemoAzure -Subscription $context.Subscription -NoResult -Arguments ($arguments + @('--tags') + @(ConvertTo-DemoTagArguments $tags))
        $null = Wait-DemoResource $context 'disk' $replacementId
    }
    Write-DemoStatus 'Verify' 'Checking replacement disk size, placement, inherited Trusted Launch metadata and source...'
    $disk = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('disk', 'show', '--ids', $replacementId)
    Assert-DemoCompatible $context $disk -Disk
    if ($snapshot -and (
        $disk['sku']['name'] -ne $DiskSku -or (Get-DemoValue $disk @('creationData', 'sourceResourceId')) -ne $snapshotId -or
        $disk['networkAccessPolicy'] -ne 'DenyAll' -or $disk['publicNetworkAccess'] -ne 'Disabled'
    )) { throw 'Created disk SKU, snapshot lineage or export restrictions do not match the request.' }
    Write-DemoStatus 'Verify' "Replacement '$($disk['name'])' passed compatibility checks." -Success
    Stop-DemoVmCleanly $context
    Assert-DemoUnchanged $context -Deallocated
    $previous = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('disk', 'show', '--ids', $context.Disk['id'])
    $previousTags = Get-DemoProvenanceTags $context $previous
    Write-DemoStatus 'Retain' "Recording rollback provenance on '$($previous['name'])'; no disks will be deleted..."
    Invoke-DemoAzure -Subscription $context.Subscription -NoResult -Arguments (@(
        'resource', 'tag', '--ids', $previous['id'], '--tags'
    ) + @(ConvertTo-DemoTagArguments $previousTags))
    $disk = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('disk', 'show', '--ids', $replacementId)
    Assert-DemoCompatible $context $disk -Disk
    Assert-DemoUnchanged $context -Deallocated
    Write-DemoStatus 'Swap' "Changing OS disk: '$($previous['name'])' -> '$($disk['name'])'..."
    # Detach protects this disk from a later VM deletion; it does not reconcile Terraform.
    Invoke-DemoAzure -Subscription $context.Subscription -NoResult -Arguments @(
        'vm', 'update', '-g', $ResourceGroup, '-n', $VmName, '--os-disk', $replacementId,
        '--set', 'storageProfile.osDisk.deleteOption=Detach', '--no-wait'
    )
    $vm = Wait-DemoResource $context 'vm' $context.Vm['id'] -ExpectedOsDiskId $replacementId
    Write-DemoStatus 'Verify' 'Checking OS-disk attachment, retained previous disk, VM identity/network/security and final power state...'
    $attached = Get-DemoValue $vm @('storageProfile', 'osDisk', 'managedDisk', 'id')
    if ($attached -ne $replacementId -or (Get-DemoValue $vm @('storageProfile', 'osDisk', 'name')) -ne $disk['name'] -or
        $vm['provisioningState'] -ne 'Succeeded' -or $vm['vmId'] -ne $context.Vm['vmId'] -or
        (Get-DemoPowerState $context) -ne 'PowerState/deallocated') {
        throw 'The VM OS-disk reference or final state did not match the requested swap.'
    }
    foreach ($property in @('identity', 'networkProfile', 'securityProfile', 'hardwareProfile')) {
        if (($vm[$property] | ConvertTo-Json -Depth 20 -Compress) -ne
            ($context.Vm[$property] | ConvertTo-Json -Depth 20 -Compress)) {
            throw "VM '$property' changed during the swap. Inspect Azure before proceeding."
        }
    }
    $replacement = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('disk', 'show', '--ids', $replacementId)
    if ($replacement['managedBy'] -ne $context.Vm['id'] -or $replacement['diskState'] -notin @('Attached', 'Reserved')) {
        throw 'The replacement disk does not report attachment/reservation to the target VM.'
    }
    $previous = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('disk', 'show', '--ids', $previous['id'])
    if ($previous['managedBy'] -or $previous['diskState'] -ne 'Unattached') { throw 'Previous OS disk was not retained and detached as expected.' }
    $elapsed = $operationTimer.Elapsed.ToString('hh\:mm\:ss')
    Write-DemoStatus 'Complete' "OS disk is now '$($disk['name'])'. Previous disk retained; VM remains deallocated. Total elapsed: $elapsed." -Success
    Write-Host "Rollback: .\scripts\Restore-DemoVm.ps1 -Subscription '$($context.Subscription)' -ResourceGroup '$ResourceGroup' -VmName '$VmName' -OsDiskId '$($previous['id'])'"
    $result = [pscustomobject]@{
        SnapshotId = $snapshotId
        SnapshotName = if ($snapshot) { $snapshot['name'] } else { $null }
        PreviousDiskId = $previous['id']
        PreviousDiskName = $previous['name']
        ReplacementDiskId = $replacementId
        ReplacementDiskName = $disk['name']
        DiskSku = $disk['sku']['name']
        PowerState = 'PowerState/deallocated'
        Elapsed = $elapsed
    }
    Set-DemoResultDisplay $result @('SnapshotName', 'PreviousDiskName', 'ReplacementDiskName', 'DiskSku', 'PowerState', 'Elapsed')
} catch {
    Write-DemoFailureState $context @($context.Disk['id'], $replacementId, $snapshotId)
    throw
}
