"""Real Terraform mock-export regressions; never Azure, state or installers."""
import base64
import copy
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

from test_delivery import ROOT, SHA, NOW, binding, evidence, guard, plan, cost_receipt
from datetime import datetime, timedelta


class RejectedArtifactRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, ARM_USE_OIDC="false", ARM_CLIENT_ID="", ARM_TENANT_ID="",
                   ARM_SUBSCRIPTION_ID="", TF_VAR_entra_admin_object_ids="[]",
                   TF_VAR_bastion_user_object_ids="[]")
        result = subprocess.run(
            ["terraform", "test", "-filter=" + str(Path("tests") / "upgrade.tftest.hcl"), "-json", "-verbose"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=180)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        if next(e for e in events if e["type"] == "version")["terraform"] != "1.16.5":
            raise AssertionError("Regression evidence requires reviewed Terraform 1.16.5")
        cls.export = next(e["test_plan"] for e in events if e["type"] == "test_plan")
        cls.vm = next(r["change"] for r in cls.export["resource_changes"]
                      if r["address"] == guard.VM)
        cls.cse = next(r["change"] for r in cls.export["resource_changes"]
                       if r["address"] == guard.CSE)
        cls.command = cls.cse["after"]["body"]["properties"]["settings"]["commandToExecute"]

    def exported_upgrade(self):
        # Test-plan exports have no prior state. Model only the existing D4
        # baseline; retain actual Terraform body/unknown markers/CSE bytes.
        p = plan()
        change = next(r["change"] for r in p["resource_changes"] if r["address"] == guard.VM)
        change["after"]["body"] = copy.deepcopy(self.vm["after"]["body"])
        change["before"]["body"] = copy.deepcopy(self.vm["after"]["body"])
        change["before"]["body"]["properties"]["hardwareProfile"]["vmSize"] = "Standard_D4s_v5"
        change["after_unknown"] = copy.deepcopy(self.vm["after_unknown"])
        cse = next(r["change"] for r in p["resource_changes"] if r["address"] == guard.CSE)
        cse["after"]["body"] = copy.deepcopy(self.cse["after"]["body"])
        return p

    def test_actual_known_body_unknown_output_accepted(self):
        self.assertIs(self.vm["after_unknown"]["output"], True)
        self.assertIsInstance(self.vm["after_unknown"]["body"], dict)
        self.assertTrue(self.vm["after_unknown"]["body"])
        self.assertEqual(guard.check_plan(self.exported_upgrade(), binding()["upgrade"])["objects"], 3)

    def test_nested_true_or_invalid_unknown_markers_rejected(self):
        for marker in ({"map": {"leaf": True}}, {"list": [False, {"leaf": True}]},
                       [False, [True]], {"invalid": None}, {"invalid": 0}, {"invalid": "false"},
                       {"true": True, "invalid": None}):
            with self.subTest(marker=marker):
                p = self.exported_upgrade()
                vm = next(r["change"] for r in p["resource_changes"] if r["address"] == guard.VM)
                vm["after_unknown"]["body"]["properties"]["mutation"] = marker
                with self.assertRaisesRegex(ValueError, "Unknown VM|Invalid Terraform unknown"):
                    guard.check_plan(p, binding()["upgrade"])
        p = self.exported_upgrade()
        vm = next(r["change"] for r in p["resource_changes"] if r["address"] == guard.VM)
        vm["after_unknown"]["body"]["properties"]["known"] = {"map": [False, {"leaf": False}]}
        guard.check_plan(p, binding()["upgrade"])

    def test_actual_command_fits_cmd_with_margin(self):
        self.assertLessEqual(len(self.command), 7500)
        self.assertLess(len("cmd.exe /d /s /c ") + len(self.command), 8191)

    def test_coverage_checkboxes_and_zero_without_receipt_rejected(self):
        b, now = binding(), NOW
        e = evidence(b, now)
        e["projected_horizon_usd"] = 0
        with self.assertRaisesRegex(ValueError, "receipt"):
            guard.qualify(b, e, SHA, now)

    @unittest.skipUnless(os.name == "nt", "cmd.exe is a Windows-only launch boundary")
    def test_cmd_boundary_reproduction_is_harmless(self):
        # Match the original wrapper's actual encoded length, but execute only
        # a comment and a local exit. No consumer/SYSTEM script is executed.
        fixture = "# " + "x" * (len(base64.b64decode(self.command.rsplit(" ", 1)[1])) // 2 - 12) + "\nexit 0"
        command = self.command.rsplit(" ", 1)[0] + " " + base64.b64encode(fixture.encode("utf-16le")).decode()
        result = subprocess.run([os.environ["ComSpec"], "/d", "/s", "/c", command],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell 5.1/cmd.exe required")
    def test_actual_compressed_launcher_preserves_native_exit(self):
        launcher = base64.b64decode(self.command[len(guard.COMMAND_PREFIX):]).decode("utf-16le")
        for code in (0, 37, 3010):
            with self.subTest(native_exit=code):
                fixture = """
if (-not [Environment]::Is64BitProcess -or $PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) { throw 'Wrong PowerShell boundary' }
& "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "exit __NATIVE_EXIT__"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
'fixture-ok'
""".replace("__NATIVE_EXIT__", str(code))
                payload = base64.b64encode(gzip.compress(fixture.encode(), mtime=0)).decode()
                text = re.sub(r"FromBase64String\('[A-Za-z0-9+/=]+'\)",
                              f"FromBase64String('{payload}')", launcher)
                command = guard.COMMAND_PREFIX + base64.b64encode(text.encode("utf-16le")).decode()
                result = subprocess.run([os.environ["ComSpec"], "/d", "/s", "/c", command],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual("fixture-ok" in result.stdout, code == 0)
        failed = re.sub(r"FromBase64String\('[A-Za-z0-9+/=]+'\)",
                        "FromBase64String('not-valid-gzip')", launcher)
        result = subprocess.run(
            [os.environ["ComSpec"], "/d", "/s", "/c",
             guard.COMMAND_PREFIX + base64.b64encode(failed.encode("utf-16le")).decode()],
            capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)

    def test_compressed_wrapper_mutation_rejected(self):
        p = self.exported_upgrade()
        settings = next(r["change"] for r in p["resource_changes"]
                        if r["address"] == guard.CSE)["after"]["body"]["properties"]["settings"]
        text = base64.b64decode(self.command[len(guard.COMMAND_PREFIX):]).decode("utf-16le")
        compressed = re.search(r"FromBase64String\('([A-Za-z0-9+/=]+)'\)", text)[1]
        decoded = gzip.decompress(base64.b64decode(compressed)).decode().replace("-AllUsers", "-PerUser")
        corrupt = base64.b64encode(gzip.compress(decoded.encode(), mtime=0)).decode()
        text = text.replace(compressed, corrupt)
        settings["commandToExecute"] = guard.COMMAND_PREFIX + base64.b64encode(text.encode("utf-16le")).decode()
        with self.assertRaisesRegex(ValueError, "differs from reviewed wrapper"):
            guard.check_plan(p, binding()["upgrade"])


class CostReceiptContracts(unittest.TestCase):
    def test_receipt_positive_and_authorized_horizon_not_eom(self):
        b, e, r = binding(), evidence(binding(), NOW), cost_receipt(binding(), NOW, (70, 40))
        e.update(actual_mtd_usd=r["actual_mtd_usd"],
                 projected_horizon_usd=r["projection"]["projected_horizon_usd"],
                 cost_receipt_sha256=guard.canonical_hash(r))
        e.update(r["qualification_inputs"])
        e["query_manifest_sha256"] = guard.query_manifest_hash(r["queries"])
        self.assertGreater(110 * 32, 2500)  # Full-month run-rate remains a warning.
        self.assertLess(e["projected_horizon_usd"], 2500)
        guard.qualify(b, e, SHA, NOW, r)

    def test_receipt_mutations_not_checkboxes(self):
        b = binding()
        for label, mutation, message in [
            ("missing", lambda r: r.pop("inventory"), None),
            ("partial", lambda r: r["queries"].pop(), "Complete per-subscription"),
            ("pruned inventory and queries", lambda r: (
                r["inventory"]["response"]["children"].pop(), r["queries"].pop()), "inventory digest"),
            ("duplicate", lambda r: r["queries"].__setitem__(1, r["queries"][0]), "exactly cover"),
            ("root pagination", lambda r: r["inventory"]["response"].update(nextLink="more"), "Paginated"),
            ("query pagination", lambda r: r["queries"][0]["response"]["properties"].update(nextLink="more"), "Paginated"),
            ("currency", lambda r: r["queries"][0]["response"]["properties"]["rows"][0].__setitem__(2, "EUR"), "mixed currency"),
            ("status", lambda r: r["queries"][0].update(http_status=403), "Successful"),
            ("filtered", lambda r: r["queries"][0]["request"]["dataset"].update(filter={"fake": True}), "unfiltered"),
            ("partial daily", lambda r: r["queries"][0]["response"]["properties"]["rows"].pop(0), "Partial actual"),
            ("boolean amount", lambda r: r["queries"][0]["response"]["properties"]["rows"][0].__setitem__(0, True), "Finite"),
            ("wrong actual", lambda r: r.update(actual_mtd_usd=0), "Actual cost total"),
            ("new raw amounts without accepted manifest", lambda r:
             r["queries"][0]["response"]["properties"]["rows"][0].__setitem__(0, 31), "query manifest digest"),
            ("unbound source", lambda r: r.update(source_sha="2" * 40), "source/binding"),
            ("unbound binding", lambda r: r.update(binding_sha256="f" * 64), "source/binding"),
            ("old inventory", lambda r: r["inventory"].update(observed_utc=(NOW - timedelta(days=2)).isoformat()), "stale"),
            ("old query", lambda r: r["queries"][0].update(observed_utc=(NOW - timedelta(days=2)).isoformat()), "stale"),
            ("projection days", lambda r: r["projection"].update(completed_days=["20261001"]), "basis"),
            ("partial-day omitted", lambda r: r["projection"].update(remaining_days=3), "full current partial day"),
            ("unchecked daily bound", lambda r: r["projection"].update(daily_bound_usd=1), "high-day projection"),
            ("insufficient lag days", lambda r: r["projection"].update(billing_lag_additional_days=1), "Billing-lag reserve"),
            ("zero projection", lambda r: r["projection"].update(projected_horizon_usd=0), "Projection total"),
            ("unchecked reserve", lambda r: r["qualification_inputs"].update(vm_reserve_usd=0), "qualification inputs"),
            ("blank lag basis", lambda r: r["projection"].update(billing_lag_basis=""), "lag basis"),
        ]:
            with self.subTest(mutation=label):
                r, e = cost_receipt(b, NOW), evidence(b, NOW)
                mutation(r)
                e["cost_receipt_sha256"] = guard.canonical_hash(r)
                if message is None:
                    with self.assertRaises(KeyError):
                        guard.qualify(b, e, SHA, NOW, r)
                else:
                    with self.assertRaisesRegex(ValueError, message):
                        guard.qualify(b, e, SHA, NOW, r)

    def test_nonfinite_bool_zero_and_unbound_receipts_rejected(self):
        b = binding()
        for value in (True, float("nan"), float("inf"), -1):
            e, r = evidence(b, NOW), cost_receipt(b, NOW)
            e["projected_horizon_usd"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Finite"):
                guard.qualify(b, e, SHA, NOW, r)
        r, e = cost_receipt(b, NOW, (0, 0)), evidence(b, NOW)
        r["qualification_inputs"]["billing_lag_reserve_usd"] = 100
        e["query_manifest_sha256"] = guard.query_manifest_hash(r["queries"])
        e.update(actual_mtd_usd=0, cost_receipt_sha256=guard.canonical_hash(r))
        with self.assertRaisesRegex(ValueError, "Zero tenant actual"):
            guard.qualify(b, e, SHA, NOW, r)
        e, r = evidence(b, NOW), cost_receipt(b, NOW)
        e["cost_receipt_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest"):
            guard.qualify(b, e, SHA, NOW, r)

    def test_actual_over_cap_still_blocked(self):
        b, r = binding(), cost_receipt(binding(), NOW, (170, 150))
        e = evidence(b, NOW)
        e.update(actual_mtd_usd=r["actual_mtd_usd"],
                 projected_horizon_usd=r["projection"]["projected_horizon_usd"],
                 cost_receipt_sha256=guard.canonical_hash(r))
        e.update(r["qualification_inputs"])
        e["query_manifest_sha256"] = guard.query_manifest_hash(r["queries"])
        with self.assertRaisesRegex(ValueError, "headroom"):
            guard.qualify(b, e, SHA, NOW, r)

    def test_horizon_expiry_return_and_lag_reserve_bound(self):
        b = binding()
        for mutation, message in [
            ({"budget_horizon_utc": "2026-10-31T21:59:59Z"}, "horizon"),
            ({"planned_return_utc": "2026-10-16T19:00:00Z"}, "planned return"),
            ({"vm_reserve_usd": 29.49}, "conservative VM reserve"),
            ({"billing_lag_reserve_usd": 0}, "Positive declared"),
        ]:
            e, r = evidence(b, NOW), cost_receipt(b, NOW)
            e.update(mutation)
            r["qualification_inputs"].update(mutation)
            if "vm_reserve_usd" in mutation:
                r["projection"]["projected_horizon_usd"] = round(
                    r["projection"]["projected_horizon_usd"] - 60 + mutation["vm_reserve_usd"], 2)
                e["projected_horizon_usd"] = r["projection"]["projected_horizon_usd"]
            e["cost_receipt_sha256"] = guard.canonical_hash(r)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, message):
                guard.qualify(b, e, SHA, NOW, r)
        late = guard.AUTHORIZED_HORIZON - timedelta(hours=1)
        r, e = cost_receipt(b, late), evidence(b, late)
        e["expires_utc"] = guard.AUTHORIZED_HORIZON.isoformat()
        guard.qualify(b, e, SHA, late, r)
        with self.assertRaises(ValueError):
            guard.qualify(b, e, SHA, guard.AUTHORIZED_HORIZON, r)

    def test_production_callers_seal_and_verify_receipt(self):
        folder = ROOT / ".terraform" / ("delivery-contract-" + uuid.uuid4().hex)
        folder.mkdir()
        original = Path.cwd()
        try:
            os.chdir(folder)
            b, r, e = binding(), cost_receipt(binding(), NOW), evidence(binding(), NOW)
            for name, value in (("upgrade.auto.tfvars.json", b), ("qualification.json", e),
                                ("cost-receipt.json", r), ("baseline.json", {"offline": True}),
                                ("tfplan.json", plan())):
                Path(name).write_text(json.dumps(value), encoding="utf-8")
            Path("tfplan").write_bytes(b"synthetic-saved-bytes")
            Path(".terraform.lock.hcl").write_bytes(b"synthetic-lock")
            with patch.object(guard, "datetime", wraps=datetime) as clock, \
                    patch.object(guard, "snapshot", return_value={"offline": True}), \
                    patch.dict(os.environ, GITHUB_RUN_ID="offline", GITHUB_RUN_ATTEMPT="1"):
                clock.now.return_value = NOW
                for operation in ("qualify", "snapshot", "plan", "seal", "verify"):
                    with patch.object(sys, "argv", ["delivery_guard.py", operation, "--sha", SHA]):
                        guard.main()
                seal = json.loads(Path("seal.json").read_text())
                self.assertEqual(set(seal["files"]), guard.SEALED_FILES)
                r["queries"].pop()
                Path("cost-receipt.json").write_text(json.dumps(r), encoding="utf-8")
                with patch.object(sys, "argv", ["delivery_guard.py", "verify", "--sha", SHA]), \
                        self.assertRaisesRegex(ValueError, "digest"):
                    guard.main()
        finally:
            os.chdir(original)
            shutil.rmtree(folder)


if __name__ == "__main__":
    unittest.main()
