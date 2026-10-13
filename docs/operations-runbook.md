# Operations runbook: Online demo and demo VM

Operator guide for this consumer repository and its two pipeline-deployed environments in the Online landing zone:

- `deployments/online`: AKS Automatic cluster, NAT Gateway, App Routing ingress, sample app (`deploy-online.yml`).
- `deployments/demo-vm`: clean Windows 11 VM behind Azure Bastion for the from-zero Copilot CLI and Squad demo (`deploy-demo-vm.yml`).

Infrastructure deployment changes go through a pull request, the required checks, and the `online` environment gate (one required reviewer, protected branches only, admin bypass off). Do not apply Terraform from a laptop. The local OS-disk snapshot/restore procedure below is an explicit operational exception, not permission to change other infrastructure locally.

## Prerequisites (operator machine)

- PowerShell 7, Azure CLI with the `bastion` extension (`az extension add -n bastion`), GitHub CLI signed in with workflow permission on this repository.
- `az login` to the tenant, then set the subscription only in your session, never on screen or in a file:

```powershell
$env:AZURE_SUBSCRIPTION_ID_ONLINE = '<set in your shell only>'
```

## Scripts

| Script | Purpose |
|---|---|
| `scripts/start-online-runner.ps1` | Starts one execution of the ephemeral, VNet-integrated self-hosted runner (Container Apps Job, no managed identity). State is private-endpoint only, so every run needs it. |
| `scripts/Invoke-GatedRun.ps1` | Dispatches a workflow, waits until GitHub has registered the environment gate, approves it, confirms the approval took effect, waits for the result (status line about every 5 minutes), and prints the plan, apply, and proof lines plus a password-leak check. |
| `scripts/Test-OnlineSecurity.ps1` | 29 read-back checks of the Online cluster: SKU, identity, network, platform, state, negative tests from the internet, app hostname DNS, HTTPS and redirect, policy summary. Prints the app URL. Exit 1 on any failure. |
| `scripts/Test-DemoVm.ps1` | 14 checks of the demo VM: Bastion tunnel and RDP handshake, NAT egress IP, GitHub, WinGet, and npm reachability, WinGet, PowerShell 7, Entra join, and a clean start (no Git, Copilot CLI, Squad, or demo clone in any profile). Exit 1 on any failure. |
| `scripts/Connect-DemoVm.ps1` | Opens a native RDP session through Bastion with Entra sign-in and MFA, or with a local account (`-LocalAccount`, for guests). |
| `scripts/Set-GuestLocalLogin.ps1` | Creates or resets a guest presenter's local VM account; the generated password goes only to the operator's clipboard. |
| `scripts/Snapshot-DemoVm.ps1` | Cleanly shuts down and deallocates the demo VM, then saves a specialized OS-disk snapshot. Leaves the VM deallocated. Supports `-WhatIf`. |
| `scripts/Restore-DemoVm.ps1` | Offers a snapshot/retained-disk picker or accepts an explicit source. New disks default to the currently attached OS disk's SKU unless overridden. Swaps the OS disk and retains the previous disk for rollback. Leaves the VM deallocated. |
| `scripts/Test-DemoVmDiskScripts.ps1` | Runs offline regression scenarios with a mocked Azure CLI. Does not access Azure or require an external test framework. |

`Invoke-GatedRun.ps1` approves on your behalf. Run it only for a change you have already reviewed.

## Change flow (plan, review, apply, prove)

The workflow runs plan, apply, app deploy, and proof in one gated job, so the gate sits before that run's plan. Use two runs:

```powershell
# 1. Plan only. Read the "Plan:" lines and the resource addresses in the output.
./scripts/Invoke-GatedRun.ps1 -Workflow deploy-online.yml -Inputs 'apply=false' -StartRunner
# 2. Apply the reviewed change. The run re-plans: stop it if its plan differs from step 1.
./scripts/Invoke-GatedRun.ps1 -Workflow deploy-online.yml -Inputs 'apply=true' -StartRunner
# 3. Independent read-back.
./scripts/Test-OnlineSecurity.ps1
```

The demo VM uses the same pattern with `-Workflow deploy-demo-vm.yml` and `-Inputs 'action=plan'`, `'action=apply'`, `'action=recreate-vm'`, or `'action=destroy'`, followed by `./scripts/Test-DemoVm.ps1`.

A healthy steady state is `No changes. Your infrastructure matches the configuration.` on a plan-only run.

## Online app hostname

