"""Provider-neutral interface between the agent loop and a model backend.

Each provider owns its own conversation history in its native wire format.
That matters: Claude's thinking blocks must be passed back unchanged, and a
local OpenAI-compatible server has its own message shape. The agent loop only
sees StepResults and hands back ToolResults.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from gabe.tools.registry import Tool

Stop = Literal["done", "tool_use", "pause", "max_tokens", "refusal"]


@dataclass
class ToolCall:
    id: str
    name: str
    input: Any
    parse_error: str | None = None  # set when the model's arguments were not valid JSON


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class StepResult:
    text: str
    tool_calls: list[ToolCall]
    stop: Stop
    usage: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None  # e.g. refusal explanation


class Conversation(ABC):
    @abstractmethod
    def add_user(self, text: str) -> None: ...

    @abstractmethod
    def add_tool_results(self, results: list[ToolResult]) -> None: ...

    @abstractmethod
    def step(self) -> StepResult:
        """Send the conversation to the model and record its reply."""


class Provider(ABC):
    # Model id per tier (haiku / sonnet / opus), so each team member can run on the model
    # its work needs. A tier with no entry uses the provider's own model.
    tiers: dict[str, str] = {}

    @abstractmethod
    def start(self, system: str, tools: list[Tool]) -> Conversation: ...

    def with_model(self, model: str) -> "Provider":
        """This provider on another model, sharing its connection. Default: unchanged."""
        return self

    def for_tier(self, tier: str | None) -> "Provider":
        model = self.tiers.get(tier) if tier else None
        return self.with_model(model) if model else self
