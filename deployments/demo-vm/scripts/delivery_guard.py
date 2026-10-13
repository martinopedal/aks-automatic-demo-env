"""Fail-closed checks for this one VM lane. Never print plan bodies or IDs."""
import argparse
import base64
import copy
from decimal import Decimal, ROUND_CEILING
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import gzip

VM = "azapi_resource.vm"
DISK = "azapi_update_resource.upgrade_disk[0]"
CSE = "azapi_resource.machine_bootstrap[0]"
STOP = "azapi_resource_action.upgrade_deallocate[0]"
START = "azapi_resource_action.upgrade_start[0]"
ALLOWED = {VM, DISK, CSE, STOP, START}
SEALED_FILES = {"tfplan", "baseline.json", "upgrade.auto.tfvars.json",
                "qualification.json", "cost-receipt.json", ".terraform.lock.hcl"}
COMMAND_PREFIX = "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand "
AUTHORIZED_HORIZON = datetime(2026, 10, 15, 21, 59, 59, tzinfo=timezone.utc)
PRIVATE_INPUTS = {
    "BINDING": "upgrade.auto.tfvars.json",
    "QUALIFICATION": "qualification.json",
    "COST_RECEIPT": "cost-receipt.json",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def query_manifest_hash(queries):
    return canonical_hash({query["subscription_id"]: canonical_hash(query) for query in queries})


def amount(value):
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and
            math.isfinite(value) and value >= 0, "Finite non-negative cost values required")
    return Decimal(str(value))


