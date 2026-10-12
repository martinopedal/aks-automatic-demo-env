# Isolated Online native ACA Sandboxes

**Preparation only. No infrastructure or runtime has been deployed by this package.**
Online/public is the primary eligible-demo placement. This package is unrelated
to hosted ACA Jobs and does not change working AKS, Corp, networks or identities.

## One source owner; five durable objects

This repository/package is the sole proposed native owner of all five IDs.
A future independently qualified bootstrap executor must consume these exact
bytes; it must not create a second copy of the group in its own source root.

1. New resource group (published AVM `resources/resource-group:0.4.4`).
2. `Microsoft.App/sandboxGroups@2026-07-01`, Sweden Central, `properties: {}`.
3â€“4. Two exact User grants of SandboxGroup Data Owner, scoped only to that group.
5. Existing consumer ServicePrincipal Contributor, scoped only to the new RG.

No environment ID, customer VNet/default connection, identity/FIC, data role for
the consumer, new role definition, budget, ports, locks or telemetry is enabled.
AVM has dormant optional nested modules; the frozen published template hash and
closed module parameter contract reject activation or alteration of any of them.
The two wrapper deployments are operational ARM records, not additional durable
workload objects. Native what-if must expose all five leaves; opaque nested
results or additional records/resources fail review rather than being filtered.

The AVM RG API is `2025-04-01` (the earlier private planning proposal used
`2024-03-01`). The approved RG/name/grant scopes are unchanged; the API choice is
visible for independent source review, not an assertion of service acceptance.
There is no published AVM SandboxGroup module. Official preview documentation:
<https://learn.microsoft.com/azure/templates/microsoft.app/2026-07-01/sandboxgroups>.
Bicep **0.47.16** emits **BCP081** for this preview type. This warning is retained,
not suppressed. Static contracts forbid unsupported/additional fields; only
real native Provider validation and full what-if can qualify service/policy.

## Actual offline checks (no Azure auth)

```powershell
python deployments/aca-sandboxes-online/source_check.py
```

This runs `bicep lint`, compiles actual ARM, compares to checked-in `main.json`,
checks the embedded AVM digest, and executes positive/negative tests. Tests mutate
both compiled ARM and real Bicep then recompile. Changed scope/type/principal,
identity, VNet/environment, roles, duplicates, extra resources, deletes, modifies,
opaque/reduced what-if and fake absence are rejected for named reasons.

`toolchain.json` pins compiler/module versions and the official Linux binary
SHA256. CI only installs that job-local compiler after an actual missing-command
check; a present wrong version fails. Python uses only its standard library.
No Terraform/backend migration, provider import, schema skip or Azure login is
used for source tests. The JSON-form YAML workflow needs no new parser dependency.

## Private contracts; no defaults

`example.scope.json` and `example.parameters.json` contain **fictional GUIDs**;
they are examples/tests, never workflow defaults. Actual tenant, subscription,
principals, claims, parameters, API responses and approval receipts must remain
outside public Git, PR text and public workflow logs.

The scope contract closes all fields and pins source repository/commit, unchanged
existing OIDC subject/issuer/audience, target region/names, the two users and the
existing consumer. `scopeApprovalSha256` binds the separately retained actual
approval receipt; it is **not** a human-approval boolean and is not execution
consent. `qualificationUTC` must be regenerated from a fresh approved-scope
read/reconciliation within 15 minutes of the protected job, not copied from an
old Corp packet. A private operator can generate exact parameters with
`guard.parameters(scope)`, then independently compare them to the frozen scope.
Assignment names are UUIDv5/DNS of lowercase `scope|principalId|roleGUID`, matching
the private planning recipe. No ARM `guid()` approximation or principal defaults.

After independent source review/green CI and normal merge, set only these
**existing `online` environment** secrets through the approved operator lane:

- `ACA_ONLINE_SCOPE_APPROVAL_JSON`: the original actual structured scope receipt,
  exact bytes matching its hash; names/count/native recipe are checked.
- `ACA_ONLINE_SCOPE_JSON`: current frozen scope, including exact merged commit.
- `ACA_ONLINE_PARAMETERS_JSON`: exact reviewed parameter document.
- `ACA_ONLINE_EVIDENCE_ROOT`: an **already existing** private persistent directory
  on the existing runner, outside the checkout/tmp, owned by the runner, mode 0700.

