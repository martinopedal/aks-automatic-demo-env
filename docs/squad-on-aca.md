# Optional side track: Squad on Azure Container Apps Jobs

This is an optional NIC 2026 side track for **Copilot CLI + Squad for Terraform**. It prepares a separate deployment of Haflidi Fridthjofsson's MIT-licensed [squad-on-aca](https://github.com/haflidif/squad-on-aca) design at commit `b82c285298592909d4b5f7fb9c196ccafc3c2850`.

It is intentionally separate from the main AKS Automatic demo: separate resource group, separate Terraform state key, separate Container Apps environment, separate Storage Queue, separate Key Vault, and separate runner label. Do not reuse the shared runner environment or existing demo resource groups.

## What it is

Haflidi's project wires GitHub issue labels to ephemeral Squad agents:

```text
GitHub issue label squad:{agent}
  -> GitHub Actions OIDC workflow
  -> Azure Storage Queue
  -> KEDA azure-queue scaler
  -> Azure Container Apps Job
  -> Copilot CLI + Squad in --yolo mode
  -> GitHub App opens a pull request
```

The thin Terraform root in `deployments/squad-on-aca/` does **not** vendor Haflidi's code. The upstream `infra/terraform` directory is a deployment root, not a reusable child module for this tenant: it creates its own randomly suffixed resource group, configures provider blocks and the GitHub provider, enables public network access for Storage and Key Vault, and assumes the deployer can create role assignments. Those choices are reasonable for a standalone repo, but they do not match this landing zone or Martin's requirement for a fixed, separate demo resource group. This repo therefore wraps the architecture in a tenant-compliant root and credits the upstream MIT project instead of copying it.

## Feasibility verdict

**As-is upstream: no, not in this tenant without policy exemptions.** The upstream queue workflow runs on GitHub-hosted runners and enqueues directly to Storage Queue. The upstream Terraform also sets Storage and Key Vault public network access to enabled. In this landing zone, Storage public access is modified to disabled and Key Vault public network access is also modified by policy. A GitHub-hosted runner cannot reach private endpoints.

**Compliant alternative: yes, with one-time admin bootstrap and a small target-workflow change.** Use a VNet-integrated Container Apps environment, private endpoints for Storage Queue and Key Vault, and run both deployment and issue-enqueue workflows on a separate self-hosted runner that has network path to those private endpoints. This PR prepares the infrastructure root and gated deployment workflow, but the live side track still depends on the manual steps below.

### Upstream suggestions for Haflidi's repo

Consider upstream PRs to add:

1. Variables for an existing resource group, fixed resource names, and required tags.
2. `public_network_access_enabled = false` plus private endpoint/DNS options for Storage Queue and Key Vault.
3. A configurable `runs-on` value for `agents/workflows/squad-queue.yml`, so policy-restricted tenants can enqueue from a VNet runner instead of GitHub-hosted runners.
4. Optional role-assignment creation with outputs for manual RBAC in restricted subscriptions.
5. `resource_provider_registrations = "none"` support for resource-group-scoped pipeline identities.
6. A documented bootstrap path for a private runner when Terraform state and data-plane endpoints are private-only.

## Prepared files

- `deployments/squad-on-aca/`: Terraform root for the optional environment.
- `.github/workflows/deploy-squad-on-aca.yml`: manual gated plan/apply/destroy workflow using OIDC and state key `squad-on-aca.tfstate`.
- This document: operator runbook and feasibility notes.

The workflow is isolated from the existing demo: PR first, plan-only run, human reads the plan, then a new `squad-aca` environment-gated apply. It must use its own OIDC app/federated credential and its own environment vars/secrets. Do not run `terraform apply` locally and do not approve your own gate.

## Prerequisites

- Azure resource providers registered: `Microsoft.App`, `Microsoft.ContainerRegistry`, `Microsoft.KeyVault`, `Microsoft.ManagedIdentity`, `Microsoft.Network`, `Microsoft.OperationalInsights`, and `Microsoft.Storage`.
- A pre-created resource group named `rg-squad-on-aca-demo` in `swedencentral`.
- A separate self-hosted runner job that registers with label `squad-on-aca-demo` and has network access to the private Terraform state account and the new private endpoints.
- GitHub environment variables for non-secret deployment values:
  - `SQUAD_ON_ACA_RESOURCE_SUFFIX`
  - `SQUAD_ON_ACA_VNET_ADDRESS_PREFIX`
  - `SQUAD_ON_ACA_ACA_SUBNET_PREFIX`
  - `SQUAD_ON_ACA_PRIVATE_ENDPOINT_SUBNET_PREFIX`
  - `SQUAD_ON_ACA_GITHUB_APP_ID`
  - `SQUAD_ON_ACA_GITHUB_APP_INSTALLATION_ID`
  - `SQUAD_ON_ACA_TARGET_REPOSITORIES_JSON`
  - optionally `SQUAD_ON_ACA_AGENT_IMAGE`
