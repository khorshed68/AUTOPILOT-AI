"""Notifications, activity, dashboard, analytics, settings, memory, integrations, demo and system routes."""
from __future__ import annotations

import secrets
import time
import uuid
from datetime import timedelta
from urllib.parse import urlencode

import redis
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.agents.llm.base import LLMError
from app.agents.llm.factory import get_llm
from app.api.deps import DB, CurrentUser, Paging
from app.core.config import settings
from app.core.errors import ExternalServiceError, IntegrationNotConnected, NotFound, ValidationFailed
from app.core.security import decrypt_secret, encrypt_secret, pkce_pair, sha256_hex
from app.database.base import utcnow
from app.integrations import factory, github, google
from app.models import (
    ActivityLog,
    Agent,
    CalendarEvent,
    Conversation,
    Document,
    Email,
    Memory,
    Notification,
    OAuthAccount,
    OAuthState,
    Task,
    UserPreference,
)
from app.schemas import api as s
from app.services import analytics as analytics_service
from app.services import documents as document_service
from app.services import employee
from app.services import memory as memory_service
from app.services.activity import log_activity
from app.services.seed import clear_demo_data, seed_demo_data
from app.utils.dates import is_valid_timezone
from app.workers.tasks import HEARTBEAT_KEY
from app.workflows import engine

notifications = APIRouter(prefix="/notifications", tags=["notifications"])
activity = APIRouter(prefix="/activity", tags=["activity"])
insights = APIRouter(tags=["insights"])
config_router = APIRouter(tags=["settings"])
integrations = APIRouter(prefix="/integrations", tags=["integrations"])
demo = APIRouter(prefix="/demo", tags=["demo"])
system = APIRouter(tags=["system"])


# Notifications -----------------------------------------------------------------------------------------------------

@notifications.get("", response_model=s.Page[s.NotificationOut])
def list_notifications(db: DB, user: CurrentUser, paging: Paging, unread: bool = False):
    stmt = select(Notification).where(Notification.user_id == user.id, Notification.deliver_at <= utcnow())
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    return paging.apply(db, stmt.order_by(Notification.deliver_at.desc()))


@notifications.get("/unread-count")
def unread_count(db: DB, user: CurrentUser):
    count = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.deliver_at <= utcnow(), Notification.read_at.is_(None)))
    return {"count": count or 0}


@notifications.put("/{notification_id}/read", response_model=s.NotificationOut)
def mark_read(notification_id: uuid.UUID, db: DB, user: CurrentUser):
    row = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    if row is None:
        raise NotFound("Notification not found.")
    row.read_at = row.read_at or utcnow()
    db.commit()
    return row


@notifications.post("/read-all", status_code=204)
def mark_all_read(db: DB, user: CurrentUser):
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None), Notification.deliver_at <= utcnow()).values(read_at=utcnow()))
    db.commit()


# Activity ----------------------------------------------------------------------------------------------------------

@activity.get("", response_model=s.Page[s.ActivityOut])
def list_activity(db: DB, user: CurrentUser, paging: Paging, status: str | None = None, actor: str | None = None, q: str | None = Query(None, max_length=200)):
    stmt = select(ActivityLog).where(ActivityLog.user_id == user.id)
    if status:
        stmt = stmt.where(ActivityLog.status == status)
    if actor:
        stmt = stmt.where(ActivityLog.actor == actor)
    if q:
        stmt = stmt.where(func.lower(ActivityLog.description).like(f"%{q.lower().replace('%', '')}%"))
    return paging.apply(db, stmt.order_by(ActivityLog.created_at.desc()))


# Dashboard, analytics, search --------------------------------------------------------------------------------------

@insights.get("/dashboard")
def dashboard(db: DB, user: CurrentUser):
    return analytics_service.overview(db, user.id)


@insights.get("/analytics")
def analytics(db: DB, user: CurrentUser, days: int = Query(30, ge=7, le=90)):
    return analytics_service.analytics(db, user.id, days)