The app is served at `https://aks-online-demo.swedencentral.cloudapp.azure.com/` by a dedicated App Routing NGINX controller (`manifests/online/ingress-controller.yaml`) whose load balancer carries the Azure DNS label `aks-online-demo`. TLS uses the NGINX default self-signed certificate, so browsers show a warning: accept it once before presenting.

## Pipeline permissions created outside Terraform

The pipeline identity cannot grant itself rights, so these were created once by an administrator and are removed at teardown:

| Grant | Scope | Why |
|---|---|---|
| Role Based Access Control Administrator with an ABAC condition (Network Contributor to service principals only) | `rg-aks-online-demo` | The root grants the cluster identity Network Contributor on the VNet |
| Role Based Access Control Administrator with an ABAC condition (two VM sign-in roles to users only) | `rg-demo-vm-online` | VM sign-in roles for presenters |
| Custom role `AKS App Routing Controller Writer (online demo)` (`customresources/read` and `write`) with an ABAC condition limited to `approuting.kubernetes.azure.com` / `nginxingresscontrollers` | the AKS cluster | `NginxIngressController` is cluster-scoped; AKS RBAC Writer covers namespaced objects only. The custom-resource ABAC attributes are an AKS preview feature |

## Demo VM

- **Connect:** `./scripts/Connect-DemoVm.ps1`. Members sign in with Entra ID (`DEMO_VM_ENTRA_ADMINS` environment variable grants Virtual Machine Administrator Login).
- **Guest presenters:** B2B guests cannot use Entra sign-in to VMs. They get Reader on the resource group (`DEMO_VM_BASTION_USERS`) and their own local account: `./scripts/Set-GuestLocalLogin.ps1 -UserName <name>` creates or resets it through a managed Run Command (protected parameter, deleted afterwards) and puts the generated password on the operator's clipboard. Hand it over privately. The guest connects with `./scripts/Connect-DemoVm.ps1 -LocalAccount` after `az login` to the tenant, so the password alone does not reach the VM. Re-run after `action=recreate-vm`.
- **Reset to clean:** `action=recreate-vm` replaces the VM, OS disk, and VM-scoped extensions, then proves the clean state. Allow 15-20 minutes: landing-zone DeployIfNotExists policies add the Azure Monitor agent, ChangeTracking, GuestAttestation, and Azure Policy extensions after creation, which keeps the VM in `Updating` for several minutes. The workflow retries and waits for a terminal state.
- **Auto-shutdown:** 19:00 W. Europe. Start it with `az vm start` before an evening rehearsal.
- **Clean-machine demo script:** see the session repository documentation at `docs/clean-machine-demo.md` in `martinopedal/squad-terraform-session-2026-10-14`.

### Save and restore a prepared OS disk

Use this local procedure to return the **same VM** to a prepared demo environment. It does not run Sysprep or generalize Windows. A snapshot retains the captured applications, profiles, local accounts, and on-disk credentials. Protect snapshots and retained disks like the original VM. It does not capture external application state, temporary storage, data disks, or provide a full-VM backup.

**Terraform restriction:** a local disk swap changes the VM outside Terraform. Do not run `apply`, `recreate-vm`, or `destroy` until you have reviewed and manually reconciled the disk drift. This is a procedural restriction; the workflow does not enforce it. Terraform configuration and state are not updated by these scripts. `recreate-vm` is a destructive marketplace reset, not a snapshot restore.

Both scripts use the connection script's defaults: `AZURE_SUBSCRIPTION_ID_ONLINE`, resource group `rg-demo-vm-online`, and VM `vm-demo-copilot`. Set the environment variable or supply `-Subscription`; the scripts do not silently use the active CLI subscription. Bastion and its CLI extension are not needed for disk operations.

Prerequisites:

- PowerShell 7, Azure CLI, and an existing `az login` to the intended subscription.
- Permission to read the VM, disks, snapshots, resource group, and locks; shut down/deallocate and update the VM; create disks/snapshots; and update retained-disk tags. Contributor on the resource group normally covers these operations, subject to locks, deny assignments, and Azure Policy. Reader and VM sign-in roles alone are insufficient.
- A successfully provisioned, nongeneralized Windows Generation 2 VM with a persistent managed OS disk and Trusted Launch. The initial implementation supports platform-key encryption, not Azure Disk Encryption or customer-managed disk encryption sets.
- A replacement of the same size and region with compatible security, storage tier, and disk placement. The scripts require existing-disk placement to match the current OS disk and reject disks already attached to any VM.
- Close applications and save work first. A normal shutdown improves filesystem consistency, but is not a database/application-consistent backup. If the VM is already deallocated, the script does not restart it to prove that its previous shutdown was clean.
- Do not overlap this procedure with deployment, auto-shutdown/startup, disk changes, or another operator's snapshot/restore operation. Preflight reads and state checks are not a distributed operation lock.

