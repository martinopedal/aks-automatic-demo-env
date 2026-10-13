#Requires -Version 7

function Write-DemoStatus {
    param([string]$Phase, [string]$Message, [switch]$Success)
    $time = [datetime]::UtcNow.ToString('HH:mm:ss', [Globalization.CultureInfo]::InvariantCulture)
    $color = if ($Success) { 'Green' } else { 'Cyan' }
    Write-Host "[$time UTC] [$Phase] $Message" -ForegroundColor $color
}

function Set-DemoResultDisplay {
    param([psobject]$Result, [string[]]$Properties)
    $display = [Management.Automation.PSPropertySet]::new('DefaultDisplayPropertySet', $Properties)
    $Result | Add-Member -MemberType MemberSet -Name PSStandardMembers -Value @($display)
    return $Result
}

function Invoke-DemoAzure {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Subscription,
        [switch]$NoResult,
        [switch]$RetryConflict
    )
    $format = if ($NoResult) { 'none' } else { 'json' }
    $attempts = if ($RetryConflict) { 3 } else { 1 }
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        $timer = [Diagnostics.Stopwatch]::StartNew()
        Write-Verbose "Azure CLI: $($Arguments[0..1] -join ' ') (attempt $attempt/$attempts)"
        $activity = "Azure CLI: $($Arguments[0..1] -join ' ')"
        try {
            Write-Progress -Id 43 -Activity $activity -Status 'Waiting for Azure CLI response...'
            $output = & az @Arguments --subscription $Subscription --only-show-errors -o $format 2>&1
            $code = $LASTEXITCODE
        } finally {
            Write-Progress -Id 43 -Activity $activity -Completed
        }
        $text = ($output | ForEach-Object { "$_" }) -join "`n"
        Write-Verbose "Azure CLI finished in $([math]::Round($timer.Elapsed.TotalSeconds, 1))s; exit code: $code."
        if ($code -eq 0) {
            if ($NoResult) { return }
            if ([string]::IsNullOrWhiteSpace($text)) { throw "Azure CLI returned no JSON for '$($Arguments[0..1] -join ' ')'." }
            $jsonOptions = @{ InputObject = $text; AsHashtable = $true; ErrorAction = 'Stop' }
            if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $jsonOptions.DateKind = 'String' }
            return ConvertFrom-Json @jsonOptions
        }
        if ($RetryConflict -and $attempt -lt $attempts -and $text -match 'AnotherOperationInProgress|RetryableError|RetryableOperationError') {
            Write-Warning "Azure operation conflict; retry $attempt of $($attempts - 1)."
            Start-Sleep -Seconds 5
            continue
        }
        throw "Azure CLI failed (exit $code): $text"
    }
}

function Get-DemoValue {
    param($Object, [string[]]$Path)
    foreach ($key in $Path) {
        if ($null -eq $Object -or $Object -isnot [System.Collections.IDictionary]) { return $null }
        $Object = $Object[$key]
    }
    return $Object
}

function New-DemoDiskName {
    param([string]$Prefix, [string]$VmName)
    return "$Prefix-$VmName-$([datetime]::UtcNow.ToString('yyyyddMM-HHmmss', [Globalization.CultureInfo]::InvariantCulture))"
}

function Assert-DemoResourceName {
    param([string]$Name)
    if ($Name -notmatch '^[a-zA-Z0-9](?:[a-zA-Z0-9_.-]{0,78}[a-zA-Z0-9_])?$') {
        throw "Invalid disk/snapshot name '$Name'. Use 1-80 letters, digits, underscores, periods or hyphens, starting with a letter/digit and ending with a letter/digit/underscore."
    }
}

function Get-DemoVm {
    param($Context)
    return Invoke-DemoAzure -Subscription $Context.Subscription -Arguments @(
        'vm', 'show', '-g', $Context.ResourceGroup, '-n', $Context.VmName
    )
}

