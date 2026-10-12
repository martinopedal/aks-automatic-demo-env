"""Workflow/header and CLI privacy contracts using public synthetic inputs only."""
import contextlib
import copy
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

import yaml

from test_delivery import ROOT, SHA, NOW, binding, evidence, guard, cost_receipt

WORKFLOW = ROOT.parents[1] / ".github" / "workflows" / "deploy-demo-vm.yml"
INPUTS = {
    "BINDING": ("DEMO_VM_UPGRADE_BINDING_JSON", "upgrade.auto.tfvars.json"),
    "QUALIFICATION": ("DEMO_VM_UPGRADE_QUALIFICATION_JSON", "qualification.json"),
    "COST_RECEIPT": ("DEMO_VM_UPGRADE_COST_RECEIPT_JSON", "cost-receipt.json"),
}
PRIVATE_FIELDS = re.compile(
    r'["\'](?:vm_id|os_disk_id|inventory|queries|qualification_inputs|'
    r'binding_sha256|cost_receipt_sha256|planned_start_utc|planned_return_utc)["\']\s*:')


def qualification_step(workflow):
    return next(step for step in workflow["jobs"]["upgrade-plan"]["steps"]
                if step.get("name") == "Private scope and owner qualification")


def check_workflow(workflow):
    job = workflow["jobs"]["upgrade-plan"]
    if job.get("environment") != "online":
        raise AssertionError("Private inputs require the existing protected online environment")
    step = qualification_step(workflow)
    index = job["steps"].index(step)
    allowed = {
        ("jobs", "upgrade-plan", "steps", index, "env", alias):
        "${{ secrets." + name + " }}"
        for alias, (name, _) in INPUTS.items()
    }
    seen = set()

    def walk(node, path=()):
        if isinstance(node, dict):
            for key, value in node.items():
                child = path + (key,)
                if len(path) and path[-1] == "env" and key in INPUTS and child not in allowed:
                    raise AssertionError("Private env alias outside the pre-masked input step")
                walk(value, child)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, path + (i,))
        elif isinstance(node, str):
            if path in allowed:
                if node != allowed[path]:
                    raise AssertionError("Private input must be an exact secrets-context reference")
                seen.add(path)
            elif (any(name in node for name, _ in INPUTS.values()) or
                  any(re.search(r"\benv\s*(?:\.\s*" + alias + r"\b|\[\s*['\"]" +
                                alias + r"['\"]\s*\])", expression)
                      for expression in re.findall(r"\$\{\{(.*?)\}\}", node, re.S)
                      for alias in INPUTS) or PRIVATE_FIELDS.search(node)):
                raise AssertionError("Private input embedded in a loggable workflow field")
    walk(workflow)
    if seen != set(allowed):
        raise AssertionError("Required pre-masked private secret input missing")


