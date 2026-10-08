# Changelog

## 2026-10-08

- Online app: dedicated App Routing NGINX controller with the Azure DNS label `aks-online-demo`, so the app is served at `https://aks-online-demo.swedencentral.cloudapp.azure.com/` (still the NGINX default self-signed certificate). The proof step and `Test-OnlineSecurity.ps1` (now 29 checks) test by hostname and require DNS to resolve to the ingress IP.
- Online root: scope the Network Contributor role definition lookup to the subscription so the role assignment matches the ID ARM stores (removes a perpetual in-place change on `ra_cluster_vnet` after the move).

## 2026-10-07

- Initial public demo environment repository for the AKS Automatic Online deployment and clean Windows 11 demo VM.
- Moved demo Terraform roots, Kubernetes manifests, operator scripts, gated deployment workflows, and the operations runbook out of the reusable module repository.
- Added pull request validation for Terraform, Trivy, Checkov, and TFLint, plus Dependabot configuration for GitHub Actions and both Terraform roots.
