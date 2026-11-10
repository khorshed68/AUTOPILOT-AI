"""User-scoped long-term memory. Memory informs the AI; it never authorises an action."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.safety import contains_secret
from app.core.errors import NotFound, ValidationFailed
from app.database.base import utcnow
from app.models import Memory, UserPreference
from app.services.activity import log_activity
from app.utils.text import score_documents

MEMORY_KINDS = ("preference", "project", "tone", "context", "other")


def create(db: Session, user_id: uuid.UUID, content: str, kind: str = "other", source: str = "user") -> Memory:
    text = (content or "").strip()
    if not text or len(text) > 1000:
        raise ValidationFailed("A memory must be 1 to 1000 characters.")
    if contains_secret(text):
        raise ValidationFailed("That looks like a password, key or card number. Sensitive values can't be stored in memory.", code="sensitive_memory")
    if kind not in MEMORY_KINDS:
        kind = "other"
    existing = db.scalar(select(Memory).where(Memory.user_id == user_id, Memory.content == text))
    if existing is not None:
        return existing
    memory = Memory(user_id=user_id, content=text, kind=kind, source=source)
    db.add(memory)
    db.flush()
    log_activity(db, user_id, "memory_saved", f"Saved memory: {text[:120]}", actor="manager" if source == "ai" else "user", entity_type="memory", entity_id=memory.id)
    return memory


def get_owned(db: Session, user_id: uuid.UUID, memory_id: uuid.UUID) -> Memory:
    memory = db.scalar(select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
    if memory is None:
        raise NotFound("Memory not found.")
    return memory


def relevant(db: Session, user_id: uuid.UUID, text: str, limit: int = 8) -> list[Memory]:
    prefs = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    if prefs is not None and not prefs.memory_enabled:
        return []
    rows = db.scalars(select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at.desc()).limit(300)).all()
    scores = score_documents(text, [(str(m.id), m.content) for m in rows])
    standing = [m for m in rows if m.kind in ("preference", "tone")][:4]
    matched = sorted((m for m in rows if str(m.id) in scores), key=lambda m: -scores[str(m.id)])
    chosen = list({m.id: m for m in [*standing, *matched]}.values())[:limit]
    now = utcnow()
    for memory in chosen:
        memory.last_used_at = now
    return chosen
