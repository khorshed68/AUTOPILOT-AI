"""Task rules shared by the REST API and agents."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.database.base import utcnow
from app.models import Task


def get_owned(db: Session, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    if task is None:
        raise NotFound("Task not found.")
    return task


def apply_status(task: Task, status: str) -> None:
    if status == "completed" and task.status != "completed":
        task.completed_at = utcnow()
        task.progress = 100
    elif status != "completed":
        task.completed_at = None
    task.status = status