function Get-DemoPowerState {
    param($Context)
    $statuses = Invoke-DemoAzure -Subscription $Context.Subscription -Arguments @(
        'vm', 'get-instance-view', '-g', $Context.ResourceGroup, '-n', $Context.VmName,
        '--query', 'instanceView.statuses'
    )
    $powers = @($statuses | Where-Object { $_['code'] -like 'PowerState/*' })
    if ($powers.Count -ne 1) { throw 'Azure did not return exactly one VM power state.' }
    return $powers[0]['code']
}

function Wait-DemoPowerState {
    param($Context, [string[]]$AllowedStates, [int]$TimeoutSeconds = 300)
    $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $lastState = ''
    $lastReport = [datetime]::MinValue
    $activity = "Waiting for $($Context.VmName) power state"
    try {
        do {
            $state = Get-DemoPowerState $Context
            if ($state -in $AllowedStates) { return $state }
            if ($state -notin @('PowerState/running', 'PowerState/stopped', 'PowerState/deallocated',
                'PowerState/starting', 'PowerState/stopping', 'PowerState/deallocating')) {
                throw "Unexpected VM power state '$state'; expected $($AllowedStates -join ', ')."
            }
            $elapsed = $timer.Elapsed.ToString('hh\:mm\:ss')
            $message = "$($state -replace '^PowerState/', '') | elapsed $elapsed | timeout ${TimeoutSeconds}s"
            Write-Progress -Id 41 -Activity $activity -Status $message
            if ($state -ne $lastState -or ([datetime]::UtcNow - $lastReport).TotalSeconds -ge 15) {
                Write-DemoStatus 'Wait' "$($Context.VmName): $message."
                $lastReport = [datetime]::UtcNow
                $lastState = $state
            }
            Start-Sleep -Seconds 5
        } while ([datetime]::UtcNow -lt $deadline)
        throw "Timed out waiting for VM power state. Last state: $state."
    } finally {
        Write-Progress -Id 41 -Activity $activity -Completed
    }
}

function Stop-DemoVmCleanly {
    param($Context)
    $state = Wait-DemoPowerState $Context @('PowerState/running', 'PowerState/stopped', 'PowerState/deallocated')
    Write-DemoStatus 'Prepare' "Current VM power state: $($state -replace '^PowerState/', '')."
    if ($state -eq 'PowerState/deallocated') {
        Write-DemoStatus 'Prepare' 'Already deallocated; no shutdown or deallocation request is needed.'
        Write-Warning 'VM is already deallocated; the cleanliness of its previous guest shutdown cannot be established.'
        return
    }
    if ($state -eq 'PowerState/running') {
        Write-DemoStatus 'Shutdown' 'Requesting normal Windows shutdown (no force power-off)...'
        Invoke-DemoAzure -Subscription $Context.Subscription -NoResult -RetryConflict -Arguments @(
            'vm', 'stop', '-g', $Context.ResourceGroup, '-n', $Context.VmName, '--no-wait'
        )
        $null = Wait-DemoPowerState $Context @('PowerState/stopped', 'PowerState/deallocated')
    }
    if ((Get-DemoPowerState $Context) -ne 'PowerState/deallocated') {
        Write-DemoStatus 'Deallocate' 'Releasing VM compute resources...'
        Invoke-DemoAzure -Subscription $Context.Subscription -NoResult -RetryConflict -Arguments @(
            'vm', 'deallocate', '-g', $Context.ResourceGroup, '-n', $Context.VmName, '--no-wait'
        )
    }
    $null = Wait-DemoPowerState $Context @('PowerState/deallocated')
    Write-DemoStatus 'Prepare' 'VM is deallocated and ready for the disk operation.' -Success
}

