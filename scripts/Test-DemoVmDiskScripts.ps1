#Requires -Version 7
<#
.SYNOPSIS
    Run offline snapshot/restore regression tests with a mocked Azure CLI.
.DESCRIPTION
    No Azure login, network access, external test framework or cloud writes.
.EXAMPLE
    .\scripts\Test-DemoVmDiskScripts.ps1
#>
$ErrorActionPreference = 'Stop'
if (Get-Variable DemoVmDiskTest -Scope Global -ErrorAction SilentlyContinue) {
    throw 'A DemoVmDiskTest fixture is already active.'
}
$global:DemoVmDiskTest = @{}
$subscription = '00000000-0000-0000-0000-000000000001'
$groupId = "/subscriptions/$subscription/resourceGroups/rg-demo-vm-online"
$vmId = "$groupId/providers/Microsoft.Compute/virtualMachines/vm-demo-copilot"
$originalId = "$groupId/providers/Microsoft.Compute/disks/original"
$snapshotId = "$groupId/providers/Microsoft.Compute/snapshots/baseline"
. (Join-Path $PSScriptRoot 'DemoVmDisk.Common.ps1')

function Copy-Fixture($Value) { return $Value | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable }

function New-FixtureDisk([string]$Id, [string]$Sku = 'StandardSSD_LRS') {
    return @{
        id = $Id; name = ($Id -split '/')[-1]; type = 'Microsoft.Compute/disks'; location = 'swedencentral'
        provisioningState = 'Succeeded'; osType = 'Windows'; hyperVGeneration = 'V2'; diskSizeGB = 127
        sku = @{ name = $Sku }; zones = @('1'); diskState = 'Unattached'; managedBy = $null
        securityProfile = @{ securityType = 'TrustedLaunch' }
        encryption = @{ type = 'EncryptionAtRestWithPlatformKey' }
        tags = @{}; timeCreated = '2026-10-12T18:00:00Z'
        networkAccessPolicy = 'DenyAll'; publicNetworkAccess = 'Disabled'
    }
}

function Reset-Fixture {
    $global:DemoVmDiskTest.requests = [System.Collections.Generic.List[object]]::new()
    $global:DemoVmDiskTest.power = 'PowerState/running'
    $global:DemoVmDiskTest.failure = ''
    $global:DemoVmDiskTest.locks = @()
    $global:DemoVmDiskTest.vmReads = 0
    $global:DemoVmDiskTest.concurrentAt = 0
    $global:DemoVmDiskTest.polls = 0
    $global:DemoVmDiskTest.creatingPolls = 0
    $global:DemoVmDiskTest.premium = 'True'
    $global:DemoVmDiskTest.dropSecurity = $false
    $global:DemoVmDiskTest.vm = @{
        id = $vmId; vmId = 'instance-1'; name = 'vm-demo-copilot'; location = 'swedencentral'; provisioningState = 'Succeeded'
        storageProfile = @{ osDisk = @{ name = 'original'; managedDisk = @{ id = $originalId }; createOption = 'FromImage' } }
        hardwareProfile = @{ vmSize = 'Standard_D4s_v5' }
        securityProfile = @{ securityType = 'TrustedLaunch'; uefiSettings = @{ secureBootEnabled = $true; vTpmEnabled = $true } }
        networkProfile = @{ networkInterfaces = @(@{ id = "$groupId/providers/Microsoft.Network/networkInterfaces/demo" }) }
        identity = @{ type = 'SystemAssigned'; principalId = 'identity-1' }
    }
    $original = New-FixtureDisk $originalId
    $original['diskState'] = 'Attached'
    $original['managedBy'] = $vmId
    $global:DemoVmDiskTest.disks = @{ $originalId = $original }
    $snap = New-FixtureDisk $snapshotId 'Standard_LRS'
    $snap['type'] = 'Microsoft.Compute/snapshots'
    $snap['creationData'] = @{ sourceResourceId = $originalId }
    $snap['tags'] = @{ DemoSourceVmId = $vmId; DemoSourceVmInstanceId = 'instance-1' }
    $global:DemoVmDiskTest.snapshots = @{ $snapshotId = $snap }
}

function Get-FixtureArgument([string[]]$Arguments, [string]$Key) {
    $index = [array]::IndexOf($Arguments, $Key)
    if ($index -ge 0) { return $Arguments[$index + 1] }
    return $null
}

