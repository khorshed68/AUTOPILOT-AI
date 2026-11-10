"""Adapter for OpenAI-compatible Chat Completions endpoints (OpenAI, Azure proxies, Ollama, vLLM, ...)."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from app.agents.llm.base import (
    LLMAuthError,
    LLMBadOutput,
    LLMError,
    LLMProvider,
    LLMRateLimited,
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
    ToolCall,
)


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"
    simulated = False

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float):
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout = timeout

    @staticmethod
    def _messages(request: LLMRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
        for m in request.messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            elif m.role == "assistant" and m.tool_calls:
                out.append({
                    "role": "assistant", "content": m.content or None,
                    "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in m.tool_calls],
                })
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def _body(self, request: LLMRequest, stream: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": self._messages(request), "stream": stream}
        if request.tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in request.tools]
            body["tool_choice"] = "required" if request.must_use_tool else "auto"
        return body

    @staticmethod
    def _raise(status: int, text: str) -> None:
        if status == 429:
            raise LLMRateLimited(text[:300])
        if status in (401, 403):
            raise LLMAuthError(text[:300])
        if status >= 500:
            raise LLMUnavailable(text[:300])
        raise LLMError(text[:300])

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            response = httpx.post(self.url, json=self._body(request), headers=self.headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(exc.__class__.__name__) from exc
        if not response.is_success:
            self._raise(response.status_code, response.text)
        try:
            choice = response.json()["choices"][0]
            message = choice["message"]
            calls = [
                ToolCall(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"] or "{}"))
                for c in message.get("tool_calls") or []
            ]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMBadOutput("Unparseable provider response") from exc
        return LLMResponse(text=message.get("content") or "", tool_calls=calls, stop_reason=choice.get("finish_reason"))

    def stream(self, request: LLMRequest) -> Iterator[str]:
        try:
            with httpx.stream("POST", self.url, json=self._body(request, stream=True), headers=self.headers, timeout=self.timeout) as response:
                if not response.is_success:
                    response.read()
                    self._raise(response.status_code, response.text)
                for line in response.iter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    delta = json.loads(line[6:])["choices"][0].get("delta", {})
                    if delta.get("content"):
                        yield delta["content"]
        except httpx.HTTPError as exc:
            raise LLMUnavailable(exc.__class__.__name__) from exc
