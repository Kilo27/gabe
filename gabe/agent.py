"""The agent loop: model step -> policy-gated tool execution -> repeat."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from gabe.audit import AuditLog
from gabe.policy import Approver, Decision, Policy, deny_all
from gabe.providers.base import Conversation, Provider, StepResult, ToolCall, ToolResult
from gabe.tools.registry import ToolRegistry, validate_input

EventHandler = Callable[[str, dict[str, Any]], None]


def _ignore(kind: str, data: dict[str, Any]) -> None:
    pass


@dataclass
class Agent:
    name: str
    provider: Provider
    system: str
    registry: ToolRegistry
    policy: Policy = field(default_factory=Policy)
    approver: Approver = deny_all
    audit: AuditLog = field(default_factory=lambda: AuditLog(None))
    on_event: EventHandler = _ignore
    max_steps: int = 40
    _conv: Conversation | None = field(default=None, init=False, repr=False)

    @property
    def conversation(self) -> Conversation:
        if self._conv is None:
            self._conv = self.provider.start(self.system, self.registry.tools())
        return self._conv

    def reset(self) -> None:
        self._conv = None

    def send(self, text: str) -> str:
        """Give the agent a message and run until it has a final answer."""
        conv = self.conversation
        conv.add_user(text)
        for _ in range(self.max_steps):
            result = conv.step()
            self.on_event("step", {"agent": self.name, "stop": result.stop, "usage": result.usage})
            if result.text and result.tool_calls:
                self.on_event("thought", {"agent": self.name, "text": result.text})

            if result.stop == "pause":
                continue  # server-side tool loop paused; resend to resume
            if result.stop == "refusal":
                return result.text or f"[{self.name} declined this request: {result.detail or 'no detail'}]"
            if result.stop == "max_tokens":
                # Never run tool calls from a truncated turn: their inputs may be cut off.
                return (result.text + "\n[response truncated: output limit reached]").strip()
            if result.stop == "tool_use" and result.tool_calls:
                conv.add_tool_results([self._run_tool(c) for c in result.tool_calls])
                continue
            return result.text
        return f"[{self.name} stopped after {self.max_steps} steps without finishing]"

    def _run_tool(self, call: ToolCall) -> ToolResult:
        def done(content: str, is_error: bool = False, outcome: str = "ok") -> ToolResult:
            self.audit.record(agent=self.name, tool=call.name, input=call.input, outcome=outcome, error=is_error)
            self.on_event("tool", {"agent": self.name, "tool": call.name, "input": call.input, "outcome": outcome})
            return ToolResult(call.id, content, is_error)

        tool = self.registry.get(call.name)
        if tool is None:
            return done(f"Unknown tool '{call.name}'.", True, "unknown_tool")
        if call.parse_error:
            return done(call.parse_error, True, "invalid_input")
        err = validate_input(tool.input_schema, call.input)
        if err:
            return done(f"Invalid input: {err}", True, "invalid_input")

        decision = self.policy.decide(tool)
        if decision is Decision.DENY:
            return done("Blocked by company policy. Do not retry; tell the user.", True, "denied_by_policy")
        if decision is Decision.ASK and not self.approver(tool, call.input):
            return done("The user did not approve this action. Do not retry unless asked.", True, "declined_by_user")

        try:
            return done(tool.run(call.input))
        except Exception as e:  # tool failures go back to the model, not up the stack
            return done(f"{type(e).__name__}: {e}", True, "error")
