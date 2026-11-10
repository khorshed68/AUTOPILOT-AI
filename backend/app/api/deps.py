"""Authentication, CSRF enforcement and shared helpers for routes."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import settings
from app.core.errors import Forbidden, Unauthorized
from app.core.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, csrf_matches, decode_session_token
from app.database.base import utcnow
from app.database.session import get_db
from app.models import AuthSession, User

DB = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DB) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    claims = decode_session_token(token) if token else None
    if claims is None:
        raise Unauthorized("Please sign in.")
    session = db.get(AuthSession, uuid.UUID(claims["sid"]))
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow() or str(session.user_id) != claims["sub"]:
        raise Unauthorized("Your session has expired. Please sign in again.")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("This account is not active.")
    if request.method not in ("GET", "HEAD", "OPTIONS") and not csrf_matches(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)):
        raise Forbidden("Missing or invalid CSRF token.", code="csrf_failed")
    rate_limit.enforce("user", str(user.id), settings.rate_limit_default_per_minute)
    request.state.session_id = session.id
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def limit_ai(user: CurrentUser) -> None:
    rate_limit.enforce("ai", str(user.id), settings.rate_limit_ai_per_minute)


class Pagination:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
        self.page, self.page_size = page, page_size

    def apply(self, db: Session, stmt):
        total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        items = db.scalars(stmt.limit(self.page_size).offset((self.page - 1) * self.page_size)).all()
        return {"items": items, "total": total, "page": self.page, "page_size": self.page_size}


Paging = Annotated[Pagination, Depends()]


def set_auth_cookies(response: Response, token: str, csrf: str) -> None:
    max_age = settings.session_hours * 3600
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=settings.cookie_secure, samesite="lax", path="/")


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
