"""Structured contracts between the orchestrator and agents."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.registry import AGENTS
from app.core.config import settings


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=32)
    agent: str
    goal: str = Field(min_length=1, max_length=1000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class MemoryProposal(BaseModel):
    kind: Literal["preference", "project", "tone", "context", "other"] = "other"
    content: str


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    intent: Literal["execute", "respond", "clarify"]
    objective: str = ""
    response: str | None = None
    question: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)
    memories: list[MemoryProposal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_graph(self):
        if len(self.steps) > settings.agent_max_plan_steps:
            raise ValueError(f"Plan exceeds {settings.agent_max_plan_steps} steps")
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate step ids")
        for step in self.steps:
            if step.agent not in AGENTS:
                raise ValueError(f"Unknown agent: {step.agent}")
            unknown = set(step.depends_on) - set(ids)
            if unknown:
                raise ValueError(f"Unknown dependencies: {unknown}")
        topological_order(self.steps)
        if self.intent == "execute" and not self.steps:
            raise ValueError("An executable plan needs steps")
        return self


def topological_order(steps: list[PlanStep]) -> list[PlanStep]:
    remaining = {s.id: s for s in steps}
    done: set[str] = set()
    ordered: list[PlanStep] = []
    while remaining:
        ready = [s for s in remaining.values() if set(s.depends_on) <= done]
        if not ready:
            raise ValueError("Plan contains a dependency cycle")
        for step in ready:
            ordered.append(step)
            done.add(step.id)
            del remaining[step.id]
    return ordered


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["execute", "respond", "clarify"]},
        "objective": {"type": "string", "description": "The user's actual goal in one sentence"},
        "response": {"type": "string", "description": "Reply when intent is respond"},
        "question": {"type": "string", "description": "Clarifying question when intent is clarify"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "agent": {"type": "string", "enum": sorted(AGENTS)},
                    "goal": {"type": "string"},
                    "inputs": {"type": "object"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "agent", "goal"],
            },
        },
        "memories": {
            "type": "array",
            "description": "Durable facts the user explicitly stated about preferences or projects. Never secrets.",
            "items": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["preference", "project", "tone", "context", "other"]}, "content": {"type": "string"}}, "required": ["content"]},
        },
    },
    "required": ["intent", "objective"],
}

AGENT_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "needs_input", "failed"]},
        "summary": {"type": "string", "description": "What was found or done, stated factually"},
        "question": {"type": "string", "description": "What you need from the user, when status is needs_input"},
        "data": {"type": "object", "description": "Structured results for later steps"},
    },
    "required": ["status", "summary"],
}


class AgentResult(BaseModel):
    """What every agent hands back. Downstream code never parses free text."""

    agent: str
    task_id: str
    status: Literal["completed", "needs_input", "failed", "skipped"]
    summary: str = ""
    question: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    duration_ms: int = 0
