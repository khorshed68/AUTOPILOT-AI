"""Application configuration, loaded only from the environment or a local .env file."""
from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore", case_sensitive=False)

    app_name: str = "AutoPilot AI"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # Secrets. Empty values are only tolerated outside production.
    secret_key: str = ""
    encryption_key: str = ""

    database_url: str = "postgresql+psycopg://autopilot:autopilot@localhost:55440/autopilot"
    redis_url: str = "redis://localhost:56390/0"
    celery_task_always_eager: bool = False

    frontend_url: str = "http://localhost:5190"
    cors_origins: str = "http://localhost:5190,http://127.0.0.1:5190"
    cookie_secure: bool = False
    session_hours: int = 24 * 7

    # LLM provider: mock | anthropic | openai (any OpenAI-compatible endpoint)
    llm_provider: Literal["mock", "anthropic", "openai"] = "mock"
    llm_model: str = ""
    llm_timeout_seconds: float = 90
    llm_max_retries: int = 2
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # Agent execution limits
    agent_max_plan_steps: int = 8
    agent_max_iterations: int = 30
    agent_max_tool_calls_per_run: int = 40
    agent_step_retries: int = 1
    agent_run_timeout_seconds: float = 300

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:5190/api/integrations/google/callback"

    # Demo mode
    demo_mode_enabled: bool = True
    demo_user_email: str = "demo@example.com"
    demo_user_password: str = ""  # the demo account is only created when this is set

    # Documents
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 15

    # Rate limits (requests per minute per client)
    rate_limit_auth_per_minute: int = 10
    rate_limit_ai_per_minute: int = 30
    rate_limit_default_per_minute: int = 300

    approval_ttl_hours: int = 72
    time_saved_minutes: dict[str, int] = Field(
        default_factory=lambda: {
            "email_analyzed": 2,
            "reply_drafted": 6,
            "task_created": 1,
            "event_scheduled": 5,
            "report_generated": 30,
            "automation_run": 3,
        }
    )

    @model_validator(mode="after")
    def _finalize(self) -> Settings:
        if self.environment == "production":
            missing = [n for n in ("secret_key", "encryption_key") if len(getattr(self, n)) < 32]
            if missing:
                raise ValueError(f"Production requires strong values for: {', '.join(missing)}")
            if not self.cookie_secure:
                raise ValueError("Production requires COOKIE_SECURE=true")
        # Outside production, unset secrets are generated per process rather than hardcoded.
        if not self.secret_key:
            self.secret_key = secrets.token_urlsafe(48)
        if not self.encryption_key:
            self.encryption_key = self.secret_key
        if not self.llm_model:
            self.llm_model = {"mock": "mock-employee-1", "anthropic": "claude-opus-5-5", "openai": "gpt-4o-mini"}[
                self.llm_provider
            ]
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
