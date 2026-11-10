"""Workflow engine: validation, condition evaluation, scheduling and step execution."""
from __future__ import annotations

import json
import logging
import re
import secrets
import time as monotonic_time
import uuid
from datetime import datetime, time, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.llm.base import LLMError, structured_call
from app.agents.llm.factory import get_llm
from app.agents.safety import SYSTEM_POLICY, wrap_untrusted
from app.agents.tools import CreateTask as CreateTaskArgs
from app.agents.tools import ToolContext, build_snapshot, create_task
from app.core.errors import AppError, ValidationFailed
from app.database.base import utcnow
from app.integrations.factory import is_demo
from app.models import Automation, AutomationExecution, Email, Task, UserPreference
from app.services import emails as email_service
from app.services.activity import log_activity, notify
from app.utils.dates import extract_deadlines, get_zone
from app.workflows.catalog import ACTION_TYPES, BOOL_OPERATORS, CONDITION_FIELD_KIND, TEXT_OPERATORS

logger = logging.getLogger("autopilot.workflows")
OPEN = ("pending", "in_progress", "waiting")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class StepFailed(Exception):
    pass


class StepSkipped(Exception):
    pass


def validate_definition(trigger_type: str, trigger_config: dict, conditions: list[dict], steps: list[dict]) -> None:
    if trigger_type in ("daily", "weekly") and not _TIME.match(str(trigger_config.get("time", ""))):
        raise ValidationFailed("Choose a time in 24-hour HH:MM format.")
    if trigger_type == "weekly" and trigger_config.get("weekday") not in range(7):
        raise ValidationFailed("Choose a day of the week.")
    if conditions and trigger_type != "new_email":
        raise ValidationFailed("Conditions are only available for email-triggered workflows.")
    for condition in conditions:
        kind = CONDITION_FIELD_KIND.get(condition.get("field"))
        if kind is None:
            raise ValidationFailed(f"Unknown condition field: {condition.get('field')}")
        operators = BOOL_OPERATORS if kind == "boolean" else TEXT_OPERATORS
        if condition.get("op") not in operators:
            raise ValidationFailed(f"Invalid operator for {condition['field']}.")
        if kind == "text" and not str(condition.get("value") or "").strip():
            raise ValidationFailed("Text conditions need a value.")
    if not steps:
        raise ValidationFailed("A workflow needs at least one action.")
    for step in steps:
        action = ACTION_TYPES.get(step.get("action_type"))
        if action is None:
            raise ValidationFailed(f"Unknown action: {step.get('action_type')}")
        if action["requires"] == "email" and trigger_type != "new_email":
            raise ValidationFailed(f"\"{action['label']}\" needs a \"New email received\" trigger.")


def compute_next_run(automation: Automation, timezone: str, after: datetime | None = None) -> datetime | None:
    if automation.trigger_type not in ("daily", "weekly") or not automation.enabled:
        return None
    zone = get_zone(timezone)
    now = (after or utcnow()).astimezone(zone)
    hour, minute = map(int, automation.trigger_config["time"].split(":"))
    for offset in range(0, 9):
        day = now.date() + timedelta(days=offset)
        if automation.trigger_type == "weekly" and day.weekday() != automation.trigger_config["weekday"]:
            continue
        candidate = datetime.combine(day, time(hour, minute), zone)
        if candidate > now:
            return candidate.astimezone(utcnow().tzinfo)
    return None


def build_email_context(email: Email, timezone: str) -> dict:
    text = f"{email.subject}\n{email.body}"
    today = utcnow().astimezone(get_zone(timezone)).date()
    return {
        "email": {
            "id": str(email.id), "subject": email.subject, "body": email.body, "sender": f"{email.sender_name or ''} <{email.sender_email}>",
            "has_deadline": bool(extract_deadlines(text, today)), "is_urgent": bool(re.search(r"\b(urgent|asap|immediately|critical)\b", text, re.I)),
        }
    }