function Assert-DemoDiskMetadata {
    param($Resource, [string]$Label)
    if ($Resource['provisioningState'] -ne 'Succeeded') { throw "$Label is not successfully provisioned." }
    if ($Resource['osType'] -ne 'Windows' -or $Resource['hyperVGeneration'] -ne 'V2') {
        throw "$Label must be a Windows Generation 2 OS disk/snapshot."
    }
    if ((Get-DemoValue $Resource @('securityProfile', 'securityType')) -ne 'TrustedLaunch') {
        throw "$Label must preserve Trusted Launch guest state."
    }
    if (-not $Resource['diskSizeGB'] -or [int]$Resource['diskSizeGB'] -lt 1) { throw "$Label has no valid disk size." }
    if ((Get-DemoValue $Resource @('encryption', 'type')) -ne 'EncryptionAtRestWithPlatformKey' -or
        (Get-DemoValue $Resource @('encryption', 'diskEncryptionSetId')) -or
        (Get-DemoValue $Resource @('encryptionSettingsCollection', 'enabled'))) {
        throw "$Label uses unsupported encryption. This workflow supports platform-key encryption only, not Azure Disk Encryption or customer-managed keys."
    }
}

function Wait-DemoResource {
    param(
        $Context, [ValidateSet('disk', 'snapshot', 'vm')][string]$Type, [string]$Id,
        [int]$TimeoutSeconds = 600, [string]$ExpectedOsDiskId
    )
    $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $name = ($Id -split '/')[-1]
    $lastState = ''
    $lastReport = [datetime]::MinValue
    $activity = "Waiting for $Type '$name'"
    Write-DemoStatus 'Wait' "$activity (timeout ${TimeoutSeconds}s)..."
    try {
        do {
            $resource = Invoke-DemoAzure -Subscription $Context.Subscription -Arguments @($Type, 'show', '--ids', $Id)
            $state = $resource['provisioningState']
            if ($state -eq 'Succeeded' -and (-not $ExpectedOsDiskId -or
                (Get-DemoValue $resource @('storageProfile', 'osDisk', 'managedDisk', 'id')) -eq $ExpectedOsDiskId)) {
                Write-DemoStatus 'Wait' "$Type '$name' is ready after $([math]::Round($timer.Elapsed.TotalSeconds, 1))s." -Success
                return $resource
            }
            if ($state -notin @('Creating', 'Updating', 'Succeeded')) {
                throw "$Type provisioning failed or returned an unknown state: $state."
            }
            $elapsed = $timer.Elapsed.ToString('hh\:mm\:ss')
            $message = "$state | elapsed $elapsed | timeout ${TimeoutSeconds}s"
            if ($state -eq 'Succeeded' -and $ExpectedOsDiskId) { $message += ' | waiting for OS-disk reference' }
            Write-Progress -Id 42 -Activity $activity -Status $message
            if ($state -ne $lastState -or ([datetime]::UtcNow - $lastReport).TotalSeconds -ge 15) {
                Write-DemoStatus 'Wait' "$Type '$name': $message."
                $lastReport = [datetime]::UtcNow
                $lastState = $state
            }
            Start-Sleep -Seconds 5
        } while ([datetime]::UtcNow -lt $deadline)
        throw "Timed out waiting for $Type '$Id' provisioning."
    } finally {
        Write-Progress -Id 42 -Activity $activity -Completed
    }
}

function Assert-DemoCompatible {
    param($Context, $Resource, [switch]$Disk)
    Assert-DemoDiskMetadata $Resource $Resource['name']
    if ($Resource['id'] -notlike "/subscriptions/$($Context.Subscription)/*" -or $Resource['location'] -ne $Context.Vm['location']) {
        throw 'The selected resource must be in the VM subscription and region.'
    }
    if ([int]$Resource['diskSizeGB'] -ne [int]$Context.Disk['diskSizeGB']) {
        throw "OS-disk swap requires matching sizes: VM disk is $($Context.Disk['diskSizeGB']) GiB, selected resource is $($Resource['diskSizeGB']) GiB."
    }
    if ($Disk) {
        if ($Resource['managedBy'] -or @($Resource['managedByExtended']).Where({ $_ }).Count -gt 0 -or $Resource['diskState'] -ne 'Unattached') {
            throw "Disk '$($Resource['name'])' is not unattached."
        }
        if (($Resource['zones'] -join ',') -ne ($Context.Disk['zones'] -join ',')) {
            throw 'Replacement disk placement must match the current OS disk.'
        }
        Assert-DemoSku $Context (Get-DemoValue $Resource @('sku', 'name'))
    }
}

