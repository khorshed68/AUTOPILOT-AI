from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, Paging
from app.core.errors import NotFound, ValidationFailed
from app.database.base import utcnow
from app.integrations.factory import is_demo
from app.models import Automation, AutomationExecution, AutomationStep, Email
from app.schemas import api as s
from app.services.activity import log_activity
from app.services.emails import user_timezone
from app.workflows import catalog, engine

router = APIRouter(prefix="/automations", tags=["automations"])


def _owned(db, user_id: uuid.UUID, automation_id: uuid.UUID) -> Automation:
    row = db.scalar(select(Automation).where(Automation.id == automation_id, Automation.user_id == user_id))
    if row is None:
        raise NotFound("Automation not found.")
    return row


def _set_steps(automation: Automation, steps: list[s.StepIn]) -> None:
    automation.steps[:] = [AutomationStep(position=i, action_type=st.action_type, config=st.config) for i, st in enumerate(steps)]


def _validate(body: s.AutomationIn) -> None:
    engine.validate_definition(body.trigger_type, body.trigger_config, body.conditions, [st.model_dump() for st in body.steps])


@router.get("/catalog")
def get_catalog(user: CurrentUser):
    return {"triggers": catalog.TRIGGERS, "condition_fields": catalog.CONDITION_FIELDS, "text_operators": catalog.TEXT_OPERATORS, "boolean_operators": catalog.BOOL_OPERATORS, "actions": catalog.ACTIONS}


@router.get("", response_model=list[s.AutomationOut])
def list_automations(db: DB, user: CurrentUser):
    stmt = select(Automation).where(Automation.user_id == user.id).order_by(Automation.created_at.desc())
    if not is_demo(db, user.id):
        stmt = stmt.where(Automation.is_demo.is_(False))
    return db.scalars(stmt).all()


@router.post("", response_model=s.AutomationOut, status_code=201)
def create(body: s.AutomationIn, db: DB, user: CurrentUser):
    _validate(body)
    automation = Automation(user_id=user.id, enabled=False, is_demo=False, **body.model_dump(exclude={"steps"}))
    _set_steps(automation, body.steps)
    db.add(automation)
    db.flush()
    log_activity(db, user.id, "automation_created", f"Created automation: {automation.name}", entity_type="automation", entity_id=automation.id, status="info")
    db.commit()
    return automation


@router.get("/{automation_id}", response_model=s.AutomationOut)
def get_one(automation_id: uuid.UUID, db: DB, user: CurrentUser):
    return _owned(db, user.id, automation_id)


@router.put("/{automation_id}", response_model=s.AutomationOut)
def update(automation_id: uuid.UUID, body: s.AutomationIn, db: DB, user: CurrentUser):
    automation = _owned(db, user.id, automation_id)
    _validate(body)
    for key, value in body.model_dump(exclude={"steps"}).items():
        setattr(automation, key, value)
    # Flush removals first so new rows can reuse positions without violating the unique constraint.
    automation.steps[:] = []
    db.flush()
    _set_steps(automation, body.steps)
    automation.next_run_at = engine.compute_next_run(automation, user_timezone(db, user.id))
    log_activity(db, user.id, "automation_updated", f"Updated automation: {automation.name}", entity_type="automation", entity_id=automation.id, status="info")
    db.commit()
    db.refresh(automation)
    return automation


@router.delete("/{automation_id}", status_code=204)
def delete(automation_id: uuid.UUID, db: DB, user: CurrentUser):
    automation = _owned(db, user.id, automation_id)
    log_activity(db, user.id, "automation_deleted", f"Deleted automation: {automation.name}", entity_type="automation", entity_id=automation.id, status="info")
    db.delete(automation)
    db.commit()


@router.post("/{automation_id}/toggle", response_model=s.AutomationOut)
def toggle(automation_id: uuid.UUID, body: s.ToggleIn, db: DB, user: CurrentUser):
    automation = _owned(db, user.id, automation_id)
    automation.enabled = body.enabled
    automation.enabled_at = utcnow() if body.enabled else None
    automation.next_run_at = engine.compute_next_run(automation, user_timezone(db, user.id))
    log_activity(db, user.id, "automation_enabled" if body.enabled else "automation_disabled", f"{'Enabled' if body.enabled else 'Disabled'} automation: {automation.name}", entity_type="automation", entity_id=automation.id, status="info")
    db.commit()
    return automation


@router.post("/{automation_id}/run", response_model=s.ExecutionOut, status_code=202)
def run(automation_id: uuid.UUID, body: s.RunIn, db: DB, user: CurrentUser):
    automation = _owned(db, user.id, automation_id)
    context: dict = {}
    if automation.trigger_type == "new_email":
        demo = is_demo(db, user.id)
        latest = db.scalar(select(Email).where(Email.user_id == user.id, Email.is_demo.is_(demo)).order_by(Email.received_at.desc()).limit(1))
        if latest is None:
            raise ValidationFailed("There are no emails to test this workflow with.")
        context = engine.build_email_context(latest, user_timezone(db, user.id))
    execution = engine.create_execution(db, automation, "test" if body.test else "manual", context, is_test=body.test)
    engine.dispatch(db, execution)
    db.refresh(execution)
    return execution


@router.get("/{automation_id}/executions", response_model=s.Page[s.ExecutionOut])
def executions(automation_id: uuid.UUID, db: DB, user: CurrentUser, paging: Paging):
    automation = _owned(db, user.id, automation_id)
    return paging.apply(db, select(AutomationExecution).where(AutomationExecution.automation_id == automation.id).order_by(AutomationExecution.created_at.desc()))
