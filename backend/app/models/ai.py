from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime


class Conversation(IdMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), default="New conversation")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(IdMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role in ('user','assistant')", name="role"),
        CheckConstraint("status in ('complete','streaming','error')", name="status"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("conversations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="complete")
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"))
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Agent(IdMixin, TimestampMixin, Base):
    """Catalogue of agents; rows are kept in sync with the code registry."""

    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    allowed_tools: Mapped[list] = mapped_column(JSONType, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentRun(IdMixin, TimestampMixin, Base):
    """One orchestrated handling of a request. Drives the AI employee status."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("status in ('running','completed','partial','failed','needs_input')", name="status"),
        Index("ix_agent_runs_user_status", "user_id", "status"),
        Index("ix_agent_runs_user_started", "user_id", "started_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("conversations.id", ondelete="SET NULL"))
    origin: Mapped[str] = mapped_column(String(16), default="chat")
    request_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="running")
    plan: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    llm_provider: Mapped[str] = mapped_column(String(32), default="mock")
    simulated_llm: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    executions: Mapped[list[AgentExecution]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="AgentExecution.started_at")


class AgentExecution(IdMixin, TimestampMixin, Base):
    """Structured record of one agent working on one plan step."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        CheckConstraint("status in ('running','completed','needs_input','failed','skipped')", name="status"),
        Index("ix_agent_executions_user_started", "user_id", "started_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    agent_name: Mapped[str] = mapped_column(String(32), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("agents.id", ondelete="SET NULL"))
    task_id: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONType)
    tool_calls: Mapped[list] = mapped_column(JSONType, default=list)
    errors: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(16), default="running")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    run: Mapped[AgentRun] = relationship(back_populates="executions")


class Memory(IdMixin, TimestampMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("kind in ('preference','project','tone','context','other')", name="kind"),
        CheckConstraint("source in ('user','ai')", name="source"),
        Index("ix_memories_user_kind", "user_id", "kind"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16), default="other")
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8), default="user")
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