function Get-FixtureTags([string[]]$Arguments) {
    $tags = @{}
    $index = [array]::IndexOf($Arguments, '--tags')
    if ($index -ge 0) {
        for ($i = $index + 1; $i -lt $Arguments.Count -and $Arguments[$i] -notlike '--*'; $i++) {
            $key, $value = $Arguments[$i] -split '=', 2
            $tags[$key] = $value
        }
    }
    return $tags
}

function az {
    $arguments = @($args | ForEach-Object { "$_" })
    $global:DemoVmDiskTest.requests.Add($arguments)
    $global:LASTEXITCODE = 0
    $operation = $arguments[0..1] -join ' '
    if ($operation -eq $global:DemoVmDiskTest.failure) {
        $global:LASTEXITCODE = 1
        return 'ERROR: injected failure'
    }
    $name = Get-FixtureArgument $arguments '-n'
    $id = Get-FixtureArgument $arguments '--ids'
    $result = $null
    switch ($operation) {
        'account show' { $result = @{ id = $subscription; state = 'Enabled' } }
        'group show' { $result = @{ id = $groupId; tags = @{} } }
        'vm show' {
            $global:DemoVmDiskTest.vmReads++
            if ($global:DemoVmDiskTest.vmReads -eq $global:DemoVmDiskTest.concurrentAt) { $global:DemoVmDiskTest.vm['storageProfile']['osDisk']['managedDisk']['id'] = "$groupId/providers/Microsoft.Compute/disks/concurrent" }
            $result = $global:DemoVmDiskTest.vm
        }
        'vm get-instance-view' { $result = @(@{ code = 'ProvisioningState/succeeded' }, @{ code = $global:DemoVmDiskTest.power }) }
        'vm stop' { $global:DemoVmDiskTest.power = 'PowerState/stopped' }
        'vm deallocate' { $global:DemoVmDiskTest.power = 'PowerState/deallocated' }
        'vm list-skus' { $result = @(@{ name = 'Standard_D4s_v5'; resourceType = 'virtualMachines'; capabilities = @(@{ name = 'PremiumIO'; value = $global:DemoVmDiskTest.premium }) }) }
        'lock list' { $result = $global:DemoVmDiskTest.locks }
        'rest --method' { $result = @{ value = @(@{ actions = @('*'); notActions = @() }) } }
        'snapshot list' { $result = @($global:DemoVmDiskTest.snapshots.Values) }
        'disk list' { $result = @($global:DemoVmDiskTest.disks.Values) }
        'snapshot show' {
            if (-not $id) { $id = "$groupId/providers/Microsoft.Compute/snapshots/$name" }
            if (-not $global:DemoVmDiskTest.snapshots.ContainsKey($id)) { $global:LASTEXITCODE = 1; return 'ERROR: ResourceNotFound' }
            $result = Copy-Fixture $global:DemoVmDiskTest.snapshots[$id]
            if ($global:DemoVmDiskTest.creatingPolls -gt 0) {
                $global:DemoVmDiskTest.creatingPolls--
                $result['provisioningState'] = 'Creating'
            }
        }
        'disk show' {
            if (-not $global:DemoVmDiskTest.disks.ContainsKey($id)) { $global:LASTEXITCODE = 1; return 'ERROR: ResourceNotFound' }
            $result = $global:DemoVmDiskTest.disks[$id]
        }
        'snapshot create' {
            $newId = "$groupId/providers/Microsoft.Compute/snapshots/$name"
            $new = New-FixtureDisk $newId 'Standard_LRS'
            $new['type'] = 'Microsoft.Compute/snapshots'
            $new['creationData'] = @{ sourceResourceId = (Get-FixtureArgument $arguments '--source') }
            $new['tags'] = Get-FixtureTags $arguments
            $global:DemoVmDiskTest.snapshots[$newId] = $new
        }
        'disk create' {
            if ($arguments -contains '--security-type') {
                $global:LASTEXITCODE = 1
                return "ERROR: (BadRequest) Security Type 'TrustedLaunch' is not supported for CreateOption 'Copy'."
            }
            $newId = "$groupId/providers/Microsoft.Compute/disks/$name"
            $new = New-FixtureDisk $newId (Get-FixtureArgument $arguments '--sku')
            $sourceId = Get-FixtureArgument $arguments '--source'
            $source = $global:DemoVmDiskTest.snapshots[$sourceId]
            foreach ($property in @('osType', 'hyperVGeneration', 'securityProfile', 'encryption')) {
                $new[$property] = Copy-Fixture $source[$property]
            }
            if ($global:DemoVmDiskTest.dropSecurity) { $new['securityProfile'] = $null }
            $new['creationData'] = @{ sourceResourceId = $sourceId }
            $new['tags'] = Get-FixtureTags $arguments
            $global:DemoVmDiskTest.disks[$newId] = $new
        }
        'resource tag' { $global:DemoVmDiskTest.disks[$id]['tags'] = Get-FixtureTags $arguments }
        'vm update' {
            $replacement = Get-FixtureArgument $arguments '--os-disk'
            $previous = $global:DemoVmDiskTest.vm['storageProfile']['osDisk']['managedDisk']['id']
            $global:DemoVmDiskTest.disks[$previous]['managedBy'] = $null
            $global:DemoVmDiskTest.disks[$previous]['diskState'] = 'Unattached'
            $global:DemoVmDiskTest.disks[$replacement]['managedBy'] = $vmId
            $global:DemoVmDiskTest.disks[$replacement]['diskState'] = 'Reserved'
            $global:DemoVmDiskTest.vm['storageProfile']['osDisk']['managedDisk']['id'] = $replacement
            $global:DemoVmDiskTest.vm['storageProfile']['osDisk']['name'] = $global:DemoVmDiskTest.disks[$replacement]['name']
        }
        default { throw "Unexpected mocked CLI operation: $operation" }
    }
    if ((Get-FixtureArgument $arguments '-o') -ne 'none') {
        if ($null -eq $result) { throw "Mock returned no JSON: $operation" }
        return ConvertTo-Json -InputObject $result -Depth 30 -Compress
    }
}

