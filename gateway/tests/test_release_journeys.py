"""Synthetic release journeys, NOT Apple, model-provider or physical-device proof.

Only the external identity verifier and model edge are fakes. Account/session,
HTTP ownership, profile, transcript, replay and revocation use the real store.
The mandatory live Spencer/moat gates remain in the release registry.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import db
import app.main as gateway
import app.magi_chat_api as native_api
from app.magi_chat_service import MAGI_DIRECT_RESPONSE, MagiChatService
from app.magi_chat_store import MagiChatStore
from app.magi_model import MagiModelResult, MagiModelToolCall
from app.provider_auth import ProviderClaims


class ReplacementModel:
    def __init__(self, label):
        self.label = label
        self.contexts = []

    async def complete(self, messages, *, system_context, request_id, tools=()):
        self.contexts.append(tuple(message.content for message in messages))
        if tools:
            return MagiModelResult(None, finish_reason="tool_calls", tool_calls=(
                MagiModelToolCall("release_direct", MAGI_DIRECT_RESPONSE, "{}"),
            ))
        return MagiModelResult(f"{self.label}: exact reply café 東京\n")


@pytest.fixture
def journey(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "release-journey.sqlite3"))
    monkeypatch.setenv("MAGISTRATE_APPLE_CLIENT_IDS", "io.magistrate.cockpit")
    monkeypatch.setenv("MAGISTRATE_PROVIDER_SESSION_SCOPES", "read,account,notifications,voice,command")
    db.init_db()
    nonce_counter = 0

    async def synthetic_verifier(provider, identity_token, raw_nonce):
        nonlocal nonce_counter
        nonce_counter += 1
        return ProviderClaims(
            provider="apple", subject=identity_token, email=None,
            email_verified=False, name=None,
            assertion_hash=hashlib.sha256(f"fixture-{nonce_counter}".encode()).hexdigest(),
            audience="io.magistrate.cockpit",
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError("Synthetic new-user journey must not touch execution infrastructure")

    monkeypatch.setattr("app.provider_auth.verify_identity_token", synthetic_verifier)
    monkeypatch.setattr(gateway.fm_client, "get_snapshot", forbidden)
    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden)
    model = ReplacementModel("provider-A")
    monkeypatch.setattr(native_api.magi_chat_service, "model", model)
    return model


def sign_in(client, subject):
    challenge = client.post("/api/v1/auth/provider/challenge", json={
        "provider": "apple", "client_platform": "native",
    })
    assert challenge.status_code == 200
    response = client.post("/api/v1/auth/provider/exchange", json={
        "provider": "apple", "challenge_id": challenge.json()["challenge_id"],
        "nonce": challenge.json()["nonce"], "identity_token": subject,
    })
    assert response.status_code == 200
    session = response.json()
    return {"Authorization": f"Bearer {session['session_token']}"}, session


def test_spencer_new_account_onboarding_chat_second_device_and_retirement(journey):
    first = TestClient(gateway.app)
    assert first.get("/api/v1/magi/conversations/current").status_code == 401
    headers, session = sign_in(first, "synthetic-spencer-subject")
    assert session["user_id"] != "default_user"
    inspected = first.get("/api/v1/auth/session", headers=headers).json()
    assert inspected["onboarding_required"] is True
    assert first.post("/api/v1/account/profile", headers=headers, data={"name": "Spencer"}).status_code == 200
    assert first.get("/api/v1/auth/session", headers=headers).json()["onboarding_required"] is False
    assert first.get("/api/v1/magi/conversations/current", headers=headers).json()["messages"] == []

    body = {"client_message_id": "spencer-first-message", "content": "Remember the project codename: Alder."}
    submitted = first.post("/api/v1/magi/messages", headers=headers, json=body)
    assert submitted.status_code == 200
    original = submitted.json()
    assert original["status"] == "completed"
    assert original["assistant_message"]["content"] == "provider-A: exact reply café 東京\n"
    calls = len(journey.contexts)

    # An independently authenticated installation sees the same canonical rows,
    # not a device-local conversation or a newly synthesized welcome message.
    second = TestClient(gateway.app)
    second_headers, second_session = sign_in(second, "synthetic-spencer-subject")
    assert second_session["user_id"] == session["user_id"]
    assert second.get("/api/v1/account/profile", headers=second_headers).json()["name"] == "Spencer"
    restored = second.get("/api/v1/magi/conversations/current", headers=second_headers).json()
    assert restored["messages"] == original["messages"]
    duplicate = second.post("/api/v1/magi/messages", headers=second_headers, json=body).json()
    assert duplicate["duplicate"] is True
    assert duplicate["messages"] == original["messages"]
    assert len(journey.contexts) == calls

    other_headers, _ = sign_in(TestClient(gateway.app), "synthetic-other-subject")
    path = f"/api/v1/magi/conversations/{original['conversation']['id']}"
    assert second.get(path, headers=other_headers).status_code == 404
    assert second.get(path + "/replay?after=0", headers=other_headers).status_code == 404
    assert second.get("/api/v1/magi/conversations/current", headers=other_headers).json()["messages"] == []

    assert second.post("/api/v1/auth/session/revoke", headers=second_headers).status_code == 200
    assert second.get(path, headers=second_headers).status_code == 401
    assert second.post("/api/v1/auth/provider/refresh", json={"refresh_token": second_session["refresh_token"]}).status_code == 401


@pytest.mark.asyncio
async def test_provider_boundary_replacement_preserves_context_and_canonical_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "provider-replacement.sqlite3"))
    first_model = ReplacementModel("provider-A")
    first = MagiChatService(first_model, store=MagiChatStore(), profile_loader=lambda _: {})
    original = await first.submit("spencer", "continuity-first", "Private fact: Alder.")
    replacement_model = ReplacementModel("provider-B")
    replacement = MagiChatService(replacement_model, store=MagiChatStore(), profile_loader=lambda _: {})
    continued = await replacement.submit("spencer", "continuity-second", "Continue our project.")
    assert continued["conversation"]["id"] == original["conversation"]["id"]
    assert "Private fact: Alder." in replacement_model.contexts[-1]
    assert original["assistant_message"]["content"] in replacement_model.contexts[-1]
    replay = replacement.store.submission("spencer", "continuity-first")
    assert replay["messages"] == original["messages"]
    await replacement.submit("other-owner", "continuity-second", "What do you know?")
    assert all("Alder" not in (content or "") for content in replacement_model.contexts[-1])
    # This proves the injectable provider boundary, NOT a second deployed
    # provider, harness migration, portable memory, cost policy or live autonomy.