Evidence storage is a real dependency: do not invent an empty artifact/inventory
or upload private plans to this public repository. Existing private persistence,
retention/write-once ACLs and independent custody of the seal digest must be
qualified. File read-only bits alone are not WORM/immutability. If unavailable,
stop before authentication and request an existing private evidence lane.

## Separate protected native qualification

The new workflow has a source job without Azure authorization and a manual,
main-only read-only `qualify` job on the unchanged runner/environment. Neither
job invokes the existing AKS workflow. No new trust/identity/protection is needed.
Do not dispatch until the above private inputs/storage and current target/policy
context have been independently accepted.

The qualifier acquires one actual job OIDC assertion, verifies immutable subject,
issuer/audience, protected environment/main, repository/workflow, source SHA,
run ID/attempt and freshness, with GitHub's issued protected-ref context, then exchanges **that same assertion** using the
existing FIC. Azure verifies its signature. The resulting actor/tenant/client are
checked; authentication is not confused with Data Owner or bootstrap authority.
Tokens remain in memory. No unconditional login, global config or `account set`.

It captures exact five-ID baseline GET statuses/complete raw responses, then
makes genuine subscription-native **Provider validate** and **FullResourcePayloads
what-if** REST requests. Both operations are attempted when native validation
returns an authorization/service denial. All asynchronous responses, numeric
HTTP statuses, request IDs/headers, timings and full raw errors are captured
before interpretation. No excludes/reduced payloads/success-shaped failed plan.

Source, compiled template, parameters, private scope, current-run bindings,
baseline and every raw response are SHA256 sealed before independent review.
A failure is explicitly `FAILED_READONLY`. There is no apply action or fake
always-blocked apply job. Failure to authorize the new graph remains visible.

## Bootstrap authority â€” exact unresolved dependency

The consumer's existing access to its AKS RG cannot create the new RG or grant
presenter roles. Three targeted read-only attempts found the existing private
root identity has subscription User Access Administrator, **not RG/group write**.
Its production-environment FIC is bound to the existing identity-bootstrap job,
not a new native workload workflow. That workflow is not a five-object executor
and touches identity/trust; do not dispatch it or copy it as a native deployment.

An operator must identify an **already authorized, unchanged** protected executor
that can create the new RG/group and the exact three grants. The scope approval
does not authorize a new actor/FIC, wider UAA/Owner or changed ABAC/trust. If no
such executor exists, return the precise authorization decision to the lead; do
not fix it by granting broad rights or provisioning from the laptop.

Only after that binding is verified should a specific private bootstrap workflow
path be proposed, with ALZ/vault context loaded before authoring. It must execute
this package's sealed source, not independently own the group. Authoring a private
apply lane is intentionally deferred; this source PR is not deployment approval.

## Remaining protected apply/runtime gates

1. Independent source review, warning/API acceptance and green CI; normal merge.
2. Existing unchanged bootstrap actor/trust + private evidence storage qualification.
3. Effective inherited policy/aliases/initiative/parameters/notScopes/exemptions/
   denies and policy-created-resource review; no policy changes/exemptions.
4. Fresh target placement/provider/service/quota context, native validate and full
   raw five-Create plan; reconcile actual scope and privately freeze all hashes.
5. Independent acceptance of the exact sealed bundle plus protected human execution
   consent. Future apply must consume the same checked bytes; **no rebuild, replan,
   parameter regeneration, approval boolean, hidden Delete/Modify or unsealed apply**.
6. Exact group/config/grants and user data-plane readbacks; doctor alone is unsafe.
7. Combined attributable costs + retained Corp storage/forecast + Online monitoring
   and a regional one-attempt estimate within **USD20 total through October 31**.
   Corp's lagged zero budget is neither headroom nor Online coverage. No new budget.
8. Run only the separately reviewed bounded credential-free probe procedure.

See [runtime-procedure.md](runtime-procedure.md). There is no claim that the native
SandboxGroup or host Copilot/Squad handoff works until actual runtime proof exists.
