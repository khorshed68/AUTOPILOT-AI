"""REST routes for email, tasks, calendar, documents and approvals."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select

from app.agents.llm.base import LLMError
from app.api.deps import DB, CurrentUser, Paging, limit_ai
from app.core.config import settings
from app.core.errors import AppError, ServiceUnavailable, ValidationFailed
from app.database.base import utcnow
from app.integrations import factory
from app.models import ApprovalRequest, Document, Email, Task
from app.schemas import api as s
from app.services import approvals
from app.services import calendar as calendar_service
from app.services import documents as document_service
from app.services import emails as email_service
from app.services.activity import log_activity
from app.services.approvals import propose
from app.services.tasks import apply_status
from app.services.tasks import get_owned as get_task
from app.utils.dates import to_utc

emails = APIRouter(prefix="/emails", tags=["emails"])
tasks = APIRouter(prefix="/tasks", tags=["tasks"])
calendar = APIRouter(prefix="/calendar", tags=["calendar"])
documents = APIRouter(prefix="/documents", tags=["documents"])
approvals_router = APIRouter(prefix="/approvals", tags=["approvals"])


def _ai(fn, *args, **kwargs):
    """AI failures become a clear 503 and never a false success."""
    try:
        return fn(*args, **kwargs)
    except LLMError as exc:
        raise ServiceUnavailable(exc.user_message, code="ai_unavailable") from exc


# Emails ------------------------------------------------------------------------------------------------------------

@emails.get("", response_model=s.Page[s.EmailOut])
def list_emails(db: DB, user: CurrentUser, paging: Paging, view: Literal["inbox", "important", "needs_reply", "archived"] = "inbox", category: str | None = None, q: str | None = Query(None, max_length=200)):
    stmt = email_service.apply_filters(email_service.base_query(db, user.id), view=view, category=category, q=q).order_by(Email.received_at.desc())
    return paging.apply(db, stmt)


@emails.post("/sync")
def sync_emails(db: DB, user: CurrentUser, page_token: str | None = Query(None, max_length=512), max_results: int = Query(25, ge=1, le=50)):
    return email_service.sync_page(db, user.id, page_token, max_results)


@emails.get("/{email_id}", response_model=s.EmailDetail)
def get_email(email_id: uuid.UUID, db: DB, user: CurrentUser):
    email = email_service.get_owned(db, user.id, email_id)
    if not email.is_read:
        email.is_read = True
        db.commit()
    return email


@emails.post("/{email_id}/analyze", response_model=s.EmailDetail, dependencies=[Depends(limit_ai)])
def analyze_email(email_id: uuid.UUID, db: DB, user: CurrentUser):
    return _ai(email_service.analyze, db, user.id, email_service.get_owned(db, user.id, email_id), actor="email")


@emails.post("/{email_id}/draft-reply", response_model=s.ApprovalOut, status_code=201, dependencies=[Depends(limit_ai)])
def draft_reply(email_id: uuid.UUID, body: s.DraftReplyIn, db: DB, user: CurrentUser):
    email = email_service.get_owned(db, user.id, email_id)
    draft, _ = _ai(email_service.draft_reply_text, db, user.id, email, body.instructions)
    approval = email_service.propose_reply(db, user.id, email, draft["subject"], draft["body"], requested_by="email")
    db.commit()
    return approval


@emails.post("/{email_id}/archive", response_model=s.EmailOut)
def archive_email(email_id: uuid.UUID, body: s.ArchiveIn, db: DB, user: CurrentUser):
    email = email_service.get_owned(db, user.id, email_id)
    email.is_archived = body.archived
    log_activity(db, user.id, "email_archived" if body.archived else "email_unarchived", f"{'Archived' if body.archived else 'Restored'} in AutoPilot: {email.subject[:120]}", entity_type="email", entity_id=email.id, simulated=email.is_demo, status="info")
    db.commit()
    return email


# Tasks -------------------------------------------------------------------------------------------------------------

def _task_out(task: Task) -> dict:
    out = s.TaskOut.model_validate(task).model_dump()
    out["overdue"] = bool(task.due_at and task.due_at < utcnow() and task.status in ("pending", "in_progress", "waiting"))
    return out


def _check_links(db, user_id, body: s.TaskIn) -> None:
    if body.related_email_id:
        email_service.get_owned(db, user_id, body.related_email_id)
    if body.related_document_id:
        document_service.get_owned(db, user_id, body.related_document_id)


def _visible_tasks(db, user_id):
    stmt = select(Task).where(Task.user_id == user_id)
    return stmt if factory.is_demo(db, user_id) else stmt.where(Task.is_demo.is_(False))


@tasks.get("", response_model=s.Page[s.TaskOut])
def list_tasks(db: DB, user: CurrentUser, paging: Paging, status: str | None = None, priority: str | None = None, category: str | None = None, overdue: bool = False, q: str | None = Query(None, max_length=200)):
    stmt = _visible_tasks(db, user.id)
    if status == "open":
        stmt = stmt.where(Task.status.in_(("pending", "in_progress", "waiting")))
    elif status:
        stmt = stmt.where(Task.status == status)
    if priority:
        stmt = stmt.where(Task.priority == priority)
    if category:
        stmt = stmt.where(Task.category == category)
    if overdue:
        stmt = stmt.where(Task.due_at < utcnow(), Task.status.in_(("pending", "in_progress", "waiting")))
    if q:
        like = f"%{q.lower().replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(func.lower(Task.title).like(like), func.lower(Task.description).like(like)))
    result = paging.apply(db, stmt.order_by(Task.due_at.is_(None), Task.due_at.asc(), Task.created_at.desc()))
    result["items"] = [_task_out(t) for t in result["items"]]
    return result


@tasks.post("", response_model=s.TaskOut, status_code=201)
def create_task(body: s.TaskIn, db: DB, user: CurrentUser):
    _check_links(db, user.id, body)
    data = body.model_dump(exclude={"status"})
    task = Task(user_id=user.id, source="manual", status="pending", **data)
    apply_status(task, body.status)
    db.add(task)
    db.flush()
    log_activity(db, user.id, "task_created", f"Created task: {task.title}", entity_type="task", entity_id=task.id)
    db.commit()
    return _task_out(task)


@tasks.get("/{task_id}", response_model=s.TaskOut)
def get_one_task(task_id: uuid.UUID, db: DB, user: CurrentUser):
    return _task_out(get_task(db, user.id, task_id))


@tasks.put("/{task_id}", response_model=s.TaskOut)
def update_task(task_id: uuid.UUID, body: s.TaskIn, db: DB, user: CurrentUser):
    task = get_task(db, user.id, task_id)
    _check_links(db, user.id, body)
    for key, value in body.model_dump(exclude={"status"}).items():
        setattr(task, key, value)
    apply_status(task, body.status)
    log_activity(db, user.id, "task_updated", f"Updated task: {task.title}", entity_type="task", entity_id=task.id, meta={"status": task.status})
    db.commit()
    return _task_out(task)


@tasks.delete("/{task_id}", status_code=204)
def delete_task(task_id: uuid.UUID, db: DB, user: CurrentUser):
    task = get_task(db, user.id, task_id)
    log_activity(db, user.id, "task_deleted", f"Deleted task: {task.title}", entity_type="task", entity_id=task.id, status="info")
    db.delete(task)
    db.commit()


# Calendar ----------------------------------------------------------------------------------------------------------

@calendar.get("/events", response_model=list[s.EventOut])
def list_events(db: DB, user: CurrentUser, start: datetime = Query(...), end: datetime = Query(...), include_proposed: bool = True):
    tz = email_service.user_timezone(db, user.id)
    start_utc, end_utc = to_utc(start, tz), to_utc(end, tz)
    if end_utc <= start_utc or end_utc - start_utc > timedelta(days=93):
        raise ValidationFailed("Use a date range of up to three months.")
    return calendar_service.events_between(db, user.id, start_utc, end_utc, include_proposed=include_proposed)


@calendar.post("/sync")
def sync_calendar(db: DB, user: CurrentUser):
    now = utcnow()
    return calendar_service.sync_range(db, user.id, now - timedelta(days=7), now + timedelta(days=60))


@calendar.get("/availability")
def availability(db: DB, user: CurrentUser, day: date = Query(..., alias="date"), duration_minutes: int = Query(60, ge=5, le=480), period: Literal["morning", "afternoon", "evening"] | None = None):
    return calendar_service.free_slots(db, user.id, day, duration_minutes, period)


def _proposal_times(db, user_id, body: s.EventProposalIn):
    tz = email_service.user_timezone(db, user_id)
    return to_utc(body.start_at, tz), to_utc(body.end_at, tz), tz


@calendar.post("/events/proposals", status_code=201)
def propose_event(body: s.EventProposalIn, db: DB, user: CurrentUser):
    start, end, _ = _proposal_times(db, user.id, body)
    found = calendar_service.conflicts(db, user.id, start, end)
    event, approval = calendar_service.propose_event(db, user.id, title=body.title, start=start, end=end, description=body.description, location=body.location, attendees=body.attendees, requested_by="user", source="manual")
    db.commit()
    return {
        "event": s.EventOut.model_validate(event).model_dump(mode="json"),
        "approval": s.ApprovalOut.model_validate(approval).model_dump(mode="json"),
        "conflicts": [{"id": str(e.id), "title": e.title, "start_at": e.start_at.isoformat(), "end_at": e.end_at.isoformat()} for e in found],
    }


@calendar.post("/events/{event_id}/execute", response_model=s.ApprovalOut)
def execute_event(event_id: uuid.UUID, body: s.ExecuteIn, db: DB, user: CurrentUser):
    """Approve and run the pending proposal for this event. Applies the same checks as /approvals/{id}/approve."""
    event = calendar_service.get_owned(db, user.id, event_id)
    approval = db.scalar(select(ApprovalRequest).where(ApprovalRequest.user_id == user.id, ApprovalRequest.action_type == "create_calendar_event", ApprovalRequest.status == "pending", ApprovalRequest.payload["event_id"].as_string() == str(event.id)))
    if approval is None:
        raise ValidationFailed("This event has no pending proposal to execute.")
    return approvals.approve(db, user.id, approval.id, body.payload_hash)


@calendar.post("/events/{event_id}/update-proposal", response_model=s.ApprovalOut, status_code=201)
def propose_update(event_id: uuid.UUID, body: s.EventProposalIn, db: DB, user: CurrentUser):
    event = calendar_service.get_owned(db, user.id, event_id)
    start, end, _ = _proposal_times(db, user.id, body)
    approval = calendar_service.propose_update(db, user.id, event, title=body.title, start=start, end=end, description=body.description, location=body.location, attendees=body.attendees)
    db.commit()
    return approval


@calendar.post("/events/{event_id}/cancel-proposal", response_model=s.ApprovalOut, status_code=201)
def propose_cancellation(event_id: uuid.UUID, db: DB, user: CurrentUser):
    event = calendar_service.get_owned(db, user.id, event_id)
    approval = propose(db, user.id, "delete_calendar_event", {"event_id": str(event.id), "title": event.title}, title=f"Delete event: {event.title}", requested_by="user", origin="calendar")
    db.commit()
    return approval


# Documents ---------------------------------------------------------------------------------------------------------

@documents.get("", response_model=s.Page[s.DocumentOut])
def list_documents(db: DB, user: CurrentUser, paging: Paging, kind: str | None = None, q: str | None = Query(None, max_length=200)):
    stmt = document_service.visible_query(db, user.id)
    if kind:
        stmt = stmt.where(Document.kind == kind)
    if q:
        like = f"%{q.lower().replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(func.lower(Document.title).like(like), func.lower(Document.content).like(like)))
    return paging.apply(db, stmt.order_by(Document.created_at.desc()))


@documents.post("/upload", response_model=s.DocumentOut, status_code=201)
def upload_document(db: DB, user: CurrentUser, file: UploadFile = File(...)):
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    doc = document_service.create_from_bytes(db, user.id, file.filename or "document", data)
    db.commit()
    return doc


@documents.post("/reports", response_model=s.DocumentDetail, status_code=201, dependencies=[Depends(limit_ai)])
def create_report(body: s.ReportIn, db: DB, user: CurrentUser):
    report = _ai(document_service.generate_report, db, user.id, body.document_ids, body.topic)
    db.commit()
    return report


@documents.post("/compare", dependencies=[Depends(limit_ai)])
def compare_documents(body: s.CompareIn, db: DB, user: CurrentUser):
    return _ai(document_service.compare, db, user.id, body.document_ids)


@documents.get("/{document_id}", response_model=s.DocumentDetail)
def get_document(document_id: uuid.UUID, db: DB, user: CurrentUser):
    return document_service.get_owned(db, user.id, document_id)


@documents.put("/{document_id}", response_model=s.DocumentDetail)
def update_document(document_id: uuid.UUID, body: s.DocumentUpdateIn, db: DB, user: CurrentUser):
    doc = document_service.get_owned(db, user.id, document_id)
    if body.content is not None and doc.kind != "report":
        raise ValidationFailed("Only generated reports can be edited.")
    if body.title is not None:
        doc.title = body.title
    if body.content is not None:
        doc.content, doc.size_bytes = body.content, len(body.content.encode())
    log_activity(db, user.id, "report_edited", f"Edited {doc.title}", entity_type="document", entity_id=doc.id, status="info")
    db.commit()
    return doc


@documents.delete("/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, db: DB, user: CurrentUser):
    from pathlib import Path

    doc = document_service.get_owned(db, user.id, document_id)
    if doc.storage_path:
        Path(doc.storage_path).unlink(missing_ok=True)
    log_activity(db, user.id, "document_deleted", f"Deleted {doc.title}", entity_type="document", entity_id=doc.id, status="info")
    db.delete(doc)
    db.commit()


@documents.post("/{document_id}/analyze", response_model=s.DocumentDetail, dependencies=[Depends(limit_ai)])
def analyze_document(document_id: uuid.UUID, db: DB, user: CurrentUser):
    doc = document_service.get_owned(db, user.id, document_id)
    return _ai(document_service.analyze, db, user.id, doc, email_service.user_timezone(db, user.id))


@documents.post("/{document_id}/share-proposal", response_model=s.ApprovalOut, status_code=201)
def propose_share(document_id: uuid.UUID, body: s.ShareIn, db: DB, user: CurrentUser):
    doc = document_service.get_owned(db, user.id, document_id)
    approval = propose(db, user.id, "share_document", {"document_id": str(doc.id), "title": doc.title, "email": body.email, "role": body.role}, title=f"Share {doc.title} with {body.email}", requested_by="user", origin="documents")
    db.commit()
    return approval


@documents.get("/{document_id}/export")
def export_document(document_id: uuid.UUID, db: DB, user: CurrentUser, format: Literal["pdf", "docx"] = "pdf"):
    doc = document_service.get_owned(db, user.id, document_id)
    if doc.kind != "report":
        raise ValidationFailed("Only generated reports can be exported.")
    try:
        data = document_service.export_pdf(doc) if format == "pdf" else document_service.export_docx(doc)
    except (ValueError, OSError) as exc:
        raise AppError("This report could not be exported.", status_code=500, code="export_failed") from exc
    mime = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    name = quote(f"{doc.title[:80]}.{format}")
    return Response(data, media_type=mime, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})


# Approvals ---------------------------------------------------------------------------------------------------------

@approvals_router.get("", response_model=s.Page[s.ApprovalOut])
def list_approvals(db: DB, user: CurrentUser, paging: Paging, status: str | None = None, conversation_id: uuid.UUID | None = None):
    stmt = select(ApprovalRequest).where(ApprovalRequest.user_id == user.id)
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    if conversation_id:
        stmt = stmt.where(ApprovalRequest.conversation_id == conversation_id)
    return paging.apply(db, stmt.order_by(ApprovalRequest.created_at.desc()))


@approvals_router.get("/{approval_id}", response_model=s.ApprovalOut)
def get_approval(approval_id: uuid.UUID, db: DB, user: CurrentUser):
    return approvals.get_owned(db, user.id, approval_id)


@approvals_router.put("/{approval_id}", response_model=s.ApprovalOut)
def edit_approval(approval_id: uuid.UUID, body: s.ApprovalEditIn, db: DB, user: CurrentUser):
    return approvals.edit(db, user.id, approval_id, body.expected_hash, body.payload)


@approvals_router.post("/{approval_id}/approve", response_model=s.ApprovalOut)
def approve(approval_id: uuid.UUID, body: s.ApproveIn, db: DB, user: CurrentUser):
    return approvals.approve(db, user.id, approval_id, body.payload_hash)


@approvals_router.post("/{approval_id}/reject", response_model=s.ApprovalOut)
def reject(approval_id: uuid.UUID, body: s.RejectIn, db: DB, user: CurrentUser):
    return approvals.reject(db, user.id, approval_id, body.reason)
