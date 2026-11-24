"""Claude via the official Anthropic SDK (Messages API, manual agent loop)."""

from __future__ import annotations

from typing import Any

import anthropic

from gabe.providers.base import Conversation, Provider, StepResult, ToolCall, ToolResult
from gabe.tools.registry import Tool

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_STOP_MAP = {
    "end_turn": "done",
    "stop_sequence": "done",
    "tool_use": "tool_use",
    "pause_turn": "pause",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


class AnthropicProvider(Provider):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        max_tokens: int = 64000,
        refusal_fallback: bool = True,
        client: Any = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.refusal_fallback = refusal_fallback
        # Zero-arg client resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login`.
        self.client = client or anthropic.Anthropic()

    def start(self, system: str, tools: list[Tool]) -> Conversation:
        return AnthropicConversation(self, system, tools)


class AnthropicConversation(Conversation):
    def __init__(self, provider: AnthropicProvider, system: str, tools: list[Tool]) -> None:
        self.p = provider
        self.system = system
        self.tool_defs = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                # Stream large tool inputs as they're generated; inputs are
                # validated client-side in the agent loop before any tool runs.
                "eager_input_streaming": True,
            }
            for t in tools
        ]
        # Append-only history in Anthropic wire format. Assistant content is
        # stored exactly as returned so thinking/fallback blocks round-trip intact.
        self.messages: list[dict[str, Any]] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # All results for one assistant turn go back in a single user message.
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_call_id,
                        "content": r.content,
                        **({"is_error": True} if r.is_error else {}),
                    }
                    for r in results
                ],
            }
        )

    def _request(self) -> dict[str, Any]:
        req: dict[str, Any] = {
            "model": self.p.model,
            "max_tokens": self.p.max_tokens,
            # System prompt is kept byte-stable (no timestamps) so it caches.
            "system": [{"type": "text", "text": self.system}],
            "messages": self.messages,
            "cache_control": {"type": "ephemeral"},
            "output_config": {"effort": self.p.effort},
        }
        if self.tool_defs:
            req["tools"] = self.tool_defs
        if self.p.refusal_fallback:
            # On a safety decline, the API re-runs the turn on a fallback model.
            req["betas"] = [FALLBACK_BETA]
            req["fallbacks"] = "default"
        return req

    def step(self) -> StepResult:
        with self.p.client.beta.messages.stream(**self._request()) as stream:
            msg = stream.get_final_message()

        self.messages.append({"role": "assistant", "content": msg.content})

        text = "".join(b.text for b in msg.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in msg.content
            if b.type == "tool_use"
        ]
        stop = _STOP_MAP.get(msg.stop_reason or "end_turn", "done")
        detail = None
        if stop == "refusal" and getattr(msg, "stop_details", None):
            detail = f"{msg.stop_details.category}: {msg.stop_details.explanation}"
        usage = {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", None),
        }
        return StepResult(text=text, tool_calls=calls, stop=stop, usage=usage, detail=detail)
