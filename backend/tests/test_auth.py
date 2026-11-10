from __future__ import annotations

from sqlalchemy import select

from app.models import User
from tests.conftest import PASSWORD, Api


def test_register_sets_httponly_session_and_hashes_password(db):
    api = Api()
    response = api.client.post("/api/auth/register", json={"email": "New@Example.com", "password": PASSWORD, "full_name": "New"})
    assert response.status_code == 201
    assert response.json()["email"] == "new@example.com"
    cookie = next(h for h in response.headers.get_list("set-cookie") if h.startswith("ap_session"))
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    user = db.scalar(select(User).where(User.email == "new@example.com"))
    assert user.hashed_password.startswith("$argon2") and PASSWORD not in user.hashed_password


def test_register_uses_valid_browser_timezone_only():
    good = Api().client.post("/api/auth/register", json={"email": "tz1@example.com", "password": PASSWORD, "full_name": "T", "timezone": "Asia/Dhaka"})
    assert good.json()["preferences"]["timezone"] == "Asia/Dhaka"
    bad = Api().client.post("/api/auth/register", json={"email": "tz2@example.com", "password": PASSWORD, "full_name": "T", "timezone": "Not/AZone"})
    assert bad.json()["preferences"]["timezone"] == "UTC"


def test_register_validation_and_duplicates(api):
    weak = Api().client.post("/api/auth/register", json={"email": "a@example.com", "password": "short", "full_name": "A"})
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "validation_failed"
    dupe = Api().client.post("/api/auth/register", json={"email": api.email, "password": PASSWORD, "full_name": "B"})
    assert dupe.status_code == 409


def test_login_logout_flow(api):
    fresh = Api(api.email)
    assert fresh.get("/auth/me").status_code == 401
    assert fresh.client.post("/api/auth/login", json={"email": api.email, "password": PASSWORD}).status_code == 200
    assert fresh.get("/auth/me").json()["email"] == api.email
    stolen_cookie = fresh.client.cookies.get("ap_session")
    assert fresh.post("/auth/logout").status_code == 204
    replay = Api(api.email)
    replay.client.cookies.set("ap_session", stolen_cookie)
    assert replay.get("/auth/me").status_code == 401, "revoked sessions must not work"


def test_wrong_password_and_lockout(api):
    fresh = Api(api.email)
    for _ in range(5):
        assert fresh.client.post("/api/auth/login", json={"email": api.email, "password": "wrong-password-1"}).status_code == 401
    locked = fresh.client.post("/api/auth/login", json={"email": api.email, "password": PASSWORD})
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "account_locked"


def test_unknown_user_gets_same_error(api):
    response = Api().client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "whatever-123"})
    assert response.status_code == 401 and response.json()["error"]["code"] == "invalid_credentials"


def test_csrf_required_on_unsafe_requests(api):
    response = api.client.post("/api/tasks", json={"title": "No token"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "csrf_failed"
    assert api.post("/tasks", json={"title": "With token"}).status_code == 201


def test_tampered_token_rejected(api):
    head, payload, _signature = api.client.cookies.get("ap_session").split(".")
    api.client.cookies.clear()
    api.client.cookies.set("ap_session", f"{head}.{payload}.AAAA")
    assert api.get("/auth/me").status_code == 401


def test_auth_rate_limit(monkeypatch):
    from app.core import rate_limit
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 3)
    rate_limit.reset_local_counters()
    client = Api().client
    codes = [client.post("/api/auth/login", json={"email": "x@example.com", "password": "abc12345678"}).status_code for _ in range(5)]
    assert 429 in codes


def test_security_headers_and_error_shape(api):
    response = api.get("/tasks/not-a-uuid")
    assert response.status_code == 422
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert set(response.json()["error"]) == {"code", "message", "details", "request_id"}
