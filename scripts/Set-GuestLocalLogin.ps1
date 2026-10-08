#Requires -Version 7
<#
.SYNOPSIS
    Create or reset a local sign-in on the demo VM for a guest presenter.
.DESCRIPTION
    B2B guests cannot use Microsoft Entra sign-in to Windows VMs (Microsoft
    Learn, "Sign in to a Windows VM in Azure by using Microsoft Entra ID",
    Requirements). They connect through Bastion (Reader on the resource
    group, DEMO_VM_BASTION_USERS) with a local account instead.

    This script generates a strong password, creates or resets the local user
    through a managed Run Command (password passed as a protected parameter),
    adds it to Administrators and Remote Desktop Users, deletes the Run
    Command, and copies the password to YOUR clipboard. Nothing is written to
    a file, the repository, Terraform state, or workflow logs. Hand the
    password over in person or by phone, never in chat.

    Run it again after action=recreate-vm (a new VM has no local users).
.EXAMPLE
    ./scripts/Set-GuestLocalLogin.ps1 -UserName haflidi -FullName 'Haflidi (demo)'
#>
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z][a-z0-9]{2,19}$')][string]$UserName,
    [string]$FullName = $UserName,
    [string]$Subscription = $env:AZURE_SUBSCRIPTION_ID_ONLINE,
    [string]$ResourceGroup = 'rg-demo-vm-online',
    [string]$VmName = 'vm-demo-copilot'
)
$ErrorActionPreference = 'Stop'
if (-not $Subscription) { throw 'Set -Subscription or $env:AZURE_SUBSCRIPTION_ID_ONLINE.' }

$power = az vm get-instance-view -g $ResourceGroup -n $VmName --subscription $Subscription `
    --query "instanceView.statuses[?starts_with(code,'PowerState/')].code | [0]" -o tsv
$started = $false
if ($power -ne 'PowerState/running') {
    Write-Host "VM is $power; starting it..."
    az vm start -g $ResourceGroup -n $VmName --subscription $Subscription -o none
    $started = $true
}

# 24 random characters plus one of each class, so Windows complexity rules always pass.
$set = [char[]]'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!#+=?@'
$pw = (-join (1..24 | ForEach-Object { $set[[Security.Cryptography.RandomNumberGenerator]::GetInt32($set.Length)] })) + 'Kq7!'

$script = @'
param([string]$UserName, [string]$FullName, [string]$Pw)
$ErrorActionPreference = 'Stop'
$sec = ConvertTo-SecureString $Pw -AsPlainText -Force
if (Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue) {
    Set-LocalUser -Name $UserName -Password $sec -PasswordNeverExpires $true
    Enable-LocalUser -Name $UserName
    $action = 'reset'
} else {
    New-LocalUser -Name $UserName -Password $sec -FullName $FullName -PasswordNeverExpires -AccountNeverExpires | Out-Null
    $action = 'created'
}
foreach ($g in 'Administrators', 'Remote Desktop Users') {
    if (-not (Get-LocalGroupMember -Group $g -Member $UserName -ErrorAction SilentlyContinue)) { Add-LocalGroupMember -Group $g -Member $UserName }
}
"local user $UserName $action"
'@

$rc = "set-local-login-$UserName"
# az runs through cmd.exe on Windows, which cannot pass a multi-line argument.
$scriptFile = New-TemporaryFile
Set-Content -Path $scriptFile -Value $script
try {
    az vm run-command create -g $ResourceGroup --vm-name $VmName --subscription $Subscription --name $rc `
        --script "@$scriptFile" --parameters "UserName=$UserName" "FullName=$FullName" --protected-parameters "Pw=$pw" `
        --async-execution false --timeout-in-seconds 300 -o none
    # create does not return the execution result; read it back.
    $out = az vm run-command show -g $ResourceGroup --vm-name $VmName --subscription $Subscription --name $rc --instance-view `
        --query 'instanceView.{state:executionState,out:output,err:error}' -o json | ConvertFrom-Json
    if ($out.state -ne 'Succeeded') { throw "Run Command $($out.state): $($out.err)" }
    Write-Host $out.out.Trim()
} finally {
    Remove-Item $scriptFile -ErrorAction SilentlyContinue
    az vm run-command delete -g $ResourceGroup --vm-name $VmName --subscription $Subscription --name $rc --yes -o none 2>$null
}

Set-Clipboard -Value $pw
Remove-Variable pw
Write-Host "Password for .\$UserName is on your clipboard. Hand it over privately, then clear the clipboard."
if ($started) { Write-Host 'The VM was started for this; it auto-shuts down at 19:00 (or run az vm deallocate).' }
