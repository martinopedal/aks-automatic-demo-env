# Changelog

## 2026-10-08

- Online app: added a concise "How we built this" section to the live page with the documented Squad, Copilot CLI, Terraform MCP, GitHub gate, pipeline, and `Test-OnlineSecurity` facts, plus a footer link to the session repository.

- Online app: replaced the "HF" initials badge in the "Your speakers" section with Haflidi Fridthjofsson's photo, wired through the site ConfigMap and mounted into the pod like Martin's photo.

- Demo VM: `scripts/Set-GuestLocalLogin.ps1` gives a B2B guest presenter a local account (Run Command with a protected parameter, password to the operator's clipboard only); `Connect-DemoVm.ps1 -LocalAccount` connects with it.

- Online app: replaced the stock ASP.NET sample with a NIC 2026 demo page (NGINX unprivileged, pinned digest, same hardening) that explains the pipeline and shows the serving pod. Proof step takes the address from the controller Service (the Ingress status can keep the previous controller IP after a class switch) and checks the page title and pod marker.

- Online app: dedicated App Routing NGINX controller with the Azure DNS label `aks-online-demo`, so the app is served at `https://aks-online-demo.swedencentral.cloudapp.azure.com/` (still the NGINX default self-signed certificate). The proof step and `Test-OnlineSecurity.ps1` (now 29 checks) test by hostname and require DNS to resolve to the ingress IP.
- Online root: scope the Network Contributor role definition lookup to the subscription so the role assignment matches the ID ARM stores (removes a perpetual in-place change on `ra_cluster_vnet` after the move).

## 2026-10-07

- Initial public demo environment repository for the AKS Automatic Online deployment and clean Windows 11 demo VM.
- Moved demo Terraform roots, Kubernetes manifests, operator scripts, gated deployment workflows, and the operations runbook out of the reusable module repository.
- Added pull request validation for Terraform, Trivy, Checkov, and TFLint, plus Dependabot configuration for GitHub Actions and both Terraform roots.
