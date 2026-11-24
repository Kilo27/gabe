"""Model providers. Gabe talks to models only through the Provider interface,
so the Anthropic API can be swapped for a locally hosted model (e.g. GLM served
by vLLM/SGLang) by changing config, not code."""

from __future__ import annotations

from typing import Any

from gabe.providers.base import Conversation, Provider, StepResult, ToolCall, ToolResult


def tier_models(cfg: dict[str, Any]) -> dict[str, str]:
    """Model id for each tier (haiku / sonnet / opus) under this provider config."""
    tiers: dict[str, str] = {}
    if cfg.get("kind", "anthropic") == "anthropic":
        from gabe.providers.anthropic_provider import DEFAULT_TIERS

        tiers = dict(DEFAULT_TIERS)
    return {**tiers, **(cfg.get("tiers") or {})}


def own_model(cfg: dict[str, Any]) -> str:
    """The model the provider runs on when no tier is asked for (Gabe's own)."""
    if cfg.get("model"):
        return str(cfg["model"])
    if cfg.get("kind", "anthropic") == "anthropic":
        from gabe.providers.anthropic_provider import DEFAULT_MODEL

        return DEFAULT_MODEL
    return "glm"


def make_provider(cfg: dict[str, Any]) -> Provider:
    kind = cfg.get("kind", "anthropic")
    opts = {k: v for k, v in cfg.items() if k not in ("kind", "tiers")}
    if kind == "anthropic":
        from gabe.providers.anthropic_provider import AnthropicProvider

        provider: Provider = AnthropicProvider(**opts)
    elif kind == "openai_compatible":
        from gabe.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(**opts)
    else:
        raise ValueError(f"unknown provider kind '{kind}'")
    provider.tiers = tier_models(cfg)
    return provider


__all__ = ["own_model", "tier_models", "Conversation", "Provider", "StepResult", "ToolCall", "ToolResult", "make_provider"]
