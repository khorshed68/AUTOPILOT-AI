"""AI employee status, derived only from backend state."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.base import utcnow
from app.models import AgentRun, ApprovalRequest, AutomationExecution, UserPreference


def get_status(db: Session, user_id: uuid.UUID) -> dict:
    prefs = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    now = utcnow()
    active = db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.user_id == user_id, AgentRun.status == "running", AgentRun.started_at > now - timedelta(minutes=15))) or 0
    active += db.scalar(select(func.count()).select_from(AutomationExecution).where(AutomationExecution.user_id == user_id, AutomationExecution.status == "running")) or 0
    pending = db.scalar(select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.user_id == user_id, ApprovalRequest.status == "pending", ApprovalRequest.expires_at > now)) or 0
    last_run = db.scalar(select(AgentRun).where(AgentRun.user_id == user_id, AgentRun.status != "running").order_by(AgentRun.started_at.desc()).limit(1))
    recent_error = last_run is not None and last_run.status == "failed" and last_run.finished_at is not None and last_run.finished_at > now - timedelta(minutes=30)

    if prefs is not None and prefs.employee_paused:
        state, detail = "paused", "Paused by you. Chat requests and automations will not run."
    elif active:
        state, detail = "working", f"{active} job{'' if active == 1 else 's'} in progress."
    elif recent_error:
        state, detail = "error", last_run.error or "The last request failed."
    elif pending:
        state, detail = "waiting_for_approval", f"{pending} action{' is' if pending == 1 else 's are'} waiting for your approval."
    else:
        state, detail = "online", "Ready for work."
    return {"state": state, "detail": detail, "active_jobs": active, "pending_approvals": pending}
