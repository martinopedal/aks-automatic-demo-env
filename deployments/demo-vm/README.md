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
   quota/SYSTEM evidence. Cost qualification requires the complete private
   tenant-root inventory and every actual-cost query, not coverage checkboxes.
   Fail closed on any missing qualification.
4. Inspect the emitted plan: existing VM update only, same disk SKU-only
   update, one exact CSE, both ordered same-VM actions, no other managed changes,
   no deletion/replacement/unknown critical VM body. Terraform's nested
   `after_unknown.body` maps/lists are inspected recursively: known/empty maps
   are valid, genuine `true` leaves and invalid marker types are rejected.
5. Store the exact saved-plan bundle **only in the existing private Terraform
   backend container**, at `demo-vm-plans/<run>/<attempt>/<source>/delivery.tar`,
   via the existing OIDC data-plane identity. No storage keys, new identity,
   new IAM/FIC, encryption-secret invention, GitHub artifacts or raw plan logs.
   Anonymous container access must be absent. Only a sanitized three-object
   summary, source/run and SHA256 hashes appear in public workflow output.
6. **Haflidi approves that specific saved-plan run**, not an unseen future plan.
   A subsequent unchanged `online` gate still requires its existing owner.
7. Retrieve and verify the same bytes/source/run/attempt/lock/binding/evidence
   **and cost receipt**, a maximum 24-hour expiry bounded by the authorized
   October 15 deadline/return time, and read-only live baseline hashes; apply
   only `tfplan`.
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
  "cost_receipt_sha256": "<canonical hash of the complete private receipt>",
  "inventory_sha256": "<canonical hash of independently qualified raw inventory>",
  "query_manifest_sha256": "<canonical per-subscription query manifest hash>",
  "actual_mtd_usd": "<numeric recomputed MTD total, rounded UP to cents>",
  "projected_horizon_usd": "<numeric total INCLUDING full VM and lag reserves>",
  "vm_reserve_usd": "<numeric full 72h VM reserve, at least 60>",
  "billing_lag_reserve_usd": "<numeric reserve for at least two observed high days>",
  "budget_horizon_utc": "2026-10-15T21:59:59Z",
  "planned_start_utc": "<private planned upgrade start, not before observation>",
  "planned_return_utc": "<earlier of planned start+72h and authorized deadline>",
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
Use evidence for every true value. The user authorized the temporary budget
horizon **through October 15, 2026, EOD UTC+02 (21:59:59Z)**, not October 31.
The cap stays **USD2,500 TOTAL tenant**, not USD2,500 incremental VM spend.
An old packet cannot apply after that deadline or its earlier expiry/return.
Planned return is privately qualified by Haflidi; the source does not
automatically downgrade, shut down shared workloads or delete the VM.

`DEMO_VM_UPGRADE_COST_RECEIPT_JSON` is a **private protected owner input**,
written locally with restrictive permissions and sealed into the same private
backend bundle. No collection permissions, new cost identity, public artifact,
new plaintext secret store or fabricated cost source is introduced. Leave it
unset until the existing authorized operator supplies actual evidence.
Receipt schema version 1 requires:

- `source_sha`, `binding_sha256`, `observed_utc`, `currency = "USD"`.
  The qualification's `cost_receipt_sha256` uses the same canonical JSON hash
  algorithm as the binding (sorted keys, compact separators, UTF-8, no NaN).
- `qualification_inputs`: exactly `tenant_limit_usd`, `vm_reserve_usd`,
  `billing_lag_reserve_usd`, `duration_hours`, `budget_horizon_utc`,
  `planned_start_utc`, `planned_return_utc`, all equal to the qualification.
- `inventory`: `observed_utc`, exact tenant-root management-group `scope`,
  complete original ARM `response` (tenant ID/name/id, null root parent,
  recursively expanded management-group/subscription children). No paginated,
  duplicate or partial inventory. The VM's subscription must belong to it.
  Its canonical response hash must equal the qualification's separately
  accepted `inventory_sha256`; pruning inventory and matching queries cannot
  pass by merely refreshing the whole-packet digest.
