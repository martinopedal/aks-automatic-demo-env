"""Fail-closed checks for this one VM lane. Never print plan bodies or IDs."""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

VM = "azapi_resource.vm"
DISK = "azapi_update_resource.upgrade_disk[0]"
CSE = "azapi_resource.machine_bootstrap[0]"
STOP = "azapi_resource_action.upgrade_deallocate[0]"
START = "azapi_resource_action.upgrade_start[0]"
ALLOWED = {VM, DISK, CSE, STOP, START}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "UTC-qualified timestamp required")
    return result.astimezone(timezone.utc)


def qualify(binding, evidence, sha, now=None):
    now = now or datetime.now(timezone.utc)
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Full reviewed source commit required")
    require(binding.get("upgrade") is not None, "Private exact upgrade binding missing")
    upgrade = binding["upgrade"]
    require(upgrade["vm_size"] == "Standard_D8s_v5" and upgrade["os_disk_sku"] == "Premium_LRS",
            "Unsupported upgrade SKU")
    require(evidence["source_sha"] == sha, "Qualification belongs to another source revision")
    binding_sha = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(evidence["binding_sha256"] == binding_sha, "Qualification binding hash mismatch")
    require(evidence["owner"] == "haflidif" and evidence["downtime_accepted"] is True,
            "Exact downtime/owner qualification missing")
    require(evidence["system_payload_accepted"] is True and upgrade["bootstrap"]["system_accepted"] is True,
            "SYSTEM payload not accepted")
    require(evidence["all_subscription_coverage"] is True and evidence["currency"] == "USD",
            "Total tenant cost coverage missing")
    require(now - timedelta(hours=24) <= utc(evidence["observed_utc"]) <= now,
            "Cost/target evidence stale or in future")
    require(now < utc(evidence["expires_utc"]) <= utc(evidence["observed_utc"]) + timedelta(hours=24),
            "Qualification expired")
    numbers = [evidence["tenant_limit_usd"], evidence["projected_eom_usd"],
               evidence["incremental_reserve_usd"]]
    require(all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and n >= 0
                for n in numbers), "Finite non-negative cost values required")
    require(evidence["tenant_limit_usd"] == 2500, "Tenant cap must remain USD2500")
    require(evidence["budget_accepted"] is True and
            evidence["projected_eom_usd"] + evidence["incremental_reserve_usd"] <= 2500,
            "BLOCKED: total tenant budget headroom unqualified; no cap override")
    require(evidence["incremental_reserve_usd"] >= 29.49, "Insufficient 72h conservative reserve")
    require(evidence["quota_accepted"] is True and evidence["windows_client_eligibility_accepted"] is True,
            "Quota/licensing qualification missing")
    require(evidence["duration_hours"] == 72 and evidence["return_plan_owner"] == "haflidif",
            "Reviewed three-day return plan owner missing; no automatic downgrade")
    require(upgrade["bootstrap"]["repository"] and
            re.fullmatch(r"[a-f0-9]{40}", upgrade["bootstrap"]["commit"]), "Immutable payload commit missing")
    return binding_sha