@insights.get("/search")
def global_search(db: DB, user: CurrentUser, q: str = Query(..., min_length=2, max_length=100)):
    like = f"%{q.lower().replace('%', '').replace('_', '')}%"
    demo_mode = factory.is_demo(db, user.id)
    limit = 5
    tasks = db.scalars(select(Task).where(Task.user_id == user.id, True if demo_mode else Task.is_demo.is_(False), func.lower(Task.title).like(like)).limit(limit)).all()
    emails = db.scalars(select(Email).where(Email.user_id == user.id, Email.is_demo.is_(demo_mode), or_(func.lower(Email.subject).like(like), func.lower(Email.sender_name).like(like))).limit(limit)).all()
    docs = db.scalars(document_service.visible_query(db, user.id).where(func.lower(Document.title).like(like)).limit(limit)).all()
    events = db.scalars(select(CalendarEvent).where(CalendarEvent.user_id == user.id, CalendarEvent.is_demo.is_(demo_mode), func.lower(CalendarEvent.title).like(like)).limit(limit)).all()
    chats = db.scalars(select(Conversation).where(Conversation.user_id == user.id, func.lower(Conversation.title).like(like)).limit(limit)).all()
    return {
        "results": [
            *[{"type": "task", "id": str(t.id), "title": t.title, "subtitle": t.status.replace("_", " "), "link": "/tasks?" + urlencode({"q": t.title[:40]})} for t in tasks],
            *[{"type": "email", "id": str(e.id), "title": e.subject, "subtitle": e.sender_name or e.sender_email, "link": f"/inbox/{e.id}"} for e in emails],
            *[{"type": "document", "id": str(d.id), "title": d.title, "subtitle": d.filename, "link": f"/documents/{d.id}"} for d in docs],
            *[{"type": "event", "id": str(e.id), "title": e.title, "subtitle": e.start_at.date().isoformat(), "link": "/calendar"} for e in events],
            *[{"type": "conversation", "id": str(c.id), "title": c.title, "subtitle": "Conversation", "link": f"/ai/{c.id}"} for c in chats],
        ]
    }


# Settings and memory -----------------------------------------------------------------------------------------------

def _prefs(db, user_id) -> UserPreference:
    return db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))


@config_router.get("/settings/preferences", response_model=s.PreferencesOut)
def get_preferences(db: DB, user: CurrentUser):
    return _prefs(db, user.id)


@config_router.put("/settings/preferences", response_model=s.PreferencesOut)
def update_preferences(body: s.PreferencesIn, db: DB, user: CurrentUser):
    prefs = _prefs(db, user.id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "timezone" in changes and not is_valid_timezone(changes["timezone"]):
        raise ValidationFailed("Unknown timezone.")
    if changes.get("integration_mode") == "live" and factory.get_google_account(db, user.id) is None:
        raise IntegrationNotConnected("Connect your Google account before switching to live mode.")
    if "notification_prefs" in changes:
        changes["notification_prefs"] = {**prefs.notification_prefs, **changes["notification_prefs"]}
    for key, value in changes.items():
        setattr(prefs, key, value)
    if prefs.work_end_hour <= prefs.work_start_hour:
        raise ValidationFailed("The working day must end after it starts.")
    log_activity(db, user.id, "settings_updated", f"Updated settings: {', '.join(changes) or 'none'}", status="info")
    db.commit()
    return prefs


@config_router.get("/agents")
def list_agents(db: DB, user: CurrentUser):
    llm_name, llm_model = settings.llm_provider, settings.llm_model
    return {"provider": llm_name, "model": llm_model, "agents": [{"name": a.name, "display_name": a.display_name, "description": a.description, "allowed_tools": a.allowed_tools, "enabled": a.enabled} for a in db.scalars(select(Agent).order_by(Agent.name)).all()]}


@config_router.get("/memories", response_model=list[s.MemoryOut])
def list_memories(db: DB, user: CurrentUser):
    return db.scalars(select(Memory).where(Memory.user_id == user.id).order_by(Memory.created_at.desc())).all()


@config_router.post("/memories", response_model=s.MemoryOut, status_code=201)
def create_memory(body: s.MemoryIn, db: DB, user: CurrentUser):
    memory = memory_service.create(db, user.id, body.content, body.kind, source="user")
    db.commit()
    return memory


@config_router.put("/memories/{memory_id}", response_model=s.MemoryOut)
def update_memory(memory_id: uuid.UUID, body: s.MemoryIn, db: DB, user: CurrentUser):
    from app.agents.safety import contains_secret

    if contains_secret(body.content):
        raise ValidationFailed("That looks like a password, key or card number. Sensitive values can't be stored in memory.", code="sensitive_memory")
    memory = memory_service.get_owned(db, user.id, memory_id)
    memory.content, memory.kind = body.content.strip(), body.kind
    db.commit()
    return memory


@config_router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: uuid.UUID, db: DB, user: CurrentUser):
    db.delete(memory_service.get_owned(db, user.id, memory_id))
    log_activity(db, user.id, "memory_deleted", "Deleted a memory", status="info")
    db.commit()


# Integrations ------------------------------------------------------------------------------------------------------

