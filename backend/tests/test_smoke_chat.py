"""Exercises each natural-language request from the product brief through the full agent stack."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Summarize my important emails.", "important email"),
        ("Find all emails that require a reply.", "need a reply"),
        ("Find my overdue tasks.", "overdue task"),
        ("Give me a summary of today's work.", "open"),
        ("Find the deadline mentioned in my latest project email.", "project atlas"),
        ("Prepare a report from my project documents.", "report"),
    ],
)
def test_brief_requests(api, message, expected):
    reply = api.chat(message)
    assert reply["status"] == "complete", reply
    assert expected in reply["content"].lower(), reply["content"]
    assert reply["meta"]["simulated_ai"] is True


def test_create_task_from_chat(api):
    reply = api.chat("Create a task to finish my assignment tomorrow.")
    assert "Finish my assignment" in reply["content"]
    tasks = api.get("/tasks?q=finish my assignment").json()["items"]
    assert len(tasks) == 1 and tasks[0]["source"] == "ai" and tasks[0]["due_at"]


def test_schedule_meeting_proposes_only(api):
    reply = api.chat("Schedule a meeting with the development team next Monday afternoon for one hour.")
    assert "not on your calendar until you approve" in reply["content"]
    pending = api.pending()
    assert len(pending) == 1
    assert pending[0]["action_type"] == "create_calendar_event"
    assert pending[0]["simulated"] is True


def test_ambiguous_time_asks(api):
    reply = api.chat("Schedule a meeting with John next Monday.")
    assert "Which time works" in reply["content"]
    assert api.pending() == []


def test_full_brief_example(api):
    reply = api.chat(
        "Someone emailed me asking for a project report by Thursday. Find the relevant documents, prepare a report, "
        "create a task, and remind me one day before the deadline."
    )
    assert reply["status"] == "complete"
    statuses = [s["status"] for s in reply["meta"]["steps"]]
    assert statuses == ["completed"] * 4, reply["meta"]["steps"]
    assert api.get("/documents?kind=report").json()["total"] == 1
    assert api.get("/tasks?q=deliver report").json()["total"] == 1