class WorkflowPrivacyContracts(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    def test_actual_workflow_uses_only_protected_pre_masked_secret_context(self):
        check_workflow(self.workflow)

    def test_actual_callers_use_prepare_and_keep_private_diagnostics_local(self):
        step = qualification_step(self.workflow)
        self.assertIn("umask 077", step["run"])
        self.assertIn("python3 scripts/delivery_guard.py prepare", step["run"])
        self.assertNotIn("add-mask", step["run"])
        for alias in INPUTS:
            self.assertNotIn("$" + alias, step["run"])
        for job_name in ("offline", "offline-windows", "upgrade-plan"):
            commands = [step.get("run", "") for step in self.workflow["jobs"][job_name]["steps"]]
            install = next(i for i, command in enumerate(commands)
                           if "pip install" in command and "tests/requirements.txt" in command)
            tests = next(i for i, command in enumerate(commands) if "unittest discover" in command)
            self.assertLess(install, tests)
        for job_name in ("upgrade-plan", "upgrade-apply"):
            steps = self.workflow["jobs"][job_name]["steps"]
            validation = next(step["run"] for step in steps
                              if "terraform validate" in step.get("run", ""))
            self.assertIn("validation.log 2>&1", validation)
            cleanup = next(step["run"] for step in steps
                           if step.get("if") == "always()")
            self.assertIn("validation.log", cleanup)
            if job_name == "upgrade-plan":
                self.assertIn("contracts.log", cleanup)

    def test_restored_vars_missing_context_and_empty_secrets_rejected(self):
        for alias, (name, _) in INPUTS.items():
            for value in ("${{ vars." + name + " }}", "", "literal-private-input",
                          "${{ secrets." + name + " || vars." + name + " }}",
                          "${{ vars['" + name + "'] }}"):
                with self.subTest(alias=alias, form=value):
                    workflow = copy.deepcopy(self.workflow)
                    qualification_step(workflow)["env"][alias] = value
                    with self.assertRaisesRegex(AssertionError, "secrets-context"):
                        check_workflow(workflow)
            workflow = copy.deepcopy(self.workflow)
            del qualification_step(workflow)["env"][alias]
            with self.assertRaisesRegex(AssertionError, "missing"):
                check_workflow(workflow)

    def test_all_jobs_reject_private_run_with_and_env_header_regressions(self):
        for job_name in self.workflow["jobs"]:
            for alias, (name, _) in INPUTS.items():
                for field, value in (
                    ("run", "${{ secrets." + name + " }}"),
                    ("run", "${{ env." + alias + " }}"),
                    ("with", {"private-input": "${{ vars." + name + " }}"}),
                    ("with", {"private-input": "${{ env['" + alias + "'] }}"}),
                    ("with", {"private-input": json.dumps(binding())}),
                    ("env", {alias: "unmasked-synthetic-private-input"}),
                ):
                    with self.subTest(job=job_name, alias=alias, field=field):
                        workflow = copy.deepcopy(self.workflow)
                        workflow["jobs"][job_name]["steps"].append({field: value})
                        with self.assertRaises(AssertionError):
                            check_workflow(workflow)
        for location in ("global", "job"):
            workflow = copy.deepcopy(self.workflow)
            node = workflow if location == "global" else workflow["jobs"]["upgrade-plan"]
            node.setdefault("env", {})["COST_RECEIPT"] = json.dumps(cost_receipt(binding(), NOW))
            with self.assertRaisesRegex(AssertionError, "env alias"):
                check_workflow(workflow)
        workflow = copy.deepcopy(self.workflow)
        workflow["jobs"]["upgrade-plan"]["environment"] = "unprotected"
        with self.assertRaisesRegex(AssertionError, "protected"):
            check_workflow(workflow)

    def test_runner_header_model_masks_secrets_before_any_shell_runs(self):
        check_workflow(self.workflow)
        fixture = {
            "BINDING": json.dumps(binding()),
            "QUALIFICATION": json.dumps(evidence(binding(), NOW)),
            "COST_RECEIPT": json.dumps(cost_receipt(binding(), NOW)),
        }
        secrets = {INPUTS[alias][0]: value for alias, value in fixture.items()}
        masks = set(secrets.values())  # Registered by the secret context, before step headers.
        header = []
        for alias, expression in qualification_step(self.workflow)["env"].items():
            match = re.fullmatch(r"\$\{\{ secrets\.([A-Z_]+) \}\}", expression)
            value = secrets[match[1]] if match else "synthetic-public-transport-status"
            header.append(f"{alias}: {'***' if value in masks else value}")
        rendered = "\n".join(header)
        for alias, value in fixture.items():
            self.assertIn(f"{alias}: ***", rendered)
            self.assertNotIn(value, rendered)


class PrivateInputCallerContracts(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT / ".terraform" / ("privacy-contract-" + uuid.uuid4().hex)
        self.folder.mkdir(parents=True)
        self.original = Path.cwd()
        os.chdir(self.folder)
        self.inputs = {
            "BINDING": json.dumps(binding()),
            "QUALIFICATION": json.dumps(evidence(binding(), NOW)),
            "COST_RECEIPT": json.dumps(cost_receipt(binding(), NOW)),
        }

    def tearDown(self):
        os.chdir(self.original)
        shutil.rmtree(self.folder)

    def call(self, operation, inputs=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, inputs or {}, clear=True), \
                patch.object(sys, "argv", ["delivery_guard.py", operation, "--sha", SHA]), \
                patch.object(guard, "datetime", wraps=datetime) as clock, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            clock.now.return_value = NOW
            status = guard.cli()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_prepare_is_exact_and_does_not_print_private_values(self):
        status, stdout, stderr = self.call("prepare", self.inputs)
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Qualification accepted", stdout)
        for alias, (_, filename) in INPUTS.items():
            self.assertEqual(Path(filename).read_text(encoding="utf-8"), self.inputs[alias])
            self.assertNotIn(self.inputs[alias], stdout)

    def test_unset_empty_and_whitespace_secrets_fail_before_creating_files(self):
        candidates = [{}]
        for alias in INPUTS:
            for value in (None, "", " \n\t "):
                inputs = dict(self.inputs)
                if value is None:
                    del inputs[alias]
                else:
                    inputs[alias] = value
                candidates.append(inputs)
        for inputs in candidates:
            with self.subTest(context=list(inputs)):
                status, stdout, stderr = self.call("prepare", inputs)
                self.assertEqual((status, stdout), (1, ""))
                self.assertIn("Delivery blocked:", stderr)
                self.assertFalse(any(Path(filename).exists() for _, filename in INPUTS.values()))
                for value in self.inputs.values():
                    self.assertNotIn(value, stderr)

    def test_all_private_file_callers_redact_payload_derived_exceptions(self):
        for alias, (_, filename) in INPUTS.items():
            Path(filename).write_text(self.inputs[alias], encoding="utf-8")
        marker = "synthetic-private-timestamp-do-not-log"
        malformed = json.loads(self.inputs["QUALIFICATION"])
        malformed["observed_utc"] = marker
        Path("qualification.json").write_text(json.dumps(malformed), encoding="utf-8")
        for operation in ("qualify", "snapshot", "plan", "seal", "verify"):
            with self.subTest(operation=operation):
                status, stdout, stderr = self.call(operation)
                self.assertEqual((status, stdout), (1, ""))
                self.assertIn("Delivery blocked:", stderr)
                self.assertNotIn(marker, stderr)
                self.assertNotIn("Traceback", stderr)
        inputs = dict(self.inputs, QUALIFICATION=json.dumps(malformed))
        status, stdout, stderr = self.call("prepare", inputs)
        self.assertEqual((status, stdout), (1, ""))
        self.assertNotIn(marker, stderr)
        for alias in INPUTS:
            for invalid in ('{"private-field": "synthetic-private-json",', "[]", "null", "true"):
                with self.subTest(alias=alias, shape=invalid):
                    status, stdout, stderr = self.call("prepare", dict(self.inputs, **{alias: invalid}))
                    self.assertEqual((status, stdout), (1, ""))
                    self.assertNotIn("synthetic-private-json", stderr)
                    self.assertNotIn("Traceback", stderr)

    def test_snapshot_captures_private_cli_stderr(self):
        with patch.object(guard.subprocess, "check_output", side_effect=RuntimeError("synthetic-stop")) as run:
            with self.assertRaises(RuntimeError):
                guard.snapshot(binding()["upgrade"])
        self.assertEqual(run.call_args.kwargs.get("stderr"), guard.subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()
