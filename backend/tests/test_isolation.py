"""One user's data must be invisible and immutable to every other user."""
from __future__ import annotations

import pytest


def _ids(api):
    task = api.post("/tasks", json={"title": "Secret task"}).json()["id"]
    email = api.get("/emails").json()["items"][0]["id"]
    doc = api.get("/documents").json()["items"][0]["id"]
    automation = api.get("/automations").json()[0]["id"]
    api.chat("Schedule a meeting with the team next Monday at 3pm for one hour.")
    approval = api.pending()[0]
    conversation = api.get("/ai/conversations").json()[0]["id"]
    memory = api.post("/memories", json={"content": "Project Falcon is confidential", "kind": "project"}).json()["id"]
    return dict(task=task, email=email, doc=doc, automation=automation, approval=approval, conversation=conversation, memory=memory)


def test_cross_user_access_is_not_found(api, other):
    i = _ids(api)
    requests = [
        ("get", f"/tasks/{i['task']}", None), ("put", f"/tasks/{i['task']}", {"title": "Hacked"}), ("delete", f"/tasks/{i['task']}", None),
        ("get", f"/emails/{i['email']}", None), ("post", f"/emails/{i['email']}/analyze", None), ("post", f"/emails/{i['email']}/draft-reply", {}),
        ("get", f"/documents/{i['doc']}", None), ("post", f"/documents/{i['doc']}/analyze", None), ("delete", f"/documents/{i['doc']}", None),
        ("get", f"/automations/{i['automation']}", None), ("post", f"/automations/{i['automation']}/run", {"test": True}), ("post", f"/automations/{i['automation']}/toggle", {"enabled": False}),
        ("get", f"/approvals/{i['approval']['id']}", None), ("post", f"/approvals/{i['approval']['id']}/approve", {"payload_hash": i["approval"]["payload_hash"]}), ("post", f"/approvals/{i['approval']['id']}/reject", {}),
        ("get", f"/ai/conversations/{i['conversation']}", None), ("post", f"/ai/conversations/{i['conversation']}/messages", {"message": "hi", "stream": False}), ("delete", f"/ai/conversations/{i['conversation']}", None),
        ("put", f"/memories/{i['memory']}", {"content": "changed"}), ("delete", f"/memories/{i['memory']}", None),
    ]
    for method, url, body in requests:
        kwargs = {"json": body} if body is not None else {}
        response = getattr(other, method)(url, **kwargs)
        assert response.status_code == 404, f"{method} {url} -> {response.status_code}"
    assert api.get(f"/approvals/{i['approval']['id']}").json()["status"] == "pending"
    assert api.get(f"/tasks/{i['task']}").json()["title"] == "Secret task"


def test_lists_are_scoped(api, other):
    api.post("/tasks", json={"title": "Only mine"})
    api.post("/memories", json={"content": "Only my memory"})
    assert other.get("/tasks?q=only mine").json()["total"] == 0
    assert other.get("/memories").json() == []
    assert "Only mine" not in str(other.get("/search?q=only mine").json())


@pytest.mark.parametrize("url", ["/tasks", "/emails", "/approvals", "/dashboard", "/analytics", "/memories", "/activity", "/notifications", "/ai/conversations", "/integrations"])
def test_auth_required(url):
    from tests.conftest import Api

    assert Api().get(url).status_code == 401


def test_task_cannot_link_foreign_records(api, other):
    foreign_email = api.get("/emails").json()["items"][0]["id"]
    response = other.post("/tasks", json={"title": "Sneaky", "related_email_id": foreign_email})
    assert response.status_code == 404


def test_memory_never_reaches_other_users(api, other, monkeypatch):
    api.post("/memories", json={"content": "Quarterly numbers are confidential", "kind": "preference"})
    from app.agents.orchestrator import Orchestrator

    captured = []
    original = Orchestrator._plan

    def spy(self, text, history):
        from app.services import memory

        captured.extend(m.content for m in memory.relevant(self.db, self.user_id, text))
        return original(self, text, history)

    monkeypatch.setattr(Orchestrator, "_plan", spy)
    other.chat("Tell me about quarterly numbers in my tasks")
    assert captured == []
