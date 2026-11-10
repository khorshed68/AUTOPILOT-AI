"""Test harness: real app, real SQL (SQLite by default, PostgreSQL via TEST_DATABASE_URL), mocked externals."""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="autopilot-tests-"))
os.environ.update(
    ENVIRONMENT="test",
    SECRET_KEY="test-secret-key-that-is-long-enough-to-use-0123456789",
    ENCRYPTION_KEY="test-encryption-key-that-is-long-enough-0123456789",
    DATABASE_URL=os.environ.get("TEST_DATABASE_URL", f"sqlite:///{(_tmp / 'test.db').as_posix()}"),
    REDIS_URL="redis://127.0.0.1:1/0",
    CELERY_TASK_ALWAYS_EAGER="true",
    LLM_PROVIDER="mock",
    LLM_MODEL="",
    DEMO_MODE_ENABLED="true",
    DEMO_USER_PASSWORD="",
    UPLOAD_DIR=str(_tmp / "uploads"),
    RATE_LIMIT_AUTH_PER_MINUTE="1000",
    RATE_LIMIT_AI_PER_MINUTE="1000",
    RATE_LIMIT_DEFAULT_PER_MINUTE="100000",
    GOOGLE_CLIENT_ID="",
    GOOGLE_CLIENT_SECRET="",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agents.llm.factory import set_provider_override  # noqa: E402
from app.agents.orchestrator import sync_agent_catalog  # noqa: E402
from app.core import rate_limit  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "CorrectHorse123"


@pytest.fixture(autouse=True)
def _database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        sync_agent_catalog(session)
    rate_limit.reset_local_counters()
    set_provider_override(None)
    yield
    set_provider_override(None)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


class Api:
    """A signed-in browser: cookies plus the CSRF header on unsafe requests."""

    def __init__(self, email: str | None = None):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"

    def register(self):
        response = self.client.post("/api/auth/register", json={"email": self.email, "password": PASSWORD, "full_name": "Test User"})
        assert response.status_code == 201, response.text
        self.user = response.json()
        return self

    @property
    def csrf(self) -> dict:
        return {"X-CSRF-Token": self.client.cookies.get("ap_csrf", "")}

    def get(self, url, **kw):
        return self.client.get(f"/api{url}", **kw)

    def post(self, url, **kw):
        return self.client.post(f"/api{url}", headers=self.csrf, **kw)

    def put(self, url, **kw):
        return self.client.put(f"/api{url}", headers=self.csrf, **kw)

    def delete(self, url, **kw):
        return self.client.delete(f"/api{url}", headers=self.csrf, **kw)

    def chat(self, message: str, conversation_id: str | None = None) -> dict:
        response = self.post("/ai/chat", json={"message": message, "conversation_id": conversation_id, "stream": False})
        assert response.status_code == 200, response.text
        return response.json()

    def pending(self) -> list[dict]:
        return self.get("/approvals?status=pending").json()["items"]


@pytest.fixture
def api() -> Api:
    return Api().register()


@pytest.fixture
def other() -> Api:
    return Api().register()
