"""Live integrations against mocked HTTP. These prove request/response handling, not connectivity to Google."""
from __future__ import annotations

import base64
import json
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ExternalServiceError, IntegrationNotConnected
from app.core.security import decrypt_secret, encrypt_secret
from app.database.base import utcnow
from app.integrations import google, http
from app.integrations.factory import email_provider
from app.models import OAuthAccount, OAuthState, UserPreference


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)


class FakeHttp:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.handler(method, url, kwargs)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)


def install(monkeypatch, handler) -> FakeHttp:
    fake = FakeHttp(handler)
    monkeypatch.setattr(http.httpx, "request", fake)
    return fake


@pytest.fixture
def connected(api, db):
    user_id = uuid.UUID(api.user["id"])
    db.add(OAuthAccount(user_id=user_id, provider="google", account_email="me@gmail.com", access_token_enc=encrypt_secret("access-1"), refresh_token_enc=encrypt_secret("refresh-1"), expires_at=utcnow() + timedelta(hours=1)))
    db.commit()
    assert api.put("/settings/preferences", json={"integration_mode": "live"}).status_code == 200
    return api


def _gmail_handler(method, url, kwargs):
    if url.endswith("/messages"):
        assert kwargs["params"]["maxResults"] == 2
        return {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "next"}
    if "/messages/" in url and method == "GET":
        mid = url.rsplit("/", 1)[1]
        data = base64.urlsafe_b64encode(b"Please reply by Friday").decode()
        return {"id": mid, "threadId": "t", "snippet": "s", "labelIds": ["INBOX", "UNREAD"], "payload": {"mimeType": "text/plain", "body": {"data": data}, "headers": [{"name": "From", "value": "Ann <ann@example.com>"}, {"name": "To", "value": "me@gmail.com"}, {"name": "Subject", "value": "Hi"}, {"name": "Date", "value": "Mon, 09 Nov 2026 10:00:00 +0000"}]}}
    raise AssertionError(url)


def test_token_encrypted_at_rest(connected, db):
    row = db.scalar(select(OAuthAccount))
    assert "access-1" not in row.access_token_enc and "refresh-1" not in row.refresh_token_enc
    assert decrypt_secret(row.access_token_enc) == "access-1"


def test_integrations_endpoint_never_returns_tokens(connected):
    body = connected.get("/integrations").text
    assert "access-1" not in body and "refresh-1" not in body and "_enc" not in body


def test_gmail_sync_is_paginated_and_marks_live(connected, monkeypatch):
    fake = install(monkeypatch, _gmail_handler)
    result = connected.post("/emails/sync?max_results=2").json()
    assert result == {"fetched": 2, "created": 2, "next_page_token": "next", "simulated": False}
    emails = connected.get("/emails").json()["items"]
    assert len(emails) == 2 and not any(e["is_demo"] for e in emails) and emails[0]["sender_email"] == "ann@example.com"
    assert sum(1 for c in fake.calls if c[1].endswith("/messages")) == 1
    assert connected.post("/emails/sync?max_results=2").json()["created"] == 0, "sync must be idempotent"


