"""Workflow engine: validation, conditions, scheduling and real execution through Celery (eager mode)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.agents.llm.base import LLMUnavailable
from app.agents.llm.factory import set_provider_override
from app.database.base import utcnow
from app.database.session import SessionLocal
from app.models import Automation, AutomationExecution
from app.workflows import engine
from tests.test_agents import Flaky


def _workflow(api, **overrides):
    body = {"name": "W", "trigger_type": "manual", "steps": [{"action_type": "get_todays_tasks"}, {"action_type": "analyze_pending"}]} | overrides
    response = api.post("/automations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _by_name(api, name):
    return next(a for a in api.get("/automations").json() if a["name"] == name)


def test_create_starts_disabled_and_run_executes_steps(api):
    workflow = _workflow(api)
    assert workflow["enabled"] is False
    execution = api.post(f"/automations/{workflow['id']}/run", json={}).json()
    log = api.get(f"/automations/{workflow['id']}/executions").json()["items"][0]
    assert log["id"] == execution["id"] and log["status"] == "succeeded"
    assert [s["status"] for s in log["step_results"]] == ["succeeded", "succeeded"] and log["duration_ms"] is not None


@pytest.mark.parametrize("patch,message", [
    ({"trigger_type": "daily", "trigger_config": {"time": "25:00"}}, "time"),
    ({"trigger_type": "weekly", "trigger_config": {"time": "09:00", "weekday": 9}}, "day"),
    ({"steps": [{"action_type": "format_disk"}]}, "Unknown action"),
    ({"steps": [{"action_type": "analyze_email"}]}, "New email"),
    ({"conditions": [{"field": "email.is_urgent", "op": "is_true"}]}, "Conditions"),
    ({"trigger_type": "new_email", "conditions": [{"field": "email.password", "op": "is_true"}], "steps": [{"action_type": "analyze_email"}]}, "Unknown condition"),
])
def test_validation(api, patch, message):
    body = {"name": "W", "trigger_type": "manual", "steps": [{"action_type": "analyze_pending"}]} | patch
    response = api.post("/automations", json=body)
    assert response.status_code == 422 and message in response.json()["error"]["message"]


@pytest.mark.parametrize("conditions,logic,expected", [
    ([{"field": "email.subject", "op": "contains", "value": "INVOICE"}], "all", True),
    ([{"field": "email.subject", "op": "not_contains", "value": "invoice"}], "all", False),
    ([{"field": "email.is_urgent", "op": "is_true"}, {"field": "email.has_deadline", "op": "is_true"}], "any", True),
    ([{"field": "email.is_urgent", "op": "is_true"}, {"field": "email.has_deadline", "op": "is_true"}], "all", False),
    ([], "all", True),
])
def test_conditions(conditions, logic, expected):
    context = {"email": {"subject": "Your invoice", "is_urgent": False, "has_deadline": True}}
    assert engine.evaluate_conditions(conditions, logic, context) is expected


def test_toggle_computes_schedule(api):
    workflow = _workflow(api, trigger_type="daily", trigger_config={"time": "20:00"})
    enabled = api.post(f"/automations/{workflow['id']}/toggle", json={"enabled": True}).json()
    assert enabled["enabled"] is True and enabled["next_run_at"]
    assert api.post(f"/automations/{workflow['id']}/toggle", json={"enabled": False}).json()["next_run_at"] is None


def test_next_run_respects_timezone():
    a = Automation(trigger_type="daily", trigger_config={"time": "08:00"}, enabled=True)
    now = datetime(2026, 11, 9, 1, 0, tzinfo=UTC)  # 07:00 in Dhaka
    assert engine.compute_next_run(a, "Asia/Dhaka", now) == datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    weekly = Automation(trigger_type="weekly", trigger_config={"time": "09:00", "weekday": 0}, enabled=True)
    assert engine.compute_next_run(weekly, "UTC", datetime(2026, 11, 10, tzinfo=UTC)).date().isoformat() == "2026-11-16"


def test_scheduler_runs_due_once_and_advances(api, db):
    workflow = _workflow(api, trigger_type="daily", trigger_config={"time": "20:00"})
    api.post(f"/automations/{workflow['id']}/toggle", json={"enabled": True})
    row = db.get(Automation, __import__("uuid").UUID(workflow["id"]))
    row.next_run_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert engine.dispatch_due(db) == 1
    assert engine.dispatch_due(db) == 0, "must not run twice for one slot"
    db.expire_all()
    assert db.get(Automation, row.id).next_run_at > utcnow()
    assert api.get(f"/automations/{workflow['id']}/executions").json()["items"][0]["status"] == "succeeded"


def test_duplicate_schedule_slot_rejected(api, db):
    workflow = _workflow(api)
    row = db.get(Automation, __import__("uuid").UUID(workflow["id"]))
    assert engine.create_execution(db, row, "daily", dedupe_key="slot") is not None
    assert engine.create_execution(db, row, "daily", dedupe_key="slot") is None


def test_important_email_assistant_end_to_end(api):
    email = api.post("/demo/simulate-email").json()
    assert email["subject"] == "Scope document needed"
    workflow = _by_name(api, "Important Email Assistant")
    log = api.get(f"/automations/{workflow['id']}/executions").json()["items"][0]
    assert log["status"] == "succeeded", log
    assert [s["status"] for s in log["step_results"]] == ["succeeded"] * 6
    kinds = sorted(a["action_type"] for a in api.pending())
    assert kinds == ["create_task", "send_email"], "the workflow may only propose"
    assert api.get("/tasks?q=scope document").json()["total"] == 0
    assert any("priority email" in n["title"] for n in api.get("/notifications").json()["items"])


def test_task_proposal_becomes_task_after_approval(api):
    api.post("/demo/simulate-email")
    approval = next(a for a in api.pending() if a["action_type"] == "create_task")
    assert api.post(f"/approvals/{approval['id']}/approve", json={"payload_hash": approval["payload_hash"]}).json()["status"] == "executed"
    assert api.get("/tasks?q=scope document").json()["total"] == 1


def test_condition_miss_is_skipped(api, db):
    from app.models import Email

    workflow = _by_name(api, "Important Email Assistant")
    with SessionLocal() as session:
        email = Email(user_id=__import__("uuid").UUID(api.user["id"]), external_id="plain", sender_email="a@example.com", subject="Hello", body="Nice day.", received_at=utcnow(), is_demo=True)
        session.add(email)
        session.commit()
        engine.dispatch_email(session, email)
    log = api.get(f"/automations/{workflow['id']}/executions").json()["items"][0]
    assert log["status"] == "skipped" and "conditions" in log["error"]


def test_old_mail_does_not_trigger(api, db):
    from app.models import Email

    with SessionLocal() as session:
        email = Email(user_id=__import__("uuid").UUID(api.user["id"]), external_id="old", sender_email="a@example.com", subject="urgent old", body="urgent", received_at=utcnow() - timedelta(days=30), is_demo=True)
        session.add(email)
        session.commit()
        assert engine.dispatch_email(session, email) == 0


def test_daily_summary_workflow(api):
    workflow = _by_name(api, "Daily Work Summary")
    api.post(f"/automations/{workflow['id']}/run", json={})
    items = api.get("/notifications?page_size=50").json()["items"]
    assert any(n["kind"] == "daily_summary" and "Tasks Open" in n["body"] for n in items)


def test_failed_step_reported_and_stops(api):
    set_provider_override(Flaky(LLMUnavailable("down")))
    workflow = _by_name(api, "Daily Work Summary")
    execution = api.post(f"/automations/{workflow['id']}/run", json={}).json()
    assert execution["status"] == "failed" and "temporarily unavailable" in execution["error"]
    assert any(n["kind"] == "automation_failed" for n in api.get("/notifications?page_size=50").json()["items"])
    assert api.get("/activity?status=failure").json()["items"][0]["action"] == "automation_failed"


def test_paused_employee_skips_runs(api):
    workflow = _workflow(api)
    api.post("/employee/pause", json={"paused": True})
    assert api.post(f"/automations/{workflow['id']}/run", json={}).json()["status"] == "skipped"


def test_redelivered_job_does_not_rerun(api, db):
    workflow = _workflow(api)
    execution = api.post(f"/automations/{workflow['id']}/run", json={}).json()
    before = api.get(f"/automations/{workflow['id']}/executions").json()["items"][0]
    engine.run_execution(db, __import__("uuid").UUID(execution["id"]))
    assert api.get(f"/automations/{workflow['id']}/executions").json()["items"][0]["finished_at"] == before["finished_at"]


def test_queue_outage_is_reported(api, monkeypatch):
    from app.workers import tasks

    def down(*a, **k):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.run_automation, "apply_async", down)
    workflow = _workflow(api)
    execution = api.post(f"/automations/{workflow['id']}/run", json={}).json()
    assert execution["status"] == "failed" and "queue is unavailable" in execution["error"]


def test_stuck_execution_recovered(api, db):
    workflow = _workflow(api)
    row = db.get(Automation, __import__("uuid").UUID(workflow["id"]))
    execution = engine.create_execution(db, row, "manual")
    execution.created_at = utcnow() - timedelta(hours=1)
    db.commit()
    assert engine.recover_stuck_executions(db) == 1
    assert db.scalar(select(AutomationExecution).where(AutomationExecution.id == execution.id)).status == "failed"


def test_update_replaces_steps(api):
    workflow = _workflow(api)
    updated = api.put(f"/automations/{workflow['id']}", json={"name": "Renamed", "trigger_type": "manual", "steps": [{"action_type": "analyze_pending"}]}).json()
    assert updated["name"] == "Renamed" and [s["action_type"] for s in updated["steps"]] == ["analyze_pending"]