def check_plan(plan, upgrade):
    require(plan.get("errored") is not True and plan.get("applyable") is True,
            "Plan not applyable")
    changes = {}
    for row in plan["resource_changes"]:
        if row["mode"] == "data":
            require(row["change"]["actions"] in (["read"], ["no-op"]), "Data mutation forbidden")
            continue
        actions = row["change"]["actions"]
        if actions == ["no-op"]:
            continue
        require(row["address"] in ALLOWED, "Change outside three-object scope")
        require("delete" not in actions and actions in (["create"], ["update"]),
                "Delete/replacement forbidden, including action rearming")
        changes[row["address"]] = row["change"]
    require(set(changes) == ALLOWED, "Initial upgrade must include VM, disk, CSE and both ordered actions")
    vm = changes[VM]
    require(vm["actions"] == ["update"] and vm["before"], "Existing VM only; create forbidden")
    before, after = vm["before"], vm["after"]
    require(after.get("id", "").lower() == upgrade["vm_id"].lower() and
            before.get("id", "").lower() == upgrade["vm_id"].lower(), "VM ID not bound")
    require(not vm.get("after_unknown", {}).get("body"), "Unknown VM body forbidden")
    for key in ("type", "name", "location", "parent_id", "tags", "identity"):
        require(before.get(key) == after.get(key), "VM non-performance metadata change")
    old = copy.deepcopy(before["body"])
    new = copy.deepcopy(after["body"])
    require(old["properties"]["hardwareProfile"]["vmSize"] == "Standard_D4s_v5" and
            new["properties"]["hardwareProfile"]["vmSize"] == "Standard_D8s_v5", "Wrong VM resize")
    old_disk = old["properties"]["storageProfile"]["osDisk"]
    new_disk = new["properties"]["storageProfile"]["osDisk"]
    require(new_disk["managedDisk"]["id"].lower() == upgrade["os_disk_id"].lower(),
            "Exact retained disk ID missing")
    require(new_disk["name"].lower() == upgrade["os_disk_id"].split("/")[-1].lower(),
            "Restored disk name differs")
    require(new_disk["createOption"] == old_disk["createOption"] == "FromImage" and
            new_disk["deleteOption"] == "Detach" and new_disk["caching"] == old_disk["caching"] == "ReadWrite",
            "Disk creation/retention/caching semantics changed")
    require(old_disk.get("managedDisk", {}).get("id", upgrade["os_disk_id"]).lower() ==
            upgrade["os_disk_id"].lower(), "Different current disk")
    require(new_disk["managedDisk"]["storageAccountType"] == "Standard_LRS",
            "VM update must preserve disk SKU until the independent disk PATCH")
    # Only authoritatively bind/retain the restored disk and resize the VM.
    old["properties"]["hardwareProfile"]["vmSize"] = "Standard_D8s_v5"
    old["properties"]["storageProfile"]["osDisk"] = new_disk
    require(old == new, "VM properties changed outside reviewed performance/retention fields")
    disk = changes[DISK]["after"]
    require(disk["resource_id"].lower() == upgrade["os_disk_id"].lower() and
            disk["body"] == {"sku": {"name": "Premium_LRS"}}, "Disk PATCH not SKU-only/exact ID")
    require(disk["type"] == "Microsoft.Compute/disks@2024-03-02" and
            not disk.get("sensitive_body_version") and not disk.get("read_override"),
            "Wrong disk API or hidden disk payload")
    for address, action in ((STOP, "deallocate"), (START, "start")):
        row = changes[address]
        require(row["actions"] == ["create"], "Operational actions must execute in this exact plan")
        value = row["after"]
        require(value["resource_id"].lower() == upgrade["vm_id"].lower() and
                value["action"] == action and value["method"] == "POST" and value["when"] == "apply",
                "Wrong operational VM action")
        require(not value.get("body") and not value.get("sensitive_body_version"),
                "Unexpected operational payload")
    cse = changes[CSE]["after"]
    require(cse["parent_id"].lower() == upgrade["vm_id"].lower() and cse["name"] == "DemoMachineBootstrap",
            "CSE target not bound")
    require(cse["type"] == "Microsoft.Compute/virtualMachines/extensions@2024-11-01" and
            not cse.get("sensitive_body_version"), "Wrong CSE API or hidden extension payload")
    props = cse["body"]["properties"]
    require(set(cse["body"]) == {"properties"} and set(props) == {
        "publisher", "type", "typeHandlerVersion", "autoUpgradeMinorVersion", "settings"
    }, "Unexpected CSE properties")
    require(props["publisher"] == "Microsoft.Compute" and props["type"] == "CustomScriptExtension" and
            props["typeHandlerVersion"] == "1.10" and props["autoUpgradeMinorVersion"] is False,
            "Unreviewed CSE handler")
    # Check the *actual emitted command*, not a status-only plan label.
    import base64
    command = props["settings"]["commandToExecute"]
    prefix = "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand "
    require(command.startswith(prefix) and set(props["settings"]) == {"commandToExecute"},
            "Unexpected CSE command/settings")
    actual = base64.b64decode(command[len(prefix):], validate=True).decode("utf-16le")
    payload = upgrade["bootstrap"]
    expected = Path(__file__).with_name("Invoke-MachineBootstrap.ps1.tftpl").read_text(encoding="utf-8")
    for name, value in {
        "script_url": f'https://raw.githubusercontent.com/{payload["repository"]}/{payload["commit"]}/{payload["script_path"]}',
        "manifest_url": f'https://raw.githubusercontent.com/{payload["repository"]}/{payload["commit"]}/{payload["manifest_path"]}',
        "script_sha256": payload["script_sha256"].upper(),
        "manifest_sha256": payload["manifest_sha256"].upper(),
    }.items():
        expected = expected.replace("${" + name + "}", value)
    require(actual.replace("\r\n", "\n") == expected.replace("\r\n", "\n"),
            "CSE command differs from reviewed wrapper and immutable payload binding")
    return {"objects": 3, "actions": 2, "replacement": False, "disk_capacity_change": False}


