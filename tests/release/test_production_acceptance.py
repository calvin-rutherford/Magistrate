"""Release admission tests use synthetic evidence, never a passing real packet."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("production_acceptance", ROOT / "scripts/production_acceptance.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
REVISION = "a" * 40
STAMP = "2026-01-01T00:00:00Z"


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = gate.load_registry()
        # ONLY receipt-validator fixtures; never actual live-service evidence.
        # Preserve explicit gap refusal tests even though all merged suites exist.
        self.fixture_registry = copy.deepcopy(self.registry)
        for suite in self.fixture_registry["suites"]:
            if not suite["commands"]:
                suite["gap"] = None
                suite["commands"] = [gate.command(".", "synthetic-fixture-only")]
        self.gap_registry = copy.deepcopy(self.fixture_registry)
        gap = next(row for row in self.gap_registry["suites"] if row["id"] == "billing-ledger")
        gap.update(commands=[], gap="Synthetic unimplemented domain")
        self.report = {
            "schema_version": "magistrate.hermetic-receipt.v1", "revision": REVISION,
            "registry_sha256": gate.registry_digest(), "clean": True,
            "started_at": STAMP, "finished_at": STAMP,
            "results": [{"id": row["id"], "status": "COMPLETE", "exit_code": 0, "reason": None}
                        for row in self.registry["suites"]],
        }
        self.packet = gate.template(self.registry, REVISION)
        evidence = self.root / "synthetic-attestation.txt"
        evidence.write_text("Synthetic validator fixture; not production evidence.\n")
        for acceptance in self.packet["acceptances"]:
            acceptance.update(status="COMPLETE", operator="Synthetic test operator", observed_at=STAMP)
            for check in acceptance["checks"]:
                check.update(status="COMPLETE", artifact={"path": evidence.name, "sha256": gate.digest(evidence)})
        self.packet_path = self.root / "packet.json"

    def save(self):
        report_path = self.root / "receipt.json"
        report_path.write_text(json.dumps(self.report))
        self.packet["reports"] = [{"path": report_path.name, "sha256": gate.digest(report_path)}]
        self.packet_path.write_text(json.dumps(self.packet))

    def verify(self):
        self.save()
        gate.verify_packet(self.packet_path, self.fixture_registry, REVISION)

    def test_registry_requires_every_domain_and_external_journey(self):
        self.assertGreaterEqual(set(gate.indexed(self.registry["suites"])), gate.REQUIRED_SUITES)
        self.assertGreaterEqual(set(gate.indexed(self.registry["acceptances"])), gate.REQUIRED_ACCEPTANCES)
        for name in gate.DOCS:
            self.assertTrue((ROOT / "docs" / f"{name}.md").is_file(), name)
        for suite in self.registry["suites"]:
            for command in suite["commands"]:
                for arg in command["argv"]:
                    if arg.startswith("tests/"):
                        self.assertTrue((ROOT / command["cwd"] / arg.split("::")[0]).exists(), arg)

    def test_authoritative_docs_links_and_statuses_are_valid(self):
        for name in gate.DOCS:
            path = ROOT / "docs" / f"{name}.md"
            for href in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                if "://" not in href:
                    self.assertTrue((path.parent / href.split("#")[0]).exists(), href)
        rows = [line for line in (ROOT / "docs/PRODUCTION_STATUS.md").read_text().splitlines()
                if line.startswith("| ") and not line.startswith("| Capability ")]
        self.assertGreater(len(rows), 20)
        for line in rows:
            self.assertIn(line.split("|")[-2].strip(), gate.STATES)

    def test_merged_registry_has_no_unimplemented_suite_or_retired_entrypoint(self):
        for suite in self.registry["suites"]:
            self.assertTrue(suite["commands"], suite["id"])
            self.assertIsNone(suite["gap"], suite["id"])
        github = gate.indexed(self.registry["suites"])["github"]
        self.assertIn("tests/test_github_app_integration.py", github["commands"][0]["argv"])
        self.assertNotIn("tests/test_github_service.py", github["commands"][0]["argv"])
        self.assertIn("dependency-security", gate.indexed(gate.indexed(self.registry["acceptances"])["activation"]["checks"]))

    def test_postgres_fixture_never_inherits_deployment_authority(self):
        spec = importlib.util.spec_from_file_location("postgres_release", ROOT / "scripts/test_postgres_release.py")
        postgres = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(postgres)
        with patch.dict(postgres.os.environ, {"MAGISTRATE_DATABASE_URL": "production", "MAGISTRATE_HOSTED_EXECUTION_ENABLED": "true", "OPENAI_API_KEY": "private", "FM_HOME": "/never-use", "STRIPE_SECRET_KEY": "private"}):
            environment = postgres.test_environment(self.root, 54321)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("FM_HOME", environment)
        self.assertNotIn("STRIPE_SECRET_KEY", environment)
        self.assertNotIn("MAGISTRATE_HOSTED_EXECUTION_ENABLED", environment)
        self.assertEqual(environment["MAGISTRATE_ENV"], "test")
        self.assertIn("@127.0.0.1:54321/", environment["MAGISTRATE_DATABASE_URL"])

    def test_spencer_and_seven_moat_checkpoints_cannot_disappear(self):
        required = {
            "spencer-new-user": {"fresh-account", "onboarding", "github-project", "billing-budget",
                                 "objective-evidence", "attention-answer", "upload-voice",
                                 "offline-and-second-device", "push-deep-link", "retirement"},
            "moat": {"provider-replacement", "harness-replacement", "context-continuity", "cost-routing",
                     "background-autonomy", "attention-loop", "device-independence"},
        }
        for name, checks in required.items():
            self.assertGreaterEqual(set(gate.indexed(gate.indexed(self.registry["acceptances"])[name]["checks"])), checks)

    def test_complete_synthetic_packet_is_structurally_valid(self):
        self.verify()

    def test_repository_gap_cannot_be_waived_by_receipt(self):
        self.save()
        with self.assertRaises(gate.GateError):
            gate.verify_packet(self.packet_path, self.gap_registry, REVISION)

    def test_template_is_not_accepted(self):
        self.packet = gate.template(self.registry, REVISION)
        with self.assertRaises(gate.GateError):
            self.verify()

    def test_missing_suite_checkpoint_and_acceptance_fail(self):
        for target in ("suite", "checkpoint", "acceptance"):
            with self.subTest(target=target):
                original_report, original_packet = copy.deepcopy(self.report), copy.deepcopy(self.packet)
                if target == "suite":
                    self.report["results"].pop()
                elif target == "checkpoint":
                    self.packet["acceptances"][0]["checks"].pop()
                else:
                    self.packet["acceptances"].pop()
                with self.assertRaises(gate.GateError):
                    self.verify()
                self.report, self.packet = original_report, original_packet

    def test_dirty_stale_failed_duplicate_and_boolean_exit_receipts_fail(self):
        mutations = [
            lambda: self.report.update(clean=False),
            lambda: self.report.update(revision="b" * 40),
            lambda: self.report.update(registry_sha256="b" * 64),
            lambda: self.report["results"][0].update(status="FAILED"),
            lambda: self.report["results"][0].update(exit_code=False),
            lambda: self.report["results"].append(self.report["results"][0]),
            lambda: self.report.update(started_at="2999-01-01T00:00:00Z"),
        ]
        for mutate in mutations:
            original = copy.deepcopy(self.report)
            mutate()
            with self.assertRaises(gate.GateError):
                self.verify()
            self.report = original

    def test_hermetic_or_unattested_evidence_cannot_be_physical_proof(self):
        for field, value in (("kind", "hermetic"), ("status", "SKIPPED")):
            original = copy.deepcopy(self.packet)
            self.packet["acceptances"][0]["checks"][0][field] = value
            with self.assertRaises(gate.GateError):
                self.verify()
            self.packet = original
        self.packet["acceptances"][0]["operator"] = None
        with self.assertRaises(gate.GateError):
            self.verify()

    def test_artifact_integrity_and_path_confinement(self):
        reference = self.packet["acceptances"][0]["checks"][0]["artifact"]
        for path in ("../outside", "/absolute", "missing.txt"):
            with self.assertRaises(gate.GateError):
                gate.artifact(self.root, {**reference, "path": path})
        symlink = self.root / "linked.txt"
        symlink.symlink_to(self.root / reference["path"])
        with self.assertRaises(gate.GateError):
            gate.artifact(self.root, {**reference, "path": symlink.name})
        (self.root / reference["path"]).write_text("Changed artifact")
        with self.assertRaises(gate.GateError):
            self.verify()

    def test_duplicate_json_keys_and_unknown_contract_fields_fail(self):
        self.packet_path.write_text('{"revision":"a","revision":"b"}')
        with self.assertRaises(gate.GateError):
            gate.load_json(self.packet_path)
        self.packet["waived"] = True
        with self.assertRaises(gate.GateError):
            self.verify()

    def test_registry_rejects_removed_gate_and_ambiguous_gap(self):
        for mutation in (lambda r: r["suites"].pop(), lambda r: r["suites"][0].update(gap="pretend")):
            changed = copy.deepcopy(self.registry)
            mutation(changed)
            path = self.root / "registry.json"
            path.write_text(json.dumps(changed))
            with self.assertRaises(gate.GateError):
                gate.load_registry(path)

    def test_missing_implementation_emits_failed_receipt_without_subprocess(self):
        output = self.root / "gap.json"
        with patch.object(gate, "git_value", side_effect=lambda *args: REVISION if args[0] == "rev-parse" else ""), patch.object(gate.subprocess, "run") as run:
            self.assertFalse(gate.run_suites(self.gap_registry, ["billing-ledger"], output))
            run.assert_not_called()
        report = json.loads(output.read_text())
        self.assertEqual(report["results"][0]["reason"], "missing_implementation")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_failed_and_unavailable_commands_cannot_pass_or_overwrite(self):
        for index, behavior in enumerate((SimpleNamespace(returncode=9), OSError(), subprocess.TimeoutExpired("test", 1))):
            output = self.root / f"command-{index}.json"
            with patch.object(gate, "git_value", side_effect=lambda *args: REVISION if args[0] == "rev-parse" else ""), patch.object(gate.subprocess, "run") as run:
                if isinstance(behavior, Exception):
                    run.side_effect = behavior
                else:
                    run.return_value = behavior
                self.assertFalse(gate.run_suites(self.registry, ["unit"], output))
                with self.assertRaises(gate.GateError):
                    gate.run_suites(self.registry, ["unit"], output)
            self.assertEqual(json.loads(output.read_text())["results"][0]["status"], "FAILED")

    def test_argv_execution_and_dirty_worktree_are_recorded_truthfully(self):
        output = self.root / "dirty.json"
        with patch.object(gate, "git_value", side_effect=lambda *args: REVISION if args[0] == "rev-parse" else " M fixture"), patch.object(gate.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(gate.run_suites(self.registry, ["unit"], output))
        report = json.loads(output.read_text())
        self.assertFalse(report["clean"])
        self.assertIsInstance(run.call_args.args[0], list)
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["env"]["TMPDIR"], "/tmp")


if __name__ == "__main__":
    unittest.main()
