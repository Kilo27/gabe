"""Model providers. Gabe talks to models only through the Provider interface,
so the Anthropic API can be swapped for a locally hosted model (e.g. GLM served
by vLLM/SGLang) by changing config, not code."""

from __future__ import annotations

from typing import Any

from gabe.providers.base import Conversation, Provider, StepResult, ToolCall, ToolResult


def make_provider(cfg: dict[str, Any]) -> Provider:
    kind = cfg.get("kind", "anthropic")
    opts = {k: v for k, v in cfg.items() if k != "kind"}
    if kind == "anthropic":
        from gabe.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(**opts)
    if kind == "openai_compatible":
        from gabe.providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(**opts)
    raise ValueError(f"unknown provider kind '{kind}'")


__all__ = ["Conversation", "Provider", "StepResult", "ToolCall", "ToolResult", "make_provider"]
