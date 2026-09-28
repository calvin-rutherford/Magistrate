"""Synthetic release journeys, NOT Apple, model-provider or physical-device proof.

Only identity, GitHub OAuth, Stripe HTTPS and model edges are fakes. Onboarding,
signed webhook ingress, project/memory/files, sessions and chat use real stores.
The mandatory live Spencer/moat gates remain in the release registry.
"""
import hashlib
import hmac
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit

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
    monkeypatch.setenv("MAGISTRATE_PROVIDER_SESSION_SCOPES", "read,account,providers,notifications,voice,command")
    monkeypatch.setenv("MAGISTRATE_OBJECT_STORAGE_DIR", str(tmp_path / "objects"))
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "synthetic-github-client")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "synthetic-github-secret")
    monkeypatch.setenv("MAGISTRATE_OAUTH_CALLBACK_BASE_URL", "https://gateway.example.test")
    monkeypatch.setenv("MAGISTRATE_OAUTH_REDIRECT_URIS", "magistrate://account")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_release_fixture")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_release_fixture")
    monkeypatch.setenv("MAGISTRATE_BILLING_RETURN_ORIGINS", "magistrate://chat")
    catalog = json.loads((Path(__file__).parents[1] / "billing_catalog.json").read_text())
    next(plan for plan in catalog["plans"] if plan["id"] == "individual")["stripe_price_id"] = "price_release_fixture"
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    monkeypatch.setenv("MAGISTRATE_BILLING_CATALOG_PATH", str(catalog_path))
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

    async def exchange(code):
        assert code == "synthetic-github-code"
        return {"access_token": "synthetic-github-token"}

    async def profile(token):
        assert token == "synthetic-github-token"
        return {"id": 12345, "login": "synthetic-spencer"}

    class SyntheticStripe:
        async def post(self, path, data, *, idempotency_key):
            assert idempotency_key
            if path == "/customers":
                return {"id": "cus_release_fixture"}
            assert path == "/checkout/sessions"
            assert ("line_items[0][price]", "price_release_fixture") in data
            return {"id": "cs_release_fixture", "url": "https://checkout.stripe.com/synthetic-fixture"}

    monkeypatch.setattr(gateway.providers["github"], "exchange_code", exchange)
    monkeypatch.setattr(gateway.providers["github"], "get_user_profile", profile)
    monkeypatch.setattr("app.billing_api.billing_service.stripe_factory", SyntheticStripe)
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
    assert first.post("/api/v1/account/onboarding/welcome", headers=headers).json()["next_step"] == "profile"
    assert first.post("/api/v1/account/profile", headers=headers, data={"name": "Spencer"}).status_code == 200
    assert first.get("/api/v1/account/onboarding", headers=headers).json()["next_step"] == "github"
    oauth = first.get("/api/v1/auth/github/connect", headers=headers)
    assert oauth.status_code == 200
    state = parse_qs(urlsplit(oauth.json()["auth_url"]).query)["state"][0]
    callback = first.get("/api/v1/auth/github/callback", params={
        "state": state, "code": "synthetic-github-code",
    }, follow_redirects=False)
    assert callback.status_code == 307
    assert callback.headers["location"] == "magistrate://account?status=success"
    assert first.get("/api/v1/account/onboarding", headers=headers).json()["next_step"] == "billing"
    checkout = first.post("/api/v1/billing/checkout", headers=headers, json={
        "catalog_id": "individual", "return_url": "magistrate://chat", "idempotency_key": "spencer-checkout",
    })
    assert checkout.status_code == 200, checkout.text
    # Neither default Free credit allocation nor checkout navigation is a paid
    # subscription. This specifically composes A2 onboarding with A4 billing.
    assert first.get("/api/v1/auth/session", headers=headers).json()["onboarding_required"] is True
    now = int(time.time())
    raw = json.dumps({"id": "evt_release_fixture", "created": now,
        "type": "customer.subscription.updated", "data": {"object": {
            "id": "sub_release_fixture", "customer": "cus_release_fixture", "status": "active",
            "metadata": {"magistrate_owner": session["user_id"], "catalog_id": "individual"},
            "items": {"data": [{"price": {"id": "price_release_fixture"}}]},
            "current_period_end": now + 3600,
        }}}).encode()
    signature = hmac.new(b"whsec_release_fixture", str(now).encode() + b"." + raw, hashlib.sha256).hexdigest()
    webhook_headers = {"Stripe-Signature": f"t={now},v1={signature}", "Content-Type": "application/json"}
    assert first.post("/api/v1/billing/webhooks/stripe", headers=webhook_headers, content=raw).json()["status"] == "processed"
    assert first.post("/api/v1/billing/webhooks/stripe", headers=webhook_headers, content=raw).json()["status"] == "duplicate"
    assert first.get("/api/v1/auth/session", headers=headers).json()["onboarding_required"] is False
    assert first.get("/api/v1/magi/conversations/current", headers=headers).json()["messages"] == []

    project = first.post("/api/v1/projects", headers=headers, json={"name": "Alder"})
    assert project.status_code == 201
    project_id = project.json()["id"]
    memory_path = f"/api/v1/magi/memory/entries/alder-fact?project_id={project_id}"
    memory = first.put(memory_path, headers=headers, json={
        "memory_key": "alder-fact", "kind": "conversation-fact", "title": "Alder codename",
        "content": "The project codename is Alder.",
    })
    assert memory.status_code == 200
    upload = first.post("/api/v1/uploads", headers=headers, files=[("files", ("alder.txt", b"Alder evidence", "text/plain"))])
    assert upload.status_code == 200
    upload_id = upload.json()["uploads"][0]["upload_id"]
    assert first.get(f"/api/v1/uploads/{upload_id}", headers=headers).content == b"Alder evidence"

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
    assert second.get(f"/api/v1/projects/{project_id}", headers=other_headers).status_code == 404
    assert second.get(f"/api/v1/magi/memory/search?project_id={project_id}&q=Alder", headers=other_headers).status_code == 404
    assert second.get(f"/api/v1/uploads/{upload_id}", headers=other_headers).status_code == 404
    remembered = second.get(f"/api/v1/magi/memory/search?project_id={project_id}&q=Alder", headers=second_headers)
    assert remembered.json()["results"][0]["id"] == memory.json()["entry"]["id"]

    cancelled = json.loads(raw)
    cancelled.update(id="evt_release_cancelled", type="customer.subscription.deleted", created=now + 1)
    cancel_raw = json.dumps(cancelled).encode()
    cancel_signature = hmac.new(b"whsec_release_fixture", str(now).encode() + b"." + cancel_raw, hashlib.sha256).hexdigest()
    assert first.post("/api/v1/billing/webhooks/stripe", headers={"Stripe-Signature": f"t={now},v1={cancel_signature}"}, content=cancel_raw).status_code == 200
    # Cancellation's active Free credit state is not paid onboarding either.
    assert first.get("/api/v1/auth/session", headers=headers).json()["onboarding_required"] is True
    assert second.post("/api/v1/auth/session/revoke", headers=second_headers).status_code == 200
    assert second.get(path, headers=second_headers).status_code == 401
    assert second.post("/api/v1/auth/provider/refresh", json={"refresh_token": second_session["refresh_token"]}).status_code == 401
    erased = first.request("DELETE", "/api/v1/account", headers=headers, json={"confirmation": f"DELETE {session['user_id']}"})
    assert erased.status_code == 200
    assert first.get(path, headers=headers).status_code == 401
    assert first.post("/api/v1/auth/provider/refresh", json={"refresh_token": session["refresh_token"]}).status_code == 401


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
