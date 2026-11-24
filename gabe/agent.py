"""The agent loop: model step -> policy-gated tool execution -> repeat."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from gabe.audit import AuditLog
from gabe.policy import Approver, Decision, Policy, deny_all
from gabe.providers.base import Conversation, Provider, StepResult, ToolCall, ToolResult
from gabe.tools.registry import ToolRegistry, validate_input
from gabe.usage import UsageLog

EventHandler = Callable[[str, dict[str, Any]], None]


def _ignore(kind: str, data: dict[str, Any]) -> None:
    pass


FAILED_MARK = re.compile(r"\n?\s*\**JOB FAILED:\**\s*(.+?)\s*$")


class Unfinished(RuntimeError):
    """The agent stopped without finishing the job (declined, cut off, ran out of steps).
    Raised, never returned as an answer, so it can't pass for a finished job."""

    def __init__(self, message: str, partial: str = "") -> None:
        super().__init__(message)
        self.partial = partial.strip()


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
    usage: UsageLog = field(default_factory=lambda: UsageLog(None))
    user: str = "unknown"  # who the work is for, for usage reporting
    task: str = "chat"  # what kind of work (chat, brief, ...), for usage reporting
    max_steps: int = 40
    # Names from the top of the hierarchy down to this agent, e.g. ("Gabe", "Operations lead").
    path: tuple[str, ...] = ()
    tier: str | None = None  # the model tier a team member runs on (None for Gabe)
    _conv: Conversation | None = field(default=None, init=False, repr=False)

    @property
    def conversation(self) -> Conversation:
        if self._conv is None:
            self._conv = self.provider.start(self.system, self.registry.tools())
        return self._conv

    def __post_init__(self) -> None:
        if not self.path:
            self.path = (self.name,)

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        self.on_event(kind, {"agent": self.name, "path": list(self.path), **data})

    def reset(self) -> None:
        self._conv = None

    def send(self, text: str) -> str:
        """Give the agent a message and run until it has a final answer."""
        conv = self.conversation
        conv.add_user(text)
        for _ in range(self.max_steps):
            result = conv.step()
            self.usage.record(user=self.user, task=self.task, agent=self.name, usage=result.usage)
            self.emit("step", {"stop": result.stop, "usage": result.usage})
            if result.text and result.tool_calls:
                self.emit("thought", {"text": result.text})

            if result.stop == "pause":
                continue  # server-side tool loop paused; resend to resume
            if result.stop == "refusal":
                raise Unfinished(f"{self.name} declined this request ({result.detail or 'no detail given'}).",
                                 result.text)
            if result.stop == "max_tokens":
                # Never run tool calls from a truncated turn: their inputs may be cut off.
                raise Unfinished(f"{self.name}'s answer was cut off at the output limit.", result.text)
            if result.stop == "tool_use" and result.tool_calls:
                conv.add_tool_results([self._run_tool(c) for c in result.tool_calls])
                continue
            # An agent that couldn't do the job says so on its last line (see prompts.OUTCOME_NOTE).
            failed = FAILED_MARK.search(result.text or "")
            if failed:
                raise Unfinished(f"{self.name} couldn't do it: {failed.group(1)}",
                                 result.text[: failed.start()])
            return result.text
        raise Unfinished(f"{self.name} stopped after {self.max_steps} steps without finishing.")

    def _run_tool(self, call: ToolCall) -> ToolResult:
        def done(content: str, is_error: bool = False, outcome: str = "ok") -> ToolResult:
            self.audit.record(
                agent=self.name, chain=" › ".join(self.path), tool=call.name, input=call.input,
                outcome=outcome, error=is_error,
            )
            self.emit("tool", {"tool": call.name, "input": call.input, "outcome": outcome})
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
        if decision is Decision.ASK:
            self.emit("approval_needed", {"tool": call.name, "input": call.input})
            verdict = self.approver(tool, call.input)
            if not verdict:
                msg = "The user did not approve this action. Do not retry unless asked."
                note = getattr(verdict, "note", "")
                if note:
                    msg += f" Their note: {note}"
                return done(msg, True, "declined_by_user")

        try:
            return done(tool.run(call.input))
        except Exception as e:  # tool failures go back to the model, not up the stack
            return done(f"{type(e).__name__}: {e}\nRetrying the same call will fail the same way unless "
                        f"something has changed.", True, "error")