function Assert-DemoSku {
    param($Context, [string]$Sku)
    if ($Sku -notin @('Standard_LRS', 'StandardSSD_LRS', 'Premium_LRS')) { throw "Unsupported OS-disk SKU '$Sku'." }
    if ($Sku -eq 'Premium_LRS') {
        $size = Get-DemoValue $Context.Vm @('hardwareProfile', 'vmSize')
        $skus = Invoke-DemoAzure -Subscription $Context.Subscription -Arguments @(
            'vm', 'list-skus', '--location', $Context.Vm['location'], '--size', $size, '--all'
        )
        $match = @($skus | Where-Object { $_['name'] -eq $size -and $_['resourceType'] -eq 'virtualMachines' })
        $premium = @($match | ForEach-Object { $_['capabilities'] } | Where-Object {
            $_['name'] -eq 'PremiumIO' -and $_['value'] -eq 'True'
        })
        if ($premium.Count -eq 0) { throw "VM size '$size' does not report Premium Storage support." }
    }
}

function Assert-DemoUnchanged {
    param($Context, [switch]$Deallocated)
    $vm = Get-DemoVm $Context
    if ($vm['provisioningState'] -ne 'Succeeded' -or
        (Get-DemoValue $vm @('storageProfile', 'osDisk', 'managedDisk', 'id')) -ne $Context.Disk['id'] -or
        $vm['vmId'] -ne $Context.Vm['vmId']) {
        throw 'The VM was changed or recreated during this operation. Refusing to continue.'
    }
    if ($Deallocated -and (Get-DemoPowerState $Context) -ne 'PowerState/deallocated') {
        throw 'The VM is no longer deallocated. Refusing to continue.'
    }
}

function Get-DemoDiskContext {
    param([string]$Subscription, [string]$ResourceGroup, [string]$VmName)
    if (-not $Subscription) { throw 'Set -Subscription or $env:AZURE_SUBSCRIPTION_ID_ONLINE.' }
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Azure CLI is required. Install it and run az login.' }
    Write-DemoStatus 'Preflight' 'Checking Azure login and subscription...'
    $account = Invoke-DemoAzure -Subscription $Subscription -Arguments @('account', 'show')
    if ($account['state'] -ne 'Enabled' -or -not $account['id']) { throw 'The specified Azure subscription is not enabled.' }
    $context = @{
        Subscription = $account['id']
        ResourceGroup = $ResourceGroup
        VmName = $VmName
    }
    Write-DemoStatus 'Preflight' "Reading resource group '$ResourceGroup'..."
    $context.Group = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('group', 'show', '-n', $ResourceGroup)
    Write-DemoStatus 'Preflight' "Reading VM '$VmName' and its current OS disk in '$ResourceGroup'..."
    $context.Vm = Get-DemoVm $context
    if ($context.Vm['provisioningState'] -ne 'Succeeded' -or -not $context.Vm['vmId']) {
        throw 'The VM is not successfully provisioned or has no VM identity.'
    }
    $os = Get-DemoValue $context.Vm @('storageProfile', 'osDisk')
    if (-not $os -or -not (Get-DemoValue $os @('managedDisk', 'id')) -or $os['diffDiskSettings']) {
        throw 'A persistent managed OS disk is required; ephemeral/unmanaged OS disks are unsupported.'
    }
    if ((Get-DemoValue $context.Vm @('securityProfile', 'securityType')) -ne 'TrustedLaunch') {
        throw 'This workflow requires the demo VM Trusted Launch configuration.'
    }
    Write-DemoStatus 'Preflight' "Reading OS disk '$($os['name'])' and checking size, encryption and security..."
    $context.Disk = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @('disk', 'show', '--ids', $os['managedDisk']['id'])
    Assert-DemoDiskMetadata $context.Disk 'Current OS disk'
    if ($context.Disk['managedBy'] -ne $context.Vm['id'] -or $context.Disk['diskState'] -notin @('Attached', 'Reserved')) {
        throw 'The current OS disk is not attached/reserved to the target VM. Refusing an inconsistent attachment.'
    }
    Assert-DemoSku $context (Get-DemoValue $context.Disk @('sku', 'name'))
    Write-DemoStatus 'Preflight' 'Reading VM instance state...'
    $statuses = Invoke-DemoAzure -Subscription $context.Subscription -Arguments @(
        'vm', 'get-instance-view', '-g', $ResourceGroup, '-n', $VmName, '--query', 'instanceView.statuses'
    )
    if (@($statuses | Where-Object { $_['code'] -eq 'OSState/generalized' }).Count) { throw 'Generalized VMs cannot use this workflow.' }
    $powers = @($statuses | Where-Object { $_['code'] -like 'PowerState/*' })
    if ($powers.Count -ne 1) { throw 'Azure did not return exactly one VM power state.' }
    $stableStates = @('PowerState/running', 'PowerState/stopped', 'PowerState/deallocated')
    $context.PowerState = if ($powers[0]['code'] -in $stableStates) { $powers[0]['code'] } else {
        Wait-DemoPowerState $context $stableStates
    }
    Write-DemoStatus 'Preflight' "${VmName}: $($context.PowerState -replace '^PowerState/', ''); region: $($context.Vm['location'])."
    Write-DemoStatus 'Preflight' "OS disk: $($context.Disk['name']); $($context.Disk['diskSizeGB']) GiB; $($context.Disk['sku']['name']); Windows Gen2 / Trusted Launch." -Success
    Write-Warning 'Snapshots preserve local accounts and on-disk credentials. Keep them protected. BitLocker/vTPM recovery and Windows boot require a separately approved boot check.'
    return $context
}

