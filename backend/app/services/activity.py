"""Audit trail and in-app notifications."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.database.base import utcnow
from app.models import ActivityLog, Notification, UserPreference


def log_activity(
    db: Session, user_id: uuid.UUID, action: str, description: str, *, actor: str = "user", entity_type: str | None = None,
    entity_id: object | None = None, status: str = "success", simulated: bool = False, meta: dict | None = None,
) -> ActivityLog:
    row = ActivityLog(
        user_id=user_id, actor=actor, action=action, description=description, entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None, status=status, simulated=simulated, meta=meta or {}, created_at=utcnow(),
    )
    db.add(row)
    return row


def notify(
    db: Session, user_id: uuid.UUID, kind: str, title: str, body: str = "", *, link: str | None = None, deliver_at: datetime | None = None,
    dedupe_key: str | None = None, preference: str | None = None, is_demo: bool = False,
) -> bool:
    """Create a notification. Returns False when suppressed by preference or an existing dedupe key."""
    if preference:
        prefs = db.scalar(select(UserPreference.notification_prefs).where(UserPreference.user_id == user_id)) or {}
        if prefs.get(preference, True) is False:
            return False
    values = dict(
        id=uuid.uuid4(), user_id=user_id, kind=kind, title=title[:255], body=body, link=link, deliver_at=deliver_at or utcnow(),
        dedupe_key=dedupe_key, is_demo=is_demo, created_at=utcnow(), updated_at=utcnow(),
    )
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(Notification).values(**values).on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"]).returning(Notification.id)
    return db.execute(statement).first() is not None
