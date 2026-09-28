#!/usr/bin/env python3
"""Content-free, fail-closed release receipts; never provisions or deploys services.

The registry contains argv arrays, not shell commands. Hermetic test receipts
cannot substitute for live-service or physical-device acceptance attestations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def command(cwd: str, *argv: str, timeout: int = 600) -> dict:
    return {"cwd": cwd, "argv": list(argv), "timeout_seconds": timeout}


def suite(name: str, owner: str, *commands: dict, gap: str | None = None) -> dict:
    return {"id": name, "owner": owner, "commands": list(commands), "gap": gap}


def gateway_tests(*files: str) -> dict:
    return command("gateway", "uv", "run", "pytest", "-v", *files)


def check(name: str, kind: str, requirement: str) -> dict:
    return {"id": name, "kind": kind, "requirement": requirement}


# Release-owned registry. Keep this in the executable entrypoint so adding a
# gate and enforcing it are reviewed together; `list` exports the JSON view.
REGISTRY_DATA: dict = {
    "schema_version": "magistrate.production-acceptance.v1",
    "suites": [
        suite("release-foundation", "A12 release", command(".", "python3", "-m", "unittest", "discover", "-s", "tests/release", "-v", timeout=120)),
        suite("unit", "A12 + module owners", gateway_tests("tests/test_magi_model.py", "tests/test_db_secrets.py", "tests/test_execution_capabilities.py")),
        suite("integration", "A12 integration", command("gateway", "uv", "run", "pytest", "-v", timeout=1200)),
        suite("tenant-authorization", "A1 identity / A11 security", gateway_tests("tests/test_auth_boundary.py", "tests/test_identity_tenancy_projects.py", "tests/test_production_security.py", "tests/test_github_app_integration.py", "tests/test_project_memory.py", "tests/test_hosted_execution.py", "tests/test_uploads.py")),
        suite("provider-auth", "A2 auth", gateway_tests("tests/test_provider_auth.py", "tests/test_oauth_transactions.py", "tests/test_friend_beta_access.py", "tests/test_billing_onboarding.py")),
        suite("github", "A3 GitHub", gateway_tests("tests/test_github_app_integration.py", "tests/test_truthful_provider_contracts.py", "tests/test_recent_activity.py")),
        suite("billing-ledger", "A4 billing", gateway_tests("tests/test_billing.py", "tests/test_billing_onboarding.py")),
        suite("magi-context-routing", "A6 context / A7 routing", gateway_tests("tests/test_magi_native_chat.py", "tests/test_magi_model.py", "tests/test_magi_routing.py", "tests/test_project_memory.py", "tests/test_magi_tools.py", "tests/test_magi_execution_handoff.py")),
        suite("execution-recovery-isolation", "A5 execution / A11 security", gateway_tests("tests/test_firstmate_intake.py", "tests/test_firstmate_execution.py", "tests/test_firstmate_decisions.py", "tests/test_hosted_execution.py", "tests/test_runtime_read_boundaries.py", "tests/test_fleet_product.py", "tests/test_pinned_firstmate_producer_integration.py")),
        suite("uploads", "A10 files", gateway_tests("tests/test_uploads.py", "tests/test_perception_protocol.py", "tests/test_storage_operations.py")),
        suite("voice", "A9 voice", gateway_tests("tests/test_stt_adapter.py", "tests/test_voice_session_backgrounds.py")),
        suite("push-deep-links", "A9 voice / A8 UI / A12 receipts", gateway_tests("tests/test_notification_transitions.py", "tests/test_push_receipts.py"), command("frontend", "npm", "run", "test:pending-intents")),
        suite("frontend", "A8 UI", command("frontend", "npm", "run", "typegen"), command("frontend", "npm", "run", "typecheck"), command("frontend", "npm", "run", "lint"), command("frontend", "npm", "test", timeout=7200), command("frontend", "npx", "expo", "export", "--platform", "web")),
        suite("migrations", "A12 + schema owners", gateway_tests("tests/test_release_restore.py", "tests/test_identity_tenancy_projects.py", "tests/test_billing_onboarding.py", "tests/test_magi_native_chat.py::test_old_gateway_database_migrates_additively_without_deleting_rows", "tests/test_db_secrets.py")),
        suite("postgres-persistence", "A1 persistence / A12 release", command(".", "python3", "scripts/test_postgres_release.py")),
        suite("backup-restore", "A12 release / A11 security", gateway_tests("tests/test_release_restore.py", "tests/test_storage_operations.py"), command(".", "bash", "scripts/test_deploy_magistrate.sh")),
        suite("security-boundaries", "A11 security", gateway_tests("tests/test_production_security.py", "tests/test_runtime_read_boundaries.py")),
        suite("release-configuration", "A12 / A9 distribution", command("frontend", "npm", "run", "test:release")),
        suite("deployment-smoke", "A12 release", command(".", "bash", "scripts/test_deployment_workflow.sh"), command(".", "bash", "scripts/test_deploy_magistrate.sh"), command(".", "bash", "scripts/test_smoke_friend_beta.sh")),
        suite("backend", "A12 retained-backend regression", command("backend", "python3", "-m", "pytest", "-v")),
        suite("pi-extension", "A12 retained-adapter regression", command("pi-extension", "npm", "run", "typecheck"), command("pi-extension", "npm", "test")),
        suite("spencer-hermetic", "A12 integration", gateway_tests("tests/test_release_journeys.py")),
        suite("moat-hermetic", "A12 / A5 / A6 / A7", gateway_tests("tests/test_release_journeys.py", "tests/test_magi_routing.py", "tests/test_project_memory.py", "tests/test_hosted_execution.py", "tests/test_magi_execution_handoff.py", "tests/test_firstmate_decisions.py", "tests/test_firstmate_execution.py", "tests/test_runtime_read_boundaries.py")),
        suite("client-contract", "A12 shared contracts", gateway_tests("tests/test_client_protocol_export.py", "tests/test_native_chat_architecture.py")),
    ],
    "acceptances": [
        {"id": "spencer-new-user", "owner": "Release operator + Spencer", "checks": [
            check("fresh-account", "live-service", "Spencer starts without a bootstrap secret, CLI access, runner config or preseeded profile; real Apple/Google sign-in creates the intended principal."),
            check("onboarding", "physical-device", "Fresh signed install prompts for the required name, then exposes Chat / Projects / Fleet / Activity / Attention with truthful empty states."),
            check("github-project", "live-service", "Install/connect the approved GitHub integration; choose only an authorized repository; create a project; another principal cannot enumerate it."),
            check("billing-budget", "live-service", "Use approved Stripe test-mode funding/subscription, duplicate and out-of-order webhooks, reservation/settlement/refund; ledger reconciles and over-budget execution is denied."),
            check("objective-evidence", "live-service", "Submit a bounded real objective via Native Magi; observe durable execution, verified checks and a full forge artifact URL, never inferred completion."),
            check("attention-answer", "physical-device", "Receive a real decision, confirm the exact answer from the canonical user row; stale/replayed/other-owner answers do not execute."),
            check("upload-voice", "physical-device", "Upload/download an owner file; record and transcribe voice into the same thread; deny microphone access; interrupt and background capture without an unintended submission."),
            check("offline-and-second-device", "physical-device", "Kill/reopen, network handoff and second-device sign-in preserve canonical IDs/revisions; no duplicate objective or cross-account cached history."),
            check("push-deep-link", "physical-device", "Real provider ticket AND receipt; tap cold/warm notification into the correct authenticated Attention item; denied/offline delivery retains in-app fallback."),
            check("retirement", "live-service", "Exercise logout, expired access, refresh reuse/revocation, account deletion/retention contract and credential retirement; old device cannot continue accessing data."),
        ]},
        {"id": "moat", "owner": "A12 release + domain operators", "checks": [
            check("provider-replacement", "live-service", "Replace the actual Magi provider through its approved adapter while keeping user-visible transcript identity and truthful failures; no terminal fallback."),
            check("harness-replacement", "live-service", "Replace an approved execution harness at a safe durable handoff with context/evidence continuity, no duplicate work and no model-owned lifecycle."),
            check("context-continuity", "live-service", "Recall an authorized project fact across provider, harness, process and device changes; verify provenance, deletion and another-tenant refusal."),
            check("cost-routing", "live-service", "Demonstrate cheap/expensive route policy, budget denial, observed usage and ledger reconciliation; missing usage is unknown, not zero."),
            check("background-autonomy", "live-service", "Disconnect all clients; worker progresses and recovers from an approved failure using durable queue/events. Reads neither wake nor schedule it."),
            check("attention-loop", "live-service", "Background work pauses on an authenticated decision, notifies the owner, accepts one confirmed answer and resumes with durable evidence."),
            check("device-independence", "physical-device", "Continue the same account/project on web and physical iPhone without runner secrets or a continuously open client."),
        ]},
        {"id": "activation", "owner": "Production account owners + A12", "checks": [
            check("provider-consoles", "live-service", "Verify Apple/Google exact audiences and redirects, GitHub installation permissions and real model credentials against the candidate endpoint."),
            check("stripe-reconciliation", "live-service", "Verify the approved Stripe account/mode, price IDs, signed webhook endpoint, replay and ledger reconciliation; no payment success inferred from browser return."),
            check("persistent-restore", "live-service", "Rehearse a backup and restore of DB, upload bytes and required secret versions into an isolated target; compare row/evidence hashes, ownership and smoke results."),
            check("isolated-workers", "live-service", "Prove per-tenant worker credentials/files/network isolation, pinned producer, one-shot recovery and restricted execution egress."),
            check("edge-monitoring", "live-service", "Verify public DNS/TLS, same-site cookies, WSS, proxy limits, redacted alerts, retention and authenticated readiness without a runtime probe."),
            check("legal-security", "operator-review", "Named legal/security owners approve entity, privacy/terms/support/deletion URLs, subprocessors, consent, incident response, data retention and release threat model."),
            check("dependency-security", "operator-review", "Review current npm and Python dependency scans, reachable advisory remediation or explicit time-bounded risk disposition, isolated build credentials and exact candidate lockfiles; a green functional suite is not a security approval."),
        ]},
        {"id": "distribution", "owner": "Expo/Apple release account owner", "checks": [
            check("eas-candidate", "live-service", "Production preflight, real ascAppId, clean candidate commit, EAS build ID/build number, signing and archive scan match the release packet."),
            check("testflight-candidate", "physical-device", "Exact candidate processes in App Store Connect and installs through TestFlight; execute DEAT-001 on recorded iPhone/iOS/network combinations."),
            check("store-submission", "operator-review", "App privacy, export compliance, screenshots, age/content rating, review account, legal URLs and release/rollback ownership approved for App Store submission."),
        ]},
    ],
}
STATES = {"COMPLETE", "BLOCKED_EXTERNAL", "FAILED"}
REQUIRED_SUITES = {
    "release-foundation", "unit", "integration", "tenant-authorization",
    "provider-auth", "github", "billing-ledger", "magi-context-routing",
    "execution-recovery-isolation", "uploads", "voice", "push-deep-links",
    "frontend", "migrations", "backup-restore", "deployment-smoke", "backend",
    "pi-extension", "spencer-hermetic", "moat-hermetic", "client-contract",
    "postgres-persistence", "security-boundaries", "release-configuration",
}
REQUIRED_ACCEPTANCES = {"spencer-new-user", "moat", "activation", "distribution"}
DOCS = (
    "PRODUCTION_STATUS", "PRODUCTION_ACTIVATION", "SAAS_ARCHITECTURE",
    "MAGISTRATE_ROUTING", "CLIENT_PROTOCOL", "SECURITY_MODEL", "BILLING_MODEL",
    "CONTEXT_PLANE", "AMBIENT_MAGI",
)
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[a-z][a-z0-9-]{1,79}\Z")
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024


class GateError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def load_json(path: Path):
    require(path.stat().st_size <= MAX_DOCUMENT_BYTES, "Evidence document is too large")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def exact(value, keys: set[str]) -> None:
    require(isinstance(value, dict) and set(value) == keys, "Unexpected contract fields")


def nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2048


def timestamp(value) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), "Expected UTC timestamp")
    try:
        result = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise GateError("Invalid UTC timestamp") from exc
    require(result <= datetime.now(timezone.utc), "Future evidence is not accepted")
    return result


def indexed(items) -> dict:
    require(isinstance(items, list), "Expected a list")
    result = {}
    for item in items:
        require(isinstance(item, dict) and isinstance(item.get("id"), str), "Missing gate id")
        require(ID.fullmatch(item["id"]) is not None and item["id"] not in result, "Invalid or duplicate gate id")
        result[item["id"]] = item
    return result


def registry_digest() -> str:
    return hashlib.sha256(json.dumps(REGISTRY_DATA, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_registry(path: Path | None = None) -> dict:
    registry = load_json(path) if path is not None else json.loads(json.dumps(REGISTRY_DATA))
    exact(registry, {"schema_version", "suites", "acceptances"})
    require(registry["schema_version"] == "magistrate.production-acceptance.v1", "Unknown registry schema")
    suites = indexed(registry["suites"])
    require(set(suites) >= REQUIRED_SUITES, "Required suite removed")
    for suite in suites.values():
        exact(suite, {"id", "owner", "commands", "gap"})
        require(nonempty(suite["owner"]), "Suite owner required")
        require(isinstance(suite["commands"], list), "Suite commands must be a list")
        require(bool(suite["commands"]) != bool(suite["gap"]), "Suite must have commands OR an explicit repository gap")
        require(suite["gap"] is None or nonempty(suite["gap"]), "Invalid suite gap")
        for command in suite["commands"]:
            exact(command, {"cwd", "argv", "timeout_seconds"})
            require(command["cwd"] in {".", "gateway", "frontend", "backend", "pi-extension"}, "Unrecognized suite directory")
            require(isinstance(command["argv"], list) and command["argv"] and all(nonempty(arg) for arg in command["argv"]), "Expected nonempty argv")
            require(type(command["timeout_seconds"]) is int and 1 <= command["timeout_seconds"] <= 7200, "Unbounded command timeout")
    acceptances = indexed(registry["acceptances"])
    require(set(acceptances) >= REQUIRED_ACCEPTANCES, "Required acceptance removed")
    for acceptance in acceptances.values():
        exact(acceptance, {"id", "owner", "checks"})
        require(nonempty(acceptance["owner"]), "Acceptance owner required")
        checks = indexed(acceptance["checks"])
        require(bool(checks), "Empty acceptance")
        for check in checks.values():
            exact(check, {"id", "kind", "requirement"})
            require(check["kind"] in {"live-service", "physical-device", "operator-review"}, "Invalid external evidence kind")
            require(nonempty(check["requirement"]), "Acceptance requirement missing")
    return registry


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def git_value(*args) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def write_json(path: Path, value) -> None:
    # Never overwrite another run's receipts or an operator's file.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def run_suites(registry: dict, selection: list[str], output: Path) -> bool:
    suites = indexed(registry["suites"])
    chosen = selection or list(suites)
    require(len(chosen) == len(set(chosen)) and set(chosen) <= set(suites), "Unknown or duplicate suite")
    require(not output.exists(), "Receipt already exists")
    revision = git_value("rev-parse", "HEAD")
    clean = not git_value("status", "--porcelain", "--untracked-files=all")
    contract_digest = registry_digest()
    report = {
        "schema_version": "magistrate.hermetic-receipt.v1", "revision": revision,
        "registry_sha256": contract_digest, "clean": clean,
        "started_at": utc_now(), "finished_at": None, "results": [],
    }
    for name in chosen:
        suite = suites[name]
        result = {"id": name, "status": "FAILED", "exit_code": None, "reason": None}
        if suite["gap"]:
            result["reason"] = "missing_implementation"
        else:
            for command in suite["commands"]:
                print(f"Running {name} ({command['cwd']})", flush=True)
                try:
                    # Deep worktrees exceed AF_UNIX limits in Chrome and the
                    # deployment fixtures. Grant-output tests also deliberately
                    # require temporary artifacts outside the release checkout.
                    code = subprocess.run(
                        command["argv"], cwd=ROOT / command["cwd"],
                        env={**os.environ, "TMPDIR": "/tmp"},
                        timeout=command["timeout_seconds"], check=False,
                    ).returncode
                except subprocess.TimeoutExpired:
                    result["reason"] = "timeout"
                    break
                except OSError:
                    result["reason"] = "command_unavailable"
                    break
                result["exit_code"] = code
                if code != 0:
                    result["reason"] = "command_failed"
                    break
            else:
                result["status"] = "COMPLETE"
        report["results"].append(result)
        print(f"{result['status']}: {name}", flush=True)
    report["finished_at"] = utc_now()
    report["clean"] = clean and not git_value("status", "--porcelain", "--untracked-files=all")
    require(git_value("rev-parse", "HEAD") == revision and registry_digest() == contract_digest, "Checkout changed during run")
    write_json(output, report)
    return all(row["status"] == "COMPLETE" for row in report["results"])


def artifact(base: Path, reference) -> Path:
    exact(reference, {"path", "sha256"})
    require(nonempty(reference["path"]) and isinstance(reference["sha256"], str) and DIGEST.fullmatch(reference["sha256"]) is not None, "Invalid artifact reference")
    relative = Path(reference["path"])
    require(not relative.is_absolute() and not {"..", "."}.intersection(relative.parts), "Artifact must be packet-relative")
    current = base
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), "Symlink evidence is not accepted")
    require(current.is_file() and current.stat().st_size > 0, "Artifact is missing or empty")
    require(digest(current) == reference["sha256"], "Artifact digest mismatch")
    return current


def verify_packet(packet_path: Path, registry: dict, revision: str) -> None:
    require(SHA.fullmatch(revision) is not None, "Expected full candidate commit SHA")
    packet = load_json(packet_path)
    exact(packet, {"schema_version", "revision", "registry_sha256", "reports", "acceptances"})
    require(packet["schema_version"] == "magistrate.release-evidence.v1", "Unknown evidence schema")
    require(packet["revision"] == revision and packet["registry_sha256"] == registry_digest(), "Stale candidate or registry evidence")
    suites = indexed(registry["suites"])
    require(isinstance(packet["reports"], list) and packet["reports"], "Hermetic receipts required")
    results = {}
    for ref in packet["reports"]:
        report = load_json(artifact(packet_path.parent, ref))
        exact(report, {"schema_version", "revision", "registry_sha256", "clean", "started_at", "finished_at", "results"})
        require(report["schema_version"] == "magistrate.hermetic-receipt.v1", "Not a hermetic receipt")
        require(report["revision"] == revision and report["registry_sha256"] == packet["registry_sha256"] and report["clean"] is True, "Dirty or stale suite receipt")
        require(timestamp(report["started_at"]) <= timestamp(report["finished_at"]), "Invalid receipt chronology")
        for name, result in indexed(report["results"]).items():
            exact(result, {"id", "status", "exit_code", "reason"})
            require(name in suites and name not in results, "Unknown or duplicate suite receipt")
            require(suites[name]["commands"] and result["status"] == "COMPLETE" and type(result["exit_code"]) is int and result["exit_code"] == 0 and result["reason"] is None, "Suite did not complete")
            results[name] = result
    require(set(results) == set(suites), "Missing mandatory suites")
    actual = indexed(packet["acceptances"])
    expected = indexed(registry["acceptances"])
    require(set(actual) == set(expected), "Missing or unknown external acceptance")
    for name, acceptance in actual.items():
        exact(acceptance, {"id", "status", "operator", "observed_at", "checks"})
        require(acceptance["status"] in STATES and acceptance["status"] == "COMPLETE", "External acceptance is not complete")
        require(nonempty(acceptance["operator"]), "Named human attestation required")
        timestamp(acceptance["observed_at"])
        checks = indexed(acceptance["checks"])
        requirements = indexed(expected[name]["checks"])
        require(set(checks) == set(requirements), "Missing or unknown acceptance checkpoint")
        for check_id, check in checks.items():
            exact(check, {"id", "status", "kind", "artifact"})
            require(check["status"] == "COMPLETE" and check["kind"] == requirements[check_id]["kind"], "Wrong evidence class or incomplete checkpoint")
            artifact(packet_path.parent, check["artifact"])


def template(registry: dict, revision: str) -> dict:
    require(SHA.fullmatch(revision) is not None, "Expected full candidate commit SHA")
    return {
        "schema_version": "magistrate.release-evidence.v1", "revision": revision,
        "registry_sha256": registry_digest(), "reports": [],
        "acceptances": [
            {"id": gate["id"], "status": "BLOCKED_EXTERNAL", "operator": None,
             "observed_at": None, "checks": [
                 {"id": check["id"], "status": "BLOCKED_EXTERNAL", "kind": check["kind"], "artifact": None}
                 for check in gate["checks"]]}
            for gate in registry["acceptances"]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("check")
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("--suite", action="append", default=[])
    run.add_argument("--output", type=Path, required=True)
    form = sub.add_parser("template")
    form.add_argument("--revision", required=True)
    form.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("packet", type=Path)
    verify.add_argument("--revision", required=True)
    args = parser.parse_args()
    try:
        registry = load_registry()
        if args.action == "check":
            require(all((ROOT / "docs" / f"{name}.md").is_file() for name in DOCS), "Authoritative release document missing")
            print("COMPLETE: release registry and document presence (not production acceptance)")
        elif args.action == "list":
            print(json.dumps(registry, indent=2))
        elif args.action == "run":
            return 0 if run_suites(registry, args.suite, args.output) else 1
        elif args.action == "template":
            write_json(args.output, template(registry, args.revision))
        else:
            verify_packet(args.packet, registry, args.revision)
            print("COMPLETE: evidence structure and hashes; release authority must review attestations")
        return 0
    except (GateError, OSError, ValueError, TypeError, KeyError) as exc:
        # Do not echo untrusted documents, provider output, or environment values.
        print(f"FAILED: {exc if isinstance(exc, GateError) else 'invalid or inaccessible release input'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
