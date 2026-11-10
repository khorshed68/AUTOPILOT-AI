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


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role in ('user','admin')", name="role"),)

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)

    preferences: Mapped[UserPreference] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")


class AuthSession(IdMixin, TimestampMixin, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    user_agent: Mapped[str | None] = mapped_column(String(255))


def default_notification_prefs() -> dict:
    return {
        "deadlines": True,
        "overdue": True,
        "approvals": True,
        "automation_failures": True,
        "daily_summary": True,
        "deadline_lead_hours": 24,
    }


class UserPreference(IdMixin, TimestampMixin, Base):
    __tablename__ = "user_preferences"
    __table_args__ = (CheckConstraint("integration_mode in ('demo','live')", name="integration_mode"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    theme: Mapped[str] = mapped_column(String(16), default="system")
    integration_mode: Mapped[str] = mapped_column(String(8), default="demo")
    communication_tone: Mapped[str] = mapped_column(String(32), default="professional")
    employee_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    memory_auto_create: Mapped[bool] = mapped_column(Boolean, default=True)
    work_start_hour: Mapped[int] = mapped_column(Integer, default=9)
    work_end_hour: Mapped[int] = mapped_column(Integer, default=18)
    notification_prefs: Mapped[dict] = mapped_column(JSONType, default=default_notification_prefs)

    user: Mapped[User] = relationship(back_populates="preferences")


class OAuthAccount(IdMixin, TimestampMixin, Base):
    """Third-party credentials. Token columns only ever hold Fernet ciphertext."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_oauth_accounts_user_provider"),
        CheckConstraint("status in ('connected','needs_reauth')", name="status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    account_email: Mapped[str | None] = mapped_column(String(320))
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    scopes: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(16), default="connected")


class OAuthState(IdMixin, Base):
    """Single-use OAuth state, tied to the initiating user. Holds the PKCE verifier encrypted."""

    __tablename__ = "oauth_states"
    __table_args__ = (Index("ix_oauth_states_state_hash", "state_hash", unique=True),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))
    state_hash: Mapped[str] = mapped_column(String(64))
    code_verifier_enc: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