- `queries`: exactly one entry per inventory subscription, with
  `subscription_id`, exact subscription `scope`, `observed_utc`,
  integer `http_status = 200`, original `request` and original `response`.
  The request must be **unfiltered ActualCost/MonthToDate, Daily,
  PreTaxCost/Sum**. Response scope and columns must match
  `PreTaxCost:Number`, `UsageDate:Number`, `Currency:String`; `nextLink` must
  explicitly be null. Every completed month-to-date day must have a USD
  numeric row for every subscription (explicit zero rows are permitted).
  Missing daily coverage/empty responses require fresh complete evidence,
  not treating a missing row as zero. Current-day rows are optional.
  `query_manifest_sha256` hashes the canonical map
  `{subscription_id: canonical_hash(complete_query_entry)}` and must equal
  the qualification's separately accepted complete-collection manifest.
  Refresh these accepted digests only from actual complete operator receipts,
  not by blessing a mutated/partial packet.
- `actual_mtd_usd`: recomputed sum of all rows, rounded **up** to cents.
  A completely zero tenant, negative/boolean/nonfinite amounts, mixed currency,
  stale/scope-mismatched/failed/filtered/paginated responses are blocked.
- `projection`: `kind =
  "operator_highest_seven_completed_days_plus_mtd_through_authorized_horizon"`,
  exact chronological `completed_days` (seven previous UTC dates as
  `YYYYMMDD`), integer `remaining_days`, numeric `daily_bound_usd`,
  integer `billing_lag_additional_days` (at least two), nonempty declared
  `billing_lag_basis`, numeric `projected_horizon_usd`.
  The guard recomputes the **highest** last-seven-completed-day tenant total
  from the raw rows and verifies the claimed daily bound and lag reserve
  (allowing only 1e-8 USD numeric-serialization tolerance for those two derived
  values). It adds **a full current-partial-day reserve and each day through
  October 15**, MTD, full VM reserve and positive lag reserve, rounded up
  to cents. It rejects a claimed projection that differs from that calculation.
  Thus October 12 evidence requires four days, not three. Evidence collected
  too early in the month to establish seven completed days is blocked.

Inventory/query observations must be the same UTC date as the receipt,
not in its future, and within 24 hours; the receipt timestamp must exactly
match the qualification's fresh observation. The full raw evidence, source,
scope and numerical inputs are bound by the receipt digest, qualification
and saved-plan seal. A digest demonstrates binding, **not independent truth
or permission to invent raw responses**; the owner still supplies actual
authorized collection. No Azure data is queried by offline tests.

Baseline observations from October 12:
USD1,335.13 MTD across all ten subscriptions, operator run-rate estimate
~USD4,005 EOM, versus the user's **USD2,500 total-tenant monthly cap**.
The EOM estimate remains a **separate baseline-month risk warning** and is
not alone a veto of the newly authorized temporary horizon. The privately
rehashed complete receipt uses the observed high day ~USD138.566,
**four full-day reserves plus two additional high days for lag plus USD60
full D8 Windows/P10 reserve**: estimated total ~USD2,226.53, leaving
~USD273.47 planning headroom under the unchanged cap through October 15.
This is an operator estimate, not native forecast, guaranteed bill/headroom
or a bound on future unplanned usage. Fresh actual receipts and exact-source
qualification are still required; neither merge nor approval overrides a
failing horizon budget check, and spending after October 15 is not approved.

Conservative Windows-included retail estimates: D8 USD0.776/h, P10 disk
USD21.68/month; 72h full ~USD58.01, incremental over D4/HDD ~USD29.49
(730h disk proration). Excludes existing Bastion/network, other tenant usage,
taxes, bandwidth and optional charges. Windows_Client/base-rate entitlement
is not independently established. The small VM delta is **not tenant headroom**.
There is no automatic 72h downgrade or global shutdown: Haflidi owns a
separately reviewed return plan and budget evidence must reserve actual runtime.

## Bootstrap boundary