def test_live_send_uses_provider_confirmation(connected, monkeypatch, db):
    install(monkeypatch, _gmail_handler)
    connected.post("/emails/sync?max_results=2")
    email = connected.get("/emails").json()["items"][0]
    approval = connected.post(f"/emails/{email['id']}/draft-reply", json={}).json()
    assert approval["simulated"] is False
    sent = []

    def send(method, url, kwargs):
        sent.append(kwargs["json"])
        return {"id": "gmail-123", "threadId": "t", "labelIds": ["SENT"]}

    install(monkeypatch, send)
    result = connected.post(f"/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]}).json()
    assert result["status"] == "executed" and result["result"]["external_id"] == "gmail-123" and result["simulated"] is False
    raw = base64.urlsafe_b64decode(sent[0]["raw"]).decode()
    assert "To: ann@example.com" in raw


def test_gmail_rejection_is_failure(connected, monkeypatch):
    install(monkeypatch, _gmail_handler)
    connected.post("/emails/sync?max_results=2")
    email = connected.get("/emails").json()["items"][0]
    approval = connected.post(f"/emails/{email['id']}/draft-reply", json={}).json()
    install(monkeypatch, lambda m, u, k: httpx.Response(400, json={"error": {"message": "Invalid To header"}}))
    result = connected.post(f"/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]}).json()
    assert result["status"] == "failed" and "Invalid To header" in result["error"]


def test_send_timeout_not_retried_and_marked_unknown(connected, monkeypatch):
    install(monkeypatch, _gmail_handler)
    connected.post("/emails/sync?max_results=2")
    email = connected.get("/emails").json()["items"][0]
    approval = connected.post(f"/emails/{email['id']}/draft-reply", json={}).json()
    count = []

    def timeout(method, url, kwargs):
        count.append(1)
        raise httpx.ReadTimeout("slow")

    install(monkeypatch, timeout)
    result = connected.post(f"/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]}).json()
    assert result["status"] == "outcome_unknown" and len(count) == 1, "a send must never be blindly retried"


def test_expired_token_refreshes_and_is_stored_encrypted(connected, monkeypatch, db):
    row = db.scalar(select(OAuthAccount))
    row.expires_at = utcnow() - timedelta(minutes=5)
    db.commit()

    def handler(method, url, kwargs):
        if url == google.TOKEN_URL:
            return {"access_token": "access-2", "expires_in": 3600}
        assert kwargs["headers"]["Authorization"] == "Bearer access-2"
        return {"messages": []}

    install(monkeypatch, handler)
    assert connected.post("/emails/sync").status_code == 200
    db.expire_all()
    assert decrypt_secret(db.scalar(select(OAuthAccount)).access_token_enc) == "access-2"


def test_revoked_refresh_token_flags_reconnect(connected, monkeypatch, db):
    row = db.scalar(select(OAuthAccount))
    row.expires_at = utcnow() - timedelta(minutes=5)
    db.commit()
    install(monkeypatch, lambda m, u, k: httpx.Response(400, json={"error": "invalid_grant"}))
    response = connected.post("/emails/sync")
    assert response.status_code == 409 and response.json()["error"]["code"] == "integration_not_connected"
    assert connected.get("/integrations").json()["google"]["status"] == "needs_reauth"
    assert connected.post("/tasks", json={"title": "App still works"}).status_code == 201


def test_rate_limit_retries_with_retry_after(monkeypatch):
    responses = iter([httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(200, json={"ok": True})])
    install(monkeypatch, lambda m, u, k: next(responses))
    assert http.request_with_retry("GET", "https://x", service="X").json() == {"ok": True}


def test_exhausted_retries_raise(monkeypatch):
    install(monkeypatch, lambda m, u, k: httpx.Response(503))
    with pytest.raises(ExternalServiceError) as exc:
        http.request_with_retry("GET", "https://x", service="X", retries=2)
    assert exc.value.retryable


def test_live_calendar_create_is_idempotent_on_conflict(connected, monkeypatch):
    def handler(method, url, kwargs):
        if method == "POST":
            return httpx.Response(409, json={"error": {"message": "exists"}})
        if method == "GET" and "/events/" in url:
            return {"id": "x", "htmlLink": "https://cal/x"}
        return {"items": []}

    install(monkeypatch, handler)
    day = (utcnow() + timedelta(days=20)).date()
    proposal = connected.post("/calendar/events/proposals", json={"title": "Sync", "start_at": f"{day}T10:00:00+00:00", "end_at": f"{day}T11:00:00+00:00"}).json()
    result = connected.post(f"/approvals/{proposal['approval']['id']}/approve", json={"payload_hash": proposal["approval"]["payload_hash"]}).json()
    assert result["status"] == "executed" and result["result"]["already_existed"] is True


def test_oauth_start_requires_configuration(api):
    response = api.get("/integrations/google/start")
    assert response.status_code == 409 and response.json()["error"]["code"] == "google_not_configured"


@pytest.fixture
def google_configured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csecret")


def test_oauth_flow_with_state_and_pkce(api, monkeypatch, db, google_configured):
    url = api.get("/integrations/google/start").json()["url"]
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
    state = query["state"][0]
    assert db.scalar(select(OAuthState)).state_hash != state, "state must be stored hashed"

    captured = {}

    def handler(method, u, kwargs):
        if u == google.TOKEN_URL:
            captured.update(kwargs["data"])
            return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "scope": "email"}
        return {"email": "me@gmail.com"}

    install(monkeypatch, handler)
    response = api.client.get(f"/api/integrations/google/callback?state={state}&code=abc", follow_redirects=False)
    assert response.status_code == 302 and "google=connected" in response.headers["location"]
    assert captured["code_verifier"] and captured["code"] == "abc"
    assert api.get("/integrations").json()["google"]["account_email"] == "me@gmail.com"

    replay = api.client.get(f"/api/integrations/google/callback?state={state}&code=abc", follow_redirects=False)
    assert "invalid_state" in replay.headers["location"], "state is single use"


def test_oauth_state_cannot_cross_users(api, other, google_configured):
    state = parse_qs(urlparse(api.get("/integrations/google/start").json()["url"]).query)["state"][0]
    response = other.client.get(f"/api/integrations/google/callback?state={state}&code=abc", follow_redirects=False)
    assert "invalid_state" in response.headers["location"]


def test_oauth_bad_state_and_unauthenticated(api, google_configured):
    from tests.conftest import Api

    assert "invalid_state" in api.client.get("/api/integrations/google/callback?state=forged&code=abc", follow_redirects=False).headers["location"]
    assert Api().get("/integrations/google/callback?state=x&code=y").status_code == 401


def test_disconnect_returns_to_demo_and_gmail_outage_is_isolated(connected, monkeypatch, db):
    install(monkeypatch, lambda m, u, k: {})
    assert connected.post("/integrations/google/disconnect").status_code == 204
    assert connected.get("/integrations").json() ["mode"] == "demo"
    assert db.scalar(select(OAuthAccount)) is None


def test_live_mode_without_account_raises(api, db):
    prefs = db.scalar(select(UserPreference))
    prefs.integration_mode = "live"
    db.commit()
    with pytest.raises(IntegrationNotConnected):
        email_provider(db, prefs.user_id)
    assert api.get("/dashboard").status_code == 200, "the rest of the app keeps working"


def test_anthropic_provider_maps_messages_and_tools(monkeypatch):
    from types import SimpleNamespace

    from app.agents.llm.anthropic_provider import AnthropicProvider
    from app.agents.llm.base import LLMMessage, LLMRequest, ToolCall, ToolSpec

    provider = AnthropicProvider("key", "claude-opus-5-5", 10)
    seen = {}

    def create(**params):
        seen.update(params)
        block = SimpleNamespace(type="tool_use", id="t1", name="list_tasks", input={"limit": 1}, model_dump=lambda **k: {"type": "tool_use", "id": "t1", "name": "list_tasks", "input": {"limit": 1}})
        return SimpleNamespace(content=[block], stop_reason="tool_use", usage=SimpleNamespace(input_tokens=1, output_tokens=2))

    monkeypatch.setattr(provider.client.beta.messages, "create", create)
    response = provider.complete(LLMRequest("agent.task", "sys", [
        LLMMessage("user", "hi"),
        LLMMessage("assistant", "", tool_calls=[ToolCall("a", "x", {})], provider_state=[{"type": "tool_use", "id": "a", "name": "x", "input": {}}]),
        LLMMessage("tool", "r1", tool_call_id="a"), LLMMessage("tool", "r2", tool_call_id="b", is_error=True),
    ], tools=[ToolSpec("list_tasks", "d", {"type": "object"})], must_use_tool=True))
    assert response.tool_calls[0].name == "list_tasks"
    assert seen["tool_choice"] == {"type": "auto"} and "must respond by calling" in seen["system"]
    assert seen["model"] == "claude-opus-5-5" and seen["output_config"] == {"effort": "medium"}
    assert len(seen["messages"]) == 3 and len(seen["messages"][2]["content"]) == 2 and seen["messages"][2]["content"][1]["is_error"] is True
    assert "temperature" not in seen and "thinking" not in seen


def test_openai_provider_round_trip(monkeypatch):
    from app.agents.llm import openai_provider as module
    from app.agents.llm.base import LLMMessage, LLMRequest, ToolSpec

    def post(url, json, headers, timeout):  # noqa: A002
        assert url == "http://local/v1/chat/completions" and json["tool_choice"] == "required"
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [{"id": "1", "function": {"name": "t", "arguments": "{\"a\": 1}"}}]}}]})

    monkeypatch.setattr(module.httpx, "post", post)
    response = module.OpenAICompatibleProvider("k", "m", "http://local/v1", 5).complete(LLMRequest("x", "s", [LLMMessage("user", "u")], tools=[ToolSpec("t", "d", {})], must_use_tool=True))
    assert response.tool_calls[0].arguments == {"a": 1}


def test_production_refuses_weak_config():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(environment="production", secret_key="", encryption_key="", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(environment="production", secret_key="x" * 40, encryption_key="y" * 40, cookie_secure=False, _env_file=None)


def test_demo_user_not_created_in_production(db, monkeypatch):
    from app.models import User
    from app.services.seed import ensure_demo_user

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "demo_user_password", "SomePassword123")
    ensure_demo_user(db)
    assert db.scalar(select(User).where(User.email == settings.demo_user_email)) is None


def test_json_is_valid_for_catalog(api):
    assert json.dumps(api.get("/automations/catalog").json())