function Assert-DemoMutationAccess {
    param($Context, [string[]]$ResourceIds, [string[]]$RequiredActions)
    Write-DemoStatus 'Preflight' 'Checking resource locks and effective permissions...'
    $locks = Invoke-DemoAzure -Subscription $Context.Subscription -Arguments @('lock', 'list', '-g', $Context.ResourceGroup)
    foreach ($lock in $locks) {
        $scope = $lock['id'] -replace '/providers/Microsoft.Authorization/locks/[^/]+$', ''
        if ($lock['level'] -eq 'ReadOnly' -and @($ResourceIds | Where-Object {
            $_ -eq $scope -or $_.StartsWith("$scope/", [StringComparison]::OrdinalIgnoreCase)
        }).Count) { throw "ReadOnly lock '$($lock['name'])' prevents this operation." }
    }
    $url = "https://management.azure.com$($Context.Group['id'])/providers/Microsoft.Authorization/permissions?api-version=2022-04-01"
    try {
        $permissions = Invoke-DemoAzure -Subscription $Context.Subscription -Arguments @('rest', '--method', 'get', '--url', $url)
    } catch {
        if ($_ -notmatch 'AuthorizationFailed|Forbidden|403') { throw }
        Write-Warning "Cannot inspect effective permissions: $_. Azure will enforce authorization on each operation."
        return
    }
    $missing = @($RequiredActions | Where-Object {
        $action = $_
        -not @($permissions['value'] | Where-Object {
            $grant = $_
            @($grant['actions'] | Where-Object { $action -like $_ }).Count -gt 0 -and
            @($grant['notActions'] | Where-Object { $action -like $_ }).Count -eq 0
        }).Count
    })
    if ($missing.Count) {
        Write-Warning "Resource-group permissions do not establish access for: $($missing -join ', '). Resource-scoped grants may apply; Reader/sign-in roles alone are insufficient. Azure will enforce permissions, deny assignments and policy."
    }
}

function Get-DemoResourceList {
    param($Context, [ValidateSet('snapshot', 'disk')][string]$Type)
    return Invoke-DemoAzure -Subscription $Context.Subscription -Arguments @($Type, 'list', '-g', $Context.ResourceGroup)
}