def evaluate_conditions(conditions: list[dict], logic: str, context: dict) -> bool:
    if not conditions:
        return True
    outcomes = []
    for condition in conditions:
        _, _, name = condition["field"].partition(".")
        actual = context.get("email", {}).get(name)
        op = condition["op"]
        if op == "is_true":
            outcomes.append(actual is True)
        elif op == "is_false":
            outcomes.append(actual is False)
        else:
            haystack, needle = str(actual or "").lower(), str(condition.get("value", "")).lower()
            outcomes.append({"contains": needle in haystack, "not_contains": needle not in haystack, "equals": haystack == needle}[op])
    return all(outcomes) if logic == "all" else any(outcomes)


def create_execution(db: Session, automation: Automation, trigger: str, context: dict | None = None, *, dedupe_key: str | None = None, is_test: bool = False) -> AutomationExecution | None:
    """Returns None when an execution with this dedupe key already exists."""
    execution = AutomationExecution(automation_id=automation.id, user_id=automation.user_id, trigger=trigger, context=context or {}, dedupe_key=dedupe_key, is_test=is_test, is_demo=is_demo(db, automation.user_id))
    db.add(execution)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return execution


def dispatch(db: Session, execution: AutomationExecution) -> None:
    from app.workers.tasks import run_automation

    try:
        run_automation.apply_async(args=[str(execution.id)], retry=False)
    except Exception as exc:  # noqa: BLE001 - any broker error means the job is not queued
        logger.error("queue_unavailable", extra={"error": exc.__class__.__name__})
        fresh = db.get(AutomationExecution, execution.id)
        if fresh.status == "queued":
            _finalize(db, fresh, "failed", "The background queue is unavailable, so this run did not start.")


def run_execution(db: Session, execution_id: uuid.UUID) -> AutomationExecution | None:
    claimed = db.execute(
        update(AutomationExecution).where(AutomationExecution.id == execution_id, AutomationExecution.status == "queued").values(status="running", started_at=utcnow())
    ).rowcount
    db.commit()
    execution = db.get(AutomationExecution, execution_id)
    if not claimed:
        return execution  # already handled by another delivery of this job
    automation = db.get(Automation, execution.automation_id)
    prefs = db.scalar(select(UserPreference).where(UserPreference.user_id == execution.user_id))
    if prefs.employee_paused and not execution.is_test:
        return _finalize(db, execution, "skipped", "Your AI employee was paused.")
    if automation.trigger_type == "new_email" and not evaluate_conditions(automation.conditions, automation.condition_logic, execution.context):
        return _finalize(db, execution, "skipped", "The conditions were not met.")

    llm = get_llm()
    state = {**execution.context, "outputs": {}}
    results: list[dict] = []
    error: str | None = None
    for step in automation.steps:
        began = monotonic_time.monotonic()
        entry = {"position": step.position, "action_type": step.action_type, "label": ACTION_TYPES[step.action_type]["label"]}
        try:
            output = _run_action(db, execution, automation, step.action_type, step.config, state, prefs, llm)
            db.commit()
            state["outputs"][step.action_type] = output
            entry.update(status="succeeded", output=output)
        except StepSkipped as exc:
            db.rollback()
            entry.update(status="skipped", output={"reason": str(exc)})
        except (StepFailed, AppError, LLMError) as exc:
            db.rollback()
            error = getattr(exc, "message", None) or getattr(exc, "user_message", None) or str(exc)
            entry.update(status="failed", error=error)
        except Exception:
            db.rollback()
            logger.exception("workflow_step_crashed", extra={"step": step.action_type})
            error = f"{entry['label']} failed due to an internal error."
            entry.update(status="failed", error=error)
        entry["duration_ms"] = int((monotonic_time.monotonic() - began) * 1000)
        results.append(entry)
        if error:
            break
    execution = db.get(AutomationExecution, execution_id)
    execution.step_results = results
    return _finalize(db, execution, "failed" if error else "succeeded", error)


def _finalize(db: Session, execution: AutomationExecution, status: str, error: str | None) -> AutomationExecution:
    automation = db.get(Automation, execution.automation_id)
    now = utcnow()
    execution.status, execution.error, execution.finished_at = status, error, now
    if execution.started_at:
        execution.duration_ms = int((now - execution.started_at).total_seconds() * 1000)
    automation.last_run_at = now
    if status != "skipped":
        log_activity(db, execution.user_id, "automation_completed" if status == "succeeded" else "automation_failed", f"{automation.name}: {'completed' if status == 'succeeded' else f'failed — {error}'}", actor="automation", entity_type="automation", entity_id=automation.id, status="success" if status == "succeeded" else "failure", simulated=execution.is_demo, meta={"execution_id": str(execution.id), "test": execution.is_test})
    if status == "failed":
        notify(db, execution.user_id, "automation_failed", f"Automation failed: {automation.name}", error or "", link="/automations", dedupe_key=f"automation-failed:{execution.id}", preference="automation_failures", is_demo=execution.is_demo)
    db.commit()
    return execution


