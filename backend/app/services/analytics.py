"""Dashboard and analytics, computed from stored records only."""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.base import utcnow
from app.integrations import factory
from app.models import (
    ActivityLog,
    AgentExecution,
    ApprovalRequest,
    Automation,
    AutomationExecution,
    CalendarEvent,
    Email,
    Task,
)
from app.services.emails import user_timezone
from app.services.employee import get_status
from app.utils.dates import get_zone

OPEN = ("pending", "in_progress", "waiting")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _task_scope(demo: bool):
    return True if demo else Task.is_demo.is_(False)


def overview(db: Session, user_id: uuid.UUID) -> dict:
    now = utcnow()
    demo = factory.is_demo(db, user_id)
    tz = user_timezone(db, user_id)
    zone = get_zone(tz)
    day_end = (now.astimezone(zone).replace(hour=23, minute=59, second=59)).astimezone(now.tzinfo)
    scope = (Task.user_id == user_id, _task_scope(demo))

    def task_count(*where) -> int:
        return db.scalar(select(func.count()).select_from(Task).where(*scope, *where)) or 0

    email_base = (Email.user_id == user_id, Email.is_demo.is_(demo))
    processed = db.scalar(select(func.count()).select_from(Email).where(*email_base, Email.analyzed_at.is_not(None))) or 0
    deadlines = task_count(Task.status.in_(OPEN), Task.due_at.is_not(None), Task.due_at >= now, Task.due_at <= now + timedelta(days=7))
    active = db.scalar(select(func.count()).select_from(Automation).where(Automation.user_id == user_id, Automation.enabled.is_(True), Automation.is_demo.is_(demo) if not demo else True)) or 0
    stats = {
        "total_tasks": task_count(), "completed_tasks": task_count(Task.status == "completed"), "pending_tasks": task_count(Task.status.in_(OPEN)),
        "overdue_tasks": task_count(Task.status.in_(OPEN), Task.due_at < now), "emails_processed": processed, "upcoming_deadlines": deadlines, "active_automations": active,
    }
    important = db.scalars(
        select(Email).where(*email_base, Email.is_archived.is_(False), or_(Email.importance == "high", Email.category.in_(("Important", "Urgent")))).order_by(Email.received_at.desc()).limit(5)
    ).all()
    pending_tasks = db.scalars(
        select(Task).where(*scope, Task.status.in_(OPEN)).order_by(Task.due_at.is_(None), Task.due_at.asc()).limit(6)
    ).all()
    events = db.scalars(
        select(CalendarEvent).where(CalendarEvent.user_id == user_id, CalendarEvent.is_demo.is_(demo), CalendarEvent.status == "confirmed", CalendarEvent.end_at >= now).order_by(CalendarEvent.start_at).limit(6)
    ).all()
    activity = db.scalars(select(ActivityLog).where(ActivityLog.user_id == user_id).order_by(ActivityLog.created_at.desc()).limit(8)).all()
    needs_reply = db.scalar(select(func.count()).select_from(Email).where(*email_base, Email.needs_reply.is_(True), Email.is_archived.is_(False))) or 0
    unanalyzed = db.scalar(select(func.count()).select_from(Email).where(*email_base, Email.analyzed_at.is_(None), Email.is_archived.is_(False))) or 0
    pending_approvals = db.scalar(select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.user_id == user_id, ApprovalRequest.status == "pending")) or 0

    recommendations = []
    if stats["overdue_tasks"]:
        recommendations.append({"title": f"Resolve {_plural(stats['overdue_tasks'], 'overdue task')}", "reason": "These are past their due date.", "link": "/tasks?overdue=true", "prompt": "Find my overdue tasks"})
    if pending_approvals:
        recommendations.append({"title": f"Review {_plural(pending_approvals, 'pending approval')}", "reason": "Nothing runs until you decide.", "link": "/approvals", "prompt": None})
    if needs_reply:
        recommendations.append({"title": f"Reply to {_plural(needs_reply, 'email')}", "reason": "Marked as waiting on your response.", "link": "/inbox?view=needs_reply", "prompt": "Find all emails that require a reply"})
    if unanalyzed:
        recommendations.append({"title": f"Triage {_plural(unanalyzed, 'new email')}", "reason": "Not analysed yet.", "link": "/ai", "prompt": "Summarize my important emails"})

    return {
        "stats": stats, "simulated": demo, "employee": get_status(db, user_id), "timezone": tz, "today_end": day_end.isoformat(),
        "important_emails": [{"id": str(e.id), "subject": e.subject, "sender": e.sender_name or e.sender_email, "category": e.category, "summary": e.ai_summary, "received_at": e.received_at.isoformat(), "deadline_at": e.deadline_at.isoformat() if e.deadline_at else None, "is_demo": e.is_demo} for e in important],
        "pending_tasks": [{"id": str(t.id), "title": t.title, "priority": t.priority, "status": t.status, "due_at": t.due_at.isoformat() if t.due_at else None, "overdue": bool(t.due_at and t.due_at < now), "is_demo": t.is_demo} for t in pending_tasks],
        "upcoming_events": [{"id": str(e.id), "title": e.title, "start_at": e.start_at.isoformat(), "end_at": e.end_at.isoformat(), "attendees": e.attendees, "is_demo": e.is_demo} for e in events],
        "recent_activity": [activity_dict(a) for a in activity],
        "recommendations": recommendations,
    }


