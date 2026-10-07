# AKS Automatic demo environment

This repository is the public consumer environment for the NIC 2026 AKS Automatic demo. It keeps demo-specific roots, scripts, workflows, and operations notes out of the reusable module repository, while consuming the module by a pinned Git tag:

```hcl
source = "git::https://github.com/martinopedal/terraform-azapi-aks-automatic.git?ref=v0.6.0"
```

The reusable module lives at [martinopedal/terraform-azapi-aks-automatic](https://github.com/martinopedal/terraform-azapi-aks-automatic). The session content and orchestration work live at [martinopedal/squad-terraform-session-2026-10-14](https://github.com/martinopedal/squad-terraform-session-2026-10-14).

## What it deploys

- **Online landing-zone AKS Automatic**: a customer-style root using BYO virtual network subnets, subnet NSGs, NAT Gateway egress, Microsoft Entra RBAC, workload identity, OIDC issuer, and AKS Application Routing ingress.
- **Hardened demo app**: Kubernetes manifests for the `online-demo` namespace with HTTPS ingress, default-deny network policy, non-root runtime, read-only root filesystem, dropped capabilities, and pinned image digest.
- **Clean Windows 11 demo VM**: a Trusted Launch Windows 11 machine behind Azure Bastion Standard, used for the from-zero Copilot CLI and Squad installation demo. The VM has no public IP and is reset through the gated workflow.

## Security chain

1. Changes enter by pull request.
2. `main` is protected by required validation checks.
3. Deployment workflows require the human-reviewed `online` GitHub environment gate.
4. GitHub OIDC is used for Azure access; there is no stored cloud credential secret.
5. Terraform runs on an ephemeral VNet-integrated self-hosted runner so private state and allow-listed AKS API access do not require public storage access.
6. Plan, apply, app deployment, and proof happen in one job. Plan files are never uploaded as artifacts.
7. Read-back scripts independently verify the Online cluster, app, and demo VM state after a run.

## Repository layout

| Path | Purpose |
|---|---|
| `deployments/online/` | Terraform root for the AKS Automatic Online environment. Backend key: `aks-automatic-online.tfstate`. |
| `deployments/demo-vm/` | Terraform root for the clean Windows 11 demo VM and Bastion access. Backend key: `demo-vm.tfstate`. |
| `manifests/online/` | Kustomize manifests for the hardened Online demo app. |
| `scripts/` | Operator scripts for starting the runner, dispatching gated runs, connecting to the VM, and running read-back checks. |
| `.github/workflows/deploy-online.yml` | Gated Online AKS plan/apply/app/proof workflow. |
| `.github/workflows/deploy-demo-vm.yml` | Gated demo VM plan/apply/recreate/destroy workflow. |
| `.github/workflows/validate.yml` | Pull request and `main` validation: Terraform, Trivy, Checkov, and TFLint. |
| `docs/operations-runbook.md` | Operator runbook for planning, applying, proving, resetting, and teardown. |

## Operating the environments

Start with the [operations runbook](docs/operations-runbook.md). The short version is:

```powershell
$env:AZURE_SUBSCRIPTION_ID_ONLINE = '<set in your shell only>'
./scripts/Invoke-GatedRun.ps1 -Workflow deploy-online.yml -Inputs 'apply=false' -StartRunner
./scripts/Invoke-GatedRun.ps1 -Workflow deploy-online.yml -Inputs 'apply=true' -StartRunner
./scripts/Test-OnlineSecurity.ps1
```

Use the same pattern for the demo VM with `deploy-demo-vm.yml` and `action=plan`, `action=apply`, or `action=recreate-vm`, followed by `./scripts/Test-DemoVm.ps1`.

## Upgrading the module

1. Open a pull request that changes only the `?ref=` tag in `deployments/online/main.tf` plus any directly required documentation.
2. Run the validation workflow.
3. Dispatch a plan-only `deploy-online.yml` run and verify the plan before approving any apply run.
4. Apply only after the plan-only run is reviewed and the `online` environment gate is approved by a human.

Do not change Terraform state keys, resource names, variable names, or resource addresses when upgrading the module.
