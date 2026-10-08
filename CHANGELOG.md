# Changelog

## 2026-10-08

- Online root: scope the Network Contributor role definition lookup to the subscription so the role assignment matches the ID ARM stores (removes a perpetual in-place change on `ra_cluster_vnet` after the move).

## 2026-10-07

- Initial public demo environment repository for the AKS Automatic Online deployment and clean Windows 11 demo VM.
- Moved demo Terraform roots, Kubernetes manifests, operator scripts, gated deployment workflows, and the operations runbook out of the reusable module repository.
- Added pull request validation for Terraform, Trivy, Checkov, and TFLint, plus Dependabot configuration for GitHub Actions and both Terraform roots.
