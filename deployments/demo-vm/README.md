# Demo VM: preserved-disk upgrade delivery

**Source preparation, not Azure execution acceptance.** A merge to `main`
queues delivery; it does not complete unattended. No live VM, disk, extension,
state or identity was changed while preparing this source.

## Bounded root-module contract

The existing `azapi_resource.vm` address, VM identity, private backend and
provider pins stay unchanged. `upgrade = null` retains the legacy D4s_v5,
from-image/StandardSSD clean-machine creation inputs and PowerShell prerequisite.
Non-null `upgrade` binds the **exact currently attached restored OS disk** using
private environment inputs, not its obsolete original source name:

1. Existing VM: D4s_v5 (4 vCPU / 16 GiB) → D8s_v5 (8 vCPU / 32 GiB).
2. Same existing 127-GiB managed OS disk: Standard_LRS → Premium_LRS.
   No capacity change, detach/attach swap, snapshot, import, delete or recreation.
3. One `DemoMachineBootstrap` Custom Script Extension: immutable, hash-verified
   packages-only AllUsers script **and sibling manifest**.

Read-only ARM data verifies VM/disk ownership and disk size before planning.
The VM body mirrors current `FromImage` creation semantics; **it does not switch
an existing VM to `Attach`**. The explicit current disk ID/name and `Detach`
retention reconcile the previous mismatched `Delete` declaration. VM disk SKU
stays at its current value during resize; `azapi_update_resource.upgrade_disk[0]`
owns the independent SKU-only disk update. There is no lifecycle drift mask;
the pre-existing platform-identity exception is unchanged.

Reviewed AzAPI actions on this same VM order **deallocate → VM update → disk SKU
update → start → CSE**. Actions are not additional Azure VM/disk objects.
Deallocation loses RAM/process/session state; persistent disk files are retained.
Failure can leave the VM deallocated. Recovery needs a new reviewed IaC plan,
not an ad-hoc CLI start, replacement or automatic destructive rollback.

`prevent_destroy` protects the existing VM, disk-update ownership and CSE.
Terraform lifecycle settings cannot be conditional: **`recreate-vm` and `destroy`
dispatches now fail closed**, before planning. **Legacy `apply` is also blocked**:
its old from-image body must not overwrite the current restored-disk binding.
The old dispatch choices remain visible but writes/reset require a separately
reviewed preservation/reset revision. Legacy comparison plans remain private;
clean-machine input compatibility is preserved offline, not licensed to mutate
this existing VM. After upgrade, keep its binding;
switching back to null is not an approved downgrade or reset.

## Real approval and private plan transport

PR checks use no credentials and plan-only mocks for **both** providers.
They are offline source checks, **not a live Azure plan**. On a qualifying merge:

1. Verify actual `demo-vm-upgrade-haflidi` protection: only `haflidif`,
   prevent self-review, no admin bypass, only branch `main` (no tags).
2. Existing `online` approval and OIDC trust authorize a fresh incremental plan.
3. Validate source, private scope, fresh all-tenant cost/owner/downtime/licensing/
   quota/SYSTEM evidence. Fail closed on any missing qualification.
4. Inspect the emitted plan: existing VM update only, same disk SKU-only
   update, one exact CSE, both ordered same-VM actions, no other managed changes,
   no deletion/replacement/unknown critical VM body.
5. Store the exact saved-plan bundle **only in the existing private Terraform
   backend container**, at `demo-vm-plans/<run>/<attempt>/<source>/delivery.tar`,
   via the existing OIDC data-plane identity. No storage keys, new identity,
   new IAM/FIC, encryption-secret invention, GitHub artifacts or raw plan logs.
   Anonymous container access must be absent. Only a sanitized three-object
   summary, source/run and SHA256 hashes appear in public workflow output.
6. **Haflidi approves that specific saved-plan run**, not an unseen future plan.
   A subsequent unchanged `online` gate still requires its existing owner.
7. Retrieve and verify the same bytes/source/run/attempt/lock/binding/evidence,
   a 24-hour expiry and read-only live baseline hashes; apply only `tfplan`.
   Terraform's stale-state check is an additional control, not a substitute
   for live drift detection. No new plan is substituted after approval.

Existing `online` Martin protection remains unchanged. The Haflidi stage has
**no OIDC permissions**, so it does not require new federated identity trust.
Haflidi reviews the PR; **Martin or another authorized non-Haflidi merger**
merges and initiates the main run. Haflidi then approves that exact saved plan.
The workflow rejects `github.actor == haflidif` before any Azure job:
the sole reviewer cannot approve his own deployment. A different authorized
actor must initiate a new source-qualified run; rerunning the same initiator
or weakening self-review protection is not a workaround.
Two self-hosted `aks-online-demo` job executions must be available, including
one after approvals; a one-shot runner completing the plan does not supply the
apply runner. The concurrency group excludes legacy/upgrade overlap.

**Private transport remains unqualified until the owner verifies existing
private-container/data-plane prefix access.** Leave
`DEMO_VM_PRIVATE_PLAN_TRANSPORT_ACCEPTED` unset/false meanwhile. A denied
upload/download blocks execution; do not widen IAM. The apply job deletes its
private bundle on completion/failure. If a run is cancelled while awaiting
approval, the backend owner must remove that run's expired prefix under the
existing retention policy; no new storage lifecycle resource is introduced.
Raw private plan/apply files are local only and removed; no failure is turned
into success. A fresh plan and fresh approvals are required after drift/expiry.

## Private input preparation (owner, not a public var file)

In the existing protected `online` environment, set
`DEMO_VM_UPGRADE_BINDING_JSON` to an object containing `upgrade`:

