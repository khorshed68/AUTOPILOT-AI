from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime

APPROVAL_STATUSES = ("pending", "executing", "executed", "failed", "rejected", "expired", "outcome_unknown")


class ApprovalRequest(IdMixin, TimestampMixin, Base):
    """A proposed sensitive action. `payload_hash` binds any approval to the exact payload shown."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','executing','executed','failed','rejected','expired','outcome_unknown')", name="status"
        ),
        Index("ix_approval_requests_user_status", "user_id", "status", "created_at"),
        Index("ix_approval_requests_dedupe", "user_id", "dedupe_hash", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    action_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSONType)
    payload_hash: Mapped[str] = mapped_column(String(64))
    dedupe_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    requested_by: Mapped[str] = mapped_column(String(32), default="user")
    origin: Mapped[str] = mapped_column(String(16), default="chat")
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"))
    derived_from_untrusted: Mapped[bool] = mapped_column(Boolean, default=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    executing_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Notification(IdMixin, TimestampMixin, Base):
    """`deliver_at` in the future makes this a pending reminder; `dedupe_key` prevents repeats."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
        Index("ix_notifications_user_deliver", "user_id", "deliver_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(255))
    deliver_at: Mapped[datetime] = mapped_column(UTCDateTime)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    dedupe_key: Mapped[str | None] = mapped_column(String(128))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class ActivityLog(IdMixin, Base):
    """Append-only audit trail for user, agent and system actions."""

    __tablename__ = "activity_logs"
    __table_args__ = (
        CheckConstraint("status in ('success','failure','info','pending')", name="status"),
        Index("ix_activity_logs_user_created", "user_id", "created_at"),
        Index("ix_activity_logs_user_action", "user_id", "action"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    actor: Mapped[str] = mapped_column(String(32), default="user")
    action: Mapped[str] = mapped_column(String(48))
    description: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(8), default="success")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