def activity_dict(a: ActivityLog) -> dict:
    return {"id": str(a.id), "actor": a.actor, "action": a.action, "description": a.description, "entity_type": a.entity_type, "entity_id": a.entity_id, "status": a.status, "simulated": a.simulated, "created_at": a.created_at.isoformat(), "meta": a.meta}


def analytics(db: Session, user_id: uuid.UUID, days: int = 30) -> dict:
    now = utcnow()
    since = now - timedelta(days=days)
    demo = factory.is_demo(db, user_id)
    zone = get_zone(user_timezone(db, user_id))
    day_key = lambda d: d.astimezone(zone).date().isoformat()  # noqa: E731
    span = [(now - timedelta(days=i)).astimezone(zone).date().isoformat() for i in range(days - 1, -1, -1)]

    tasks = db.scalars(select(Task).where(Task.user_id == user_id, _task_scope(demo))).all()
    emails = db.scalars(select(Email).where(Email.user_id == user_id, Email.is_demo.is_(demo), Email.analyzed_at.is_not(None))).all()
    runs = db.scalars(select(AutomationExecution).where(AutomationExecution.user_id == user_id, AutomationExecution.created_at >= since, AutomationExecution.is_test.is_(False), AutomationExecution.status.in_(("succeeded", "failed")))).all()
    agent_rows = db.execute(select(AgentExecution.agent_name, func.count(), func.avg(AgentExecution.duration_ms)).where(AgentExecution.user_id == user_id, AgentExecution.started_at >= since, AgentExecution.status != "skipped").group_by(AgentExecution.agent_name)).all()
    action_rows = db.execute(select(ActivityLog.action, func.count()).where(ActivityLog.user_id == user_id, ActivityLog.created_at >= since, ActivityLog.status == "success").group_by(ActivityLog.action)).all()

    completed = [t for t in tasks if t.status == "completed"]
    pending = [t for t in tasks if t.status in OPEN]
    succeeded = sum(1 for r in runs if r.status == "succeeded")
    failed = len(runs) - succeeded
    durations = [r.duration_ms for r in runs if r.duration_ms is not None]

    completed_by_day: Counter = Counter(day_key(t.completed_at) for t in completed if t.completed_at and t.completed_at >= since)
    created_by_day: Counter = Counter(day_key(t.created_at) for t in tasks if t.created_at >= since)
    run_by_day: dict[str, Counter] = defaultdict(Counter)
    for r in runs:
        run_by_day[day_key(r.created_at)][r.status] += 1

    weekly: Counter = Counter()
    for t in completed:
        if t.completed_at and t.completed_at >= now - timedelta(weeks=8):
            weekly[t.completed_at.astimezone(zone).strftime("%G-W%V")] += 1

    actions = dict(action_rows)
    per_action = settings.time_saved_minutes
    counts = {
        "email_analyzed": actions.get("email_analyzed", 0), "reply_drafted": sum(1 for _ in db.scalars(select(ApprovalRequest.id).where(ApprovalRequest.user_id == user_id, ApprovalRequest.action_type == "send_email", ApprovalRequest.created_at >= since))),
        "task_created": actions.get("ai_task_created", 0), "event_scheduled": actions.get("executed_create_calendar_event", 0), "report_generated": actions.get("report_generated", 0), "automation_run": succeeded,
    }
    minutes = sum(per_action[k] * v for k, v in counts.items())
    return {
        "days": days, "simulated": demo,
        "totals": {
            "emails_processed": len(emails), "tasks_completed": len(completed), "tasks_pending": len(pending),
            "automation_success_rate": round(succeeded / len(runs) * 100, 1) if runs else None, "automation_failure_rate": round(failed / len(runs) * 100, 1) if runs else None,
            "automation_runs": len(runs), "avg_execution_ms": round(sum(durations) / len(durations)) if durations else None,
        },
        "time_saved": {"minutes_estimate": minutes, "is_estimate": True, "assumed_minutes_per_action": per_action, "counts": counts, "explanation": "Estimate only: the number of each action in this period multiplied by an assumed manual effort per action. Not a measurement."},
        "agents": [{"agent": a, "runs": c, "avg_ms": round(m or 0)} for a, c, m in sorted(agent_rows, key=lambda r: -r[1])],
        "task_trend": [{"date": d, "created": created_by_day.get(d, 0), "completed": completed_by_day.get(d, 0)} for d in span],
        "weekly_productivity": [{"week": w, "completed": weekly[w]} for w in sorted(weekly)],
        "email_categories": [{"category": c or "Uncategorized", "count": n} for c, n in Counter(e.category for e in emails).most_common()],
        "automation_history": [{"date": d, "succeeded": run_by_day[d]["succeeded"], "failed": run_by_day[d]["failed"]} for d in span],
    }