- `vm_id`, `os_disk_id`: actual existing IDs from private readback.
- `vm_size = "Standard_D8s_v5"`, `os_disk_sku = "Premium_LRS"` explicitly.
- `bootstrap`: `repository`, full 40-hex published `commit`, sibling
  `script_path`/`manifest_path` ending in `bootstrap-demo-vm.ps1` and
  `bootstrap-demo-vm.packages.json`, both exact 64-hex SHA256 values,
  `system_accepted = true` **only after actual acceptance**.

`DEMO_VM_UPGRADE_QUALIFICATION_JSON` must bind `source_sha` and
`binding_sha256` (SHA256 of the complete binding JSON serialized with sorted
keys, compact separators and UTF-8). Required fields:

```json
{
  "source_sha": "<exact merged 40-hex source>",
  "binding_sha256": "<64-hex canonical private binding hash>",
  "owner": "haflidif",
  "observed_utc": "<fresh UTC evidence timestamp>",
  "expires_utc": "<UTC expiry, maximum 24h from observation>",
  "currency": "USD",
  "tenant_limit_usd": 2500,
  "projected_eom_usd": "<fresh numeric total-tenant conservative estimate>",
  "incremental_reserve_usd": "<numeric reserve, at least 29.49 for 72h>",
  "all_subscription_coverage": false,
  "budget_accepted": false,
  "downtime_accepted": false,
  "system_payload_accepted": false,
  "quota_accepted": false,
  "windows_client_eligibility_accepted": false,
  "duration_hours": 72,
  "return_plan_owner": "haflidif"
}
```

This intentionally **blocked schema illustration is not runnable approval**.
Owner declarations are protected inputs, not manufactured human approval:
the real Haflidi environment gate still releases the exact saved plan.
Use evidence for every true value. Baseline observations from October 12:
USD1,335.13 MTD across all ten subscriptions, operator run-rate estimate
~USD4,005 EOM, versus the user's **USD2,500 total-tenant monthly cap**.
That estimate is neither native forecast nor guaranteed bill/headroom.
Machine checks require estimate plus reserve within the unchanged cap;
neither merge nor approval overrides a failing budget check.

Conservative Windows-included retail estimates: D8 USD0.776/h, P10 disk
USD21.68/month; 72h full ~USD58.01, incremental over D4/HDD ~USD29.49
(730h disk proration). Excludes existing Bastion/network, other tenant usage,
taxes, bandwidth and optional charges. Windows_Client/base-rate entitlement
is not independently established. The small VM delta is **not tenant headroom**.
There is no automatic 72h downgrade or global shutdown: Haflidi owns a
separately reviewed return plan and budget evidence must reserve actual runtime.

## Bootstrap boundary

The consumer is a minimal verification wrapper, not a second installer.
It stages the exact published script/manifest bytes, checks both SHA256 values,
requires x64 LocalSYSTEM, invokes Windows PowerShell with `-AllUsers`, propagates
nonzero/reboot-required failure and checks a **fresh** core receipt with exact
hashes, 11 pinned versions and Program Files paths.
No authentication/repository initialization/roster. Machine PATH/tools are for
both presenters; Desktop app, VS Code user extensions, auth and presenter
readiness remain explicitly deferred. No default Docker, WSL, reboot or
execution-policy bypass. Offline tests are not SYSTEM/CSE runtime qualification.

## Repeatable root validation

Toolchain tested: Terraform **1.16.5**, AzAPI **2.13.0**, AzureRM **4.81.0**
(unchanged committed lock), TFLint **0.64.0** bundled rules. Configure providers
at this root through approved `ARM_*` OIDC inputs; never commit credentials,
actual binding JSON, state or plans. This is a root deployment, not a new module
or backend. The existing private key remains `demo-vm.tfstate`.

From `deployments\demo-vm` on Windows:

```powershell
terraform fmt -check -recursive
terraform init -backend=false -input=false -lockfile=readonly
terraform validate
terraform test '-filter=tests\upgrade.tftest.hcl'
python -m unittest discover -s tests -p 'test_*.py'
tflint --format compact
```

Inspect test isolation before execution: all seven Terraform runs are plan mode,
both providers mocked, no modules/external programs/provisioners/live effects.
Tests assert emitted VM/disk/CSE/action contracts plus default compatibility and
intended negative failures. Python mutations cover replacement, wrong targets,
capacity, unexpected scope, wrapper mutation and budget/provenance/protection.
Outputs remain VM ID, Bastion name, native-RDP command and egress address.
Static checks cannot certify Azure acceptance, quota, identities, preservation
of every guest file, both presenters or a rehearsal. Independent source/plan
review and exact downtime approval remain required.

Actionlint's scoped configuration lists the existing `aks-online-demo`
self-hosted label explicitly; it does not suppress diagnostics or invent a
runner. Run `actionlint -config-file tests\actionlint.yaml` against
`..\..\.github\workflows\deploy-demo-vm.yml` from this root. The runtime runner
and private-plan transport qualifications are still independent blockers.

References: [VM 2024-11-01 schema](https://learn.microsoft.com/azure/templates/microsoft.compute/2024-11-01/virtualmachines),
[disk SKU update API](https://learn.microsoft.com/rest/api/compute/disks/update?view=rest-compute-2024-03-02),
[conversion and deallocation requirements](https://learn.microsoft.com/azure/virtual-machines/disks-convert-types),
[pinned AzAPI action contract](https://github.com/Azure/terraform-provider-azapi/blob/v2.13.0/docs/resources/resource_action.md).
