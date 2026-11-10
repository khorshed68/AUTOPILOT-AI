"""Simulated providers for demo mode. Results are always marked simulated and never touch a real service."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ExternalServiceError
from app.database.base import utcnow
from app.integrations.base import EmailPage, ExternalResult, RemoteEmail, RemoteEvent, RemoteFile
from app.models import CalendarEvent, Document, Email

SIM_FAILURE_DOMAIN = "bounce.example.com"


class DemoEmailProvider:
    name = "demo"
    simulated = True

    def __init__(self, db: Session, user_id: uuid.UUID):
        self.db, self.user_id = db, user_id

    def fetch_page(self, page_token: str | None, max_results: int) -> EmailPage:
        rows = self.db.scalars(
            select(Email).where(Email.user_id == self.user_id, Email.is_demo.is_(True)).order_by(Email.received_at.desc())
        ).all()
        offset = int(page_token or 0)
        page = rows[offset : offset + max_results]
        emails = [
            RemoteEmail(e.external_id, e.thread_id, e.sender_name, e.sender_email, e.recipients, e.subject, e.snippet, e.body, e.received_at, e.labels)
            for e in page
        ]
        next_offset = offset + max_results
        return EmailPage(emails, str(next_offset) if next_offset < len(rows) else None)

    def send(self, to, cc, subject, body, thread_id, in_reply_to) -> ExternalResult:
        if any(addr.lower().endswith("@" + SIM_FAILURE_DOMAIN) for addr in to):
            raise ExternalServiceError(f"Simulated delivery failure: {SIM_FAILURE_DOMAIN} rejected the message.")
        return ExternalResult(f"sim-msg-{uuid.uuid4().hex[:12]}", True, "demo", {"to": to, "subject": subject})


class DemoCalendarProvider:
    name = "demo"
    simulated = True

    def __init__(self, db: Session, user_id: uuid.UUID):
        self.db, self.user_id = db, user_id

    def list_events(self, start: datetime, end: datetime) -> list[RemoteEvent]:
        rows = self.db.scalars(
            select(CalendarEvent).where(
                CalendarEvent.user_id == self.user_id, CalendarEvent.is_demo.is_(True), CalendarEvent.status == "confirmed",
                CalendarEvent.end_at > start, CalendarEvent.start_at < end,
            )
        ).all()
        return [
            RemoteEvent(e.external_id or str(e.id), e.title, e.description, e.location, e.start_at, e.end_at, e.timezone, e.attendees)
            for e in rows
        ]

    def create_event(self, payload: dict, idempotency_key: str) -> ExternalResult:
        return ExternalResult(f"sim-evt-{idempotency_key[:16]}", True, "demo", {"title": payload["title"], "created_at": utcnow().isoformat()})

    def update_event(self, external_id: str, payload: dict) -> ExternalResult:
        return ExternalResult(external_id, True, "demo", {"updated": True})

    def delete_event(self, external_id: str) -> ExternalResult:
        return ExternalResult(external_id, True, "demo", {"deleted": True})


class DemoDriveProvider:
    name = "demo"
    simulated = True

    def __init__(self, db: Session, user_id: uuid.UUID):
        self.db, self.user_id = db, user_id

    def list_files(self, query: str | None, page_size: int) -> list[RemoteFile]:
        stmt = select(Document).where(Document.user_id == self.user_id, Document.is_demo.is_(True))
        rows = self.db.scalars(stmt.limit(page_size)).all()
        return [RemoteFile(str(d.id), d.filename, d.mime_type) for d in rows if not query or query.lower() in d.filename.lower()]

    def download(self, file_id: str) -> tuple[bytes, str, str]:
        doc = self.db.scalar(select(Document).where(Document.user_id == self.user_id, Document.id == uuid.UUID(file_id)))
        if doc is None:
            raise ExternalServiceError("Simulated Drive file not found.")
        return doc.content.encode(), doc.filename, "text/plain"

    def share(self, file_id: str, email: str, role: str) -> ExternalResult:
        return ExternalResult(f"sim-perm-{uuid.uuid4().hex[:10]}", True, "demo", {"file_id": file_id, "shared_with": email, "role": role})
