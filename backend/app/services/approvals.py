"""Human approval system.

Rules enforced here, never in the client:
  * an action can only run through `approve`, with the hash of the payload the user reviewed;
  * editing a payload changes its hash, so an earlier approval cannot apply to it;
  * pending -> executing is one atomic UPDATE, so an action cannot run twice;
  * success is recorded only from the provider's confirmation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, Conflict, ExternalServiceError, NotFound, ValidationFailed
from app.database.base import utcnow
from app.integrations import factory
from app.models import ApprovalRequest, CalendarEvent, Document, Email, Task
from app.services.activity import log_activity, notify
from app.utils.dates import is_valid_timezone

logger = logging.getLogger("autopilot.approvals")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SendEmailPayload(_Strict):
    to: list[EmailStr] = Field(min_length=1, max_length=20)
    cc: list[EmailStr] = Field(default_factory=list, max_length=20)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=50_000)
    email_id: uuid.UUID | None = None

    @field_validator("subject")
    @classmethod
    def _single_line(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Subject must be a single line")
        return value


class _EventFields(_Strict):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=8000)
    location: str | None = Field(default=None, max_length=255)
    start_at: datetime
    end_at: datetime
    timezone: str
    attendees: list[EmailStr] = Field(default_factory=list, max_length=50)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        if not is_valid_timezone(value):
            raise ValueError("Unknown timezone")
        return value

    @field_validator("start_at", "end_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Must include a UTC offset")
        return value

    @model_validator(mode="after")
    def _order(self):
        if self.end_at <= self.start_at:
            raise ValueError("End must be after start")
        return self


class CreateEventPayload(_EventFields):
    event_id: uuid.UUID | None = None


class UpdateEventPayload(_EventFields):
    event_id: uuid.UUID


class DeleteEventPayload(_Strict):
    event_id: uuid.UUID
    title: str


class ShareDocumentPayload(_Strict):
    document_id: uuid.UUID
    title: str
    email: EmailStr
    role: Literal["reader", "commenter", "writer"] = "reader"


class CreateTaskPayload(_Strict):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=8000)
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    due_at: datetime | None = None
    category: str | None = None
    related_email_id: uuid.UUID | None = None
    related_document_id: uuid.UUID | None = None


PAYLOADS: dict[str, type[_Strict]] = {
    "send_email": SendEmailPayload,
    "create_calendar_event": CreateEventPayload,
    "update_calendar_event": UpdateEventPayload,
    "delete_calendar_event": DeleteEventPayload,
    "share_document": ShareDocumentPayload,
    "create_task": CreateTaskPayload,
}


IMMUTABLE_REFERENCES = ("email_id", "event_id", "document_id", "related_email_id", "related_document_id")


def validate_payload(action_type: str, payload: dict) -> dict:
    model = PAYLOADS.get(action_type)
    if model is None:
        raise ValidationFailed(f"Unknown action type: {action_type}")
    try:
        return model.model_validate(payload).model_dump(mode="json")
    except ValueError as exc:
        raise ValidationFailed("The action details are invalid.", details=json.loads(exc.json(include_url=False, include_context=False, include_input=False)) if hasattr(exc, "json") else str(exc)) from exc


def payload_hash(action_type: str, payload: dict, version: int) -> str:
    canonical = json.dumps({"action": action_type, "payload": payload, "version": version}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _dedupe_hash(action_type: str, payload: dict) -> str:
    return hashlib.sha256(json.dumps([action_type, payload], sort_keys=True).encode()).hexdigest()


def get_owned(db: Session, user_id: uuid.UUID, approval_id: uuid.UUID) -> ApprovalRequest:
    row = db.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id, ApprovalRequest.user_id == user_id))
    if row is None:
        raise NotFound("Approval request not found.")
    return row


def propose(
    db: Session, user_id: uuid.UUID, action_type: str, payload: dict, *, title: str, description: str = "", requested_by: str = "user",
    origin: str = "chat", conversation_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None, derived_from_untrusted: bool = False,
) -> ApprovalRequest:
    clean = validate_payload(action_type, payload)
    _check_references(db, user_id, clean)
    dedupe = _dedupe_hash(action_type, clean)
    existing = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.user_id == user_id, ApprovalRequest.dedupe_hash == dedupe, ApprovalRequest.status == "pending")
    )
    if existing is not None and existing.expires_at > utcnow():
        return existing
    demo = factory.is_demo(db, user_id)
    row = ApprovalRequest(
        user_id=user_id, action_type=action_type, title=title[:255], description=description, payload=clean, version=1,
        payload_hash=payload_hash(action_type, clean, 1), dedupe_hash=dedupe, requested_by=requested_by, origin=origin,
        conversation_id=conversation_id, run_id=run_id, derived_from_untrusted=derived_from_untrusted, simulated=demo and action_type != "create_task",
        expires_at=utcnow() + timedelta(hours=settings.approval_ttl_hours),
    )
    db.add(row)
    db.flush()
    log_activity(db, user_id, "approval_requested", f"Approval requested: {title}", actor=requested_by, entity_type="approval", entity_id=row.id, status="pending", simulated=row.simulated)
    notify(db, user_id, "approval", "Approval needed", title, link="/approvals", dedupe_key=f"approval:{row.id}", preference="approvals", is_demo=row.simulated)
    return row


def _check_references(db: Session, user_id: uuid.UUID, payload: dict) -> None:
    """Payload ids are attacker-influenced, so each must belong to the acting user."""
    refs = [("email_id", Email), ("related_email_id", Email), ("event_id", CalendarEvent), ("document_id", Document), ("related_document_id", Document)]
    for key, model in refs:
        if payload.get(key) and db.scalar(select(model.id).where(model.id == uuid.UUID(payload[key]), model.user_id == user_id)) is None:
            raise NotFound(f"{model.__name__} referenced by this action was not found.")


def edit(db: Session, user_id: uuid.UUID, approval_id: uuid.UUID, expected_hash: str, new_payload: dict) -> ApprovalRequest:
    row = get_owned(db, user_id, approval_id)
    _require_open(db, row)
    if row.payload_hash != expected_hash:
        raise Conflict("This action changed since you opened it. Review the latest version.", code="payload_changed")
    clean = validate_payload(row.action_type, new_payload)
    _check_references(db, user_id, clean)
    # Links to other records decide what the action touches, so an edit can't retarget them.
    if any(clean.get(key) != row.payload.get(key) for key in IMMUTABLE_REFERENCES):
        raise ValidationFailed("This action's linked record can't be changed. Propose a new action instead.", code="reference_immutable")
    row.payload = clean
    row.version += 1
    row.payload_hash = payload_hash(row.action_type, clean, row.version)
    row.dedupe_hash = _dedupe_hash(row.action_type, clean)
    log_activity(db, user_id, "approval_edited", f"Edited proposed action: {row.title}", entity_type="approval", entity_id=row.id, status="info", simulated=row.simulated, meta={"version": row.version})
    db.commit()
    return row


def _require_open(db: Session, row: ApprovalRequest) -> None:
    if row.status != "pending":
        raise Conflict(f"This action is already {row.status}.", code="approval_not_pending")
    if row.expires_at <= utcnow():
        row.status = "expired"
        db.commit()
        raise Conflict("This approval has expired. Ask your AI employee to propose it again.", code="approval_expired")


def reject(db: Session, user_id: uuid.UUID, approval_id: uuid.UUID, reason: str | None = None) -> ApprovalRequest:
    row = get_owned(db, user_id, approval_id)
    _require_open(db, row)
    claimed = db.execute(update(ApprovalRequest).where(ApprovalRequest.id == row.id, ApprovalRequest.status == "pending").values(status="rejected", decided_at=utcnow(), error=reason)).rowcount
    if not claimed:
        raise Conflict("This action was already decided.", code="approval_not_pending")
    if row.payload.get("event_id") and row.action_type == "create_calendar_event":
        event = db.get(CalendarEvent, uuid.UUID(row.payload["event_id"]))
        if event is not None and event.user_id == user_id and event.status == "proposed":
            event.status = "rejected"
    log_activity(db, user_id, "approval_rejected", f"Rejected: {row.title}", entity_type="approval", entity_id=row.id, status="info", simulated=row.simulated)
    db.commit()
    db.refresh(row)
    return row


def approve(db: Session, user_id: uuid.UUID, approval_id: uuid.UUID, reviewed_hash: str) -> ApprovalRequest:
    row = get_owned(db, user_id, approval_id)
    _require_open(db, row)
    if row.payload_hash != reviewed_hash:
        raise Conflict("The action changed after you reviewed it, so it needs a new approval.", code="payload_changed")
    # What the user saw must match where it would run: a card shown as simulated must never hit a real service.
    if row.action_type != "create_task" and row.simulated != factory.is_demo(db, user_id):
        row.status = "expired"
        row.error = "Integration mode changed after this was proposed."
        db.commit()
        raise Conflict("You switched between demo and live after this was proposed, so it wasn't run. Ask for it again.", code="mode_changed")
    # Re-validate against current rules: the stored payload may predate them.
    validate_payload(row.action_type, row.payload)
    _check_references(db, user_id, row.payload)

    now = utcnow()
    claimed = db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == row.id, ApprovalRequest.user_id == user_id, ApprovalRequest.status == "pending", ApprovalRequest.payload_hash == reviewed_hash)
        .values(status="executing", decided_at=now, executing_since=now)
    ).rowcount
    db.commit()
    if not claimed:
        raise Conflict("This action was already decided.", code="approval_not_pending")
    db.refresh(row)
    return _execute(db, row)


def _execute(db: Session, row: ApprovalRequest) -> ApprovalRequest:
    handler = EXECUTORS[row.action_type]
    try:
        result = handler(db, row)
    except ExternalServiceError as exc:
        db.rollback()
        return _finish_failure(db, row, exc.message, "outcome_unknown" if exc.outcome_unknown else "failed")
    except AppError as exc:
        db.rollback()
        return _finish_failure(db, row, exc.message, "failed")
    except Exception:
        db.rollback()
        logger.exception("approval_execution_crashed", extra={"approval_id": str(row.id)})
        return _finish_failure(db, row, "The action failed with an internal error. It was not confirmed as completed.", "outcome_unknown")
    row = db.get(ApprovalRequest, row.id)
    row.status = "executed"
    row.result = result
    row.executed_at = utcnow()
    row.simulated = bool(result.get("simulated", False))
    prefix = "Simulated: " if row.simulated else ""
    log_activity(db, row.user_id, f"executed_{row.action_type}", f"{prefix}{row.title}", entity_type="approval", entity_id=row.id, simulated=row.simulated, meta={"external_id": result.get("external_id")})
    db.commit()
    return row


def _finish_failure(db: Session, row: ApprovalRequest, message: str, status: str) -> ApprovalRequest:
    row = db.get(ApprovalRequest, row.id)
    row.status = status
    row.error = message
    log_activity(db, row.user_id, f"failed_{row.action_type}", f"Failed: {row.title} — {message}", entity_type="approval", entity_id=row.id, status="failure", simulated=row.simulated)
    notify(db, row.user_id, "approval_failed", f"Action failed: {row.title}", message, link="/approvals", dedupe_key=f"approval-failed:{row.id}", is_demo=row.simulated)
    db.commit()
    return row


def recover_stuck(db: Session, older_than_minutes: int = 10) -> int:
    """A crash mid-execution leaves the outcome unknown; it is surfaced to the user and never retried blindly."""
    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    rows = db.scalars(select(ApprovalRequest).where(ApprovalRequest.status == "executing", ApprovalRequest.executing_since < cutoff)).all()
    for row in rows:
        _finish_failure(db, row, "Execution was interrupted before the service confirmed it. Check the external service before retrying.", "outcome_unknown")
    return len(rows)


def expire_pending(db: Session) -> int:
    return db.execute(update(ApprovalRequest).where(ApprovalRequest.status == "pending", ApprovalRequest.expires_at <= utcnow()).values(status="expired")).rowcount


# Executors -----------------------------------------------------------------------------------------------------------

def _exec_send_email(db: Session, row: ApprovalRequest) -> dict:
    p = row.payload
    thread_id = in_reply_to = None
    if p.get("email_id"):
        source = db.get(Email, uuid.UUID(p["email_id"]))
        thread_id, in_reply_to = source.thread_id, source.rfc822_message_id
    provider = factory.email_provider(db, row.user_id)
    result = provider.send(p["to"], p.get("cc", []), p["subject"], p["body"], thread_id, in_reply_to).as_dict()
    if p.get("email_id"):
        source.needs_reply = False
    return result


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _exec_create_event(db: Session, row: ApprovalRequest) -> dict:
    p = row.payload
    provider = factory.calendar_provider(db, row.user_id)
    result = provider.create_event(p, str(row.id)).as_dict()
    event = db.get(CalendarEvent, uuid.UUID(p["event_id"])) if p.get("event_id") else None
    if event is None:
        event = CalendarEvent(user_id=row.user_id, source="ai", is_demo=result["simulated"])
        db.add(event)
    event.external_id, event.status = result["external_id"], "confirmed"
    event.title, event.description, event.location = p["title"], p.get("description", ""), p.get("location")
    event.start_at, event.end_at, event.timezone, event.attendees = _dt(p["start_at"]), _dt(p["end_at"]), p["timezone"], p.get("attendees", [])
    db.flush()
    return {**result, "event_id": str(event.id)}


def _exec_update_event(db: Session, row: ApprovalRequest) -> dict:
    p = row.payload
    event = db.get(CalendarEvent, uuid.UUID(p["event_id"]))
    result = factory.calendar_provider(db, row.user_id).update_event(event.external_id or str(event.id), p).as_dict()
    event.title, event.description, event.location = p["title"], p.get("description", ""), p.get("location")
    event.start_at, event.end_at, event.timezone, event.attendees = _dt(p["start_at"]), _dt(p["end_at"]), p["timezone"], p.get("attendees", [])
    return result


def _exec_delete_event(db: Session, row: ApprovalRequest) -> dict:
    event = db.get(CalendarEvent, uuid.UUID(row.payload["event_id"]))
    result = factory.calendar_provider(db, row.user_id).delete_event(event.external_id or str(event.id)).as_dict()
    event.status = "cancelled"
    return result


def _exec_share_document(db: Session, row: ApprovalRequest) -> dict:
    p = row.payload
    doc = db.get(Document, uuid.UUID(p["document_id"]))
    if not doc.drive_file_id and not factory.is_demo(db, row.user_id):
        raise AppError("Only documents imported from Google Drive can be shared through Drive.")
    provider = factory.drive_provider(db, row.user_id)
    return provider.share(doc.drive_file_id or str(doc.id), p["email"], p["role"]).as_dict()


def _exec_create_task(db: Session, row: ApprovalRequest) -> dict:
    p = row.payload
    task = Task(
        user_id=row.user_id, title=p["title"], description=p.get("description", ""), priority=p.get("priority", "medium"),
        due_at=_dt(p["due_at"]) if p.get("due_at") else None, category=p.get("category"), source="automation",
        related_email_id=uuid.UUID(p["related_email_id"]) if p.get("related_email_id") else None,
        related_document_id=uuid.UUID(p["related_document_id"]) if p.get("related_document_id") else None,
    )
    db.add(task)
    db.flush()
    return {"external_id": None, "simulated": False, "provider": "autopilot", "task_id": str(task.id)}


EXECUTORS: dict[str, Callable[[Session, ApprovalRequest], dict]] = {
    "send_email": _exec_send_email,
    "create_calendar_event": _exec_create_event,
    "update_calendar_event": _exec_update_event,
    "delete_calendar_event": _exec_delete_event,
    "share_document": _exec_share_document,
    "create_task": _exec_create_task,
}
