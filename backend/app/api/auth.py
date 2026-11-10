from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, clear_auth_cookies, set_auth_cookies
from app.core.config import settings
from app.core.errors import Conflict, RateLimited, Unauthorized
from app.core.rate_limit import limit_auth
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_session_token,
    hash_password,
    new_csrf_token,
    session_expiry,
    verify_password,
)
from app.database.base import utcnow
from app.models import AuthSession, User
from app.schemas.api import LoginIn, RegisterIn, UserOut
from app.services.activity import log_activity
from app.services.seed import create_preferences, seed_demo_data

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15


def _start_session(db: Session, user: User, request: Request, response: Response) -> None:
    expires = session_expiry()
    session = AuthSession(user_id=user.id, expires_at=expires, user_agent=(request.headers.get("user-agent") or "")[:255])
    db.add(session)
    db.flush()
    set_auth_cookies(response, create_session_token(user.id, session.id, expires), new_csrf_token())


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit_auth)])
def register(body: RegisterIn, request: Request, response: Response, db: DB):
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise Conflict("An account with this email already exists.", code="email_taken")
    user = User(email=email, hashed_password=hash_password(body.password), full_name=body.full_name.strip())
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("An account with this email already exists.", code="email_taken") from exc
    create_preferences(db, user.id, body.timezone)
    db.flush()
    if settings.demo_mode_enabled:
        seed_demo_data(db, user.id)
    log_activity(db, user.id, "account_created", "Account created")
    _start_session(db, user, request, response)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=UserOut, dependencies=[Depends(limit_auth)])
def login(body: LoginIn, request: Request, response: Response, db: DB):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        verify_password(body.password, DUMMY_PASSWORD_HASH)  # keep timing uniform for unknown accounts
        raise Unauthorized("Incorrect email or password.", code="invalid_credentials")
    if user.locked_until and user.locked_until > utcnow():
        raise RateLimited("Too many failed attempts. Try again later.", code="account_locked")
    if not user.is_active or not verify_password(body.password, user.hashed_password):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = utcnow() + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
        log_activity(db, user.id, "login_failed", "Failed sign-in attempt", status="failure", meta={"ip": request.client.host if request.client else None})
        db.commit()
        raise Unauthorized("Incorrect email or password.", code="invalid_credentials")
    user.failed_logins, user.locked_until = 0, None
    _start_session(db, user, request, response)
    log_activity(db, user.id, "login", "Signed in", status="info")
    db.commit()
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DB, user: CurrentUser):
    session = db.get(AuthSession, request.state.session_id)
    session.revoked_at = utcnow()
    db.commit()
    clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user
