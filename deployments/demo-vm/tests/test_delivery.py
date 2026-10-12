"""Offline unit/mutation contracts: synthetic ARM/plan values, no Azure calls."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import gzip
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load("delivery_guard")
policy = load("check_environment")
VM_ID = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/virtualMachines/vm-demo-copilot"
DISK_ID = VM_ID.split("/providers/")[0] + "/providers/Microsoft.Compute/disks/restored-offline-disk"
SHA = "1" * 40
NOW = datetime(2026, 10, 12, 19, tzinfo=timezone.utc)


def binding():
    return {"upgrade": {"vm_id": VM_ID, "os_disk_id": DISK_ID, "vm_size": "Standard_D8s_v5",
                        "os_disk_sku": "Premium_LRS", "bootstrap": {
                            "repository": "example/offline-payload", "commit": "f04ec69afe46971118493b8a1434a1e2b398f814",
                            "script_path": "public/scripts/bootstrap-demo-vm.ps1",
                            "manifest_path": "public/scripts/bootstrap-demo-vm.packages.json",
                            "script_sha256": "3cc9fe4eb576c3795d0550e235c97d94227a992b32bd078ce6b2920fdc2c2ca8",
                            "manifest_sha256": "9ea0fd7ab57759ee029b5604e17fd579c3e84f4f5c63e664c81837fb501cc7c0",
                            "system_accepted": True}}}


def evidence(b, now):
    receipt = cost_receipt(b, now)
    return {"source_sha": SHA, "binding_sha256": hashlib.sha256(json.dumps(
        b, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "owner": "haflidif", "downtime_accepted": True, "system_payload_accepted": True,
        "all_subscription_coverage": True, "currency": "USD", "observed_utc": now.isoformat(),
        "expires_utc": (now + timedelta(hours=1)).isoformat(), "tenant_limit_usd": 2500,
        "actual_mtd_usd": receipt["actual_mtd_usd"],
        "cost_receipt_sha256": guard.canonical_hash(receipt),
        "inventory_sha256": guard.canonical_hash(receipt["inventory"]["response"]),
        "query_manifest_sha256": guard.query_manifest_hash(receipt["queries"]),
        "projected_horizon_usd": receipt["projection"]["projected_horizon_usd"],
        "vm_reserve_usd": 60, "billing_lag_reserve_usd": receipt["qualification_inputs"]["billing_lag_reserve_usd"],
        "budget_accepted": True,
        "budget_horizon_utc": guard.AUTHORIZED_HORIZON.isoformat(),
        "planned_start_utc": now.isoformat(),
        "planned_return_utc": min(now + timedelta(hours=72), guard.AUTHORIZED_HORIZON).isoformat(),
        "quota_accepted": True, "windows_client_eligibility_accepted": True,
        "duration_hours": 72, "return_plan_owner": "haflidif"}


def cost_receipt(b, now, daily_costs=(30, 20)):
    """Public synthetic full inventory/query receipts, never real tenant data."""
    tenant = "00000000-0000-0000-0000-000000000010"
    subs = ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]
    root_id = f"/providers/Microsoft.Management/managementGroups/{tenant}"
    daily = sum(daily_costs)
    remaining = (guard.AUTHORIZED_HORIZON.date() - now.date()).days + 1
    return {"schema_version": 1, "source_sha": SHA, "binding_sha256": guard.canonical_hash(b),
            "observed_utc": now.isoformat(), "currency": "USD",
            "qualification_inputs": {"tenant_limit_usd": 2500, "vm_reserve_usd": 60,
                                     "billing_lag_reserve_usd": daily * 2, "duration_hours": 72,
                                     "budget_horizon_utc": guard.AUTHORIZED_HORIZON.isoformat(),
                                     "planned_start_utc": now.isoformat(),
                                     "planned_return_utc": min(now + timedelta(hours=72), guard.AUTHORIZED_HORIZON).isoformat()},
            "actual_mtd_usd": daily * now.day,
            "projection": {"kind": "operator_highest_seven_completed_days_plus_mtd_through_authorized_horizon",
                           "completed_days": [(now - timedelta(days=d)).strftime("%Y%m%d") for d in range(7, 0, -1)],
                           "remaining_days": remaining, "billing_lag_basis": "Synthetic operator lag allowance",
                           "daily_bound_usd": daily, "billing_lag_additional_days": 2,
                           "projected_horizon_usd": daily * (now.day + remaining + 2) + 60},
            "inventory": {"observed_utc": now.isoformat(), "scope": root_id, "response": {
                "tenantId": tenant, "id": root_id, "name": tenant,
                "type": "Microsoft.Management/managementGroups", "details": {"parent": None},
                "children": [{"id": f"/subscriptions/{s}", "name": s, "type": "/subscriptions",
                              "children": None} for s in subs]}},
            "queries": [{"subscription_id": s, "scope": f"/subscriptions/{s}", "http_status": 200,
                         "observed_utc": now.isoformat(), "request": {
                             "type": "ActualCost", "timeframe": "MonthToDate", "dataset": {
                                 "granularity": "Daily", "aggregation": {
                                     "totalCost": {"name": "PreTaxCost", "function": "Sum"}}}},
                         "response": {"id": f"subscriptions/{s}/providers/Microsoft.CostManagement/query/offline",
                                      "type": "Microsoft.CostManagement/query", "properties": {
                                          "nextLink": None, "columns": [
                                              {"name": "PreTaxCost", "type": "Number"},
                                              {"name": "UsageDate", "type": "Number"},
                                              {"name": "Currency", "type": "String"}],
                                          "rows": [[daily_costs[i], int(now.replace(day=d).strftime("%Y%m%d")), "USD"]
                                                   for d in range(1, now.day + 1)]}}}
                        for i, s in enumerate(subs)]}


def emitted_command(upgrade):
    # Actual consumer template bytes, not a parallel implementation.
    text = (ROOT / "scripts" / "Invoke-MachineBootstrap.ps1.tftpl").read_text(encoding="utf-8")
    b = upgrade["bootstrap"]
    for key, value in {
        "script_url": f'https://raw.githubusercontent.com/{b["repository"]}/{b["commit"]}/{b["script_path"]}',
        "manifest_url": f'https://raw.githubusercontent.com/{b["repository"]}/{b["commit"]}/{b["manifest_path"]}',
        "script_sha256": b["script_sha256"].upper(), "manifest_sha256": b["manifest_sha256"].upper()
    }.items():
        text = text.replace("${" + key + "}", value)
    compressed = base64.b64encode(gzip.compress(text.encode("utf-8"), mtime=0)).decode()
    launcher = (ROOT / "scripts" / "Invoke-CompressedBootstrap.ps1.tftpl").read_text(encoding="utf-8")
    launcher = launcher.replace("${payload_gzip}", compressed)
    return guard.COMMAND_PREFIX + base64.b64encode(launcher.encode("utf-16le")).decode()


def plan():
    old = {"id": VM_ID, "type": "Microsoft.Compute/virtualMachines@2024-11-01", "name": "vm-demo-copilot",
           "location": "swedencentral", "parent_id": VM_ID.split("/providers/")[0], "tags": {"test": "offline"},
           "identity": [{"type": "SystemAssigned"}], "body": {"properties": {
               "hardwareProfile": {"vmSize": "Standard_D4s_v5"},
               "storageProfile": {"osDisk": {"name": "restored-offline-disk", "createOption": "FromImage",
                                            "caching": "ReadWrite", "managedDisk": {"storageAccountType": "Standard_LRS"}}},
               "licenseType": "Windows_Client", "networkProfile": {"networkInterfaces": []}}}}
    new = copy.deepcopy(old)
    new["body"]["properties"]["hardwareProfile"]["vmSize"] = "Standard_D8s_v5"
    disk = new["body"]["properties"]["storageProfile"]["osDisk"]
    disk["deleteOption"] = "Detach"
    disk["managedDisk"]["id"] = DISK_ID
    values = {guard.VM: (["update"], old, new),
              guard.DISK: (["create"], None, {"type": "Microsoft.Compute/disks@2024-03-02",
                                            "resource_id": DISK_ID, "body": {"sku": {"name": "Premium_LRS"}}}),
              guard.CSE: (["create"], None, {"parent_id": VM_ID, "name": "DemoMachineBootstrap",
                                           "type": "Microsoft.Compute/virtualMachines/extensions@2024-11-01",
                                           "body": {"properties": {"publisher": "Microsoft.Compute",
                                                                  "type": "CustomScriptExtension",
                                                                  "typeHandlerVersion": "1.10",
                                                                  "autoUpgradeMinorVersion": False,
                                                                  "settings": {"commandToExecute": emitted_command(binding()["upgrade"])}}}})}
    for address, action in ((guard.STOP, "deallocate"), (guard.START, "start")):
        values[address] = (["create"], None, {"resource_id": VM_ID, "action": action,
                                            "method": "POST", "when": "apply"})
    return {"applyable": True, "resource_changes": [
        {"mode": "managed", "address": address, "change": {"actions": actions, "before": before,
                                                        "after": after, "after_unknown": {}}}
        for address, (actions, before, after) in values.items()]}


class DeliveryContracts(unittest.TestCase):
    def test_positive_exact_three_objects_and_real_wrapper(self):
        self.assertEqual(guard.check_plan(plan(), binding()["upgrade"]),
                         {"objects": 3, "actions": 2, "replacement": False, "disk_capacity_change": False})

    def mutate(self, address, mutation, message):
        p = plan()
        change = next(r["change"] for r in p["resource_changes"] if r["address"] == address)
        mutation(change)
        with self.assertRaisesRegex(ValueError, message):
            guard.check_plan(p, binding()["upgrade"])

    def test_replacement_forbidden(self):
        self.mutate(guard.VM, lambda c: c.update(actions=["delete", "create"]), "Delete/replacement")

    def test_new_vm_forbidden(self):
        self.mutate(guard.VM, lambda c: c.update(actions=["create"], before=None), "Existing VM")

    def test_wrong_disk_forbidden(self):
        self.mutate(guard.VM, lambda c: c["after"]["body"]["properties"]["storageProfile"]["osDisk"]["managedDisk"].update(
            id=DISK_ID + "-wrong"), "Exact retained disk")

    def test_capacity_patch_forbidden(self):
        self.mutate(guard.DISK, lambda c: c["after"]["body"].update(properties={"diskSizeGB": 128}), "SKU-only")

    def test_hidden_extension_payload_forbidden(self):
        self.mutate(guard.CSE, lambda c: c["after"]["body"]["properties"].update(
            protectedSettings={"commandToExecute": "foreign-payload"}), "Unexpected CSE properties")

    def test_wrong_sku_forbidden(self):
        self.mutate(guard.VM, lambda c: c["after"]["body"]["properties"]["hardwareProfile"].update(
            vmSize="Standard_D16s_v5"), "Wrong VM resize")

    def test_foreign_vm_body_change_forbidden(self):
        self.mutate(guard.VM, lambda c: c["after"]["body"]["properties"].update(licenseType="Windows_Server"),
                    "outside reviewed")

    def test_unknown_body_forbidden(self):
        self.mutate(guard.VM, lambda c: c.update(after_unknown={"body": True}), "Unknown VM")

    def test_action_must_execute_same_plan(self):
        self.mutate(guard.STOP, lambda c: c.update(actions=["no-op"]), "must include")

    def test_command_mutation_forbidden(self):
        self.mutate(guard.CSE, lambda c: c["after"]["body"]["properties"]["settings"].update(
            commandToExecute="powershell.exe -Command Invoke-WebRequest latest"), "Unexpected CSE command")

    def test_emitted_wrapper_mutation_forbidden(self):
        def corrupt(c):
            command = c["after"]["body"]["properties"]["settings"]["commandToExecute"]
            prefix, encoded = command.rsplit(" ", 1)
            text = base64.b64decode(encoded).decode("utf-16le").replace("exit $LASTEXITCODE", "exit 0")
            c["after"]["body"]["properties"]["settings"]["commandToExecute"] = prefix + " " + \
                base64.b64encode(text.encode("utf-16le")).decode()
        self.mutate(guard.CSE, corrupt, "differs from reviewed wrapper")

    def test_outside_scope_forbidden(self):
        p = plan()
        p["resource_changes"].append({"mode": "managed", "address": "azapi_resource.ra_reader[\"offline\"]",
                                     "change": {"actions": ["create"]}})
        with self.assertRaisesRegex(ValueError, "outside three-object"):
            guard.check_plan(p, binding()["upgrade"])

    def test_cost_headroom_and_provenance(self):
        b, now = binding(), NOW
        good = evidence(b, now)
        receipt = cost_receipt(b, now)
        self.assertEqual(guard.qualify(b, good, SHA, now, receipt), good["binding_sha256"])
        for key, value, message in [
            ("projected_horizon_usd", 4005, "Projection total"), ("tenant_limit_usd", 3000, "qualification inputs"),
            ("all_subscription_coverage", False, "coverage"), ("downtime_accepted", False, "downtime"),
            ("source_sha", "2" * 40, "another source"), ("system_payload_accepted", False, "SYSTEM"),
            ("expires_utc", (now - timedelta(seconds=1)).isoformat(), "expired"),
            ("observed_utc", (now - timedelta(hours=25)).isoformat(), "stale"),
            ("projected_horizon_usd", float("nan"), "Finite"),
        ]:
            with self.subTest(key=key):
                bad = dict(good, **{key: value})
                with self.assertRaisesRegex(ValueError, message):
                    guard.qualify(b, bad, SHA, now, receipt)

    def test_snapshot_is_read_only_exact_target(self):
        vm = {"id": VM_ID, "properties": {"hardwareProfile": {"vmSize": "Standard_D4s_v5"},
                                        "storageProfile": {"osDisk": {"managedDisk": {"id": DISK_ID}}}}}
        disk = {"id": DISK_ID, "managedBy": VM_ID, "properties": {"diskSizeGB": 127}, "sku": {"name": "Standard_LRS"}}
        with patch.object(guard.subprocess, "check_output", side_effect=[json.dumps(vm), json.dumps(disk)]) as run:
            self.assertEqual(set(guard.snapshot(binding()["upgrade"])), {"vm", "disk"})
            for call in run.call_args_list:
                self.assertEqual(call.args[0][:4], ["az", "rest", "--method", "get"])

    def test_real_environment_policy_contract(self):
        env = {"can_admins_bypass": False, "protection_rules": [{
            "type": "required_reviewers", "prevent_self_review": True,
            "reviewers": [{"type": "User", "reviewer": {"id": 42}}]}],
            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
        branches = {"total_count": 1, "branch_policies": [{"name": "main", "type": "branch"}]}
        policy.check(env, branches, 42)
        for key in ("can_admins_bypass", "deployment_branch_policy"):
            bad = copy.deepcopy(env)
            bad[key] = True
            with self.assertRaises(ValueError):
                policy.check(bad, branches, 42)
        bad = copy.deepcopy(env)
        bad["protection_rules"][0]["reviewers"].append({"type": "User", "reviewer": {"id": 43}})
        with self.assertRaises(ValueError):
            policy.check(bad, branches, 42)

    def test_haflidi_cannot_initiate_self_approval(self):
        policy.check_initiator("martinopedal")
        with self.assertRaisesRegex(ValueError, "cannot initiate and approve"):
            policy.check_initiator("haflidif")

    def test_actual_workflow_no_public_plan_or_improvised_key(self):
        workflow = (ROOT.parents[1] / ".github" / "workflows" / "deploy-demo-vm.yml").read_text(encoding="utf-8")
        self.assertNotIn("upload-artifact", workflow)
        self.assertNotIn("DEMO_VM_PLAN_KEY", workflow)
        self.assertIn("demo-vm-plans/$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT/$GITHUB_SHA/delivery.tar", workflow)
        self.assertIn("environment: demo-vm-upgrade-haflidi", workflow)
        self.assertEqual(workflow.count("environment: online"), 3)
        self.assertIn("terraform apply -input=false tfplan", workflow)
        self.assertIn("PRIVATE_TRANSPORT_ACCEPTED", workflow)
        self.assertIn("if: inputs.action != 'plan'", workflow)
        self.assertIn("exit 1", workflow.split("Block legacy writes")[1].split("- name: Checkout")[0])
        main = (ROOT / "main.tf").read_text(encoding="utf-8")
        self.assertIn("prevent_destroy = true", main)
        self.assertEqual(main.count("ignore_changes"), 1)  # inherited identity exception only

    def test_offline_lane_is_platform_locked_and_has_no_live_bindings(self):
        workflow = (ROOT.parents[1] / ".github" / "workflows" / "deploy-demo-vm.yml").read_text(encoding="utf-8")
        offline = workflow.split("  offline:", 1)[1].split("  upgrade-policy:", 1)[0]
        for setting in ('ARM_USE_OIDC: false', 'ARM_CLIENT_ID: ""', 'ARM_TENANT_ID: ""',
                        'ARM_SUBSCRIPTION_ID: ""', 'TF_VAR_entra_admin_object_ids: "[]"',
                        'TF_VAR_bastion_user_object_ids: "[]"', '-lockfile=readonly'):
            self.assertIn(setting, offline)
        lock = (ROOT / ".terraform.lock.hcl").read_text(encoding="utf-8")
        for package_hash in ("h1:LYUEAcDIKUaTcBRfKKdf/RCgV7sS6pb+jOPqjB7edbE=",
                             "h1:olVprUWhmFTARlydw/r7VIx4fata2gy3zsAWfPGRrhg="):
            self.assertIn(package_hash, lock)  # signed unchanged-pin Linux package contents
        self.assertIn('version     = "2.13.0"', lock)
        self.assertIn('version     = "4.81.0"', lock)

    def test_wrapper_has_visible_exit_hash_freshness_and_deferred_contract(self):
        template = (ROOT / "scripts" / "Invoke-MachineBootstrap.ps1.tftpl").read_text(encoding="utf-8")
        for field in ("ScriptSha256", "ManifestSha256", "VerifiedAtUtc", "RunId", "PinnedVersion",
                      "$LASTEXITCODE -ne 0", "-AllUsers", "S-1-5-18", "DeferredPerUser",
                      '[Regex]::Escape($rows[0].PinnedVersion)', 'v?$pin'):
            self.assertIn(field, template)
        self.assertNotIn("winget", template.lower())
        self.assertNotIn("ExecutionPolicy", template)


if __name__ == "__main__":
    unittest.main()