function Start-Sleep { param($Seconds); $global:DemoVmDiskTest.polls++ }
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    try { & $Action | Out-Null } catch {
        if ("$_" -notmatch $Pattern) { throw "Expected '$Pattern', received: $_" }
        return
    }
    throw "Expected failure matching '$Pattern'."
}
function Get-Mutations {
    return ,@($global:DemoVmDiskTest.requests | Where-Object { ($_[0..1] -join ' ') -in @('vm stop', 'vm deallocate', 'snapshot create', 'disk create', 'resource tag', 'vm update') })
}
function Assert-NoUnsafeCommands {
    foreach ($request in $global:DemoVmDiskTest.requests) {
        Assert-True ($request -notcontains '--skip-shutdown') 'Force power-off was requested.'
        Assert-True ($request[1] -notin @('delete', 'start')) 'Automatic deletion/start was requested.'
        Assert-True ((Get-FixtureArgument $request '--subscription') -eq $subscription) 'A command omitted the explicit subscription.'
    }
}

$tests = [ordered]@{
    'Names use UTC year-day-month and reject invalid names' = {
        $name = New-DemoDiskName 'osdisk' 'vm-demo-copilot'
        Assert-True ($name -match "^osdisk-vm-demo-copilot-$([datetime]::UtcNow.ToString('yyyyddMM', [Globalization.CultureInfo]::InvariantCulture))-\d{6}$") 'Unexpected UTC name.'
        Assert-True (([datetimeoffset]'2026-10-12T19:30:00Z').UtcDateTime.ToString('yyyyddMM-HHmmss') -eq '20261210-193000') 'Timestamp format changed.'
        Assert-Throws { Assert-DemoResourceName 'bad/name' } 'Invalid'
        Assert-Throws { Assert-DemoResourceName ('a' * 81) } 'Invalid'
    }
    'Snapshot WhatIf and VM defaults perform no writes' = {
        & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -WhatIf | Out-Null
        Assert-True ((Get-Mutations).Count -eq 0) 'WhatIf mutated Azure.'
        $show = @($global:DemoVmDiskTest.requests | Where-Object { ($_[0..1] -join ' ') -eq 'vm show' })[0]
        Assert-True ((Get-FixtureArgument $show '-n') -eq 'vm-demo-copilot') 'VM default changed.'
        Assert-True ((Get-FixtureArgument $show '-g') -eq 'rg-demo-vm-online') 'Resource-group default changed.'
    }
    'Missing subscription, login and unknown power state fail' = {
        Assert-Throws { & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription '' -WhatIf } 'Set -Subscription'
        $global:DemoVmDiskTest.failure = 'account show'
        Assert-Throws { & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -WhatIf } 'Azure CLI failed'
        $global:DemoVmDiskTest.failure = ''
        $global:DemoVmDiskTest.power = 'PowerState/unknown'
        Assert-Throws { & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -WhatIf } 'Unexpected VM power state'
        Assert-True ((Get-Mutations).Count -eq 0) 'Preflight failure mutated Azure.'
    }
    'Subscription environment default is honored and restored' = {
        $previous = $env:AZURE_SUBSCRIPTION_ID_ONLINE
        try {
            $env:AZURE_SUBSCRIPTION_ID_ONLINE = $subscription
            & "$PSScriptRoot\Snapshot-DemoVm.ps1" -WhatIf | Out-Null
            Assert-True ((Get-Mutations).Count -eq 0) 'Environment-default WhatIf mutated Azure.'
        } finally {
            $env:AZURE_SUBSCRIPTION_ID_ONLINE = $previous
        }
    }
    'Clean shutdown precedes deallocation and snapshot' = {
        $result = & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -SnapshotName saved -Confirm:$false
        $operations = @((Get-Mutations) | ForEach-Object { $_[0..1] -join ' ' })
        Assert-True (($operations -join ',') -eq 'vm stop,vm deallocate,snapshot create') 'Snapshot sequence is unsafe.'
        Assert-True ($result.PowerState -eq 'PowerState/deallocated') 'Snapshot left VM running.'
        Assert-True ($global:DemoVmDiskTest.snapshots[$result.SnapshotId]['tags']['DemoSourceVmInstanceId'] -eq 'instance-1') 'Missing VM-instance provenance.'
    }
    'Already deallocated snapshot never starts or stops VM' = {
        $global:DemoVmDiskTest.power = 'PowerState/deallocated'
        $null = & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -SnapshotName saved -Confirm:$false
        Assert-True ((Get-Mutations).Count -eq 1) 'Deallocated VM was unnecessarily changed.'
    }
    'Snapshot creation polls and timeout is explicit' = {
        $global:DemoVmDiskTest.creatingPolls = 1
        $null = & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -SnapshotName saved -Confirm:$false
        Assert-True ($global:DemoVmDiskTest.polls -eq 1) 'Provisioning was not polled.'
        $context = Get-DemoDiskContext $subscription 'rg-demo-vm-online' 'vm-demo-copilot'
        $global:DemoVmDiskTest.creatingPolls = 10
        Assert-Throws { Wait-DemoResource $context 'snapshot' $snapshotId -TimeoutSeconds 0 } 'Timed out'
    }
    'Collision, ReadOnly lock and shutdown failure prevent capture' = {
        Assert-Throws { & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -Confirm:$false } 'already exists'
        $global:DemoVmDiskTest.locks = @(@{ id = "$groupId/providers/Microsoft.Authorization/locks/frozen"; name = 'frozen'; level = 'ReadOnly' })
        Assert-Throws { & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -SnapshotName saved -Confirm:$false } 'ReadOnly lock'
        $global:DemoVmDiskTest.locks = @()
        $global:DemoVmDiskTest.failure = 'vm stop'
        Assert-Throws { & "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -SnapshotName saved -Confirm:$false } 'Azure CLI failed'
        Assert-True (-not $global:DemoVmDiskTest.snapshots.ContainsKey("$groupId/providers/Microsoft.Compute/snapshots/saved")) 'Failed shutdown still captured.'
    }
    'Restore WhatIf bypasses picker and performs no writes' = {
        & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -WhatIf | Out-Null
        Assert-True ((Get-Mutations).Count -eq 0) 'Restore WhatIf mutated Azure.'
    }
    'Snapshot restore matches attached SSD regardless of snapshot storage tier' = {
        $result = & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -DiskName restored -Confirm:$false
        Assert-True ($result.DiskSku -eq 'StandardSSD_LRS') 'Restore did not match the attached SSD disk.'
        Assert-True ($global:DemoVmDiskTest.disks[$originalId]['diskState'] -eq 'Unattached') 'Old disk was not retained.'
        Assert-True ($global:DemoVmDiskTest.disks[$originalId]['tags']['DemoSourceVmId'] -eq $vmId) 'Rollback disk is missing provenance.'
        Assert-True ($result.PowerState -eq 'PowerState/deallocated') 'Restore did not remain deallocated.'
        $create = @($global:DemoVmDiskTest.requests | Where-Object { ($_[0..1] -join ' ') -eq 'disk create' })[0]
        Assert-True ((Get-FixtureArgument $create '--zone') -eq '1') 'Disk zone was lost.'
        Assert-True ((Get-FixtureArgument $create '--size-gb') -eq '127') 'Disk size was lost.'
        Assert-True ($create -notcontains '--security-type') 'Snapshot copy supplied the unsupported security-type override.'
        Assert-True ($global:DemoVmDiskTest.disks[$result.ReplacementDiskId]['securityProfile']['securityType'] -eq 'TrustedLaunch') 'Inherited Trusted Launch was lost.'
        Assert-True ($global:DemoVmDiskTest.vm['securityProfile']['uefiSettings']['vTpmEnabled']) 'vTPM was lost.'
        $operations = @((Get-Mutations) | ForEach-Object { $_[0..1] -join ' ' })
        Assert-True (($operations -join ',') -eq 'disk create,vm stop,vm deallocate,resource tag,vm update') 'Restore sequence is unsafe.'
    }
    'Copy rejects an explicit Trusted Launch override like Azure' = {
        Assert-Throws {
            Invoke-DemoAzure -Subscription $subscription -NoResult -Arguments @(
                'disk', 'create', '-g', 'rg-demo-vm-online', '-n', 'bad-copy',
                '--source', $snapshotId, '--security-type', 'TrustedLaunch'
            )
        } "not supported for CreateOption 'Copy'"
        Assert-True (-not $global:DemoVmDiskTest.disks.ContainsKey("$groupId/providers/Microsoft.Compute/disks/bad-copy")) 'Rejected copy created a disk.'
    }
    'Missing inherited security prevents shutdown and swap' = {
        $global:DemoVmDiskTest.dropSecurity = $true
        Assert-Throws {
            & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -DiskName restored -Confirm:$false
        } 'preserve Trusted Launch'
        Assert-True ($global:DemoVmDiskTest.power -eq 'PowerState/running') 'An incompatible copy shut down the VM.'
        Assert-True ((Get-Mutations).Count -eq 1) 'The incompatible copy performed mutations beyond disk creation.'
        Assert-True (@($global:DemoVmDiskTest.requests | Where-Object { ($_[0..1] -join ' ') -eq 'vm update' }).Count -eq 0) 'An incompatible copy was swapped in.'
    }
    'New-disk defaults match all supported attached disk SKUs' = {
        foreach ($sku in @('Standard_LRS', 'StandardSSD_LRS', 'Premium_LRS')) {
            Reset-Fixture
            $global:DemoVmDiskTest.disks[$originalId]['sku']['name'] = $sku
            $result = & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -DiskName restored -Confirm:$false
            Assert-True ($result.DiskSku -eq $sku) "Restore did not default to attached disk SKU '$sku'."
            $create = @($global:DemoVmDiskTest.requests | Where-Object { ($_[0..1] -join ' ') -eq 'disk create' })[0]
            Assert-True ((Get-FixtureArgument $create '--sku') -eq $sku) 'Disk creation did not receive the resolved SKU.'
        }
    }
    'Explicit SKU overrides take precedence over attached Premium tier' = {
        foreach ($sku in @('Standard_LRS', 'StandardSSD_LRS', 'Premium_LRS')) {
            Reset-Fixture
            $global:DemoVmDiskTest.disks[$originalId]['sku']['name'] = 'Premium_LRS'
            $result = & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -DiskName restored -DiskSku $sku -Confirm:$false
            Assert-True ($result.DiskSku -eq $sku) 'Explicit SKU override was ignored.'
        }
    }
    'Premium default appears in snapshot preview without writes' = {
        $global:DemoVmDiskTest.disks[$originalId]['sku']['name'] = 'Premium_LRS'
        $messages = & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -WhatIf 6>&1 | Out-String
        Assert-True ($messages -match 'New-disk default: Premium_LRS' -and $messages -match 'new disk tier: Premium_LRS') 'Premium default was not shown before confirmation.'
        Assert-True ((Get-Mutations).Count -eq 0) 'Premium preview mutated Azure.'
    }
    'Unsupported tier and non-Premium VM fail before shutdown' = {
        $global:DemoVmDiskTest.premium = 'False'
        Assert-Throws { & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -DiskSku Premium_LRS -Confirm:$false } 'Premium Storage support'
        Assert-Throws { & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -DiskSku UltraSSD_LRS -WhatIf } 'ValidateSet'
        Assert-True ((Get-Mutations).Count -eq 0) 'Unsupported tier mutated Azure.'
    }
    'Existing HDD rollback changes no tier and creates no disk' = {
        $global:DemoVmDiskTest.disks[$originalId]['sku']['name'] = 'Premium_LRS'
        $id = "$groupId/providers/Microsoft.Compute/disks/rollback"
        $global:DemoVmDiskTest.disks[$id] = New-FixtureDisk $id 'Standard_LRS'
        $result = & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -OsDiskId $id -Confirm:$false
        Assert-True ($result.DiskSku -eq 'Standard_LRS') 'Existing disk tier was changed.'
        Assert-True (@($global:DemoVmDiskTest.requests | Where-Object { ($_[0..1] -join ' ') -eq 'disk create' }).Count -eq 0) 'Rollback created a disk.'
        Assert-Throws { & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -OsDiskId $originalId -DiskSku StandardSSD_LRS -WhatIf } 'Parameter set'
    }
    'Incompatible size, security, zone, encryption and attachment fail before writes' = {
        foreach ($property in @('size', 'security', 'zone', 'encryption', 'attachment')) {
            Reset-Fixture
            $id = "$groupId/providers/Microsoft.Compute/disks/bad"
            $disk = New-FixtureDisk $id
            switch ($property) {
                size { $disk['diskSizeGB'] = 128 }
                security { $disk['securityProfile']['securityType'] = 'Standard' }
                zone { $disk['zones'] = @('2') }
                encryption { $disk['encryption']['type'] = 'EncryptionAtRestWithCustomerKey' }
                attachment { $disk['managedBy'] = $vmId }
            }
            $global:DemoVmDiskTest.disks[$id] = $disk
            Assert-Throws { & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -OsDiskId $id -Confirm:$false } 'matching sizes|Trusted Launch|placement|encryption|unattached'
            Assert-True ((Get-Mutations).Count -eq 0) 'Incompatible restore mutated Azure.'
        }
    }
    'Concurrent VM change prevents mutation' = {
        $global:DemoVmDiskTest.concurrentAt = 2
        Assert-Throws { & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -DiskName restored -Confirm:$false } 'changed or recreated'
        Assert-True ((Get-Mutations).Count -eq 0) 'Concurrent change was not stopped.'
    }
    'Failed swap retains both disks and does not claim success' = {
        $global:DemoVmDiskTest.failure = 'vm update'
        Assert-Throws { & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -SnapshotName baseline -DiskName restored -Confirm:$false } 'Azure CLI failed'
        Assert-True ($global:DemoVmDiskTest.disks.ContainsKey("$groupId/providers/Microsoft.Compute/disks/restored")) 'Failure deleted the replacement.'
        Assert-True ($global:DemoVmDiskTest.vm['storageProfile']['osDisk']['managedDisk']['id'] -eq $originalId) 'Failed swap changed attachment.'
    }
    'Picker includes other snapshots and disks but excludes unrelated and attached resources' = {
        $otherSnapshot = Copy-Fixture $global:DemoVmDiskTest.snapshots[$snapshotId]
        $otherSnapshot['id'] = "$groupId/providers/Microsoft.Compute/snapshots/other"
        $otherSnapshot['name'] = 'other'
        $otherSnapshot['timeCreated'] = '2026-09-30T18:00:00Z'
        $global:DemoVmDiskTest.snapshots[$otherSnapshot['id']] = $otherSnapshot
        $id = "$groupId/providers/Microsoft.Compute/disks/retained"
        $global:DemoVmDiskTest.disks[$id] = New-FixtureDisk $id
        $global:DemoVmDiskTest.disks[$id]['creationData'] = @{ sourceResourceId = $otherSnapshot['id'] }
        $unrelated = "$groupId/providers/Microsoft.Compute/disks/unrelated"
        $global:DemoVmDiskTest.disks[$unrelated] = New-FixtureDisk $unrelated
        $attached = "$groupId/providers/Microsoft.Compute/disks/attached"
        $global:DemoVmDiskTest.disks[$attached] = New-FixtureDisk $attached
        $global:DemoVmDiskTest.disks[$attached]['creationData'] = @{ sourceResourceId = $snapshotId }
        $global:DemoVmDiskTest.disks[$attached]['managedBy'] = $vmId
        $conflicting = "$groupId/providers/Microsoft.Compute/disks/other-vm"
        $global:DemoVmDiskTest.disks[$conflicting] = New-FixtureDisk $conflicting
        $global:DemoVmDiskTest.disks[$conflicting]['creationData'] = @{ sourceResourceId = $snapshotId }
        $global:DemoVmDiskTest.disks[$conflicting]['tags'] = @{ DemoSourceVmId = "$vmId-other" }
        $dataDisk = "$groupId/providers/Microsoft.Compute/disks/data"
        $global:DemoVmDiskTest.disks[$dataDisk] = New-FixtureDisk $dataDisk
        $global:DemoVmDiskTest.disks[$dataDisk]['osType'] = $null
        $global:DemoVmDiskTest.disks[$dataDisk]['creationData'] = @{ sourceResourceId = $snapshotId }
        $context = Get-DemoDiskContext $subscription 'rg-demo-vm-online' 'vm-demo-copilot'
        $entries = @(Get-DemoRestoreCandidates $context)
        Assert-True ($entries.Count -eq 3) 'Picker candidates were incorrectly filtered.'
        Assert-True ($entries[-1].Resource['id'] -eq $otherSnapshot['id']) 'Picker sorted names rather than timestamps.'
        Assert-True (@($entries | Where-Object { $_.Resource['id'] -eq $id }).Count -eq 1) 'A disk from a different snapshot was hidden.'
    }
    'Picker traces the current untagged restored disk back to saved snapshots' = {
        $global:DemoVmDiskTest.disks[$originalId]['creationData'] = @{ sourceResourceId = $snapshotId }
        $global:DemoVmDiskTest.snapshots[$snapshotId]['tags'] = @{}
        $global:DemoVmDiskTest.snapshots[$snapshotId]['creationData']['sourceResourceId'] = "$groupId/providers/Microsoft.Compute/disks/deleted-original"
        $context = Get-DemoDiskContext $subscription 'rg-demo-vm-online' 'vm-demo-copilot'
        Assert-True (@(Get-DemoRestoreCandidates $context).Count -eq 1) 'Verified current-disk lineage was lost.'
    }
    'Picker cancellation and invalid choice are read-only' = {
        $context = Get-DemoDiskContext $subscription 'rg-demo-vm-online' 'vm-demo-copilot'
        function Read-DemoRestoreChoice { return '' }
        Assert-Throws { Select-DemoRestoreSource $context 'StandardSSD_LRS' } 'cancelled'
        function Read-DemoRestoreChoice { return '999' }
        Assert-Throws { Select-DemoRestoreSource $context 'StandardSSD_LRS' } 'Invalid'
        Assert-True ((Get-Mutations).Count -eq 0) 'Picker cancellation mutated Azure.'
    }
    'Picker table and selected-source summary hide resource IDs' = {
        $context = Get-DemoDiskContext $subscription 'rg-demo-vm-online' 'vm-demo-copilot'
        $entries = @(Get-DemoRestoreCandidates $context)
        $table = Show-DemoRestoreCandidates $entries 'StandardSSD_LRS' 6>&1 | Out-String
        Assert-True ($table -match 'Type' -and $table -match 'Created \(UTC\)' -and $table -match 'Disk tier') 'Picker table is missing useful headings.'
        Assert-True ($table -match 'baseline' -and $table -match 'StandardSSD_LRS') 'Picker omitted the resource name/new disk tier.'
        Assert-True ($table -notmatch '/subscriptions/') 'Picker still contains wrapping resource IDs.'
        $summary = Show-DemoRestoreSource $entries[0].Resource 'Snapshot' 'StandardSSD_LRS' 6>&1 | Out-String
        Assert-True ($summary -match 'Source: original' -and $summary -match 'Snapshot storage: Standard_LRS') 'Selected-source details are missing.'
        Assert-True ($summary -notmatch '/subscriptions/') 'Selected-source summary exposes full IDs by default.'
        Assert-True ((Get-Mutations).Count -eq 0) 'Displaying the picker mutated Azure.'
    }
    'Picker numbering returns the displayed resource' = {
        $second = Copy-Fixture $global:DemoVmDiskTest.snapshots[$snapshotId]
        $second['id'] = "$groupId/providers/Microsoft.Compute/snapshots/older"
        $second['name'] = 'older'
        $second['timeCreated'] = '2026-09-30T18:00:00Z'
        $global:DemoVmDiskTest.snapshots[$second['id']] = $second
        $context = Get-DemoDiskContext $subscription 'rg-demo-vm-online' 'vm-demo-copilot'
        function Read-DemoRestoreChoice { return '2' }
        $selection = Select-DemoRestoreSource $context 'StandardSSD_LRS'
        Assert-True ($selection.Resource['id'] -eq $second['id']) 'Picker number mapped to the wrong resource.'
    }
    'Status and compact result preserve structured IDs without pipeline noise' = {
        $result = @(& "$PSScriptRoot\Snapshot-DemoVm.ps1" -Subscription $subscription -SnapshotName saved -Confirm:$false)
        Assert-True ($result.Count -eq 1) 'Status messages leaked into the data pipeline.'
        Assert-True ($result[0].SnapshotId -match '/subscriptions/' -and $result[0].Elapsed) 'Structured ID or elapsed result is missing.'
        $display = $result[0] | Format-List | Out-String
        Assert-True ($display -match 'SnapshotName' -and $display -match 'Elapsed') 'Compact result omitted useful fields.'
        Assert-True ($display -notmatch '/subscriptions/') 'Compact result still displays full resource IDs.'
    }
    'Wait reports state and elapsed time, then clears progress on timeout' = {
        $global:DemoVmDiskTest.progress = [System.Collections.Generic.List[object]]::new()
        function Write-Progress {
            param($Id, $Activity, $Status, [switch]$Completed)
            $global:DemoVmDiskTest.progress.Add(@{ Id = $Id; Activity = $Activity; Status = $Status; Completed = $Completed.IsPresent })
        }
        $context = Get-DemoDiskContext $subscription 'rg-demo-vm-online' 'vm-demo-copilot'
        $global:DemoVmDiskTest.creatingPolls = 10
        $messages = & {
            Assert-Throws { Wait-DemoResource $context 'snapshot' $snapshotId -TimeoutSeconds 0 } 'Timed out'
        } 6>&1 | Out-String
        Assert-True ($messages -match 'Creating \| elapsed') 'Waiting had no state/elapsed status.'
        Assert-True ($global:DemoVmDiskTest.progress[-1]['Completed']) 'Failed wait left its progress indicator open.'
    }
    'Picker reports an empty inventory instead of defaulting' = {
        $global:DemoVmDiskTest.snapshots = @{}
        $context = Get-DemoDiskContext $subscription 'rg-demo-vm-online' 'vm-demo-copilot'
        Assert-Throws { Select-DemoRestoreSource $context 'StandardSSD_LRS' } 'No compatible'
        Assert-True ((Get-Mutations).Count -eq 0) 'Empty inventory mutated Azure.'
    }
    'Noninteractive picker refuses to select automatically' = {
        if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) {
            Assert-Throws { & "$PSScriptRoot\Restore-DemoVm.ps1" -Subscription $subscription -WhatIf } 'Interactive selection is unavailable'
            Assert-True ((Get-Mutations).Count -eq 0) 'Noninteractive picker mutated Azure.'
        }
    }
}

$passed = 0
try {
    foreach ($test in $tests.GetEnumerator()) {
        Reset-Fixture
        try {
            & $test.Value | Out-Null
            Assert-NoUnsafeCommands
            $passed++
            Write-Host "PASS $($test.Key)"
        } catch {
            throw "FAIL $($test.Key): $_`n$($_.ScriptStackTrace)"
        }
    }
} finally {
    Remove-Variable DemoVmDiskTest -Scope Global
}
"Passed $passed offline regression scenarios."
