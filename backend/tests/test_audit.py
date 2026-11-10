"""Regression tests for the findings of the security and completeness audit."""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.agents.llm.factory import set_provider_override
from app.database.base import utcnow
from app.database.session import SessionLocal
from app.models import AgentRun, Email, Message, OAuthAccount, UserPreference
from tests.test_agents import Scripted, call, finish, plan_for, sequence


def _reply(api, subject="Dinner"):
    email = next(e for e in api.get("/emails?page_size=50").json()["items"] if subject in e["subject"])
    return email, api.post(f"/emails/{email['id']}/draft-reply", json={}).json()


def _set_mode(api, mode):
    with SessionLocal() as session:
        prefs = session.scalar(select(UserPreference).where(UserPreference.user_id == uuid.UUID(api.user["id"])))
        prefs.integration_mode = mode
        session.commit()


def test_simulated_card_never_runs_for_real_after_switching_to_live(api, monkeypatch):
    from app.integrations.google import GmailProvider

    _, approval = _reply(api)
    assert approval["simulated"] is True
    with SessionLocal() as session:
        session.add(OAuthAccount(user_id=uuid.UUID(api.user["id"]), provider="google", access_token_enc="x"))
        session.commit()
    _set_mode(api, "live")
    sent = []
    monkeypatch.setattr(GmailProvider, "send", lambda self, *a: sent.append(a))
    response = api.post(f"/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "mode_changed"
    assert sent == []
    assert api.get(f"/approvals/{approval['id']}").json()["status"] == "expired"


def test_edit_cannot_retarget_linked_record(api):
    first = api.get("/emails?page_size=50").json()["items"]
    _, approval = _reply(api)
    other = next(e for e in first if e["id"] != approval["payload"]["email_id"])
    payload = {**approval["payload"], "email_id": other["id"]}
    response = api.put(f"/approvals/{approval['id']}", json={"expected_hash": approval["payload_hash"], "payload": payload})
    assert response.status_code == 422 and response.json()["error"]["code"] == "reference_immutable"


def test_subject_header_injection_rejected(api):
    _, approval = _reply(api)
    payload = {**approval["payload"], "subject": "Hi\r\nBcc: spy@example.com"}
    assert api.put(f"/approvals/{approval['id']}", json={"expected_hash": approval["payload_hash"], "payload": payload}).status_code == 422


def test_conversation_history_is_marked_as_data(api):
    provider = Scripted({"intent": "respond", "objective": "x", "response": "ok"}, sequence())
    set_provider_override(provider)
    first = api.chat("hello")
    api.chat("and again", first["conversation_id"])
    prompt = provider.requests[-1].messages[0].content
    assert "<untrusted_data" in prompt and "recent_turns" in prompt.split("<untrusted_data")[1]
    assert "recent_turns" not in prompt.split("<untrusted_data")[0]


def test_contact_lookup_taints_the_run(api):
    day = (utcnow() + timedelta(days=20)).date()
    provider = Scripted(plan_for("calendar"), sequence(call("find_contact", name="Priya"), call("propose_event", title="Sync", start=f"{day}T10:00:00", end=f"{day}T11:00:00"), finish()))
    set_provider_override(provider)
    api.chat("x")
    assert api.pending()[0]["derived_from_untrusted"] is True


def test_docx_zip_bomb_refused_without_crashing(api, monkeypatch):
    from app.services import documents

    monkeypatch.setattr(documents, "MAX_DOCX_UNZIPPED_BYTES", 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"0" * 100_000)
    response = api.post("/documents/upload", files={"file": ("bomb.docx", buffer.getvalue())})
    assert response.status_code == 201 and response.json()["status"] == "failed" and "unsafe" in response.json()["error"]


@pytest.mark.parametrize("name,data", [("broken.docx", b"PK\x03\x04garbage"), ("broken.pdf", b"%PDF-1.7 garbage")])
def test_corrupt_files_fail_gracefully(api, name, data):
    response = api.post("/documents/upload", files={"file": (name, data)})
    assert response.status_code == 201 and response.json()["status"] == "failed"


def test_orphaned_chat_run_becomes_retryable(api):
    from app.agents.orchestrator import recover_stuck_runs

    reply = api.chat("Find my overdue tasks")
    with SessionLocal() as session:
        message = session.get(Message, uuid.UUID(reply["id"]))
        run = session.get(AgentRun, message.run_id)
        message.status, run.status, run.started_at = "streaming", "running", utcnow() - timedelta(hours=1)
        session.commit()
        assert recover_stuck_runs(session) == 1
    detail = api.get(f"/ai/conversations/{reply['conversation_id']}").json()["messages"][-1]
    assert detail["status"] == "error" and detail["meta"]["error"]["retryable"] is True


def test_confirmed_event_update_needs_approval(api):
    day = (utcnow() + timedelta(days=25)).date()
    created = api.post("/calendar/events/proposals", json={"title": "Old", "start_at": f"{day}T08:00:00+00:00", "end_at": f"{day}T09:00:00+00:00"}).json()
    api.post(f"/approvals/{created['approval']['id']}/approve", json={"payload_hash": created["approval"]["payload_hash"]})
    update = {"title": "New", "start_at": f"{day}T12:00:00+00:00", "end_at": f"{day}T13:00:00+00:00"}
    approval = api.post(f"/calendar/events/{created['event']['id']}/update-proposal", json=update).json()
    assert approval["action_type"] == "update_calendar_event" and approval["status"] == "pending"
    window = f"start={day}T00:00:00Z&end={day}T23:59:00Z"
    assert [e["title"] for e in api.get(f"/calendar/events?{window}").json()] == ["Old"]
    assert api.post(f"/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]}).json()["status"] == "executed"
    assert [e["title"] for e in api.get(f"/calendar/events?{window}").json()] == ["New"]


def test_proposed_event_cannot_be_updated(api):
    day = (utcnow() + timedelta(days=26)).date()
    created = api.post("/calendar/events/proposals", json={"title": "P", "start_at": f"{day}T08:00:00+00:00", "end_at": f"{day}T09:00:00+00:00"}).json()
    update = {"title": "Q", "start_at": f"{day}T12:00:00+00:00", "end_at": f"{day}T13:00:00+00:00"}
    assert api.post(f"/calendar/events/{created['event']['id']}/update-proposal", json=update).status_code == 422


def test_reply_threads_with_message_id(api, monkeypatch):
    from app.integrations.base import ExternalResult
    from app.integrations.demo import DemoEmailProvider

    email, approval = _reply(api)
    with SessionLocal() as session:
        session.get(Email, uuid.UUID(email["id"])).rfc822_message_id = "<abc@mail.example.com>"
        session.commit()
    captured = []
    monkeypatch.setattr(DemoEmailProvider, "send", lambda self, to, cc, subject, body, thread_id, in_reply_to: captured.append(in_reply_to) or ExternalResult("x", True, "demo"))
    api.post(f"/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]})
    assert captured == ["<abc@mail.example.com>"]


def test_smoke_live_refuses_to_claim_success_on_mock(api, capsys):
    from app.smoke_live import main

    assert main(["--user", api.email]) == 2
    output = capsys.readouterr().out
    assert "[SKIP] AI provider" in output and "[SKIP] Google" in output and "[PASS]" not in output


def test_smoke_live_is_read_only_and_reports_failures(api, db, monkeypatch):
    import httpx

    from app.core.security import encrypt_secret
    from app.integrations import http
    from app.smoke_live import main

    db.add(OAuthAccount(user_id=uuid.UUID(api.user["id"]), provider="google", access_token_enc=encrypt_secret("t"), expires_at=utcnow() + timedelta(hours=1)))
    db.commit()
    methods = []

    def fake(method, url, **kwargs):
        methods.append(method)
        return httpx.Response(200, json={"messages": [], "items": [], "files": []})

    monkeypatch.setattr(http.httpx, "request", fake)
    assert main(["--user", api.email]) == 2  # AI is still mock
    assert methods and set(methods) == {"GET"}
    monkeypatch.setattr(http.httpx, "request", lambda m, u, **k: httpx.Response(403, json={"error": {"message": "denied"}}))
    assert main(["--user", api.email]) == 1


def test_production_hides_api_docs():
    import subprocess
    import sys

    code = "import os; os.environ.update(ENVIRONMENT='production', SECRET_KEY='s'*40, ENCRYPTION_KEY='e'*40, COOKIE_SECURE='true'); from app.main import app; print(app.docs_url, app.openapi_url)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # noqa: S603
    assert result.stdout.strip().endswith("None None")
