"""Approval policy: decides whether a tool call runs, needs a human, or is blocked.

Every company sets its own rules in config. Defaults are by risk level; per-tool
and per-integration overrides win over the defaults.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from gabe.tools.registry import Risk, Tool


class Decision(str, enum.Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


_STRICTNESS = {Decision.ALLOW: 0, Decision.ASK: 1, Decision.DENY: 2}


def stricter(a: Decision, b: Decision) -> Decision:
    return a if _STRICTNESS[a] >= _STRICTNESS[b] else b


# Called when a decision is ASK: return True to approve the call.
Approver = Callable[[Tool, dict[str, Any]], bool]


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
    # Per-agent limit on top of the company rules (a team member's freedom level).
    # It can only make a decision stricter, never looser.
    ceiling: dict[Risk, Decision] = field(default_factory=dict)

    def decide(self, tool: Tool) -> Decision:
        if tool.name in self.tool_overrides:
            decision = self.tool_overrides[tool.name]
        elif tool.integration in self.integration_overrides:
            decision = self.integration_overrides[tool.integration]
        else:
            decision = self.defaults.get(tool.risk, Decision.ASK)
        return stricter(decision, self.ceiling.get(tool.risk, Decision.ALLOW))

    def restricted(self, ceiling: dict[Risk, Decision]) -> "Policy":
        """A copy of this policy that is at least as strict as `ceiling` (and the current one)."""
        merged = {r: stricter(self.ceiling.get(r, Decision.ALLOW), ceiling.get(r, Decision.ALLOW)) for r in Risk}
        return replace(self, ceiling=merged)

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
