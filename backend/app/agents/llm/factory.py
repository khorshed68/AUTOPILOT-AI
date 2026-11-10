"""Builds the configured provider and wraps it with bounded retries."""
from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator

from app.agents.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse
from app.core.config import settings

logger = logging.getLogger("autopilot.llm")
_override: LLMProvider | None = None


class ResilientProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, max_retries: int):
        self.inner, self.max_retries = inner, max_retries
        self.name, self.model, self.simulated = inner.name, inner.model, inner.simulated

    def complete(self, request: LLMRequest) -> LLMResponse:
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                response = self.inner.complete(request)
                logger.info("llm_call", extra={"purpose": request.purpose, "provider": self.name, "ms": int((time.monotonic() - started) * 1000), "usage": response.usage})
                return response
            except LLMError as exc:
                logger.warning("llm_error", extra={"purpose": request.purpose, "kind": exc.__class__.__name__, "attempt": attempt})
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                time.sleep((2**attempt) * 0.5 + random.uniform(0, 0.25))  # noqa: S311
                attempt += 1

    def stream(self, request: LLMRequest) -> Iterator[str]:
        return self.inner.stream(request)


def set_provider_override(provider: LLMProvider | None) -> None:
    """Test hook for injecting a scripted provider."""
    global _override
    _override = provider


def get_llm() -> LLMProvider:
    if _override is not None:
        return ResilientProvider(_override, 0)
    if settings.llm_provider == "anthropic":
        from app.agents.llm.anthropic_provider import AnthropicProvider

        inner: LLMProvider = AnthropicProvider(settings.anthropic_api_key, settings.llm_model, settings.llm_timeout_seconds)
    elif settings.llm_provider == "openai":
        from app.agents.llm.openai_provider import OpenAICompatibleProvider

        inner = OpenAICompatibleProvider(settings.openai_api_key, settings.llm_model, settings.openai_base_url, settings.llm_timeout_seconds)
    else:
        from app.agents.llm.mock import MockProvider

        inner = MockProvider()
    return ResilientProvider(inner, settings.llm_max_retries)