Preview and save the prepared environment:

```powershell
.\scripts\Snapshot-DemoVm.ps1 -WhatIf
$saved = .\scripts\Snapshot-DemoVm.ps1
$saved.SnapshotName
```

The script requests normal Windows shutdown without force power-off, verifies deallocation, creates a full managed snapshot, and reads back its source/security metadata and disabled export access. It never starts the VM or deletes resources. A failed shutdown stops the operation.

Automatic names use UTC in the exact format **`yyyyddMM-HHmmss`** (year, day, month):

| Resource | Name |
|---|---|
| Snapshot | `snap-osdisk-vm-demo-copilot-20261210-193000` |
| New OS disk | `osdisk-vm-demo-copilot-20261210-193000` |

These examples represent October 12, 2026 at 19:30 UTC. Names are not chronologically sortable across months; the picker uses actual timestamps. `-SnapshotName` and `-DiskName` override automatic names. An existing name is an error, not an overwrite.

Restore using the numbered picker:

```powershell
.\scripts\Restore-DemoVm.ps1
```

The picker lists saved OS snapshots and compatible unattached OS disks attributable to this VM, including disks from different snapshots. Its numbered table shows resource type, name, creation time in UTC, size, and the new/existing disk tier without long resource IDs. After selection, a summary explains the source and action before confirmation.

It verifies provenance through VM/instance tags or the disk/snapshot source-ID chain, not similar names. Untagged resources used by the current VM can appear when their lineage is verifiable. It excludes unrelated/unverifiable resources, attached disks, and incompatible resources. Blank input cancels; an empty list or invalid choice makes no changes.

Choosing a snapshot creates a new disk using the **currently attached OS disk's SKU** by default, regardless of the snapshot's storage tier or the tier used when it was captured. For example, an attached `Premium_LRS` disk makes new restores Premium, even from an older snapshot. Use `-DiskSku` to select another supported tier explicitly.

Snapshot storage remains `Standard_LRS`, independent of the restored disk's tier. Choosing an existing disk uses it unchanged, including its storage tier. To change that tier through this restore procedure, select a snapshot and create a new disk with the desired `-DiskSku` instead.

For explicit selection or automation:

```powershell
.\scripts\Restore-DemoVm.ps1 -SnapshotName $saved.SnapshotName -WhatIf
.\scripts\Restore-DemoVm.ps1 -SnapshotName $saved.SnapshotName

# Optional new-disk name and tier; Premium requires a compatible VM size.
.\scripts\Restore-DemoVm.ps1 -SnapshotName '<snapshot-name>' `
    -DiskName '<new-disk-name>' -DiskSku Premium_LRS

