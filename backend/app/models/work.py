from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime

EMAIL_CATEGORIES = ("Important", "Work", "Personal", "Promotions", "Finance", "Notifications", "Urgent")
TASK_PRIORITIES = ("critical", "high", "medium", "low")
TASK_STATUSES = ("pending", "in_progress", "waiting", "completed", "cancelled")


class Email(IdMixin, TimestampMixin, Base):
    __tablename__ = "emails"
    __table_args__ = (
        UniqueConstraint("user_id", "external_id", "is_demo", name="uq_emails_user_external"),
        CheckConstraint("importance in ('low','normal','high')", name="importance"),
        Index("ix_emails_user_received", "user_id", "is_demo", "received_at"),
        Index("ix_emails_user_needs_reply", "user_id", "needs_reply"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(128))
    thread_id: Mapped[str | None] = mapped_column(String(128))
    rfc822_message_id: Mapped[str | None] = mapped_column(String(998))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    sender_email: Mapped[str] = mapped_column(String(320))
    recipients: Mapped[list] = mapped_column(JSONType, default=list)
    subject: Mapped[str] = mapped_column(String(998), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    labels: Mapped[list] = mapped_column(JSONType, default=list)
    category: Mapped[str | None] = mapped_column(String(16))
    importance: Mapped[str] = mapped_column(String(8), default="normal")
    ai_summary: Mapped[str | None] = mapped_column(Text)
    suggested_action: Mapped[str | None] = mapped_column(Text)
    action_items: Mapped[list] = mapped_column(JSONType, default=list)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deadline_text: Mapped[str | None] = mapped_column(String(255))
    needs_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    injection_flags: Mapped[list] = mapped_column(JSONType, default=list)
    analyzed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    automation_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Document(IdMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("kind in ('upload','report','drive')", name="kind"),
        CheckConstraint("status in ('ready','failed')", name="status"),
        Index("ix_documents_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    storage_path: Mapped[str | None] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(8), default="upload")
    status: Mapped[str] = mapped_column(String(8), default="ready")
    content: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)
    analysis: Mapped[dict | None] = mapped_column(JSONType)
    sources: Mapped[list] = mapped_column(JSONType, default=list)
    drive_file_id: Mapped[str | None] = mapped_column(String(128))
    injection_flags: Mapped[list] = mapped_column(JSONType, default=list)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Task(IdMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("priority in ('critical','high','medium','low')", name="priority"),
        CheckConstraint("status in ('pending','in_progress','waiting','completed','cancelled')", name="status"),
        CheckConstraint("progress between 0 and 100", name="progress"),
        Index("ix_tasks_user_status_due", "user_id", "status", "due_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(8), default="medium")
    status: Mapped[str] = mapped_column(String(12), default="pending")
    category: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(default=0)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    related_email_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("emails.id", ondelete="SET NULL"))
    related_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class CalendarEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint("status in ('confirmed','proposed','cancelled','rejected')", name="status"),
        CheckConstraint("end_at > start_at", name="time_order"),
        Index("ix_calendar_events_user_start", "user_id", "is_demo", "start_at"),
        UniqueConstraint("user_id", "external_id", "is_demo", name="uq_calendar_events_user_external"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    external_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str | None] = mapped_column(String(255))
    start_at: Mapped[datetime] = mapped_column(UTCDateTime)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    attendees: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(12), default="confirmed")
    source: Mapped[str] = mapped_column(String(16), default="manual")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
