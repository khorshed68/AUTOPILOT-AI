"""Background jobs. Each one opens its own session and is safe to run more than once."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import timedelta

import redis
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from app.agents.orchestrator import recover_stuck_runs
from app.core.config import settings
from app.database.base import utcnow
from app.database.session import SessionLocal
from app.models import AutomationExecution, Task, UserPreference
from app.services import approvals
from app.services.activity import notify
from app.utils.dates import get_zone
from app.workers.celery_app import celery
from app.workflows import engine

logger = logging.getLogger("autopilot.worker")
HEARTBEAT_KEY = "autopilot:worker:heartbeat"
OPEN = ("pending", "in_progress", "waiting")


@celery.task(name="app.workers.tasks.run_automation", bind=True)
def run_automation(self, execution_id: str) -> None:
    with SessionLocal() as db:
        try:
            engine.run_execution(db, uuid.UUID(execution_id))
        except SoftTimeLimitExceeded:
            db.rollback()
            execution = db.get(AutomationExecution, uuid.UUID(execution_id))
            engine._finalize(db, execution, "failed", "The run exceeded its time limit.")


@celery.task(name="app.workers.tasks.tick")
def tick() -> dict:
    write_heartbeat()
    with SessionLocal() as db:
        scheduled = engine.dispatch_due(db)
        emails = engine.dispatch_new_emails(db)
    return {"scheduled": scheduled, "emails": emails}


def write_heartbeat() -> None:
    try:
        redis.Redis.from_url(settings.redis_url, socket_timeout=2).set(HEARTBEAT_KEY, str(time.time()), ex=180)
    except redis.RedisError:
        logger.warning("heartbeat_failed")


def scan_deadlines_for(db) -> int:
    now = utcnow()
    created = 0
    prefs_by_user = {p.user_id: p for p in db.scalars(select(UserPreference)).all()}
    tasks = db.scalars(select(Task).where(Task.status.in_(OPEN), Task.due_at.is_not(None), Task.due_at <= now + timedelta(days=7))).all()
    for task in tasks:
        prefs = prefs_by_user.get(task.user_id)
        if prefs is None:
            continue
        zone = get_zone(prefs.timezone)
        if task.due_at < now:
            # One overdue reminder per task per local day.
            key = f"overdue:{task.id}:{now.astimezone(zone).date()}"
            created += notify(db, task.user_id, "overdue", f"Overdue: {task.title}", f"This was due {task.due_at.astimezone(zone):%a %d %b, %H:%M}.", link="/tasks?overdue=true", dedupe_key=key, preference="overdue", is_demo=task.is_demo)
        elif task.due_at <= now + timedelta(hours=int(prefs.notification_prefs.get("deadline_lead_hours", 24))):
            key = f"deadline:{task.id}:{task.due_at.isoformat()}"
            created += notify(db, task.user_id, "deadline", f"Due soon: {task.title}", f"Due {task.due_at.astimezone(zone):%a %d %b, %H:%M}.", link="/tasks", dedupe_key=key, preference="deadlines", is_demo=task.is_demo)
    db.commit()
    return created


@celery.task(name="app.workers.tasks.scan_deadlines")
def scan_deadlines() -> int:
    with SessionLocal() as db:
        return scan_deadlines_for(db)


@celery.task(name="app.workers.tasks.recover")
def recover() -> dict:
    with SessionLocal() as db:
        result = {"runs": recover_stuck_runs(db), "executions": engine.recover_stuck_executions(db), "approvals": approvals.recover_stuck(db), "expired": approvals.expire_pending(db)}
        db.commit()
    return result
