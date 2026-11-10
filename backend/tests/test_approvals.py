"""The approval gate: payload binding, single execution, truthful outcomes."""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select

from app.core.errors import ExternalServiceError
from app.database.session import SessionLocal
from app.integrations.base import ExternalResult
from app.models import ActivityLog, ApprovalRequest, CalendarEvent


@pytest.fixture
def reply_approval(api):
    email = next(e for e in api.get("/emails").json()["items"] if "Dinner" in e["subject"])
    response = api.post(f"/emails/{email['id']}/draft-reply", json={})
    assert response.status_code == 201
    return response.json()


def test_draft_does_not_send(api, reply_approval, db):
    assert reply_approval["status"] == "pending"
    assert reply_approval["payload"]["to"] == ["helen.reid@homemail.example.com"]
    assert db.scalar(select(ActivityLog).where(ActivityLog.action == "executed_send_email")) is None


def test_approve_requires_exact_hash(api, reply_approval):
    response = api.post(f"/approvals/{reply_approval['id']}/approve", json={"payload_hash": "0" * 64})
    assert response.status_code == 409 and response.json()["error"]["code"] == "payload_changed"
    assert api.get(f"/approvals/{reply_approval['id']}").json()["status"] == "pending"


def test_approve_executes_and_labels_simulation(api, reply_approval):
    result = api.post(f"/approvals/{reply_approval['id']}/approve", json={"payload_hash": reply_approval["payload_hash"]}).json()
    assert result["status"] == "executed"
    assert result["simulated"] is True
    assert result["result"]["simulated"] is True and result["result"]["external_id"].startswith("sim-msg-")


def test_edit_invalidates_prior_approval(api, reply_approval):
    payload = {**reply_approval["payload"], "body": "Edited text"}
    edited = api.put(f"/approvals/{reply_approval['id']}", json={"expected_hash": reply_approval["payload_hash"], "payload": payload}).json()
    assert edited["payload_hash"] != reply_approval["payload_hash"] and edited["status"] == "pending" and edited["version"] == 2
    stale = api.post(f"/approvals/{reply_approval['id']}/approve", json={"payload_hash": reply_approval["payload_hash"]})
    assert stale.status_code == 409
    assert api.post(f"/approvals/{reply_approval['id']}/approve", json={"payload_hash": edited["payload_hash"]}).json()["status"] == "executed"


def test_reverting_an_edit_still_needs_new_approval(api, reply_approval):
    original = reply_approval["payload"]
    first = api.put(f"/approvals/{reply_approval['id']}", json={"expected_hash": reply_approval["payload_hash"], "payload": {**original, "body": "X"}}).json()
    reverted = api.put(f"/approvals/{reply_approval['id']}", json={"expected_hash": first["payload_hash"], "payload": original}).json()
    assert reverted["payload"] == original and reverted["payload_hash"] != reply_approval["payload_hash"]


def test_edit_validates_payload(api, reply_approval):
    bad = {**reply_approval["payload"], "to": ["not-an-email"]}
    assert api.put(f"/approvals/{reply_approval['id']}", json={"expected_hash": reply_approval["payload_hash"], "payload": bad}).status_code == 422
    extra = {**reply_approval["payload"], "bcc": ["spy@example.com"]}
    assert api.put(f"/approvals/{reply_approval['id']}", json={"expected_hash": reply_approval["payload_hash"], "payload": extra}).status_code == 422


def test_reject_is_final(api, reply_approval):
    assert api.post(f"/approvals/{reply_approval['id']}/reject", json={"reason": "no"}).json()["status"] == "rejected"
    assert api.post(f"/approvals/{reply_approval['id']}/approve", json={"payload_hash": reply_approval["payload_hash"]}).status_code == 409


def test_double_approve_executes_once(api, reply_approval, monkeypatch):
    from app.integrations.demo import DemoEmailProvider

    calls = []
    monkeypatch.setattr(DemoEmailProvider, "send", lambda self, *a: calls.append(a) or ExternalResult("x", True, "demo"))
    url = f"/api/approvals/{reply_approval['id']}/approve"
    results = []

    def fire():
        results.append(api.client.post(url, headers=api.csrf, json={"payload_hash": reply_approval["payload_hash"]}).status_code)

    threads = [threading.Thread(target=fire) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1
    assert sorted(results)[0] == 200 and results.count(200) == 1


def test_external_failure_reported_as_failure(api):
    email = next(e for e in api.get("/emails").json()["items"] if "on-call" in e["subject"])
    approval = api.post(f"/emails/{email['id']}/draft-reply", json={}).json()
    result = api.post(f"/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]}).json()
    assert result["status"] == "failed" and "rejected" in result["error"] and result["result"] is None
    kinds = [n["kind"] for n in api.get("/notifications").json()["items"]]
    assert "approval_failed" in kinds


def test_unconfirmed_outcome_is_not_success(api, reply_approval, monkeypatch):
    from app.integrations.demo import DemoEmailProvider

    def boom(self, *a):
        raise ExternalServiceError("Timed out", outcome_unknown=True)

    monkeypatch.setattr(DemoEmailProvider, "send", boom)
    result = api.post(f"/approvals/{reply_approval['id']}/approve", json={"payload_hash": reply_approval["payload_hash"]}).json()
    assert result["status"] == "outcome_unknown"


def test_calendar_event_created_only_after_approval(api, db):
    api.chat("Schedule a meeting with the development team next Monday afternoon for one hour.")
    approval = api.pending()[0]
    event = db.get(CalendarEvent, __import__("uuid").UUID(approval["payload"]["event_id"]))
    assert event.status == "proposed" and event.external_id is None
    result = api.post(f"/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]}).json()
    assert result["status"] == "executed"
    db.expire_all()
    event = db.get(CalendarEvent, event.id)
    assert event.status == "confirmed" and event.external_id.startswith("sim-evt-")


def test_rejected_proposal_marks_event(api, db):
    api.chat("Schedule a meeting with the team next Monday at 3pm for one hour.")
    approval = api.pending()[0]
    api.post(f"/approvals/{approval['id']}/reject", json={})
    db.expire_all()
    assert db.get(CalendarEvent, __import__("uuid").UUID(approval["payload"]["event_id"])).status == "rejected"


def test_expired_approval_cannot_run(api, reply_approval):
    from datetime import timedelta

    from app.database.base import utcnow

    with SessionLocal() as session:
        row = session.scalar(select(ApprovalRequest))
        row.expires_at = utcnow() - timedelta(minutes=1)
        session.commit()
    response = api.post(f"/approvals/{reply_approval['id']}/approve", json={"payload_hash": reply_approval["payload_hash"]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "approval_expired"


def test_duplicate_proposal_is_deduplicated(api, reply_approval):
    email = next(e for e in api.get("/emails").json()["items"] if "Dinner" in e["subject"])
    again = api.post(f"/emails/{email['id']}/draft-reply", json={}).json()
    assert again["id"] == reply_approval["id"]


def test_interrupted_execution_marked_unknown_not_retried(api, reply_approval):
    from datetime import timedelta

    from app.database.base import utcnow
    from app.services.approvals import recover_stuck

    with SessionLocal() as session:
        row = session.scalar(select(ApprovalRequest))
        row.status, row.executing_since = "executing", utcnow() - timedelta(hours=1)
        session.commit()
        assert recover_stuck(session) == 1
    assert api.get(f"/approvals/{reply_approval['id']}").json()["status"] == "outcome_unknown"
