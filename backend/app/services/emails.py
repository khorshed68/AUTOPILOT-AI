"""Inbox sync, querying, AI analysis and reply drafting."""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.agents.llm.base import structured_call
from app.agents.llm.factory import get_llm
from app.agents.safety import SYSTEM_POLICY, detect_injection, wrap_untrusted
from app.core.errors import NotFound, ValidationFailed
from app.database.base import utcnow
from app.integrations import factory
from app.models import Email, UserPreference
from app.models.work import EMAIL_CATEGORIES
from app.services.activity import log_activity
from app.services.approvals import propose
from app.utils.dates import get_zone, local_end_of_day

MAX_BODY_FOR_LLM = 20_000
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(EMAIL_CATEGORIES)},
        "importance": {"type": "string", "enum": ["low", "normal", "high"]},
        "summary": {"type": "string"},
        "needs_reply": {"type": "boolean"},
        "suggested_action": {"type": "string"},
        "action_items": {"type": "array", "items": {"type": "string"}},
        "deadline_date": {"type": ["string", "null"], "description": "YYYY-MM-DD only if the email states a deadline"},
        "deadline_text": {"type": ["string", "null"], "description": "The exact words stating the deadline"},
    },
    "required": ["category", "importance", "summary", "needs_reply", "suggested_action", "action_items"],
}


def user_timezone(db: Session, user_id: uuid.UUID) -> str:
    return db.scalar(select(UserPreference.timezone).where(UserPreference.user_id == user_id)) or "UTC"


def apply_filters(stmt, *, view: str = "inbox", category: str | None = None, q: str | None = None):
    if view == "archived":
        stmt = stmt.where(Email.is_archived.is_(True))
    else:
        stmt = stmt.where(Email.is_archived.is_(False))
    if view == "important":
        stmt = stmt.where(or_(Email.importance == "high", Email.category.in_(("Important", "Urgent"))))
    elif view == "needs_reply":
        stmt = stmt.where(Email.needs_reply.is_(True))
    if category:
        stmt = stmt.where(Email.category == category)
    if q:
        like = f"%{q.lower().replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(func.lower(Email.subject).like(like), func.lower(Email.sender_email).like(like), func.lower(Email.sender_name).like(like), func.lower(Email.body).like(like)))
    return stmt


def base_query(db: Session, user_id: uuid.UUID):
    return select(Email).where(Email.user_id == user_id, Email.is_demo.is_(factory.is_demo(db, user_id)))


def get_owned(db: Session, user_id: uuid.UUID, email_id: uuid.UUID) -> Email:
    email = db.scalar(select(Email).where(Email.id == email_id, Email.user_id == user_id))
    if email is None:
        raise NotFound("Email not found.")
    return email


def sync_page(db: Session, user_id: uuid.UUID, page_token: str | None = None, max_results: int = 25) -> dict:
    """Fetch one page from the provider. Never walks the whole mailbox."""
    provider = factory.email_provider(db, user_id)
    page = provider.fetch_page(page_token, min(max_results, 50))
    created = 0
    if not provider.simulated:
        known = set(db.scalars(select(Email.external_id).where(Email.user_id == user_id, Email.is_demo.is_(False), Email.external_id.in_([e.external_id for e in page.emails]))).all())
        for remote in page.emails:
            if remote.external_id in known:
                continue
            db.add(Email(
                user_id=user_id, external_id=remote.external_id, thread_id=remote.thread_id, rfc822_message_id=remote.message_id, sender_name=remote.sender_name, sender_email=remote.sender_email,
                recipients=remote.recipients, subject=remote.subject[:998], snippet=remote.snippet, body=remote.body, received_at=remote.received_at, labels=remote.labels,
                is_read="UNREAD" not in remote.labels, injection_flags=detect_injection(f"{remote.subject}\n{remote.body}"),
            ))
            created += 1
        if created:
            log_activity(db, user_id, "emails_synced", f"Synced {created} new emails from Gmail", actor="system", meta={"count": created})
        db.commit()
    return {"fetched": len(page.emails), "created": created, "next_page_token": page.next_page_token, "simulated": provider.simulated}