@integrations.get("")
def list_integrations(db: DB, user: CurrentUser):
    accounts = {a.provider: a for a in db.scalars(select(OAuthAccount).where(OAuthAccount.user_id == user.id)).all()}
    gaccount = accounts.get("google")
    gh = accounts.get("github")
    return {
        "mode": _prefs(db, user.id).integration_mode,
        "google": {"configured": settings.google_configured, "connected": gaccount is not None, "status": gaccount.status if gaccount else None, "account_email": gaccount.account_email if gaccount else None, "scopes": gaccount.scopes if gaccount else [], "services": ["Gmail", "Google Calendar", "Google Drive"]},
        "github": {"connected": gh is not None, "account": gh.account_email if gh else None},
        "ai": {"provider": settings.llm_provider, "model": settings.llm_model, "simulated": settings.llm_provider == "mock"},
    }


@integrations.get("/google/start")
def google_start(db: DB, user: CurrentUser):
    if not settings.google_configured:
        raise IntegrationNotConnected("Google OAuth is not configured on this server. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.", code="google_not_configured")
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    db.execute(delete(OAuthState).where(OAuthState.user_id == user.id, OAuthState.provider == "google"))
    db.add(OAuthState(user_id=user.id, provider="google", state_hash=sha256_hex(state), code_verifier_enc=encrypt_secret(verifier), expires_at=utcnow() + timedelta(minutes=10)))
    db.commit()
    return {"url": google.build_auth_url(state, challenge)}


def _integration_redirect(**params: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.frontend_url}/integrations?{urlencode(params)}", status_code=302)


@integrations.get("/google/callback", include_in_schema=False)
def google_callback(db: DB, user: CurrentUser, state: str = Query(...), code: str | None = None, error: str | None = None):
    stored = db.scalar(select(OAuthState).where(OAuthState.state_hash == sha256_hex(state), OAuthState.user_id == user.id, OAuthState.provider == "google"))
    if stored is None or stored.expires_at <= utcnow():
        return _integration_redirect(google="invalid_state")
    verifier = decrypt_secret(stored.code_verifier_enc)
    db.delete(stored)  # state is single-use, regardless of what happens next
    db.commit()
    if error or not code:
        return _integration_redirect(google="denied")
    try:
        tokens = google.exchange_code(code, verifier)
    except ExternalServiceError:
        return _integration_redirect(google="exchange_failed")
    account = factory.get_google_account(db, user.id) or OAuthAccount(user_id=user.id, provider="google")
    account.access_token_enc = encrypt_secret(tokens["access_token"])
    if tokens.get("refresh_token"):
        account.refresh_token_enc = encrypt_secret(tokens["refresh_token"])
    account.expires_at = utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    account.scopes = tokens.get("scope", "").split()
    account.status = "connected"
    account.account_email = google.fetch_account_email(tokens["access_token"])
    db.add(account)
    log_activity(db, user.id, "integration_connected", f"Connected Google account {account.account_email or ''}".strip(), entity_type="integration", entity_id="google")
    db.commit()
    return _integration_redirect(google="connected")


@integrations.post("/google/disconnect", status_code=204)
def google_disconnect(db: DB, user: CurrentUser):
    account = factory.get_google_account(db, user.id)
    if account is None:
        raise IntegrationNotConnected("Google is not connected.")
    try:
        google.revoke_token(decrypt_secret(account.refresh_token_enc) or decrypt_secret(account.access_token_enc))
    except ValueError:
        pass  # undecryptable tokens are being removed anyway
    db.delete(account)
    prefs = _prefs(db, user.id)
    prefs.integration_mode = "demo"
    log_activity(db, user.id, "integration_disconnected", "Disconnected Google", entity_type="integration", entity_id="google", status="info")
    db.commit()


@integrations.get("/google/drive/files")
def drive_files(db: DB, user: CurrentUser, q: str | None = Query(None, max_length=100)):
    provider = factory.drive_provider(db, user.id)
    return {"simulated": provider.simulated, "files": [f.__dict__ for f in provider.list_files(q, 25)]}


@integrations.post("/google/drive/import", response_model=s.DocumentOut, status_code=201)
def drive_import(body: s.DriveImportIn, db: DB, user: CurrentUser):
    provider = factory.drive_provider(db, user.id)
    if provider.simulated:
        raise ValidationFailed("Importing from Drive requires live mode. Demo documents are already in your library.")
    data, name, _mime = provider.download(body.file_id)
    doc = document_service.create_from_bytes(db, user.id, name, data, kind="drive", drive_file_id=body.file_id)
    db.commit()
    return doc


