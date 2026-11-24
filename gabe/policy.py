"""Approval policy: decides whether a tool call runs, needs a human, or is blocked.

Every company sets its own rules in config. Defaults are by risk level; per-tool
and per-integration overrides win over the defaults.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable

from gabe.tools.registry import Risk, Tool


class Decision(str, enum.Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class Declined:
    """A refusal that carries a note back to the model. Falsy, like False."""

    note: str = ""

    def __bool__(self) -> bool:
        return False


# Called when a decision is ASK: return True to approve the call, or False / Declined(note)
# to refuse it. An approver may edit `args` in place before approving (edit-then-approve).
Approver = Callable[[Tool, dict[str, Any]], "bool | Declined"]


def deny_all(tool: Tool, args: dict[str, Any]) -> bool:
    """Approver for unattended runs: anything that needs a human is refused."""
    return False


@dataclass
class Policy:
    defaults: dict[Risk, Decision] = field(
        default_factory=lambda: {
            Risk.READ: Decision.ALLOW,
            Risk.WRITE: Decision.ALLOW,
            Risk.EXTERNAL: Decision.ASK,
        }
    )
    tool_overrides: dict[str, Decision] = field(default_factory=dict)
    integration_overrides: dict[str, Decision] = field(default_factory=dict)

    def decide(self, tool: Tool) -> Decision:
        if tool.name in self.tool_overrides:
            return self.tool_overrides[tool.name]
        if tool.integration in self.integration_overrides:
            return self.integration_overrides[tool.integration]
        return self.defaults.get(tool.risk, Decision.ASK)

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> "Policy":
        cfg = cfg or {}
        policy = cls()
        for risk, decision in (cfg.get("defaults") or {}).items():
            policy.defaults[Risk(risk)] = Decision(decision)
        policy.tool_overrides = {k: Decision(v) for k, v in (cfg.get("tools") or {}).items()}
        policy.integration_overrides = {
            k: Decision(v) for k, v in (cfg.get("integrations") or {}).items()
        }
        return policy
