"""Backend tool registry. This is the only way an agent can read or change anything.

Each tool has a typed schema, and every handler scopes its queries to `ctx.user_id`. Identifiers supplied
by the model are never trusted to cross users.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.database.base import utcnow
from app.integrations import factory
from app.models import ActivityLog, ApprovalRequest, CalendarEvent, Email, Task
from app.services import calendar as calendar_service
from app.services import documents as document_service
from app.services import emails as email_service
from app.services.activity import log_activity, notify
from app.services.approvals import propose
from app.utils.dates import extract_all_dates, get_zone, local_end_of_day, to_utc
from app.utils.text import query_terms, score_documents, truncate

logger = logging.getLogger("autopilot.tools")
OPEN = ("pending", "in_progress", "waiting")


@dataclass
class ToolContext:
    db: Session
    user_id: uuid.UUID
    timezone: str
    run_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    origin: str = "chat"
    agent: str = "manager"
    tainted: bool = False
    simulated_llm: bool = False
    ledger: list[dict] = field(default_factory=list)

    @property
    def zone(self):
        return get_zone(self.timezone)

    @property
    def demo(self) -> bool:
        return factory.is_demo(self.db, self.user_id)

    def local(self, value: datetime | None) -> str | None:
        return value.astimezone(self.zone).strftime("%a %d %b %Y, %H:%M") if value else None

    def record(self, kind: str, title: str, **extra: Any) -> None:
        self.ledger.append({"kind": kind, "title": title, "agent": self.agent, **extra})


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[Args]
    handler: Callable[[ToolContext, Any], dict]
    untrusted_output: bool = False


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, args: type[Args], *, untrusted_output: bool = False):
    def register(fn):
        TOOLS[name] = Tool(name, description, args, fn, untrusted_output)
        return fn

    return register


class ToolDenied(Exception):
    pass


def execute_tool(ctx: ToolContext, allowed: frozenset[str], name: str, raw_args: dict) -> tuple[dict, bool]:
    """Run one tool call. Returns (result, is_untrusted); failures come back as {"error": ...}."""
    if name not in allowed or name not in TOOLS:
        logger.warning("tool_denied", extra={"agent": ctx.agent, "tool": name, "user_id": str(ctx.user_id)})
        log_activity(ctx.db, ctx.user_id, "tool_denied", f"Blocked {ctx.agent} agent from using '{name[:60]}'", actor=ctx.agent, status="failure")
        ctx.db.commit()
        raise ToolDenied(f"Tool '{name}' is not permitted for the {ctx.agent} agent.")
    definition = TOOLS[name]
    try:
        args = definition.args.model_validate(raw_args)
    except ValidationError as exc:
        return {"error": "Invalid arguments: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())}, False
    try:
        result = definition.handler(ctx, args)
        ctx.db.commit()
    except AppError as exc:
        ctx.db.rollback()
        return {"error": exc.message}, False
    except Exception:
        ctx.db.rollback()
        logger.exception("tool_crashed", extra={"tool": name})
        return {"error": f"{name} failed due to an internal error."}, False
    if definition.untrusted_output:
        ctx.tainted = True
    return result, definition.untrusted_output


def tool_schema(name: str) -> dict:
    schema = TOOLS[name].args.model_json_schema()
    schema.pop("title", None)
    return schema


# Email -------------------------------------------------------------------------------------------------------------

class ListEmails(Args):
    view: Literal["inbox", "important", "needs_reply", "archived"] = "inbox"
    category: str | None = None
    limit: int = Field(default=10, ge=1, le=25)


def _email_brief(e: Email) -> dict:
    return {
        "id": str(e.id), "subject": e.subject, "sender": e.sender_name or e.sender_email, "sender_email": e.sender_email, "received_at": e.received_at.isoformat(), "snippet": truncate(e.snippet, 200),
        "category": e.category, "importance": e.importance, "needs_reply": e.needs_reply, "summary": e.ai_summary, "suggested_action": e.suggested_action, "analyzed": e.analyzed_at is not None,
        "deadline_at": e.deadline_at.isoformat() if e.deadline_at else None, "injection_flags": e.injection_flags,
    }


@tool("list_emails", "List recent emails (metadata and snippet only).", ListEmails, untrusted_output=True)
def list_emails(ctx: ToolContext, a: ListEmails) -> dict:
    stmt = email_service.apply_filters(email_service.base_query(ctx.db, ctx.user_id), view=a.view, category=a.category).order_by(Email.received_at.desc()).limit(a.limit)
    return {"emails": [_email_brief(e) for e in ctx.db.scalars(stmt).all()]}


class SearchEmails(Args):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=5, ge=1, le=15)


@tool("search_emails", "Search emails by keywords, newest and best matches first.", SearchEmails, untrusted_output=True)
def search_emails(ctx: ToolContext, a: SearchEmails) -> dict:
    rows = ctx.db.scalars(email_service.base_query(ctx.db, ctx.user_id).where(Email.is_archived.is_(False)).order_by(Email.received_at.desc()).limit(300)).all()
    scores = score_documents(a.query, [(str(e.id), f"{e.subject} {e.subject} {e.sender_name or ''} {e.sender_email} {e.body}") for e in rows])
    ranked = sorted((e for e in rows if str(e.id) in scores), key=lambda e: (-scores[str(e.id)], -e.received_at.timestamp()))
    return {"emails": [_email_brief(e) for e in ranked[: a.limit]]}


class EmailRef(Args):
    email_id: uuid.UUID


@tool("get_email", "Read the full content of one email.", EmailRef, untrusted_output=True)
def get_email(ctx: ToolContext, a: EmailRef) -> dict:
    e = email_service.get_owned(ctx.db, ctx.user_id, a.email_id)
    return {**_email_brief(e), "body": e.body[:email_service.MAX_BODY_FOR_LLM]}


class SaveAnalysis(Args):
    email_id: uuid.UUID
    category: Literal["Important", "Work", "Personal", "Promotions", "Finance", "Notifications", "Urgent"]
    importance: Literal["low", "normal", "high"]
    summary: str = Field(max_length=2000)
    needs_reply: bool
    suggested_action: str = Field(max_length=500)
    action_items: list[str] = Field(default_factory=list, max_length=10)
    deadline_date: date | None = None
    deadline_text: str | None = Field(default=None, max_length=255)


@tool("save_email_analysis", "Store category, importance, summary, deadline and reply status for an email.", SaveAnalysis)
def save_email_analysis(ctx: ToolContext, a: SaveAnalysis) -> dict:
    email = email_service.get_owned(ctx.db, ctx.user_id, a.email_id)
    data = a.model_dump()
    data["deadline_date"] = a.deadline_date.isoformat() if a.deadline_date else None
    email_service.store_analysis(ctx.db, email, data, ctx.timezone, actor=ctx.agent, simulated=ctx.simulated_llm)
    return {"saved": True, "email_id": str(email.id)}


class ProposeReply(Args):
    email_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)


@tool("propose_email_reply", "Create a reply draft for human approval. The recipient is always the original sender. This does not send anything.", ProposeReply)
def propose_email_reply(ctx: ToolContext, a: ProposeReply) -> dict:
    email = email_service.get_owned(ctx.db, ctx.user_id, a.email_id)
    approval = email_service.propose_reply(ctx.db, ctx.user_id, email, a.subject, a.body, requested_by=ctx.agent, origin=ctx.origin, conversation_id=ctx.conversation_id, run_id=ctx.run_id, tainted=ctx.tainted)
    ctx.record("approval", approval.title, approval_id=str(approval.id), status="awaiting_approval")
    return {"status": "awaiting_approval", "executed": False, "approval_id": str(approval.id)}


# Calendar ----------------------------------------------------------------------------------------------------------

class ListEvents(Args):
    days: int = Field(default=7, ge=1, le=60)


def _event_brief(ctx: ToolContext, e: CalendarEvent) -> dict:
    return {"id": str(e.id), "title": e.title, "local_start": ctx.local(e.start_at), "local_end": ctx.local(e.end_at), "start_at": e.start_at.isoformat(), "end_at": e.end_at.isoformat(), "attendees": e.attendees, "status": e.status}


@tool("list_events", "List upcoming calendar events.", ListEvents, untrusted_output=True)
def list_events(ctx: ToolContext, a: ListEvents) -> dict:
    now = utcnow()
    warning = calendar_service._sync_best_effort(ctx.db, ctx.user_id, now, now + timedelta(days=a.days))
    events = calendar_service.events_between(ctx.db, ctx.user_id, now, now + timedelta(days=a.days))
    return {"events": [_event_brief(ctx, e) for e in events], "warning": warning}


class FreeSlots(Args):
    date: date
    duration_minutes: int = Field(default=60, ge=5, le=480)
    period: Literal["morning", "afternoon", "evening"] | None = None


@tool("find_free_slots", "Find open time slots on a date, in the user's timezone.", FreeSlots)
def find_free_slots(ctx: ToolContext, a: FreeSlots) -> dict:
    return calendar_service.free_slots(ctx.db, ctx.user_id, a.date, a.duration_minutes, a.period)


class TimeRange(Args):
    start: datetime = Field(description="Local time in the user's timezone, or ISO 8601 with an offset")
    end: datetime


@tool("check_conflicts", "Check whether a time range overlaps existing events.", TimeRange, untrusted_output=True)
def check_conflicts(ctx: ToolContext, a: TimeRange) -> dict:
    found = calendar_service.conflicts(ctx.db, ctx.user_id, to_utc(a.start, ctx.timezone), to_utc(a.end, ctx.timezone))
    return {"conflicts": [_event_brief(ctx, e) for e in found]}


class FindContact(Args):
    name: str = Field(min_length=1, max_length=120)


@tool("find_contact", "Look up an email address by name from the people who have emailed the user.", FindContact, untrusted_output=True)
def find_contact(ctx: ToolContext, a: FindContact) -> dict:
    rows = ctx.db.execute(
        email_service.base_query(ctx.db, ctx.user_id).with_only_columns(Email.sender_name, Email.sender_email).where(func.lower(Email.sender_name).like(f"%{a.name.lower().replace('%', '')}%")).distinct().limit(5)
    ).all()
    return {"matches": [{"name": n, "email": e} for n, e in rows]}


class ProposeEvent(Args):
    title: str = Field(min_length=1, max_length=255)
    start: datetime = Field(description="Local time in the user's timezone, or ISO 8601 with an offset")
    end: datetime
    description: str = Field(default="", max_length=4000)
    location: str | None = None
    attendees: list[str] = Field(default_factory=list, max_length=25)


@tool("propose_event", "Propose a new calendar event for human approval. This does not create it.", ProposeEvent)
def propose_event(ctx: ToolContext, a: ProposeEvent) -> dict:
    event, approval = calendar_service.propose_event(
        ctx.db, ctx.user_id, title=a.title, start=to_utc(a.start, ctx.timezone), end=to_utc(a.end, ctx.timezone), description=a.description, location=a.location, attendees=a.attendees,
        requested_by=ctx.agent, origin=ctx.origin, conversation_id=ctx.conversation_id, run_id=ctx.run_id, tainted=ctx.tainted,
    )
    ctx.record("approval", approval.title, approval_id=str(approval.id), status="awaiting_approval")
    return {"status": "awaiting_approval", "executed": False, "approval_id": str(approval.id), "event_id": str(event.id)}


class ProposeUpdate(ProposeEvent):
    event_id: uuid.UUID


@tool("propose_event_update", "Propose changing an existing event, for human approval.", ProposeUpdate)
def propose_event_update(ctx: ToolContext, a: ProposeUpdate) -> dict:
    event = calendar_service.get_owned(ctx.db, ctx.user_id, a.event_id)
    approval = calendar_service.propose_update(
        ctx.db, ctx.user_id, event, title=a.title, start=to_utc(a.start, ctx.timezone), end=to_utc(a.end, ctx.timezone), description=a.description, location=a.location, attendees=a.attendees,
        requested_by=ctx.agent, origin=ctx.origin, conversation_id=ctx.conversation_id, run_id=ctx.run_id, tainted=ctx.tainted,
    )
    ctx.record("approval", approval.title, approval_id=str(approval.id), status="awaiting_approval")
    return {"status": "awaiting_approval", "executed": False, "approval_id": str(approval.id)}


class EventRef(Args):
    event_id: uuid.UUID


@tool("propose_event_cancellation", "Propose deleting an event, for human approval.", EventRef)
def propose_event_cancellation(ctx: ToolContext, a: EventRef) -> dict:
    event = calendar_service.get_owned(ctx.db, ctx.user_id, a.event_id)
    approval = propose(ctx.db, ctx.user_id, "delete_calendar_event", {"event_id": str(event.id), "title": event.title}, title=f"Delete event: {event.title}", requested_by=ctx.agent, origin=ctx.origin, conversation_id=ctx.conversation_id, run_id=ctx.run_id, derived_from_untrusted=ctx.tainted)
    ctx.record("approval", approval.title, approval_id=str(approval.id), status="awaiting_approval")
    return {"status": "awaiting_approval", "executed": False, "approval_id": str(approval.id)}


# Tasks -------------------------------------------------------------------------------------------------------------

class ListTasks(Args):
    status: Literal["open", "pending", "in_progress", "waiting", "completed", "cancelled", "all"] = "open"
    overdue: bool = False
    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=50)


def _task_visible(ctx: ToolContext):
    return Task.user_id == ctx.user_id, (True if ctx.demo else Task.is_demo.is_(False))


def _task_brief(ctx: ToolContext, t: Task) -> dict:
    return {"id": str(t.id), "title": t.title, "description": truncate(t.description, 300), "priority": t.priority, "status": t.status, "progress": t.progress, "due_at": t.due_at.isoformat() if t.due_at else None, "due_local": ctx.local(t.due_at)}


@tool("list_tasks", "List the user's tasks with optional filters.", ListTasks, untrusted_output=True)
def list_tasks(ctx: ToolContext, a: ListTasks) -> dict:
    stmt = select(Task).where(*_task_visible(ctx))
    if a.status == "open" or a.overdue:
        stmt = stmt.where(Task.status.in_(OPEN))
    elif a.status != "all":
        stmt = stmt.where(Task.status == a.status)
    if a.overdue:
        stmt = stmt.where(Task.due_at < utcnow())
    if a.query:
        terms = query_terms(a.query)
        if terms:
            stmt = stmt.where(or_(*[func.lower(Task.title).like(f"%{t}%") for t in terms]))
    rows = ctx.db.scalars(stmt.order_by(Task.due_at.is_(None), Task.due_at.asc()).limit(a.limit)).all()
    return {"tasks": [_task_brief(ctx, t) for t in rows]}


class CreateTask(Args):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    due_date: date | None = None
    category: str | None = Field(default=None, max_length=64)
    related_email_id: uuid.UUID | None = None
    related_document_id: uuid.UUID | None = None


@tool("create_task", "Create a task. In automations this becomes a proposal for human approval.", CreateTask)
def create_task(ctx: ToolContext, a: CreateTask) -> dict:
    due = local_end_of_day(a.due_date, ctx.zone) if a.due_date else None
    if a.related_email_id:
        email_service.get_owned(ctx.db, ctx.user_id, a.related_email_id)
    if a.related_document_id:
        document_service.get_owned(ctx.db, ctx.user_id, a.related_document_id)
    if ctx.origin == "automation":
        payload = {"title": a.title, "description": a.description, "priority": a.priority, "due_at": due.isoformat() if due else None, "category": a.category, "related_email_id": str(a.related_email_id) if a.related_email_id else None, "related_document_id": str(a.related_document_id) if a.related_document_id else None}
        approval = propose(ctx.db, ctx.user_id, "create_task", payload, title=f"Create task: {a.title}", requested_by=ctx.agent, origin=ctx.origin, run_id=ctx.run_id, derived_from_untrusted=ctx.tainted)
        ctx.record("approval", approval.title, approval_id=str(approval.id), status="awaiting_approval")
        return {"status": "awaiting_approval", "executed": False, "approval_id": str(approval.id)}
    recent = ctx.db.scalar(select(Task).where(Task.user_id == ctx.user_id, Task.title == a.title, Task.source == "ai", Task.due_at == due, Task.created_at > utcnow() - timedelta(minutes=10)))
    if recent is not None:
        return {"status": "completed", "task_id": str(recent.id), "deduplicated": True}
    task = Task(user_id=ctx.user_id, title=a.title, description=a.description, priority=a.priority, due_at=due, category=a.category, source="ai", related_email_id=a.related_email_id, related_document_id=a.related_document_id, is_demo=False)
    ctx.db.add(task)
    ctx.db.flush()
    log_activity(ctx.db, ctx.user_id, "ai_task_created", f"Created task: {a.title}", actor=ctx.agent, entity_type="task", entity_id=task.id)
    ctx.record("done", f"Created task: {a.title}", entity="task", entity_id=str(task.id))
    return {"status": "completed", "task_id": str(task.id), "due_local": ctx.local(due)}


class UpdateTask(Args):
    task_id: uuid.UUID
    status: Literal["pending", "in_progress", "waiting", "completed", "cancelled"] | None = None
    priority: Literal["critical", "high", "medium", "low"] | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    due_date: date | None = None


@tool("update_task", "Update a task's status, priority, progress or due date.", UpdateTask)
def update_task(ctx: ToolContext, a: UpdateTask) -> dict:
    from app.services.tasks import apply_status, get_owned

    task = get_owned(ctx.db, ctx.user_id, a.task_id)
    if a.status:
        apply_status(task, a.status)
    if a.priority:
        task.priority = a.priority
    if a.progress is not None:
        task.progress = a.progress
    if a.due_date:
        task.due_at = local_end_of_day(a.due_date, ctx.zone)
    log_activity(ctx.db, ctx.user_id, "ai_task_updated", f"Updated task: {task.title}", actor=ctx.agent, entity_type="task", entity_id=task.id, meta={"status": task.status})
    ctx.record("done", f"Updated task: {task.title} ({task.status})", entity="task", entity_id=str(task.id))
    return {"status": "completed", "task": _task_brief(ctx, task)}


# Documents ---------------------------------------------------------------------------------------------------------

class SearchDocs(Args):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=5, ge=1, le=10)


@tool("search_documents", "Search the user's documents. Returns titles and scores.", SearchDocs, untrusted_output=True)
def search_documents(ctx: ToolContext, a: SearchDocs) -> dict:
    found = document_service.search(ctx.db, ctx.user_id, a.query, a.limit)
    return {"documents": [{"id": str(d.id), "title": d.title, "filename": d.filename, "score": round(s, 2)} for d, s in found]}


class DocRef(Args):
    document_id: uuid.UUID


@tool("read_document", "Read a document's extracted text.", DocRef, untrusted_output=True)
def read_document(ctx: ToolContext, a: DocRef) -> dict:
    d = document_service.get_owned(ctx.db, ctx.user_id, a.document_id)
    return {"id": str(d.id), "title": d.title, "content": d.content[:document_service.MAX_LLM_CHARS], "injection_flags": d.injection_flags}


@tool("extract_document_dates", "Extract dates and deadlines stated in a document.", DocRef, untrusted_output=True)
def extract_document_dates(ctx: ToolContext, a: DocRef) -> dict:
    d = document_service.get_owned(ctx.db, ctx.user_id, a.document_id)
    return {"dates": extract_all_dates(d.content, utcnow().astimezone(ctx.zone).date())[:30]}


class SaveReport(Args):
    title: str = Field(min_length=1, max_length=200)
    content_markdown: str = Field(min_length=1, max_length=100_000)
    source_document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


@tool("save_report", "Save a markdown report draft. Separate '## Verified facts' (cite [S1], [S2]...) from '## AI suggestions'.", SaveReport)
def save_report(ctx: ToolContext, a: SaveReport) -> dict:
    import re

    sources = [document_service.get_owned(ctx.db, ctx.user_id, i) for i in a.source_document_ids]
    cited = {int(n) for n in re.findall(r"\[S(\d+)\]", a.content_markdown)}
    if cited and max(cited) > len(sources):
        return {"error": f"The report cites [S{max(cited)}] but only {len(sources)} source(s) were provided."}
    report = document_service.save_report(ctx.db, ctx.user_id, a.title, a.content_markdown, sources, actor=ctx.agent, simulated=ctx.simulated_llm)
    ctx.record("done", f"Saved report draft: {a.title}", entity="document", entity_id=str(report.id))
    return {"status": "completed", "document_id": str(report.id)}


# Reports and notifications -----------------------------------------------------------------------------------------

class Snapshot(Args):
    period: Literal["today", "week"] = "today"


@tool("work_snapshot", "Get facts about the user's work for a period, computed from stored records.", Snapshot, untrusted_output=True)
def work_snapshot(ctx: ToolContext, a: Snapshot) -> dict:
    return build_snapshot(ctx.db, ctx.user_id, ctx.timezone, a.period)


def build_snapshot(db: Session, user_id: uuid.UUID, timezone: str, period: str) -> dict:
    now = utcnow()
    zone = get_zone(timezone)
    demo = factory.is_demo(db, user_id)
    local_now = now.astimezone(zone)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start -= timedelta(days=start.weekday())
    end = start + timedelta(days=7 if period == "week" else 1)
    scope = (Task.user_id == user_id, True if demo else Task.is_demo.is_(False))
    completed = db.scalars(select(Task).where(*scope, Task.status == "completed", Task.completed_at >= start)).all()
    open_tasks = db.scalars(select(Task).where(*scope, Task.status.in_(OPEN)).order_by(Task.due_at.is_(None), Task.due_at.asc())).all()
    events = db.scalars(select(CalendarEvent).where(CalendarEvent.user_id == user_id, CalendarEvent.is_demo.is_(demo), CalendarEvent.status == "confirmed", CalendarEvent.start_at >= start, CalendarEvent.start_at < end).order_by(CalendarEvent.start_at)).all()
    reply = db.scalar(select(func.count()).select_from(Email).where(Email.user_id == user_id, Email.is_demo.is_(demo), Email.needs_reply.is_(True), Email.is_archived.is_(False))) or 0
    pending = db.scalar(select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.user_id == user_id, ApprovalRequest.status == "pending")) or 0
    activity = db.scalar(select(func.count()).select_from(ActivityLog).where(ActivityLog.user_id == user_id, ActivityLog.created_at >= start)) or 0
    return {
        "period": period, "tasks_completed": len(completed), "completed_titles": [t.title for t in completed][:10], "tasks_open": len(open_tasks),
        "tasks_overdue": sum(1 for t in open_tasks if t.due_at and t.due_at < now), "top_open_tasks": [{"title": t.title, "priority": t.priority} for t in open_tasks[:5]],
        "events": [{"title": e.title, "start_at": e.start_at.isoformat()} for e in events], "emails_needing_reply": reply, "pending_approvals": pending, "activity_count": activity,
    }


class Deadlines(Args):
    days: int = Field(default=7, ge=1, le=60)


@tool("upcoming_deadlines", "List task and email deadlines in the coming days.", Deadlines, untrusted_output=True)
def upcoming_deadlines(ctx: ToolContext, a: Deadlines) -> dict:
    now, horizon = utcnow(), utcnow() + timedelta(days=a.days)
    tasks = ctx.db.scalars(select(Task).where(*_task_visible(ctx), Task.status.in_(OPEN), Task.due_at >= now, Task.due_at <= horizon)).all()
    emails = ctx.db.scalars(email_service.base_query(ctx.db, ctx.user_id).where(Email.deadline_at >= now, Email.deadline_at <= horizon, Email.is_archived.is_(False))).all()
    items = [{"type": "task", "title": t.title, "due_at": t.due_at.isoformat(), "due_local": ctx.local(t.due_at)} for t in tasks]
    items += [{"type": "email", "title": e.subject, "due_at": e.deadline_at.isoformat(), "due_local": ctx.local(e.deadline_at)} for e in emails]
    return {"items": sorted(items, key=lambda i: i["due_at"])}


class Reminder(Args):
    title: str = Field(min_length=1, max_length=200)
    remind_at: datetime = Field(description="Local time in the user's timezone, or ISO 8601 with an offset")
    task_id: uuid.UUID | None = None


@tool("schedule_reminder", "Schedule an in-app reminder.", Reminder)
def schedule_reminder(ctx: ToolContext, a: Reminder) -> dict:
    moment = to_utc(a.remind_at, ctx.timezone)
    if moment <= utcnow():
        return {"error": "That reminder time has already passed."}
    if a.task_id:
        from app.services.tasks import get_owned

        get_owned(ctx.db, ctx.user_id, a.task_id)
    created = notify(ctx.db, ctx.user_id, "reminder", a.title, f"Reminder set by your AI employee for {ctx.local(moment)}.", link="/tasks", deliver_at=moment, dedupe_key=f"reminder:{a.task_id or a.title[:80]}:{moment.isoformat()}"[:128])
    log_activity(ctx.db, ctx.user_id, "reminder_scheduled", f"Reminder: {a.title} at {ctx.local(moment)}", actor=ctx.agent)
    ctx.record("done", f"Scheduled reminder: {a.title}", entity="notification", remind_at=moment.isoformat())
    return {"status": "completed", "scheduled": True, "already_existed": not created, "deliver_at": moment.isoformat()}


class Notify(Args):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=2000)


@tool("send_notification", "Show an in-app notification to the user.", Notify)
def send_notification(ctx: ToolContext, a: Notify) -> dict:
    notify(ctx.db, ctx.user_id, "ai", a.title, a.body)
    ctx.record("done", f"Notified you: {a.title}", entity="notification")
    return {"status": "completed"}