@integrations.post("/github", status_code=201)
def github_connect(body: s.GitHubConnectIn, db: DB, user: CurrentUser):
    login = github.get_login(body.token)
    account = db.scalar(select(OAuthAccount).where(OAuthAccount.user_id == user.id, OAuthAccount.provider == "github")) or OAuthAccount(user_id=user.id, provider="github")
    account.access_token_enc, account.account_email, account.status = encrypt_secret(body.token), login, "connected"
    db.add(account)
    log_activity(db, user.id, "integration_connected", f"Connected GitHub as {login}", entity_type="integration", entity_id="github")
    db.commit()
    return {"connected": True, "account": login}


@integrations.get("/github/issues")
def github_issues(db: DB, user: CurrentUser):
    account = db.scalar(select(OAuthAccount).where(OAuthAccount.user_id == user.id, OAuthAccount.provider == "github"))
    if account is None:
        raise IntegrationNotConnected("GitHub is not connected.")
    return {"issues": github.list_assigned_issues(decrypt_secret(account.access_token_enc))}


@integrations.delete("/github", status_code=204)
def github_disconnect(db: DB, user: CurrentUser):
    db.execute(delete(OAuthAccount).where(OAuthAccount.user_id == user.id, OAuthAccount.provider == "github"))
    log_activity(db, user.id, "integration_disconnected", "Disconnected GitHub", entity_type="integration", entity_id="github", status="info")
    db.commit()


# Demo --------------------------------------------------------------------------------------------------------------

def _require_demo_enabled() -> None:
    if not settings.demo_mode_enabled:
        raise ValidationFailed("Demo mode is disabled on this server.")


@demo.post("/reset", status_code=204)
def reset_demo(db: DB, user: CurrentUser):
    _require_demo_enabled()
    clear_demo_data(db, user.id)
    db.flush()
    seed_demo_data(db, user.id)
    db.commit()


@demo.post("/clear", status_code=204)
def clear_demo(db: DB, user: CurrentUser):
    clear_demo_data(db, user.id)
    db.commit()


@demo.post("/simulate-email", response_model=s.EmailOut, status_code=201)
def simulate_email(db: DB, user: CurrentUser):
    """Deliver a simulated email and run matching workflows, exactly as a new Gmail message would."""
    _require_demo_enabled()
    if not factory.is_demo(db, user.id):
        raise ValidationFailed("Simulated email is only available in demo mode.")
    body = "Hi,\n\nThe client needs the signed scope document urgently. Please send it by Friday and let me know if anything is blocking you.\n\nThanks,\nElena"
    email = Email(user_id=user.id, external_id=f"demo-sim-{uuid.uuid4().hex[:10]}", thread_id=None, sender_name="Elena Petrova", sender_email="elena.petrova@northwind.example.com", recipients=["you@example.com"], subject="Scope document needed", snippet=body[:160], body=body, received_at=utcnow(), labels=["INBOX"], is_demo=True)
    db.add(email)
    db.commit()
    engine.dispatch_email(db, email)
    return email


# System ------------------------------------------------------------------------------------------------------------

@system.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@system.get("/system/status")
def system_status(db: DB, user: CurrentUser):
    db_ok = worker_online = redis_ok = False
    try:
        db.execute(text("select 1"))
        db_ok = True
    except SQLAlchemyError:
        db.rollback()
    try:
        beat = redis.Redis.from_url(settings.redis_url, socket_timeout=1, socket_connect_timeout=1).get(HEARTBEAT_KEY)
        redis_ok = True
        worker_online = bool(beat and time.time() - float(beat) < 180)
    except redis.RedisError:
        pass
    try:
        llm = get_llm()
        ai = {"provider": llm.name, "model": llm.model, "simulated": llm.simulated, "ready": True}
    except LLMError as exc:
        ai = {"provider": settings.llm_provider, "model": settings.llm_model, "simulated": False, "ready": False, "message": exc.user_message}
    return {"database": db_ok, "redis": redis_ok, "worker_online": worker_online, "ai": ai, "demo_mode_enabled": settings.demo_mode_enabled, "environment": settings.environment}


@system.get("/employee/status")
def employee_status(db: DB, user: CurrentUser):
    return employee.get_status(db, user.id)


@system.post("/employee/pause")
def employee_pause(body: s.PauseIn, db: DB, user: CurrentUser):
    prefs = _prefs(db, user.id)
    prefs.employee_paused = body.paused
    log_activity(db, user.id, "employee_paused" if body.paused else "employee_resumed", f"AI employee {'paused' if body.paused else 'resumed'}", status="info")
    db.commit()
    return employee.get_status(db, user.id)