def email_for_llm(email: Email) -> dict:
    return {
        "id": str(email.id), "sender_name": email.sender_name, "sender_email": email.sender_email, "subject": email.subject,
        "received_at": email.received_at.isoformat(), "snippet": email.snippet, "body": email.body[:MAX_BODY_FOR_LLM],
    }


def store_analysis(db: Session, email: Email, analysis: dict, timezone: str, *, actor: str, simulated: bool) -> Email:
    email.category = analysis["category"] if analysis.get("category") in EMAIL_CATEGORIES else email.category
    email.importance = analysis["importance"] if analysis.get("importance") in ("low", "normal", "high") else "normal"
    email.ai_summary = analysis.get("summary")
    email.suggested_action = analysis.get("suggested_action")
    email.action_items = analysis.get("action_items") or []
    email.needs_reply = bool(analysis.get("needs_reply"))
    email.deadline_text = (analysis.get("deadline_text") or None)
    email.deadline_at = None
    if analysis.get("deadline_date"):
        try:
            email.deadline_at = local_end_of_day(date.fromisoformat(analysis["deadline_date"]), get_zone(timezone))
        except ValueError:
            email.deadline_text = None
    email.analyzed_at = utcnow()
    log_activity(db, email.user_id, "email_analyzed", f"Categorised \"{email.subject[:120]}\" as {email.category}", actor=actor, entity_type="email", entity_id=email.id, simulated=email.is_demo or simulated, meta={"category": email.category})
    return email


def analyze(db: Session, user_id: uuid.UUID, email: Email, *, actor: str = "email") -> Email:
    llm = get_llm()
    system = "Analyse the email for the mailbox owner. Report only what the email states; leave deadline fields null unless a deadline is explicit.\n\n" + SYSTEM_POLICY
    tz = user_timezone(db, user_id)
    now = utcnow().astimezone(get_zone(tz))
    user = f"Current local date: {now.date()} ({tz}).\n{wrap_untrusted(email_for_llm(email), secrets.token_hex(8))}"
    result = structured_call(llm, purpose="email.analyze", system=system, user=user, schema=ANALYSIS_SCHEMA, max_tokens=4000)
    store_analysis(db, email, result, tz, actor=actor, simulated=llm.simulated)
    db.commit()
    return email


def draft_reply_text(db: Session, user_id: uuid.UUID, email: Email, instructions: str | None) -> tuple[dict, bool]:
    tone = db.scalar(select(UserPreference.communication_tone).where(UserPreference.user_id == user_id)) or "professional"
    llm = get_llm()
    schema = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["subject", "body"]}
    system = f"Draft a reply for the mailbox owner in a {tone} tone. Never commit to anything the owner has not stated. Output only the reply.\n\n{SYSTEM_POLICY}"
    task = json.dumps({"tone": tone, "instructions": instructions})
    user = f"<task>{task}</task>\n{wrap_untrusted(email_for_llm(email), secrets.token_hex(8))}"
    return structured_call(llm, purpose="email.draft_reply", system=system, user=user, schema=schema, max_tokens=4000), llm.simulated


def propose_reply(
    db: Session, user_id: uuid.UUID, email: Email, subject: str, body: str, *, requested_by: str = "user", origin: str = "inbox",
    conversation_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None, tainted: bool = True,
):
    if "@" not in email.sender_email or email.sender_email.endswith("unknown"):
        raise ValidationFailed("This email has no address to reply to.")
    payload = {"to": [email.sender_email], "cc": [], "subject": subject, "body": body, "email_id": str(email.id)}
    return propose(
        db, user_id, "send_email", payload, title=f"Reply to {email.sender_name or email.sender_email}", description=f"Re: {email.subject[:200]}",
        requested_by=requested_by, origin=origin, conversation_id=conversation_id, run_id=run_id, derived_from_untrusted=tainted,
    )


def to_timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