def _email(db: Session, execution: AutomationExecution, state: dict) -> Email:
    email_id = state.get("email", {}).get("id")
    if not email_id:
        raise StepFailed("This step needs an email, but the run has none.")
    return email_service.get_owned(db, execution.user_id, uuid.UUID(email_id))


def _render(template: str, state: dict) -> str:
    def lookup(match: re.Match) -> str:
        section, _, key = match[1].partition(".")
        value = (state.get("email", {}) if section == "email" else state.get("outputs", {}).get(section, {})).get(key)
        return str(value)[:300] if value is not None else ""

    return re.sub(r"\{([a-z_]+\.[a-z_]+)\}", lookup, template)


def _run_action(db: Session, execution: AutomationExecution, automation: Automation, action: str, config: dict, state: dict, prefs: UserPreference, llm) -> dict:
    user_id, tz = execution.user_id, prefs.timezone
    zone = get_zone(tz)
    now = utcnow()
    ctx = ToolContext(db=db, user_id=user_id, timezone=tz, origin="automation", agent=ACTION_TYPES[action]["agent"], tainted=True, simulated_llm=llm.simulated)

    if action == "analyze_email":
        email = email_service.analyze(db, user_id, _email(db, execution, state), actor="email")
        return {"category": email.category, "importance": email.importance, "needs_reply": email.needs_reply, "summary": email.ai_summary}
    if action == "generate_summary":
        email = _email(db, execution, state)
        if email.ai_summary is None:
            email = email_service.analyze(db, user_id, email, actor="email")
        return {"summary": email.ai_summary}
    if action == "extract_deadline":
        email = _email(db, execution, state)
        found = extract_deadlines(f"{email.subject}\n{email.body}", now.astimezone(zone).date())
        if email.deadline_at:
            return {"deadline": email.deadline_at.astimezone(zone).date().isoformat(), "text": email.deadline_text}
        if not found:
            return {"deadline": None, "text": None}
        return {"deadline": found[0]["date"], "text": found[0]["text"]}
    if action == "propose_task":
        email = _email(db, execution, state)
        deadline = (state["outputs"].get("extract_deadline") or {}).get("deadline")
        result = create_task(ctx, CreateTaskArgs(title=_render(config.get("title") or f"Follow up: {email.subject[:200]}", state), description=f"From {email.sender_name or email.sender_email}.", priority="high" if email.importance == "high" else "medium", due_date=deadline, related_email_id=email.id))
        return {"approval_id": result.get("approval_id"), "status": result["status"]}
    if action == "prepare_reply_draft":
        email = _email(db, execution, state)
        if not email.needs_reply:
            raise StepSkipped("This email does not need a reply.")
        draft, _ = email_service.draft_reply_text(db, user_id, email, config.get("instructions"))
        approval = email_service.propose_reply(db, user_id, email, draft["subject"], draft["body"], requested_by="email", origin="automation")
        return {"approval_id": str(approval.id), "status": "awaiting_approval"}
    if action == "notify_user":
        title = _render(config.get("title") or f"{automation.name} finished", state)
        body = _render(config.get("body") or "", state) or (state["outputs"].get("generate_summary") or {}).get("summary") or ""
        link = f"/inbox/{state['email']['id']}" if state.get("email") else "/automations"
        delivered = notify(db, user_id, "automation", title or automation.name, body, link=link, dedupe_key=f"exec:{execution.id}:{title[:60]}"[:128], is_demo=execution.is_demo)
        return {"delivered": delivered}
    if action == "get_todays_tasks":
        day_end = now.astimezone(zone).replace(hour=23, minute=59)
        rows = db.scalars(select(Task).where(Task.user_id == user_id, Task.status.in_(OPEN), Task.due_at <= day_end).order_by(Task.due_at)).all()
        return {"count": len(rows), "titles": [t.title for t in rows][:15]}
    if action == "get_completed_activities":
        snap = build_snapshot(db, user_id, tz, "today")
        return {"tasks_completed": snap["tasks_completed"], "titles": snap["completed_titles"], "activity_count": snap["activity_count"]}
    if action == "analyze_pending":
        snap = build_snapshot(db, user_id, tz, "today")
        return {key: snap[key] for key in ("tasks_open", "tasks_overdue", "emails_needing_reply", "pending_approvals")}
    if action == "generate_daily_summary":
        snap = build_snapshot(db, user_id, tz, "today")
        facts = {key: snap[key] for key in ("tasks_completed", "tasks_open", "tasks_overdue", "emails_needing_reply", "pending_approvals", "activity_count")}
        schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
        system = f"Write a short daily work summary using only these numbers.\n\n{SYSTEM_POLICY}"
        result = structured_call(llm, purpose="workflow.summary", system=system, user=wrap_untrusted(json.dumps(facts), secrets.token_hex(8)), schema=schema, max_tokens=2000)
        local_date = now.astimezone(zone).date().isoformat()
        notify(db, user_id, "daily_summary", "Daily work summary", result["summary"], link="/analytics", dedupe_key=f"daily:{automation.id}:{local_date}:{execution.id if execution.is_test else ''}"[:128], preference="daily_summary", is_demo=execution.is_demo)
        return {"summary": result["summary"]}
    raise StepFailed(f"Unsupported action: {action}")