def unknown_leaves(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        raise ValueError("Invalid Terraform unknown marker shape")
    # Validate every leaf, even after finding True.
    return any([unknown_leaves(child) for child in children])


def utc(value):
    require(isinstance(value, str), "UTC-qualified timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "UTC-qualified timestamp required")
    return result.astimezone(timezone.utc)


def check_cost_receipt(receipt, evidence, binding, sha, binding_sha, now):
    require(isinstance(receipt, dict), "Complete actual-cost receipt required")
    require(receipt.get("schema_version") == 1 and type(receipt["schema_version"]) is int,
            "Unsupported cost receipt schema")
    require(receipt["source_sha"] == sha and receipt["binding_sha256"] == binding_sha,
            "Cost receipt source/binding mismatch")
    require(evidence.get("cost_receipt_sha256") == canonical_hash(receipt),
            "Canonical cost receipt digest mismatch")
    observed = utc(receipt["observed_utc"])
    require(receipt["observed_utc"] == evidence["observed_utc"] and
            now - timedelta(hours=24) <= observed <= now, "Cost receipt stale/unbound")
    require(receipt["currency"] == evidence["currency"] == "USD", "Cost receipt currency mismatch")
    inputs = receipt["qualification_inputs"]
    require(set(inputs) == {"tenant_limit_usd", "vm_reserve_usd", "billing_lag_reserve_usd",
                           "duration_hours", "budget_horizon_utc", "planned_start_utc", "planned_return_utc"} and
            all(inputs[key] == evidence[key] for key in inputs), "Cost receipt qualification inputs mismatch")
    amount(inputs["tenant_limit_usd"])
    amount(inputs["vm_reserve_usd"])
    require(amount(inputs["billing_lag_reserve_usd"]) > 0, "Positive declared billing-lag reserve required")
    require(type(inputs["duration_hours"]) is int, "Integer qualification duration required")

    def fresh(value):
        stamp = utc(value)
        require(observed - timedelta(hours=24) <= stamp <= observed and
                stamp.date() == observed.date(), "Inventory/query receipt stale or wrong query day")

    inventory = receipt["inventory"]
    fresh(inventory["observed_utc"])
    root = inventory["response"]
    require(re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", root["tenantId"]) and root["name"] == root["tenantId"] and
            root["id"] == inventory["scope"] ==
            f'/providers/Microsoft.Management/managementGroups/{root["tenantId"]}' and
            root["details"]["parent"] is None, "Complete tenant-root inventory required")
    subscriptions = set()

    def walk(node):
        require(isinstance(node, dict) and not node.get("nextLink"), "Paginated/invalid inventory forbidden")
        if node["type"] == "/subscriptions":
            identifier = node["name"]
            require(re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", identifier) and
                    node["id"] == f"/subscriptions/{identifier}" and identifier not in subscriptions and
                    node.get("children") in (None, []), "Invalid/duplicate inventory subscription")
            subscriptions.add(identifier)
        else:
            require(node["type"] == "Microsoft.Management/managementGroups" and
                    isinstance(node.get("children"), (list, type(None))), "Invalid inventory node")
            for child in node["children"] or []:
                walk(child)

    walk(root)
    require(evidence.get("inventory_sha256") == canonical_hash(root),
            "Qualified tenant inventory digest mismatch")
    require(subscriptions and binding["upgrade"]["vm_id"].split("/")[2] in subscriptions,
            "Target subscription absent from tenant-root inventory")
    queries = receipt["queries"]
    require(isinstance(queries, list) and len(queries) == len(subscriptions),
            "Complete per-subscription queries required")
    expected_request = {"type": "ActualCost", "timeframe": "MonthToDate", "dataset": {
        "granularity": "Daily", "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}}}}
    daily, seen = {}, set()
    complete_days = {(observed.date().replace(day=1) + timedelta(days=d)).strftime("%Y%m%d")
                     for d in range(observed.day - 1)}
    for query in queries:
        sub = query["subscription_id"]
        require(sub in subscriptions and sub not in seen and query["scope"] == f"/subscriptions/{sub}",
                "Query scopes do not exactly cover inventory")
        seen.add(sub)
        fresh(query["observed_utc"])
        require(type(query["http_status"]) is int and query["http_status"] == 200 and
                query["request"] == expected_request, "Successful unfiltered actual-MTD query required")
        response = query["response"]
        require(response["type"] == "Microsoft.CostManagement/query" and
                re.fullmatch(rf"/?subscriptions/{re.escape(sub)}/providers/Microsoft.CostManagement/query/[^/]+",
                             response["id"]), "Query response scope mismatch")
        props = response["properties"]
        require(props.get("nextLink") is None and "nextLink" in props and not response.get("nextLink"),
                "Paginated cost query forbidden")
        require(props["columns"] == [{"name": "PreTaxCost", "type": "Number"},
                                    {"name": "UsageDate", "type": "Number"},
                                    {"name": "Currency", "type": "String"}],
                "Actual-cost columns missing/wrong currency schema")
        dates = set()
        require(isinstance(props["rows"], list), "Actual-cost rows required")
        for row in props["rows"]:
            require(isinstance(row, list) and len(row) == 3 and type(row[1]) is int and row[2] == "USD",
                    "Invalid cost row or mixed currency")
            day = str(row[1])
            require(day in complete_days | {observed.strftime("%Y%m%d")} and day not in dates,
                    "Duplicate/out-of-period actual cost")
            dates.add(day)
            daily[day] = daily.get(day, Decimal(0)) + amount(row[0])
        require(complete_days <= dates, "Partial actual-cost daily coverage")
    require(seen == subscriptions, "Incomplete tenant subscription coverage")
    require(evidence.get("query_manifest_sha256") == query_manifest_hash(queries),
            "Qualified per-subscription query manifest digest mismatch")
    actual = sum(daily.values(), Decimal(0))
    require(actual > 0, "Zero tenant actual cost is not qualified")
    require(amount(receipt["actual_mtd_usd"]) == actual.quantize(Decimal(".01"), rounding=ROUND_CEILING) and
            amount(evidence["actual_mtd_usd"]) == amount(receipt["actual_mtd_usd"]),
            "Actual cost total/qualification mismatch")
    projection = receipt["projection"]
    days = [(observed.date() - timedelta(days=d)).strftime("%Y%m%d") for d in range(7, 0, -1)]
    require(projection["kind"] == "operator_highest_seven_completed_days_plus_mtd_through_authorized_horizon" and
            projection["completed_days"] == days and set(days) <= complete_days,
            "Completed-day projection basis missing")
    horizon = utc(inputs["budget_horizon_utc"])
    require(horizon == AUTHORIZED_HORIZON and now < horizon and observed < horizon,
            "Authorized October 15 budget horizon missing/expired")
    remaining = (horizon.date() - observed.date()).days + 1
    require(type(projection["remaining_days"]) is int and projection["remaining_days"] == remaining,
            "Projection must reserve full current partial day through authorized horizon")
    require(isinstance(projection["billing_lag_basis"], str) and projection["billing_lag_basis"].strip(),
            "Declared billing-lag basis required")
    bound = max(daily[d] for d in days)
    lag_days = projection["billing_lag_additional_days"]
    require(bound > 0 and abs(amount(projection["daily_bound_usd"]) - bound) <= Decimal(".00000001"),
            "Observed high-day projection bound mismatch")
    require(type(lag_days) is int and lag_days >= 2 and
            abs(amount(inputs["billing_lag_reserve_usd"]) - bound * lag_days) <= Decimal(".00000001"),
            "Billing-lag reserve must cover at least two observed high-cost days")
    projected = (actual + bound * (remaining + lag_days) +
                 amount(inputs["vm_reserve_usd"])).quantize(
                     Decimal(".01"), rounding=ROUND_CEILING)
    require(projected > 0 and amount(projection["projected_horizon_usd"]) == projected and
            amount(evidence["projected_horizon_usd"]) == projected,
            "Projection total/qualification mismatch")


def qualify(binding, evidence, sha, now=None, cost_receipt=None):
    now = now or datetime.now(timezone.utc)
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Full reviewed source commit required")
    require(binding.get("upgrade") is not None, "Private exact upgrade binding missing")
    upgrade = binding["upgrade"]
    require(upgrade["vm_size"] == "Standard_D8s_v5" and upgrade["os_disk_sku"] == "Premium_LRS",
            "Unsupported upgrade SKU")
    require(evidence["source_sha"] == sha, "Qualification belongs to another source revision")
    binding_sha = canonical_hash(binding)
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
    check_cost_receipt(cost_receipt, evidence, binding, sha, binding_sha, now)
    numbers = [evidence["tenant_limit_usd"], evidence["projected_horizon_usd"], evidence["vm_reserve_usd"]]
    for number in numbers:
        amount(number)
    require(evidence["tenant_limit_usd"] == 2500, "Tenant cap must remain USD2500")
    require(evidence["budget_accepted"] is True and
            amount(evidence["projected_horizon_usd"]) <= 2500,
            "BLOCKED: total tenant budget headroom unqualified; no cap override")
    require(evidence["vm_reserve_usd"] >= 60, "Insufficient full 72h conservative VM reserve")
    require(evidence["quota_accepted"] is True and evidence["windows_client_eligibility_accepted"] is True,
            "Quota/licensing qualification missing")
    require(type(evidence["duration_hours"]) is int and evidence["duration_hours"] == 72 and evidence["return_plan_owner"] == "haflidif",
            "Reviewed three-day return plan owner missing; no automatic downgrade")
    start, end = utc(evidence["planned_start_utc"]), utc(evidence["planned_return_utc"])
    require(start >= utc(evidence["observed_utc"]) and start < AUTHORIZED_HORIZON and
            end == min(start + timedelta(hours=72), AUTHORIZED_HORIZON) and now < end and
            utc(evidence["expires_utc"]) <= min(end, AUTHORIZED_HORIZON),
            "Private planned return must bound 72h/deadline and qualification expiry")
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
    markers = vm.get("after_unknown", {})
    require(isinstance(markers, dict), "Invalid Terraform unknown marker shape")
    require(not unknown_leaves(markers.get("body", False)), "Unknown VM body forbidden")
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
    command = props["settings"]["commandToExecute"]
    require(command.startswith(COMMAND_PREFIX) and set(props["settings"]) == {"commandToExecute"},
            "Unexpected CSE command/settings")
    require(len(command) <= 7500, "CSE command exceeds safe Windows cmd launch bound")
    launcher = base64.b64decode(command[len(COMMAND_PREFIX):], validate=True).decode("utf-16le")
    match = re.search(r"\[Convert\]::FromBase64String\('([A-Za-z0-9+/=]+)'\)", launcher)
    require(match is not None, "CSE command differs from reviewed wrapper launcher")
    expected_launcher = Path(__file__).with_name("Invoke-CompressedBootstrap.ps1.tftpl").read_text(encoding="utf-8")
    expected_launcher = expected_launcher.replace("${payload_gzip}", match[1])
    require(launcher.replace("\r\n", "\n") == expected_launcher.replace("\r\n", "\n"),
            "CSE command differs from reviewed wrapper launcher")
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(match[1], validate=True))) as stream:
        decoded = stream.read(65537)
    require(len(decoded) <= 65536, "Compressed wrapper exceeds trusted size bound")
    actual = decoded.decode("utf-8")
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
                                      text=True, stderr=subprocess.PIPE)
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
        result[label] = canonical_hash(data)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["prepare", "qualify", "plan", "snapshot", "seal", "verify"])
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA"))
    args = parser.parse_args()
    if args.operation == "prepare":
        raw_inputs = {alias: os.environ.get(alias, "") for alias in PRIVATE_INPUTS}
        require(all(value.strip() for value in raw_inputs.values()), "Required private secret input missing")
        parsed = {alias: json.loads(value) for alias, value in raw_inputs.items()}
        require(all(isinstance(value, dict) for value in parsed.values()), "Private input objects required")
        binding, evidence, receipt = (parsed[alias] for alias in PRIVATE_INPUTS)
    else:
        binding = json.loads(Path("upgrade.auto.tfvars.json").read_text(encoding="utf-8"))
        evidence = json.loads(Path("qualification.json").read_text(encoding="utf-8"))
        receipt = json.loads(Path("cost-receipt.json").read_text(encoding="utf-8"))
    qualify(binding, evidence, args.sha, cost_receipt=receipt)
    if args.operation in ("prepare", "qualify"):
        if args.operation == "prepare":
            for alias, filename in PRIVATE_INPUTS.items():
                Path(filename).write_text(raw_inputs[alias], encoding="utf-8")
        print("Qualification accepted; no execution approval implied")
    elif args.operation == "plan":
        result = check_plan(json.loads(Path("tfplan.json").read_text(encoding="utf-8")), binding["upgrade"])
        print(json.dumps(result))
    elif args.operation == "snapshot":
        Path("baseline.json").write_text(json.dumps(snapshot(binding["upgrade"])), encoding="utf-8")
    elif args.operation == "seal":
        seal = {"source_sha": args.sha, "created_utc": datetime.now(timezone.utc).isoformat(),
                "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
                "files": {p: digest(p) for p in sorted(SEALED_FILES)}}
        Path("seal.json").write_text(json.dumps(seal), encoding="utf-8")
    else:
        seal = json.loads(Path("seal.json").read_text(encoding="utf-8"))
        require(seal["source_sha"] == args.sha and seal["run_id"] == os.environ["GITHUB_RUN_ID"] and
                seal["run_attempt"] == os.environ["GITHUB_RUN_ATTEMPT"], "Plan/source/run binding mismatch")
        require(datetime.now(timezone.utc) - timedelta(hours=24) <= utc(seal["created_utc"]) <=
                datetime.now(timezone.utc), "Saved plan expired")
        require(set(seal["files"]) == SEALED_FILES, "Incomplete sealed file set")
        require(all(digest(p) == value for p, value in seal["files"].items()), "Sealed file hash mismatch")
        require(snapshot(binding["upgrade"]) == json.loads(Path("baseline.json").read_text(encoding="utf-8")),
                "Live baseline changed since reviewed plan; new plan and approvals required")
        print("Exact saved plan/source/baseline binding verified")


def cli():
    try:
        main()
        return 0
    except Exception:
        # Even built-in ValueError/shape errors can include parsed private data.
        print("Delivery blocked: private input/schema validation or required tool failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
