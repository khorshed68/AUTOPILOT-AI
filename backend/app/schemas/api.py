"""Request and response models for the REST API."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("password")
    @classmethod
    def _strength(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("Password must contain letters and numbers")
        return value


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PreferencesOut(ORM):
    timezone: str
    theme: str
    integration_mode: str
    communication_tone: str
    employee_paused: bool
    memory_enabled: bool
    memory_auto_create: bool
    work_start_hour: int
    work_end_hour: int
    notification_prefs: dict


class PreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str | None = None
    theme: Literal["light", "dark", "system"] | None = None
    integration_mode: Literal["demo", "live"] | None = None
    communication_tone: Literal["professional", "friendly", "concise", "formal"] | None = None
    memory_enabled: bool | None = None
    memory_auto_create: bool | None = None
    work_start_hour: int | None = Field(default=None, ge=0, le=23)
    work_end_hour: int | None = Field(default=None, ge=1, le=24)
    notification_prefs: dict[str, bool | int] | None = None


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    created_at: datetime
    preferences: PreferencesOut


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: uuid.UUID | None = None
    stream: bool = True


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    stream: bool = True


class MessageOut(ORM):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    status: str
    meta: dict
    created_at: datetime


class ConversationOut(ORM):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class EmailOut(ORM):
    id: uuid.UUID
    sender_name: str | None
    sender_email: str
    subject: str
    snippet: str
    received_at: datetime
    category: str | None
    importance: str
    ai_summary: str | None
    suggested_action: str | None
    action_items: list
    deadline_at: datetime | None
    deadline_text: str | None
    needs_reply: bool
    is_read: bool
    is_archived: bool
    injection_flags: list
    analyzed_at: datetime | None
    is_demo: bool


class EmailDetail(EmailOut):
    body: str
    recipients: list


class DraftReplyIn(BaseModel):
    instructions: str | None = Field(default=None, max_length=2000)


class ArchiveIn(BaseModel):
    archived: bool = True


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=8000)
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    status: Literal["pending", "in_progress", "waiting", "completed", "cancelled"] = "pending"
    category: str | None = Field(default=None, max_length=64)
    progress: int = Field(default=0, ge=0, le=100)
    due_at: datetime | None = None
    related_email_id: uuid.UUID | None = None
    related_document_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title is required")
        return value.strip()


class TaskOut(ORM):
    id: uuid.UUID
    title: str
    description: str
    priority: str
    status: str
    category: str | None
    progress: int
    due_at: datetime | None
    completed_at: datetime | None
    source: str
    related_email_id: uuid.UUID | None
    related_document_id: uuid.UUID | None
    is_demo: bool
    created_at: datetime
    updated_at: datetime
    overdue: bool = False


class EventOut(ORM):
    id: uuid.UUID
    title: str
    description: str
    location: str | None
    start_at: datetime
    end_at: datetime
    timezone: str
    attendees: list
    status: str
    source: str
    is_demo: bool


class EventProposalIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    start_at: datetime
    end_at: datetime
    description: str = Field(default="", max_length=8000)
    location: str | None = Field(default=None, max_length=255)
    attendees: list[EmailStr] = Field(default_factory=list, max_length=50)


class ExecuteIn(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)


class DocumentOut(ORM):
    id: uuid.UUID
    title: str
    filename: str
    mime_type: str
    size_bytes: int
    kind: str
    status: str
    error: str | None
    analysis: dict | None
    sources: list
    injection_flags: list
    is_demo: bool
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentOut):
    content: str


class ReportIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=6)
    topic: str = Field(min_length=1, max_length=300)


class CompareIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=2, max_length=4)


class DocumentUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=200_000)


class ShareIn(BaseModel):
    email: EmailStr
    role: Literal["reader", "commenter", "writer"] = "reader"


class StepIn(BaseModel):
    action_type: str
    config: dict[str, Any] = Field(default_factory=dict)


class AutomationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    trigger_type: Literal["manual", "daily", "weekly", "new_email"]
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    condition_logic: Literal["all", "any"] = "all"
    steps: list[StepIn] = Field(min_length=1, max_length=12)


class StepOut(ORM):
    id: uuid.UUID
    position: int
    action_type: str
    config: dict


class ExecutionOut(ORM):
    id: uuid.UUID
    automation_id: uuid.UUID
    status: str
    trigger: str
    is_test: bool
    step_results: list
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    is_demo: bool
    created_at: datetime


class AutomationOut(ORM):
    id: uuid.UUID
    name: str
    description: str
    trigger_type: str
    trigger_config: dict
    conditions: list
    condition_logic: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    is_demo: bool
    steps: list[StepOut]
    created_at: datetime


class ToggleIn(BaseModel):
    enabled: bool


class RunIn(BaseModel):
    test: bool = False


class ApprovalOut(ORM):
    id: uuid.UUID
    action_type: str
    title: str
    description: str
    payload: dict
    payload_hash: str
    version: int
    status: str
    requested_by: str
    origin: str
    conversation_id: uuid.UUID | None
    derived_from_untrusted: bool
    simulated: bool
    result: dict | None
    error: str | None
    expires_at: datetime
    decided_at: datetime | None
    executed_at: datetime | None
    created_at: datetime


class ApproveIn(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ApprovalEditIn(BaseModel):
    expected_hash: str = Field(min_length=64, max_length=64)
    payload: dict[str, Any]


class NotificationOut(ORM):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    link: str | None
    deliver_at: datetime
    read_at: datetime | None
    is_demo: bool


class ActivityOut(ORM):
    id: uuid.UUID
    actor: str
    action: str
    description: str
    entity_type: str | None
    entity_id: str | None
    status: str
    simulated: bool
    created_at: datetime
    meta: dict


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    kind: Literal["preference", "project", "tone", "context", "other"] = "other"


class MemoryOut(ORM):
    id: uuid.UUID
    kind: str
    content: str
    source: str
    last_used_at: datetime | None
    created_at: datetime


class GitHubConnectIn(BaseModel):
    token: str = Field(min_length=20, max_length=255)


class DriveImportIn(BaseModel):
    file_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class PauseIn(BaseModel):
    paused: bool


class AvailabilityQuery(BaseModel):
    date: date
    duration_minutes: int = 60