# Roll back by attaching the retained previous OS disk, without creating a disk.
.\scripts\Restore-DemoVm.ps1 -OsDiskId '<previous-os-disk-resource-id>' -WhatIf
.\scripts\Restore-DemoVm.ps1 -OsDiskId '<previous-os-disk-resource-id>'
```

Supported new-disk tiers are `Standard_LRS` (HDD), `StandardSSD_LRS`, and `Premium_LRS`. `-DiskSku`/`-DiskName` do not apply to existing-disk selection. In noninteractive automation, supply a source and use `-Confirm:$false` only after reviewing the operation. The picker never silently selects the newest snapshot or first disk.

Restore creates and verifies a replacement before shutting down the target when possible. Snapshot copies inherit their Windows generation and Trusted Launch security metadata; the script does not set `--security-type`, which Azure rejects with `CreateOption=Copy`. It checks the inherited metadata before shutdown or swap, rather than weakening the VM's security configuration.

Restore then deallocates, rechecks the VM and disk, tags the previous disk for future rollback, swaps the OS-disk reference, and verifies that the old disk remains detached. The replacement uses delete option `Detach` to protect it from later VM deletion. The VM, NIC, identity, and Trusted Launch settings remain in place. Both operations leave the VM **deallocated**.

Both scripts print timestamped phases by default: preflight, preparation, creation, waiting, verification, and completion. Restore also shows retention and swap phases. Waiting shows the current Azure state and elapsed time, with periodic text updates and a progress indicator. The timeout is a limit on waiting, not an estimated completion time or a fabricated percentage.

Each VM power-state wait allows **30 minutes** by default, including normal Windows shutdown, deallocation, and waiting for an existing transition during preflight. Windows shutdown can exceed five minutes. Use `-PowerStateTimeoutSeconds` on either script to change this limit (60-86400 seconds); for example, `-PowerStateTimeoutSeconds 3600` allows one hour per power-state wait. Disk/snapshot provisioning retains its separate 10-minute wait limit.

If a power-state wait expires, the script stops safely without forcing power-off or proceeding to capture/swap. The Azure operation can still finish after the script exits. Inspect the VM before retrying. If restore already created a compatible unattached replacement disk, select that **Disk** in the picker or pass its ID with `-OsDiskId`; do not select the snapshot again unless you want another disk created.

Add `-Verbose` to see individual Azure CLI operation timings, full resource IDs, and source lineage:

```powershell
.\scripts\Snapshot-DemoVm.ps1 -WhatIf -Verbose
.\scripts\Restore-DemoVm.ps1 -SnapshotName '<snapshot-name>' -WhatIf -Verbose
```

The default result display uses names, state, and elapsed time. Full IDs remain available on the returned objects for automation (`SnapshotId`, `PreviousDiskId`, and `ReplacementDiskId`).

Restore prints the exact rollback command. On failure it reads back the requested/retained disk and snapshot states, distinguishes missing resources from retained ones, and reports the actual readable VM attachment/power state. It does not delete partially created resources or automatically undo an uncertain swap; inspect Azure before retrying.

Disk-reference verification while deallocated does **not** prove Windows boots. Start the VM separately with `Connect-DemoVm.ps1` when you are ready, verify Windows and Entra/local sign-in, and deallocate again if required. BitLocker/vTPM recovery or identity changes made after an old snapshot may require operator intervention. Do not disable protections or expose recovery credentials. `Test-DemoVm.ps1` additionally asserts a clean install baseline; use it only when the restored snapshot is meant to contain no demo-installed tools.

Snapshots and all retained disks continue to incur storage charges while the VM is deallocated. Delete only explicitly reviewed, unattached resources after you no longer need rollback.

References: [managed disk snapshots](https://learn.microsoft.com/en-us/azure/virtual-machines/snapshot-copy-managed-disk), [Windows OS-disk swaps](https://learn.microsoft.com/en-us/azure/virtual-machines/windows/os-disk-swap), and [Trusted Launch FAQs](https://learn.microsoft.com/en-us/azure/virtual-machines/trusted-launch-faq).

## Known races and how they are handled

| Race | Symptom | Handling |
|---|---|---|
| Gate approval right after dispatch | Approval API returns success but the run stays `waiting` | `Invoke-GatedRun.ps1` waits for the pending deployment, approves, and re-checks |
| Policy extensions after VM creation | Extension or run command fails with "another operation in progress" | Retries in the workflow; `ignore_changes = [identity]` for the platform-added identity |
| One-shot proof after apply | Proof reads a VM still `Updating` | Proof waits, bounded, for a terminal provisioning state |
| OIDC assertion lifetime | AKS calls fail after a long apply | Workflow re-runs `azure/login` before cluster calls |
| Defender NSG assessment lag | New subnets show "without NSG" for up to 24 hours | Not a defect; NSGs are attached in the same apply and enforced by policy |

## Teardown (complete by 2026-10-31)

1. `./scripts/Invoke-GatedRun.ps1 -Workflow deploy-demo-vm.yml -Inputs 'action=destroy' -StartRunner`
2. `deploy-online.yml` deliberately has no destroy input, so a mis-dispatched run cannot remove the cluster before the session. After 2026-10-14, add a gated `destroy` input in a pull request (mirroring `deploy-demo-vm.yml`), review its plan-only output, then run it through `Invoke-GatedRun.ps1`.
3. **Policy-created backup vault.** `Deploy-VM-Backup` creates a Recovery Services vault (`RSVault-swedencentral-*`) in `rg-demo-vm-online` and may enroll the VM. Terraform does not own it. If the VM was enrolled: `az backup protection disable --delete-backup-data true --yes`. Soft delete is enabled with enhanced security (14 days), so the vault can be deleted only after the soft-deleted items expire. **Start teardown by 2026-10-17** to finish by the 31st.
4. After reconciling any local OS-disk swaps, review retained snapshots and detached OS disks. Terraform does not own these copies; remove only explicitly identified resources after confirming that no VM uses them and rollback is no longer needed.
5. Remove role assignments (including the custom role assignment on the cluster and the custom role definition `AKS App Routing Controller Writer (online demo)`) and the `DEMO_VM_*` environment variables, then confirm both resource groups contain nothing billable.