The consumer is a minimal verification wrapper, not a second installer.
Terraform GZip-compresses that trusted wrapper as UTF-8 and embeds it in a
short UTF-16LE `EncodedCommand` launcher. Windows PowerShell 5.1 decompresses
**in memory**, without credentials/payload in a launcher file. The plan guard
compares both the exact trusted launcher and decompressed wrapper to source
and the immutable binding. Terraform and Python enforce **7,500 characters
maximum for the actual emitted command**, leaving margin under Windows
`cmd.exe`'s 8,191-character launch limit (including the command-processor prefix).
It stages the exact published script/manifest bytes, checks both SHA256 values,
requires x64 LocalSYSTEM, invokes Windows PowerShell with `-AllUsers`, propagates
the original native nonzero/reboot-required exit code and checks a **fresh** core receipt with exact
hashes, 11 pinned versions and Program Files paths.
No authentication/repository initialization/roster. Machine PATH/tools are for
both presenters; Desktop app, VS Code user extensions, auth and presenter
readiness remain explicitly deferred. No default Docker, WSL, reboot or
execution-policy bypass. Offline tests are not SYSTEM/CSE runtime qualification.
The producer has independent **source** approval; immutable publication,
committed-raw-byte digests and live SYSTEM/CSE acceptance remain pending.
Do not pin the working-copy manifest hash: Git LF normalization changes its
bytes. Recompute both hashes from the actual published commit before binding.

## Repeatable root validation

Toolchain tested: Terraform **1.16.5**, AzAPI **2.13.0**, AzureRM **4.81.0**
(unchanged selected versions; committed lock includes verified
`windows_amd64` **and** `linux_amd64` package hashes), TFLint **0.64.0** bundled rules. Configure providers
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
The 33 Python tests include an actual Terraform 1.16.5 JSON mock-plan export
(known nested VM body plus computed AzAPI output unknown), nested true/invalid
marker mutations, actual rendered command length, and complete synthetic
positive/negative tenant receipts with production qualify/seal/verify callers.
Windows additionally executes the actual compressed-launcher shape with only
harmless local fixtures under `cmd.exe`/64-bit PowerShell 5.1 and verifies native
exit codes 0, 37 and 3010. This does not install anything, use networking,
run the SYSTEM consumer, or prove the real VM. CI runs Linux source tests and
a credential-free Windows regression job; no provider versions are changed.
Outputs remain VM ID, Bastion name, native-RDP command and egress address.
Static checks cannot certify Azure acceptance, quota, identities, preservation
of every guest file, both presenters or a rehearsal. Independent source/plan
review and exact downtime approval remain required.

The platform hashes were recorded with
`terraform providers lock -platform=linux_amd64 -platform=windows_amd64`.
This adds the Linux package-content checksums for the already selected versions;
it does not upgrade either provider or remove existing signed archive hashes.
Without those Linux `h1` values, readonly initialization can download a signed
archive yet subsequent Linux validation rejects the extracted cached package.
CI therefore keeps `-lockfile=readonly`, rather than repairing the lock at runtime.
Offline PR checks explicitly clear Azure authentication/user-binding variables;
provider mocks must not inherit private live-environment identifiers.

Actionlint's scoped configuration lists the existing `aks-online-demo`
self-hosted label explicitly; it does not suppress diagnostics or invent a
runner. Run `actionlint -config-file tests\actionlint.yaml` against
`..\..\.github\workflows\deploy-demo-vm.yml` from this root. The runtime runner
and private-plan transport qualifications are still independent blockers.

References: [VM 2024-11-01 schema](https://learn.microsoft.com/azure/templates/microsoft.compute/2024-11-01/virtualmachines),
[disk SKU update API](https://learn.microsoft.com/rest/api/compute/disks/update?view=rest-compute-2024-03-02),
[conversion and deallocation requirements](https://learn.microsoft.com/azure/virtual-machines/disks-convert-types),
[pinned AzAPI action contract](https://github.com/Azure/terraform-provider-azapi/blob/v2.13.0/docs/resources/resource_action.md).