function Assert-DemoNameAvailable {
    param($Context, [string]$Type, [string]$Name)
    Assert-DemoResourceName $Name
    if (@(Get-DemoResourceList $Context $Type | Where-Object { $_['name'] -eq $Name }).Count) {
        throw "$Type '$Name' already exists. Choose a different name; resources are never overwritten."
    }
}

function Get-DemoProvenanceTags {
    param($Context, $Resource, [string]$SnapshotId)
    $tags = @{}
    if ($Resource['tags']) {
        foreach ($key in $Resource['tags'].Keys) { $tags[$key] = $Resource['tags'][$key] }
    }
    $tags['DemoSourceVmId'] = $Context.Vm['id']
    $tags['DemoSourceVmInstanceId'] = $Context.Vm['vmId']
    $tags['DemoSourceDiskId'] = $Context.Disk['id']
    $tags['DemoSourceDiskSku'] = $Context.Disk['sku']['name']
    $tags['DemoSourceDiskZones'] = if ($Context.Disk['zones']) { $Context.Disk['zones'] -join ',' } else { 'regional' }
    $tags['DemoCapturedUtc'] = [datetime]::UtcNow.ToString('o')
    if ($SnapshotId) { $tags['DemoSourceSnapshotId'] = $SnapshotId }
    if ($tags.Count -gt 50) { throw 'Provenance tags would exceed the Azure 50-tag limit. Reduce existing tags before retrying.' }
    foreach ($key in $tags.Keys) {
        if ($key.Length -gt 512 -or ([string]$tags[$key]).Length -gt 256) {
            throw "Tag '$key' exceeds Azure's key/value length limit. Shorten resource names or tags before retrying."
        }
    }
    return $tags
}

function ConvertTo-DemoTagArguments {
    param([hashtable]$Tags)
    foreach ($key in $Tags.Keys) { "$key=$($Tags[$key])" }
}

function Test-DemoProvenanceConflict {
    param($Context, $Resource)
    $vmId = Get-DemoValue $Resource @('tags', 'DemoSourceVmId')
    $instanceId = Get-DemoValue $Resource @('tags', 'DemoSourceVmInstanceId')
    return (($vmId -and $vmId -ne $Context.Vm['id']) -or
        ($instanceId -and $instanceId -ne $Context.Vm['vmId']))
}

function Get-DemoOwnedResources {
    param($Context)
    Write-DemoStatus 'Inventory' 'Loading snapshots and disks, then checking VM provenance...'
    $snapshots = @(Get-DemoResourceList $Context 'snapshot')
    $disks = @(Get-DemoResourceList $Context 'disk')
    $ownedDisks = @($Context.Disk['id'])
    $ownedSnapshots = @()
    $snapshotIds = @($snapshots | ForEach-Object { $_['id'] })
    foreach ($resource in @($snapshots) + @($disks)) {
        if ((Get-DemoValue $resource @('tags', 'DemoSourceVmId')) -eq $Context.Vm['id'] -and
            (Get-DemoValue $resource @('tags', 'DemoSourceVmInstanceId')) -eq $Context.Vm['vmId']) {
            if ($resource['type'] -eq 'Microsoft.Compute/snapshots') { $ownedSnapshots += $resource['id'] }
            else { $ownedDisks += $resource['id'] }
        }
    }
    # Walk disk/snapshot lineage in both directions, including untagged resources already used by this VM.
    do {
        $count = $ownedDisks.Count + $ownedSnapshots.Count
        foreach ($disk in $disks) {
            if (Test-DemoProvenanceConflict $Context $disk) { continue }
            $source = Get-DemoValue $disk @('creationData', 'sourceResourceId')
            if ($source -and $disk['id'] -in $ownedDisks -and $source -in $snapshotIds -and $source -notin $ownedSnapshots) {
                $ownedSnapshots += $source
            }
            if ($source -and $source -in $ownedSnapshots -and $disk['id'] -notin $ownedDisks) { $ownedDisks += $disk['id'] }
        }
        foreach ($snapshot in $snapshots) {
            if (Test-DemoProvenanceConflict $Context $snapshot) { continue }
            $source = Get-DemoValue $snapshot @('creationData', 'sourceResourceId')
            if ($source -and $snapshot['id'] -in $ownedSnapshots -and
                $source -like '*/providers/Microsoft.Compute/disks/*' -and $source -notin $ownedDisks) {
                $ownedDisks += $source
            }
            if ($source -and $source -in $ownedDisks -and $snapshot['id'] -notin $ownedSnapshots) { $ownedSnapshots += $snapshot['id'] }
        }
    } while (($ownedDisks.Count + $ownedSnapshots.Count) -gt $count)
    return @{
        Snapshots = @($snapshots | Where-Object { $_['id'] -in $ownedSnapshots -and -not (Test-DemoProvenanceConflict $Context $_) })
        Disks = @($disks | Where-Object { $_['id'] -in $ownedDisks -and -not (Test-DemoProvenanceConflict $Context $_) })
    }
}