def snapshot(upgrade):
    result = {}
    for label, identifier, api in (("vm", upgrade["vm_id"], "2024-11-01"),
                                   ("disk", upgrade["os_disk_id"], "2024-03-02")):
        raw = subprocess.check_output(["az", "rest", "--method", "get", "--url",
                                       f"https://management.azure.com{identifier}?api-version={api}"],
                                      text=True)
        data = json.loads(raw)
        require(data["id"].lower() == identifier.lower(), "Live target ID mismatch")
        if label == "vm":
            props = data["properties"]
            require(props["hardwareProfile"]["vmSize"] == "Standard_D4s_v5", "Live VM baseline changed")
            require(props["storageProfile"]["osDisk"]["managedDisk"]["id"].lower() ==
                    upgrade["os_disk_id"].lower(), "Live attached OS disk changed")
        else:
            require(data["properties"]["diskSizeGB"] == 127 and data["sku"]["name"] == "Standard_LRS" and
                    data["managedBy"].lower() == upgrade["vm_id"].lower(), "Live disk baseline changed")
        # Canonical whole GET hashes detect drift, including foreign policy writes,
        # without publishing any ARM response, principal or target identifiers.
        result[label] = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["qualify", "plan", "snapshot", "seal", "verify"])
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA"))
    args = parser.parse_args()
    binding = json.loads(Path("upgrade.auto.tfvars.json").read_text(encoding="utf-8"))
    evidence = json.loads(Path("qualification.json").read_text(encoding="utf-8"))
    qualify(binding, evidence, args.sha)
    if args.operation == "qualify":
        print("Qualification accepted; no execution approval implied")
    elif args.operation == "plan":
        result = check_plan(json.loads(Path("tfplan.json").read_text(encoding="utf-8")), binding["upgrade"])
        print(json.dumps(result))
    elif args.operation == "snapshot":
        Path("baseline.json").write_text(json.dumps(snapshot(binding["upgrade"])), encoding="utf-8")
    elif args.operation == "seal":
        seal = {"source_sha": args.sha, "created_utc": datetime.now(timezone.utc).isoformat(),
                "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
                "files": {p: digest(p) for p in ("tfplan", "baseline.json", "upgrade.auto.tfvars.json", "qualification.json",
                                               ".terraform.lock.hcl")}}
        Path("seal.json").write_text(json.dumps(seal), encoding="utf-8")
    else:
        seal = json.loads(Path("seal.json").read_text(encoding="utf-8"))
        require(seal["source_sha"] == args.sha and seal["run_id"] == os.environ["GITHUB_RUN_ID"] and
                seal["run_attempt"] == os.environ["GITHUB_RUN_ATTEMPT"], "Plan/source/run binding mismatch")
        require(datetime.now(timezone.utc) - timedelta(hours=24) <= utc(seal["created_utc"]) <=
                datetime.now(timezone.utc), "Saved plan expired")
        require(set(seal["files"]) == {"tfplan", "baseline.json", "upgrade.auto.tfvars.json",
                                      "qualification.json", ".terraform.lock.hcl"}, "Incomplete sealed file set")
        require(all(digest(p) == value for p, value in seal["files"].items()), "Sealed file hash mismatch")
        require(snapshot(binding["upgrade"]) == json.loads(Path("baseline.json").read_text(encoding="utf-8")),
                "Live baseline changed since reviewed plan; new plan and approvals required")
        print("Exact saved plan/source/baseline binding verified")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
        # Exception details from CLI/file content may contain private values.
        message = str(exc) if isinstance(exc, ValueError) else "required private input/tool unavailable"
        print(f"Delivery blocked: {message}", file=sys.stderr)
        sys.exit(1)
