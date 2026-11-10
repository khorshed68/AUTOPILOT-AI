"""Tasks, email, calendar, documents, analytics, notifications and demo mode."""
from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta

import pytest
from docx import Document as Docx
from reportlab.pdfgen import canvas

from app.utils.dates import extract_deadlines, parse_when

# Tasks

def test_task_crud_and_overdue(api):
    created = api.post("/tasks", json={"title": "  Write spec  ", "priority": "high", "due_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(), "category": "Docs"}).json()
    assert created["title"] == "Write spec" and created["overdue"] is True
    assert any(t["id"] == created["id"] for t in api.get("/tasks?overdue=true&page_size=100").json()["items"])
    updated = api.put(f"/tasks/{created['id']}", json={"title": "Write spec", "status": "completed"}).json()
    assert updated["status"] == "completed" and updated["progress"] == 100 and updated["completed_at"] and updated["overdue"] is False
    assert api.delete(f"/tasks/{created['id']}").status_code == 204
    assert api.get(f"/tasks/{created['id']}").status_code == 404


def test_task_validation(api):
    assert api.post("/tasks", json={"title": "   "}).status_code == 422
    assert api.post("/tasks", json={"title": "x", "priority": "urgent"}).status_code == 422
    assert api.post("/tasks", json={"title": "x", "progress": 150}).status_code == 422


def test_task_filter_and_pagination(api):
    for i in range(3):
        api.post("/tasks", json={"title": f"Paged {i}", "priority": "low"})
    page = api.get("/tasks?q=paged&page_size=2&page=2").json()
    assert page["total"] == 3 and len(page["items"]) == 1


# Email

def test_inbox_views(api):
    inbox = api.get("/emails?page_size=50").json()
    assert inbox["total"] == 12 and all(e["is_demo"] for e in inbox["items"])
    api.chat("Summarize my important emails.")
    assert api.get("/emails?view=important").json()["total"] > 0
    assert api.get("/emails?view=needs_reply").json()["total"] > 0
    assert api.get("/emails?category=Finance").json()["total"] == 1


@pytest.mark.parametrize("sender,expected", [("Accounts Payable", "Finance")])
def test_seeded_category(api, sender, expected):
    email = next(e for e in api.get("/emails?page_size=50").json()["items"] if e["sender_name"] == sender)
    assert email["category"] == expected


def test_analysis_extracts_deadline_and_category(api):
    email = next(e for e in api.get("/emails?page_size=50").json()["items"] if "Atlas" in e["subject"])
    result = api.post(f"/emails/{email['id']}/analyze").json()
    assert result["category"] == "Important" and result["deadline_at"] and result["needs_reply"] is True and result["ai_summary"]


def test_urgent_detection(api):
    email = next(e for e in api.get("/emails?page_size=50").json()["items"] if "URGENT" in e["subject"])
    assert api.post(f"/emails/{email['id']}/analyze").json()["category"] == "Urgent"


def test_archive(api):
    email = api.get("/emails").json()["items"][0]
    api.post(f"/emails/{email['id']}/archive", json={"archived": True})
    assert api.get("/emails?view=archived").json()["total"] == 1
    assert email["id"] not in [e["id"] for e in api.get("/emails?page_size=50").json()["items"]]


def test_live_mode_requires_connection_and_data_stays_separate(api):
    response = api.put("/settings/preferences", json={"integration_mode": "live"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "integration_not_connected"


# Dates

def test_date_parsing():
    monday = date(2026, 11, 9)  # a Monday
    assert parse_when("next Monday", monday).date == date(2026, 11, 16)
    assert parse_when("tomorrow at 3pm", monday).start.hour == 15
    assert parse_when("on Thursday", monday).date == date(2026, 11, 12)
    assert parse_when("Dec 3rd", monday).date == date(2026, 12, 3)
    assert parse_when("March 3", monday).date == date(2027, 3, 3)
    assert parse_when("for 90 minutes", monday).duration_minutes == 90
    assert parse_when("for one hour", monday).duration_minutes == 60


def test_ambiguity_is_reported_not_guessed():
    monday = date(2026, 11, 9)
    assert parse_when("sometime soon", monday).date is None
    when = parse_when("next week", monday)
    assert when.date is None and "Which day" in when.ambiguities[0]
    when = parse_when("tomorrow at 3", monday)
    assert when.start is None and "AM or" in when.ambiguities[0]


def test_extract_deadlines():
    found = extract_deadlines("Please send it by Friday. Final deadline is December 15.", date(2026, 11, 9))
    assert [f["date"] for f in found] == ["2026-11-13", "2026-12-15"]
    assert extract_deadlines("We met on Friday.", date(2026, 11, 9)) == []


# Calendar

def _next_day():
    return (datetime.now(UTC) + timedelta(days=30)).date()


def test_availability_excludes_busy_time(api):
    day = _next_day()
    start, end = f"{day}T10:00:00+00:00", f"{day}T11:00:00+00:00"
    proposal = api.post("/calendar/events/proposals", json={"title": "Block", "start_at": start, "end_at": end}).json()
    assert proposal["approval"]["status"] == "pending"
    labels = [s["label"] for s in api.get(f"/calendar/availability?date={day}&duration_minutes=60").json()["slots"]]
    assert "10:00–11:00" not in labels and "09:00–10:00" in labels


def test_conflicts_detected(api):
    day = _next_day()
    first = api.post("/calendar/events/proposals", json={"title": "A", "start_at": f"{day}T14:00:00+00:00", "end_at": f"{day}T15:00:00+00:00"}).json()
    api.post(f"/calendar/events/{first['event']['id']}/execute", json={"payload_hash": first["approval"]["payload_hash"]})
    second = api.post("/calendar/events/proposals", json={"title": "B", "start_at": f"{day}T14:30:00+00:00", "end_at": f"{day}T15:30:00+00:00"}).json()
    assert [c["title"] for c in second["conflicts"]] == ["A"]


def test_timezone_is_applied(api):
    api.put("/settings/preferences", json={"timezone": "Asia/Dhaka"})
    day = _next_day()
    proposal = api.post("/calendar/events/proposals", json={"title": "Local", "start_at": f"{day}T15:00:00", "end_at": f"{day}T16:00:00"}).json()
    assert proposal["approval"]["payload"]["start_at"].endswith("+06:00") and "T15:00:00" in proposal["approval"]["payload"]["start_at"]
    assert proposal["event"]["start_at"].startswith(f"{day}T09:00:00")


def test_event_validation(api):
    day = _next_day()
    assert api.post("/calendar/events/proposals", json={"title": "Bad", "start_at": f"{day}T15:00:00+00:00", "end_at": f"{day}T14:00:00+00:00"}).status_code == 422
    assert api.put("/settings/preferences", json={"timezone": "Mars/Base"}).status_code == 422


def test_event_deletion_needs_approval(api):
    day = _next_day()
    first = api.post("/calendar/events/proposals", json={"title": "Del", "start_at": f"{day}T08:00:00+00:00", "end_at": f"{day}T09:00:00+00:00"}).json()
    api.post(f"/approvals/{first['approval']['id']}/approve", json={"payload_hash": first["approval"]["payload_hash"]})
    approval = api.post(f"/calendar/events/{first['event']['id']}/cancel-proposal").json()
    assert approval["action_type"] == "delete_calendar_event" and approval["status"] == "pending"
    events = api.get(f"/calendar/events?start={day}T00:00:00Z&end={day}T23:59:00Z").json()
    assert any(e["title"] == "Del" and e["status"] == "confirmed" for e in events)


# Documents

def _upload(api, name, data):
    return api.post("/documents/upload", files={"file": (name, data)})


def _pdf() -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, "Falcon launch deadline is December 15. Budget is 5000.")
    pdf.save()
    return buffer.getvalue()


def _docx() -> bytes:
    doc = Docx()
    doc.add_paragraph("Falcon design notes describe the rollout plan in detail.")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("name,data,needle", [("a.txt", b"Plain Falcon text here for testing.", "Plain Falcon"), ("b.csv", b"name,value\nFalcon,10\n", "Falcon | 10"), ("c.pdf", _pdf(), "December 15"), ("d.docx", _docx(), "rollout plan")])
def test_upload_extracts_text(api, name, data, needle):
    response = _upload(api, name, data)
    assert response.status_code == 201 and response.json()["status"] == "ready"
    assert needle in api.get(f"/documents/{response.json()['id']}").json()["content"]


@pytest.mark.parametrize("name,data", [("x.exe", b"MZ"), ("x.pdf", b"not a pdf"), ("x.docx", b"not a zip"), ("x.txt", b"\x00\x01binary"), ("x.txt", b"")])
def test_upload_rejects_bad_files(api, name, data):
    assert _upload(api, name, data).status_code == 422


def test_upload_size_limit(api, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "max_upload_mb", 1)
    response = _upload(api, "big.txt", b"a" * (1024 * 1024 + 2))
    assert response.status_code == 422 and response.json()["error"]["code"] == "file_too_large"


def test_path_traversal_name_is_neutralised(api):
    from pathlib import Path

    from app.core.config import settings

    response = _upload(api, "../../../evil.txt", b"Some harmless text content.")
    assert response.json()["filename"] == "evil.txt"
    assert not (Path(settings.upload_dir).parent.parent / "evil.txt").exists()


def test_document_analyze(api):
    doc = next(d for d in api.get("/documents").json()["items"] if "Status Notes" in d["title"])
    result = api.post(f"/documents/{doc['id']}/analyze").json()
    assert result["analysis"]["summary"] and any("10-12" in d["date"] for d in result["analysis"]["dates"])


def test_report_cites_only_sources(api):
    docs = api.get("/documents").json()["items"]
    atlas = [d["id"] for d in docs if "Atlas" in d["title"]]
    report = api.post("/documents/reports", json={"document_ids": atlas, "topic": "Atlas budget milestones"}).json()
    assert report["kind"] == "report" and len(report["sources"]) == 2
    assert "## Verified facts" in report["content"] and "## AI suggestions" in report["content"] and "[S1]" in report["content"]
    source_text = " ".join(api.get(f"/documents/{i}").json()["content"] for i in atlas)
    for line in report["content"].split("## Verified facts")[1].split("## AI suggestions")[0].splitlines():
        if line.startswith("- "):
            statement = line[2:].rsplit(" [S", 1)[0]
            assert " ".join(statement.split()) in " ".join(source_text.split()), statement


def test_report_does_not_quote_title_lines_as_facts(api):
    reply = api.chat("Prepare a report from my project documents.")
    report = api.get("/documents?kind=report").json()["items"][0]
    content = api.get(f"/documents/{report['id']}").json()["content"]
    assert reply["status"] == "complete"
    assert "- Project Atlas Status Notes [S" not in content


def test_no_sources_means_no_invented_report(api):
    api.post("/demo/clear")
    reply = api.chat("Prepare a report from my project documents.")
    assert "won't invent" in reply["content"]
    assert api.get("/documents?kind=report").json()["total"] == 0


def test_report_with_hallucinated_citation_filtered(api):
    from app.agents.llm.base import SUBMIT_TOOL, LLMProvider, LLMResponse, ToolCall
    from app.agents.llm.factory import set_provider_override

    class Liar(LLMProvider):
        name, model, simulated = "liar", "x", False

        def complete(self, request):
            return LLMResponse(tool_calls=[ToolCall("1", SUBMIT_TOOL, {"title": "T", "executive_summary": "E", "suggestions": [], "facts": [{"statement": "Real", "source": "S1"}, {"statement": "Invented", "source": "S9"}]})])

    set_provider_override(Liar())
    doc = api.get("/documents").json()["items"][0]["id"]
    report = api.post("/documents/reports", json={"document_ids": [doc], "topic": "x"}).json()
    assert "Real" in report["content"] and "Invented" not in report["content"]


@pytest.mark.parametrize("fmt,magic", [("pdf", b"%PDF"), ("docx", b"PK")])
def test_export(api, fmt, magic):
    doc = api.get("/documents").json()["items"][0]["id"]
    report = api.post("/documents/reports", json={"document_ids": [doc], "topic": "atlas"}).json()
    response = api.get(f"/documents/{report['id']}/export?format={fmt}")
    assert response.status_code == 200 and response.content.startswith(magic)


def test_report_edit_and_compare(api):
    docs = [d["id"] for d in api.get("/documents").json()["items"]]
    report = api.post("/documents/reports", json={"document_ids": docs[:1], "topic": "x"}).json()
    assert api.put(f"/documents/{report['id']}", json={"content": "# Edited"}).json()["content"] == "# Edited"
    assert api.put(f"/documents/{docs[0]}", json={"content": "nope"}).status_code == 422
    assert api.post("/documents/compare", json={"document_ids": docs[:2]}).json()["summary"]


def test_share_requires_approval(api):
    doc = api.get("/documents").json()["items"][0]
    approval = api.post(f"/documents/{doc['id']}/share-proposal", json={"email": "friend@example.com"}).json()
    assert approval["status"] == "pending" and approval["action_type"] == "share_document"


# Analytics, activity, notifications, demo

def test_analytics_from_stored_records(api):
    before = api.get("/analytics").json()
    assert before["totals"]["tasks_completed"] == 8 and before["simulated"] is True
    assert before["time_saved"]["is_estimate"] is True and "Estimate" in before["time_saved"]["explanation"]
    task = api.post("/tasks", json={"title": "Finish me"}).json()
    api.put(f"/tasks/{task['id']}", json={"title": "Finish me", "status": "completed"})
    after = api.get("/analytics").json()
    assert after["totals"]["tasks_completed"] == 9
    assert after["task_trend"][-1]["completed"] >= before["task_trend"][-1]["completed"] + 1


def test_analytics_empty_state_has_no_fake_rates(api):
    api.post("/demo/clear")
    result = api.get("/analytics").json()
    assert result["totals"]["automation_success_rate"] is None and result["totals"]["tasks_completed"] == 0


def test_dashboard_reflects_data(api):
    data = api.get("/dashboard").json()
    # One seeded task is due at 17:00 today, so the overdue count depends on when the test runs.
    overdue = api.get("/tasks?overdue=true&page_size=100").json()["total"]
    assert data["stats"]["total_tasks"] == 15 and data["stats"]["overdue_tasks"] == overdue >= 2 and data["stats"]["active_automations"] == 2
    assert data["employee"]["state"] == "online" and data["simulated"] is True


def test_activity_logged(api):
    api.post("/tasks", json={"title": "Audit me"})
    items = api.get("/activity").json()["items"]
    assert items[0]["action"] == "task_created" and "Audit me" in items[0]["description"]


def test_notification_read_flow(api):
    assert api.get("/notifications/unread-count").json()["count"] == 1
    item = api.get("/notifications").json()["items"][0]
    assert api.put(f"/notifications/{item['id']}/read").json()["read_at"]
    assert api.get("/notifications/unread-count").json()["count"] == 0


def test_future_reminder_hidden_until_due(api, db):
    api.chat("Remind me to call the bank tomorrow at 10am")
    assert not any("bank" in n["title"].lower() for n in api.get("/notifications").json()["items"])
    from sqlalchemy import select

    from app.models import Notification

    row = db.scalar(select(Notification).where(Notification.kind == "reminder"))
    assert "bank" in row.title.lower() and row.deliver_at > datetime.now(UTC)


def test_deadline_scan_dedupes_and_respects_preferences(api, db):
    from app.workers.tasks import scan_deadlines_for

    first = scan_deadlines_for(db)
    assert first > 0 and scan_deadlines_for(db) == 0, "reminders must not repeat"
    other_kinds = {n["kind"] for n in api.get("/notifications?page_size=100").json()["items"]}
    assert {"overdue", "deadline"} <= other_kinds


def test_disabled_preference_suppresses(api, db):
    from app.workers.tasks import scan_deadlines_for

    api.put("/settings/preferences", json={"notification_prefs": {"overdue": False, "deadlines": False}})
    assert scan_deadlines_for(db) == 0


def test_demo_clear_and_reset(api):
    api.post("/tasks", json={"title": "Real task"})
    api.post("/demo/clear")
    assert api.get("/emails").json()["total"] == 0
    tasks = api.get("/tasks").json()["items"]
    assert [t["title"] for t in tasks] == ["Real task"]
    api.post("/demo/reset")
    assert api.get("/emails?page_size=50").json()["total"] == 12


def test_global_search(api):
    types = {r["type"] for r in api.get("/search?q=atlas").json()["results"]}
    assert {"email", "document"} <= types