function Get-DemoRestoreCandidates {
    param($Context)
    $owned = Get-DemoOwnedResources $Context
    $candidates = @()
    foreach ($kind in @('Snapshots', 'Disks')) {
        foreach ($resource in $owned[$kind]) {
            if ($resource['id'] -eq $Context.Disk['id']) { continue }
            try {
                Assert-DemoCompatible $Context $resource -Disk:($kind -eq 'Disks')
            } catch {
                Write-Warning "Excluded '$($resource['name'])': $_"
                continue
            }
            $time = $resource['timeCreated']
            if (-not $time) { $time = Get-DemoValue $resource @('tags', 'DemoCapturedUtc') }
            $parsed = [datetimeoffset]::MinValue
            if ($time -is [datetimeoffset]) { $parsed = $time }
            elseif ($time -is [datetime]) { $parsed = [datetimeoffset]$time }
            elseif (-not [datetimeoffset]::TryParse("$time", [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::AssumeUniversal, [ref]$parsed)) {
                Write-Warning "Excluded '$($resource['name'])': missing/invalid creation timestamp."
                continue
            }
            $candidates += [pscustomobject]@{
                Kind = if ($kind -eq 'Snapshots') { 'Snapshot' } else { 'Disk' }
                Resource = $resource
                Time = $parsed.ToUniversalTime()
            }
        }
    }
    $candidates = @($candidates | Sort-Object Time -Descending)
    return $candidates
}

function Read-DemoRestoreChoice {
    if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) {
        throw 'Interactive selection is unavailable. Supply -SnapshotName or -OsDiskId for automation.'
    }
    return Read-Host 'Choose an entry number (blank cancels)'
}

function Show-DemoRestoreCandidates {
    param([object[]]$Candidates, [string]$DiskSku)
    Write-Host ''
    Write-Host 'Available restore sources (newest first)' -ForegroundColor Cyan
    Write-Host "Snapshot = create a new $DiskSku disk. Disk = attach existing disk without changing its tier."
    $width = [math]::Max(80, $Host.UI.RawUI.WindowSize.Width - 2)
    $nameWidth = [math]::Max(12, [math]::Min(60, $width - 58))
    $rows = for ($i = 0; $i -lt $Candidates.Count; $i++) {
        $entry = $Candidates[$i]
        $resource = $entry.Resource
        Write-Verbose "Choice $($i + 1): $($resource['id']); source: $(Get-DemoValue $resource @('creationData', 'sourceResourceId'))"
        [pscustomobject]@{
            Choice = $i + 1
            Type = $entry.Kind
            Name = $resource['name']
            CreatedUtc = $entry.Time.UtcDateTime.ToString('yyyy-MM-dd HH:mm:ss', [Globalization.CultureInfo]::InvariantCulture)
            GiB = $resource['diskSizeGB']
            DiskTier = if ($entry.Kind -eq 'Snapshot') { $DiskSku } else { $resource['sku']['name'] }
        }
    }
    $columns = @(
        @{ Label = '#'; Expression = { $_.Choice }; Width = 3 },
        @{ Label = 'Type'; Expression = { $_.Type }; Width = 8 },
        @{ Label = 'Name'; Expression = { $_.Name }; Width = $nameWidth },
        @{ Label = 'Created (UTC)'; Expression = { $_.CreatedUtc }; Width = 19 },
        @{ Label = 'GiB'; Expression = { $_.GiB }; Width = 4 },
        @{ Label = 'Disk tier'; Expression = { $_.DiskTier }; Width = 15 }
    )
    Write-Host (($rows | Format-Table -Property $columns -Wrap | Out-String -Width $width).TrimEnd())
    Write-Host ''
}

