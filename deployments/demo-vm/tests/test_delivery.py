"""Offline unit/mutation contracts: synthetic ARM/plan values, no Azure calls."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
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


def binding():
    return {"upgrade": {"vm_id": VM_ID, "os_disk_id": DISK_ID, "vm_size": "Standard_D8s_v5",
                        "os_disk_sku": "Premium_LRS", "bootstrap": {
                            "repository": "example/offline-payload", "commit": SHA,
                            "script_path": "public/scripts/bootstrap-demo-vm.ps1",
                            "manifest_path": "public/scripts/bootstrap-demo-vm.packages.json",
                            "script_sha256": "a" * 64, "manifest_sha256": "b" * 64,
                            "system_accepted": True}}}


def evidence(b, now):
    return {"source_sha": SHA, "binding_sha256": hashlib.sha256(json.dumps(
        b, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "owner": "haflidif", "downtime_accepted": True, "system_payload_accepted": True,
        "all_subscription_coverage": True, "currency": "USD", "observed_utc": now.isoformat(),
        "expires_utc": (now + timedelta(hours=1)).isoformat(), "tenant_limit_usd": 2500,
        "projected_eom_usd": 2000, "incremental_reserve_usd": 29.49, "budget_accepted": True,
        "quota_accepted": True, "windows_client_eligibility_accepted": True,
        "duration_hours": 72, "return_plan_owner": "haflidif"}


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
    return "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand " + \
        base64.b64encode(text.encode("utf-16le")).decode()


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
            text = base64.b64decode(encoded).decode("utf-16le").replace("-AllUsers", "-PerUser")
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
        b, now = binding(), datetime.now(timezone.utc)
        good = evidence(b, now)
        self.assertEqual(guard.qualify(b, good, SHA, now), good["binding_sha256"])
        for key, value, message in [
            ("projected_eom_usd", 4005, "headroom"), ("tenant_limit_usd", 3000, "USD2500"),
            ("all_subscription_coverage", False, "coverage"), ("downtime_accepted", False, "downtime"),
            ("source_sha", "2" * 40, "another source"), ("system_payload_accepted", False, "SYSTEM"),
            ("expires_utc", (now - timedelta(seconds=1)).isoformat(), "expired"),
            ("observed_utc", (now - timedelta(hours=25)).isoformat(), "stale"),
            ("projected_eom_usd", float("nan"), "Finite"),
        ]:
            with self.subTest(key=key):
                bad = dict(good, **{key: value})
                with self.assertRaisesRegex(ValueError, message):
                    guard.qualify(b, bad, SHA, now)

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