# Scheduler entry points --------------------------------------------------------------------------------------------

def dispatch_due(db: Session) -> int:
    now = utcnow()
    due = db.scalars(select(Automation).where(Automation.enabled.is_(True), Automation.trigger_type.in_(("daily", "weekly")), Automation.next_run_at <= now).limit(200)).all()
    count = 0
    for automation in due:
        slot = automation.next_run_at.isoformat()
        tz = db.scalar(select(UserPreference.timezone).where(UserPreference.user_id == automation.user_id)) or "UTC"
        automation.next_run_at = compute_next_run(automation, tz, now)
        db.commit()
        if execution := create_execution(db, automation, automation.trigger_type, dedupe_key=f"schedule:{slot}"):
            dispatch(db, execution)
            count += 1
    return count


def dispatch_email(db: Session, email: Email) -> int:
    """Start each enabled email workflow that was already on when this email arrived."""
    tz = db.scalar(select(UserPreference.timezone).where(UserPreference.user_id == email.user_id)) or "UTC"
    automations = db.scalars(select(Automation).where(Automation.user_id == email.user_id, Automation.enabled.is_(True), Automation.trigger_type == "new_email")).all()
    context = build_email_context(email, tz)
    started = 0
    for automation in automations:
        if automation.enabled_at and email.received_at < automation.enabled_at:
            continue
        if execution := create_execution(db, automation, "new_email", context, dedupe_key=f"email:{email.id}"):
            dispatch(db, execution)
            started += 1
    email.automation_checked_at = utcnow()
    db.commit()
    return started


def dispatch_new_emails(db: Session) -> int:
    user_ids = db.scalars(select(Automation.user_id).where(Automation.enabled.is_(True), Automation.trigger_type == "new_email").distinct()).all()
    total = 0
    for user_id in user_ids:
        if not is_demo(db, user_id):
            try:
                email_service.sync_page(db, user_id, None, 25)
            except AppError as exc:
                logger.warning("email_poll_failed", extra={"user_id": str(user_id), "reason": exc.code})
                continue
        pending = db.scalars(select(Email).where(Email.user_id == user_id, Email.automation_checked_at.is_(None)).limit(25)).all()
        total += sum(dispatch_email(db, email) for email in pending)
    return total


def recover_stuck_executions(db: Session, older_than_minutes: int = 15) -> int:
    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    rows = db.scalars(select(AutomationExecution).where(AutomationExecution.status.in_(("running", "queued")), AutomationExecution.created_at < cutoff)).all()
    for row in rows:
        _finalize(db, row, "failed", "The run was interrupted or never started, and has been marked as failed.")
    return len(rows)
