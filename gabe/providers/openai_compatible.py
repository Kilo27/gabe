"""Locally hosted models (e.g. GLM served by vLLM or SGLang) via an
OpenAI-compatible /v1/chat/completions endpoint with tool calling.

This is the path to running Gabe fully on-prem. Point `base_url` at the
local server; nothing else in Gabe changes.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from gabe.providers.base import Conversation, Provider, StepResult, ToolCall, ToolResult
from gabe.tools.registry import Tool

_FINISH_MAP = {"stop": "done", "tool_calls": "tool_use", "length": "max_tokens"}


class OpenAICompatibleProvider(Provider):
    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        model: str = "glm",
        api_key: str = "EMPTY",
        max_tokens: int = 8192,
        timeout: float = 600.0,
        extra_body: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.extra_body = extra_body or {}
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def start(self, system: str, tools: list[Tool]) -> Conversation:
        return OpenAICompatibleConversation(self, system, tools)


class OpenAICompatibleConversation(Conversation):
    def __init__(self, p: OpenAICompatibleProvider, system: str, tools: list[Tool]) -> None:
        self.p = p
        self.tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            content = f"ERROR: {r.content}" if r.is_error else r.content
            self.messages.append({"role": "tool", "tool_call_id": r.tool_call_id, "content": content})

    def step(self) -> StepResult:
        body: dict[str, Any] = {
            "model": self.p.model,
            "messages": self.messages,
            "max_tokens": self.p.max_tokens,
            **self.p.extra_body,
        }
        if self.tool_defs:
            body["tools"] = self.tool_defs
        resp = self.p.http.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        msg = choice["message"]

        stored: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            stored["tool_calls"] = msg["tool_calls"]
        self.messages.append(stored)

        calls = []
        for tc in msg.get("tool_calls") or []:
            raw = tc["function"].get("arguments") or "{}"
            try:
                args, err = json.loads(raw), None
            except json.JSONDecodeError as e:
                args, err = {}, f"invalid JSON arguments: {e}"
            calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"], input=args, parse_error=err))

        stop = _FINISH_MAP.get(choice.get("finish_reason") or "stop", "done")
        if calls and stop == "done":
            stop = "tool_use"  # some servers report "stop" even when tools were called
        return StepResult(
            text=msg.get("content") or "",
            tool_calls=calls,
            stop=stop,
            usage=_normalise_usage(data, self.p.model),
        )


def _normalise_usage(data: dict[str, Any], model: str) -> dict[str, Any]:
    """OpenAI-style usage → Gabe's shape (gabe/usage.py). prompt_tokens includes cached ones."""
    raw = data.get("usage") or {}
    if not raw:
        return {}
    prompt = raw.get("prompt_tokens") or 0
    cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return {
        "model": data.get("model") or model,
        "input_tokens": max(prompt - cached, 0),
        "cache_read_tokens": cached,
        "cache_write_tokens": 0,
        "output_tokens": raw.get("completion_tokens") or 0,
    }
