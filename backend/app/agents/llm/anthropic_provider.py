"""Claude adapter built on the official Anthropic SDK."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import anthropic

from app.agents.llm.base import (
    LLMAuthError,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRateLimited,
    LLMRefused,
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
    ToolCall,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
EFFORT_PREFIXES = ("claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-haiku-5", "claude-opus-4-")


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    simulated = False

    def __init__(self, api_key: str, model: str, timeout: float):
        if not api_key:
            raise LLMAuthError("ANTHROPIC_API_KEY is not set")
        self.model = model
        # Retries are handled by ResilientProvider so they are observable and bounded in one place.
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)

    @staticmethod
    def _messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                block = {"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}
                if message.is_error:
                    block["is_error"] = True
                # All results of one turn must arrive in one user message.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif message.role == "assistant" and message.provider_state is not None:
                out.append({"role": "assistant", "content": message.provider_state})
            else:
                out.append({"role": message.role, "content": message.content})
        return out

    def _params(self, request: LLMRequest) -> dict[str, Any]:
        system = request.system
        if request.must_use_tool:
            names = ", ".join(t.name for t in request.tools)
            # Forced tool_choice is rejected by current models, so the requirement is stated in the prompt.
            system += f"\n\nYou must respond by calling one of these tools, never with plain text: {names}."
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": system,
            "messages": self._messages(request.messages),
        }
        if request.tools:
            params["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in request.tools]
            params["tool_choice"] = {"type": "auto"}
        if self.model.startswith(EFFORT_PREFIXES):
            params["output_config"] = {"effort": "medium"}
        if self.model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["extra_body"] = {"fallbacks": "default"}
        return params

    def _call(self, params: dict[str, Any]):
        if "betas" in params:
            return self.client.beta.messages.create(**params)
        return self.client.messages.create(**params)

    @staticmethod
    def _translate(exc: Exception) -> LLMError:
        if isinstance(exc, anthropic.RateLimitError):
            return LLMRateLimited(str(exc))
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return LLMAuthError(str(exc))
        if isinstance(exc, anthropic.APIStatusError) and exc.status_code < 500:
            return LLMError(str(exc))
        return LLMUnavailable(str(exc))

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            message = self._call(self._params(request))
        except anthropic.AnthropicError as exc:
            raise self._translate(exc) from exc
        if message.stop_reason == "refusal":
            raise LLMRefused("Model declined the request")
        text = "".join(b.text for b in message.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in message.content if b.type == "tool_use"]
        return LLMResponse(
            text=text, tool_calls=calls, stop_reason=message.stop_reason,
            provider_state=[b.model_dump(mode="json", exclude_none=True) for b in message.content],
            usage={"input": message.usage.input_tokens, "output": message.usage.output_tokens},
        )

    def stream(self, request: LLMRequest) -> Iterator[str]:
        params = self._params(request)
        manager = self.client.beta.messages.stream(**params) if "betas" in params else self.client.messages.stream(**params)
        try:
            with manager as stream:
                yield from stream.text_stream
                if stream.get_final_message().stop_reason == "refusal":
                    raise LLMRefused("Model declined the request")
        except anthropic.AnthropicError as exc:
            raise self._translate(exc) from exc
