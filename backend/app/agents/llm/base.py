"""Provider-neutral LLM contract. The rest of the application only sees these types."""
from __future__ import annotations

import abc
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

SUBMIT_TOOL = "submit_result"


class LLMError(Exception):
    retryable = False
    user_message = "The AI provider could not complete this request."


class LLMUnavailable(LLMError):
    retryable = True
    user_message = "The AI provider is temporarily unavailable. Your data is safe; please try again shortly."


class LLMRateLimited(LLMError):
    retryable = True
    user_message = "The AI provider is rate limiting requests. Please try again in a minute."


class LLMAuthError(LLMError):
    user_message = "The AI provider rejected the configured credentials. Check the API key in the server configuration."


class LLMRefused(LLMError):
    user_message = "The AI provider declined this request."


class LLMBadOutput(LLMError):
    retryable = True
    user_message = "The AI returned a malformed response."


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMMessage:
    role: str  # user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    is_error: bool = False
    provider_state: Any = None  # opaque provider content replayed unchanged on the next turn


@dataclass
class LLMRequest:
    purpose: str
    system: str
    messages: list[LLMMessage]
    tools: list[ToolSpec] = field(default_factory=list)
    must_use_tool: bool = False
    max_tokens: int = 8000


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    provider_state: Any = None
    usage: dict[str, int] = field(default_factory=dict)


class LLMProvider(abc.ABC):
    name: str = "base"
    model: str = ""
    simulated: bool = False

    @abc.abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def stream(self, request: LLMRequest) -> Iterator[str]:
        yield self.complete(request).text


def structured_call(provider: LLMProvider, *, purpose: str, system: str, user: str, schema: dict[str, Any], max_tokens: int = 8000) -> dict[str, Any]:
    """One-shot call returning JSON matching `schema`, via a tool call so all providers behave alike."""
    request = LLMRequest(
        purpose=purpose,
        system=system,
        messages=[LLMMessage("user", user)],
        tools=[ToolSpec(SUBMIT_TOOL, "Submit the final structured result.", schema)],
        must_use_tool=True,
        max_tokens=max_tokens,
    )
    response = provider.complete(request)
    for call in response.tool_calls:
        if call.name == SUBMIT_TOOL:
            return call.arguments
    parsed = parse_json_text(response.text)
    if parsed is None:
        raise LLMBadOutput(f"{purpose}: no structured result returned")
    return parsed


def parse_json_text(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
