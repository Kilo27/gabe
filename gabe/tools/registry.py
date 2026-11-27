"""Tool registry: every capability Gabe has is a Tool registered here.

Tools are provider-neutral. Each one carries a JSON Schema for its input, a
handler, and a risk level that the approval policy uses to decide whether it
may run unattended.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any, Callable


class Risk(str, enum.Enum):
    READ = "read"          # looks things up, changes nothing
    WRITE = "write"        # changes Gabe's own/internal state (tasks, notes, drafts)
    EXTERNAL = "external"  # acts on the outside world (sends email, posts, pays)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    risk: Risk = Risk.READ
    integration: str = "core"
    # Needs a person's OK whatever the risk defaults say, unless the company sets a rule
    # for this exact action (policy.tools).
    ask_first: bool = False

    def run(self, args: dict[str, Any]) -> str:
        out = self.handler(**args)
        if isinstance(out, str):
            return out
        return json.dumps(out, default=str, indent=2)


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Small helper to build an object input schema."""
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": False,
    }


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_input(input_schema: dict[str, Any], args: Any) -> str | None:
    """Shallow validation of tool input. Returns an error message or None.

    Streamed tool inputs are not validated server-side, so every call is
    checked here before its handler runs.
    """
    if not isinstance(args, dict):
        return "tool input must be a JSON object"
    props = input_schema.get("properties", {})
    for key in input_schema.get("required", []):
        if key not in args:
            return f"missing required field '{key}'"
    if input_schema.get("additionalProperties") is False:
        extra = set(args) - set(props)
        if extra:
            return f"unexpected field(s): {', '.join(sorted(extra))}"
    for key, value in args.items():
        expected = props.get(key, {}).get("type")
        types = _JSON_TYPES.get(expected) if isinstance(expected, str) else None
        if types is None:
            continue
        if isinstance(value, bool) and bool not in types:
            return f"field '{key}' must be {expected}"
        if not isinstance(value, types):
            return f"field '{key}' must be {expected}"
        enum_values = props[key].get("enum")
        if enum_values and value not in enum_values:
            return f"field '{key}' must be one of {enum_values}"
    return None


@dataclass
class ToolRegistry:
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool
        return tool

    def add(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        risk: Risk = Risk.READ,
        integration: str = "core",
        ask_first: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form: @registry.add("name", "desc", schema(...), Risk.WRITE)."""

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register(Tool(name, description, input_schema, fn, risk, integration, ask_first))
            return fn

        return wrap

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tools(self) -> list[Tool]:
        # Sorted so the tool list is byte-stable across runs (keeps the prompt cache warm).
        return [self._tools[n] for n in sorted(self._tools)]

    def subset(self, names: list[str] | None) -> "ToolRegistry":
        """A registry holding only the named tools (all of them when names is None)."""
        if names is None:
            return ToolRegistry(dict(self._tools))
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"unknown tool(s): {', '.join(missing)}")
        return ToolRegistry({n: self._tools[n] for n in names})
