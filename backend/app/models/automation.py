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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime


class Automation(IdMixin, TimestampMixin, Base):
    __tablename__ = "automations"
    __table_args__ = (
        CheckConstraint("trigger_type in ('manual','daily','weekly','new_email')", name="trigger_type"),
        CheckConstraint("condition_logic in ('all','any')", name="condition_logic"),
        Index("ix_automations_due", "enabled", "trigger_type", "next_run_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    trigger_type: Mapped[str] = mapped_column(String(16))
    trigger_config: Mapped[dict] = mapped_column(JSONType, default=dict)
    conditions: Mapped[list] = mapped_column(JSONType, default=list)
    condition_logic: Mapped[str] = mapped_column(String(3), default="all")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    steps: Mapped[list[AutomationStep]] = relationship(
        back_populates="automation", cascade="all, delete-orphan", order_by="AutomationStep.position"
    )
    executions: Mapped[list[AutomationExecution]] = relationship(back_populates="automation", cascade="all, delete-orphan")


class AutomationStep(IdMixin, TimestampMixin, Base):
    __tablename__ = "automation_steps"
    __table_args__ = (UniqueConstraint("automation_id", "position", name="uq_automation_steps_position"),)

    automation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("automations.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict] = mapped_column(JSONType, default=dict)

    automation: Mapped[Automation] = relationship(back_populates="steps")


class AutomationExecution(IdMixin, TimestampMixin, Base):
    __tablename__ = "automation_executions"
    __table_args__ = (
        CheckConstraint("status in ('queued','running','succeeded','failed','skipped')", name="status"),
        UniqueConstraint("automation_id", "dedupe_key", name="uq_automation_executions_dedupe"),
        Index("ix_automation_executions_user_created", "user_id", "created_at"),
        Index("ix_automation_executions_status", "status", "created_at"),
    )

    automation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("automations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(12), default="queued")
    trigger: Mapped[str] = mapped_column(String(16))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(128))
    context: Mapped[dict] = mapped_column(JSONType, default=dict)
    step_results: Mapped[list] = mapped_column(JSONType, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    automation: Mapped[Automation] = relationship(back_populates="executions")