function Show-DemoRestoreSource {
    param($Resource, [string]$Kind, [string]$DiskSku)
    $source = Get-DemoValue $Resource @('creationData', 'sourceResourceId')
    Write-Host ''
    Write-Host "Selected $($Kind.ToLowerInvariant()): $($Resource['name'])" -ForegroundColor Cyan
    Write-Host "  OS: $($Resource['osType']) Gen2 / $($Resource['securityProfile']['securityType']) | size: $($Resource['diskSizeGB']) GiB"
    if ($source) { Write-Host "  Source: $(($source -split '/')[-1])" }
    if ($Kind -eq 'Snapshot') {
        Write-Host "  Snapshot storage: $($Resource['sku']['name']) | new disk tier: $DiskSku"
        Write-Host '  Action: create a new disk from this snapshot, then swap the OS disk.'
    } else {
        Write-Host "  Disk tier: $($Resource['sku']['name']) (unchanged)"
        Write-Host '  Action: attach this existing disk; no disk will be created.'
    }
    Write-Verbose "Selected resource ID: $($Resource['id'])"
}

function Select-DemoRestoreSource {
    param($Context, [string]$DiskSku)
    $candidates = @(Get-DemoRestoreCandidates $Context)
    if (-not $candidates.Count) { throw 'No compatible snapshots or unattached OS disks with verifiable VM provenance. Supply -SnapshotName or -OsDiskId for an explicitly selected resource.' }
    Show-DemoRestoreCandidates $candidates $DiskSku
    $choice = Read-DemoRestoreChoice
    if ([string]::IsNullOrWhiteSpace($choice)) { throw 'Restore cancelled; no resources were changed.' }
    $index = 0
    if (-not [int]::TryParse($choice, [ref]$index) -or $index -lt 1 -or $index -gt $candidates.Count) {
        throw 'Invalid restore selection; no resources were changed.'
    }
    return $candidates[$index - 1]
}

function Write-DemoFailureState {
    param($Context, [string[]]$ResourceIds)
    Write-Warning 'Operation failed. No automatic cleanup or rollback was attempted. Checking actual resource state...'
    foreach ($id in @($ResourceIds | Where-Object { $_ } | Select-Object -Unique)) {
        $type = if ($id -like '*/snapshots/*') { 'snapshot' } else { 'disk' }
        $name = ($id -split '/')[-1]
        Write-Verbose "Resource to inspect: $id"
        try {
            $resource = Invoke-DemoAzure -Subscription $Context.Subscription -Arguments @($type, 'show', '--ids', $id)
            $state = if ($type -eq 'disk') { $resource['diskState'] } else { $resource['provisioningState'] }
            Write-DemoStatus 'Recovery' "$type '$name': $state (retained)."
        } catch {
            if ($_ -match 'ResourceNotFound|DiskNotFound|SnapshotNotFound') {
                Write-DemoStatus 'Recovery' "$type '$name': not found; nothing to clean up for this name."
            } else { Write-Warning "Cannot read $type '$name': $_" }
        }
    }
    try {
        $vm = Get-DemoVm $Context
        $power = Get-DemoPowerState $Context
        $diskId = Get-DemoValue $vm @('storageProfile', 'osDisk', 'managedDisk', 'id')
        Write-Warning "Actual VM state: $($power -replace '^PowerState/', ''); OS disk: $(($diskId -split '/')[-1])."
        Write-Verbose "Actual attached OS disk ID: $diskId"
    } catch {
        Write-Warning "Unable to read final VM state: $_. Inspect Azure before retrying."
    }
}
