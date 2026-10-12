# Later supervised native probe — preparation, NOT executed

Do not create a microVM until the infrastructure/exact sealed-plan approval,
policy, combined cost and presenter Data Owner gates in README have passed.
Freeze and independently review the actual command bytes/target before execution.
No unreviewed manifest/apply consent is inferred from the scope approval.

Use a host already signed in through its normal approved flow; check status
before any separately needed sign-in. Never copy auth caches, tokens, OAuth,
GitHub credentials or Copilot binaries into the VM. No secrets, identity, gateway,
allowlist, port, volume, snapshot/commit or external installer in this test.

Bind SUB/RG/GROUP to the frozen private target; REGION is swedencentral. Every
`aca` call must spell all four top-level flags, and pass the command's explicit
`--group` when supported. Confirm installed command help beforehand. In preview.4,
doctor ignored flags and read cached Management: its green 9/9 is not a gate.
Reject any request/response ID, group ID/config or region that differs from the
frozen target. Do not alter global `aca config` or Azure account defaults.

Prepare a **private** evidence folder. A supervisor enforces a wall-clock deadline
of **600 seconds from creation**, one sandbox maximum, and bounded per-command
waits. Idle policies are not active-runtime timers. Capture every stdout/stderr,
command envelope, UTC start/end, numeric process exit and actual request/resource
ID privately. Abort the next test on any failure/deadline; preserve evidence first.
The supervisor must remain available for exact-ID cleanup even on shell failure.

Example command envelopes (variables are not workflow/public defaults):

```powershell
# Read-only infrastructure + data-plane gate for the approved presenter user.
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandboxgroup get --name "$GROUP" -o json
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox list --group "$GROUP" -o json
# Require exact group ARM ID/location, no environment/customer VNet/identity,
# exact presenter grants and successful authenticated data-plane access.
# Require no existing sandbox before one new test. Capture exits, not only JSON.

# Deny/Full must be passed AT CREATION, not patched after startup.
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox create --group "$GROUP" --disk ubuntu --cpu 1000m --memory 2048Mi --egress-default Deny --traffic-inspection Full --wait-timeout 90 -o json
# Record the returned exact new UUID privately as TEST_ID before any operation.

aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox get --group "$GROUP" --id "$TEST_ID" -o json
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox egress show --group "$GROUP" --id "$TEST_ID"
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox lifecycle show --group "$GROUP" --id "$TEST_ID"
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox exec --group "$GROUP" --id "$TEST_ID" -c "uname -a; id; test ! -d /host"
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox fs write --group "$GROUP" --id "$TEST_ID" --path /tmp/native-proof.txt --file ./credential-free-fixture.txt
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox fs cat --group "$GROUP" --id "$TEST_ID" --path /tmp/native-proof.txt
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox stop --group "$GROUP" --id "$TEST_ID" --wait-timeout 60
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox resume --group "$GROUP" --id "$TEST_ID" --wait-timeout 60
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox fs cat --group "$GROUP" --id "$TEST_ID" --path /tmp/native-proof.txt
# Record nonzero command propagation too; never treat expected failure as success.
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox exec --group "$GROUP" --id "$TEST_ID" -c "exit 7"
```

Read back Deny/Full, empty rules and no ports; fail if creation/readback differs.
Do not use `uname`/absence of /host as sole proof of isolation. Record native
hardware-isolation resource/service identity and fs/test result correlation.
For network denial, use only a separately reviewed credential-free endpoint/test;
no temporary Allow, installer registry or broad host rule is implied here.

## Actual host orchestration acceptance

From the host GitHub Copilot CLI/Squad session load the ACA Sandboxes skill and
request: "Using only the recorded approved test sandbox, upload the credential-free
fixture, run the shell validation, then return exact command stdout/stderr and
numeric exits. Use the frozen Online target on every tool call; create nothing,
install nothing, expose no ports and do not access host credentials."

Capture the actual prompt -> skill/tool selection -> exact-target microVM exec/fs
-> tests -> returned results trace. A manually run shell command is **not** proof
of this host-orchestration chain; no inside-VM Copilot OAuth is needed/approved.
Correlate trace to TEST_ID and hashes of the same credential-free fixture/results.

## Only exact new-test cleanup

Capture all proof before deletion. This test has no state worth retaining, and
snapshot/commit would create unapproved durable storage: do **not** snapshot.
Require TEST_ID to be the UUID returned by this run and not pre-existing. Never
use label selectors, list-and-delete loops, group/RG teardown or a cached ID.

```powershell
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox delete --group "$GROUP" --id "$TEST_ID" --yes
aca --subscription "$SUB" --resource-group "$RG" --sandbox-group "$GROUP" --region "$REGION" sandbox list --group "$GROUP" -o json
```

Capture cleanup numeric exit and absence of only this new ID, retained group/RG
and retained Corp unchanged. If cleanup fails, alert the supervisor immediately;
do not claim zero spend or success. No helper/supervisor may be left running after
acceptance/cleanup handoff. This document is a procedure proposal, not runtime proof.