- New `squad-aca` GitHub environment variables for the state backend (`SQUAD_ACA_TFSTATE_RESOURCE_GROUP`, `SQUAD_ACA_TFSTATE_STORAGE_ACCOUNT`, `SQUAD_ACA_TFSTATE_CONTAINER_NAME`) pointing at the existing state account/container with only the new key `squad-on-aca.tfstate`. Do not create or modify existing state containers.
- GitHub App private key and Copilot token uploaded to Key Vault after apply.

Concrete subscription, tenant, principal IDs, and private address ranges belong in GitHub environment variables/secrets or local shell variables only, not in committed files.

## One-time admin steps

Run these with placeholders replaced in a secure operator shell. Do not paste secret values into logs.

```bash
SUBSCRIPTION_ID="<online-subscription-id>"
LOCATION="swedencentral"
RG="rg-squad-on-aca-demo"
PIPELINE_CLIENT_ID="<client-id-for-the-deployment-oidc-app>"
PIPELINE_OBJECT_ID="$(az ad sp show --id "$PIPELINE_CLIENT_ID" --query id -o tsv)"

az account set --subscription "$SUBSCRIPTION_ID"

az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.ManagedIdentity
az provider register --namespace Microsoft.Network
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.Storage

az group create \
  --name "$RG" \
  --location "$LOCATION" \
  --tags owner=martinopedal expiry=2026-10-31 purpose=nic-2026-optional-squad-on-aca lifecycle=demo purgeable=true

az role assignment create \
  --assignee-object-id "$PIPELINE_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RG"
```

If Martin chooses a separate deployment app registration instead of the existing deployment identity, add a federated credential for the `online` environment:

```bash
DEPLOYMENT_APP_ID="<new-squad-aca-application-client-id>"
az ad app federated-credential create \
  --id "$DEPLOYMENT_APP_ID" \
  --parameters '{"name":"aks-demo-squad-on-aca-online","issuer":"https://token.actions.githubusercontent.com","subject":"repo:martinopedal/aks-automatic-demo-env:environment:squad-aca","audiences":["api://AzureADTokenExchange"]}'
```

After the gated apply creates the managed identity and data-plane resources, either set `SQUAD_ON_ACA_MANAGE_ROLE_ASSIGNMENTS=true` only if the pipeline has constrained RBAC-admin rights for these specific roles, or have an admin run:

```bash
AGENT_PRINCIPAL_ID="$(az identity show -g "$RG" -n id-squad-on-aca-agent --query principalId -o tsv)"
STORAGE_ID="$(az storage account show -g "$RG" -n "<storage-account-name>" --query id -o tsv)"
KV_ID="$(az keyvault show -g "$RG" -n "<key-vault-name>" --query id -o tsv)"
ACR_ID="$(az acr show -g "$RG" -n "<acr-name>" --query id -o tsv)"

az role assignment create --assignee-object-id "$AGENT_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Storage Queue Data Reader" --scope "$STORAGE_ID"
az role assignment create --assignee-object-id "$AGENT_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Storage Queue Data Message Processor" --scope "$STORAGE_ID"
az role assignment create --assignee-object-id "$AGENT_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Storage Queue Data Message Sender" --scope "$STORAGE_ID"
az role assignment create --assignee-object-id "$AGENT_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" --scope "$KV_ID"
az role assignment create --assignee-object-id "$AGENT_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role AcrPull --scope "$ACR_ID"
```

Grant the human or break-glass operator who will upload secrets and build the image Key Vault/ACR access:

```bash
OPERATOR_OBJECT_ID="<object-id-of-secret-uploader-and-image-builder>"
az role assignment create \
  --assignee-object-id "$OPERATOR_OBJECT_ID" \
  --assignee-principal-type User \
  --role AcrPush \
  --scope "$ACR_ID"
```

Build Haflidi's agent image from the pinned upstream commit and push it to this demo ACR:

```bash
az acr build \
  --registry "<acr-name>" \
  --image "squad-agent:latest" \
  "https://github.com/haflidif/squad-on-aca.git#b82c285298592909d4b5f7fb9c196ccafc3c2850:agents/base"
```

Grant the same operator Key Vault access:

```bash
OPERATOR_OBJECT_ID="<object-id-of-secret-uploader-and-image-builder>"
az role assignment create \
  --assignee-object-id "$OPERATOR_OBJECT_ID" \
  --assignee-principal-type User \
  --role "Key Vault Secrets Officer" \
  --scope "$KV_ID"
```

