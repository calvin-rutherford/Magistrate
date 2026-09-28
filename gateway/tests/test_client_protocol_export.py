import json
import os
import subprocess
import sys

from app import db
from app.main import app
from scripts.export_client_protocol import client_protocol


def test_protocol_export_is_deterministic_and_has_no_runtime_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Schema export must not initialize or inspect persistence")

    monkeypatch.setattr(db, "init_db", forbidden)
    first = json.dumps(client_protocol(), sort_keys=True)
    assert first == json.dumps(client_protocol(), sort_keys=True)
    document = json.loads(first)
    assert document["schema_version"] == "magistrate.client-protocol.v1"
    assert document["conversation"]["schema_version"] == "magi.native-chat.v1"
    schemas = document["schemas"]
    for name in ("native_message", "submit_objective", "completion_evidence", "decision_events", "answer_decision",
                 "measured_usage", "checkout", "portal", "memory_write", "perception_event",
                 "perception_confirmation", "execution_requirements"):
        assert schemas[name]["additionalProperties"] is False
    request = schemas["native_message"]
    assert request["properties"]["source"]["enum"] == ["text", "voice"]
    assert {"user_id", "owner_user_id", "tenant_id", "target", "provider", "harness"}.isdisjoint(request["properties"])
    assert schemas["execution_event"]["discriminator"]["propertyName"] == "phase"
    assert set(schemas["answer_decision"]["properties"]) == {"decision_id", "decision_revision"}
    assert request["properties"]["explicit_confirmation"]["default"] is False
    for name in ("checkout", "portal", "memory_write", "perception_event", "execution_requirements"):
        assert {"user_id", "owner_user_id", "tenant_id", "stripe_customer_id"}.isdisjoint(schemas[name]["properties"])


def test_cli_never_opens_the_configured_deployment_database(tmp_path):
    deployment = tmp_path / "must-not-create.sqlite3"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.export_client_protocol"],
        env={**os.environ, "MAGISTRATE_ENV": "production",
             "MAGISTRATE_DB_PATH": str(deployment), "MAGISTRATE_SECRET_KEY": "invalid-do-not-use",
             "MAGISTRATE_DATABASE_URL": "postgresql://never-connect@127.0.0.1:1/production",
             "MAGISTRATE_STATE_DIR": str(tmp_path / "must-not-create-state")},
        capture_output=True, text=True, check=True,
    )
    assert not deployment.exists()
    assert not (tmp_path / "must-not-create-state").exists()
    assert json.loads(result.stdout) == client_protocol()
    assert "invalid-do-not-use" not in result.stdout + result.stderr


def test_exported_paths_match_the_executable_native_routes():
    document = client_protocol()
    paths = set(app.openapi()["paths"]) | {"/api/v1/events"}
    assert any(getattr(route, "path", None) == "/api/v1/events" for route in app.routes)
    for key, path in document["conversation"].items():
        if key != "schema_version":
            assert path in paths
    assert set(document["domains"].values()) <= paths
    assert document["events"]["path"] in paths
    assert document["events"]["authentication"] == "first-frame"
    assert document["events"]["conversation_type"] == "magi_messages"
    assert document["events"]["activity_type"] == "activity_records"