Because Key Vault public access is disabled, run the secret upload commands from a network path that resolves the Key Vault private endpoint, such as the separate self-hosted runner or a peered admin host:

```bash
az keyvault secret set \
  --vault-name "<key-vault-name>" \
  --name "github-app-private-key" \
  --file "<path-to-downloaded-github-app-private-key.pem>"

az keyvault secret set \
  --vault-name "<key-vault-name>" \
  --name "copilot-pat" \
  --value "<fine-grained-copilot-token>"
```

## GitHub App and target repository setup

Create a GitHub App for the demo bot and install it only on the target demo repository. Minimum permissions for Haflidi's entrypoint are:

- Contents: read/write
- Issues: read/write
- Pull requests: read/write
- Metadata: read-only

Generate a private key and upload it to Key Vault as shown above. Copy Haflidi's `agents/workflows/squad-queue.yml` to the target repo, but change it to run on the private runner label when Storage Queue public access is disabled:

```yaml
runs-on: [self-hosted, squad-on-aca-demo]
```

Set target-repo Actions variables from Terraform outputs:

```bash
gh variable set SQUAD_AZURE_CLIENT_ID --repo "<owner/repo>" --body "<squad_agent_client_id>"
gh variable set SQUAD_AZURE_TENANT_ID --repo "<owner/repo>" --body "<tenant-id>"
gh variable set SQUAD_AZURE_SUBSCRIPTION_ID --repo "<owner/repo>" --body "<subscription-id>"
gh variable set SQUAD_STORAGE_ACCOUNT --repo "<owner/repo>" --body "<storage-account-name>"
gh variable set SQUAD_QUEUE_NAME --repo "<owner/repo>" --body "squad-work-queue"
```

Initialize or verify the target repo's Squad team and labels, then ensure labels like `squad:{agent-name}`, `squad:processing`, and `squad:pr-open` exist.

## Copilot CLI authentication and `--yolo`

GitHub's Copilot CLI authentication docs say headless automation should provide a token via environment variables checked in this order: `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, then `GITHUB_TOKEN`. Supported tokens include fine-grained PATs with **Copilot Requests** permission; classic `ghp_` PATs are not supported by Copilot CLI. The token owner must have an active Copilot license. Haflidi's entrypoint stores this token in Key Vault as `copilot-pat` and uses it only for the Copilot CLI invocation; GitHub PR and issue operations use a GitHub App installation token.

The job runs `copilot --yolo`, which allows all tools without interactive confirmation. Security implications: the agent can execute commands and edit files inside the cloned target repository. Mitigations in this design are target-repo scoping, a narrowly installed GitHub App, branch protection, no direct pushes to protected branches, PR-only output, ephemeral containers, private Key Vault secrets, and human review before merge.

## Region availability check

Read-only checks on the Online subscription confirmed `Microsoft.App/jobs` and `Microsoft.App/managedEnvironments` list **Sweden Central** as supported. Microsoft Learn documents event-driven Container Apps jobs and queue-message scaling through KEDA; the `azure-queue` scaler is therefore available as part of Container Apps jobs in this region.

## Demo script (2-4 minutes)

1. Before the session, run the plan-only workflow and review the plan. Only apply after the `online` environment gate is approved by a human.
2. Start the separate `squad-on-aca-demo` runner so the target repo can enqueue to the private Storage Queue.
3. In the target repo, label a prepared issue with `squad:{agent-name}`.
4. Open the target repo's `Squad Issue Queue` workflow and show the enqueue step.
5. In Azure, show the Container App Job execution created by KEDA.
6. Open the PR created by the GitHub App bot and explain that it is review-only output.

## Cost and teardown

Expected cost for a short demo is low, but not zero:

- Container App Job: near-zero idle; charged while executions run.
- Storage Queue: negligible transaction cost.
- Key Vault: small monthly fixed cost plus operations.
- Log Analytics: ingestion-based; keep retention low.
- ACR Basic: small fixed monthly cost.
- Private endpoints and a separate runner environment add the main always-on cost.

Teardown is via `.github/workflows/deploy-squad-on-aca.yml` with `action=destroy`, after a reviewed destroy plan and `online` environment approval. Purge Key Vault only if demo retention policy allows it.

## Limitations

- Not a mainline NIC demo dependency; keep it optional.
- Requires a private runner to enqueue because Storage Queue public access is disabled.
- Requires manual GitHub App, Key Vault secret upload, image build/push, and RBAC bootstrap.
- The prepared Terraform root creates the Azure control plane but does not copy Haflidi's container source or target-repo workflow.
- If role assignments remain admin-managed, the first apply will create resources but the live queue/job path will not work until RBAC is completed.
- Copilot CLI unattended behavior can change; retest token auth before the session.
