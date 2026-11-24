from __future__ import annotations

from pathlib import Path

import pytest

from gabe.providers.base import Conversation, Provider, StepResult, ToolResult


class ScriptedConversation(Conversation):
    def __init__(self, script, log):
        self.script = script
        self.log = log

    def add_user(self, text):
        self.log.append(("user", text))

    def add_tool_results(self, results: list[ToolResult]):
        self.log.append(("tool_results", results))

    def step(self) -> StepResult:
        item = self.script.pop(0)
        if isinstance(item, BaseException):  # lets a test make a model call fail
            raise item
        return item


class ScriptedProvider(Provider):
    """Returns pre-baked StepResults in order; records what the agent sent."""

    def __init__(self, script):
        self.script = list(script)
        self.log = []
        self.systems = []

    def start(self, system, tools):
        self.systems.append((system, [t.name for t in tools]))
        return ScriptedConversation(self.script, self.log)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    (tmp_path / "playbooks").mkdir()
    (tmp_path / "playbooks" / "triage.md").write_text("Triage inbox daily.")
    cfg = tmp_path / "gabe.yaml"
    cfg.write_text(
        """
company: {name: TestCo, timezone: UTC}
data_dir: data
playbooks_dir: playbooks
integrations: [core, memory, tasks, {name: email, settings: {from_address: g@test.co}}]
policy:
  tools: {forget: deny}
delegates:
  scheduler:
    description: tasks person
    instructions: Manage tasks.
    tools: [list_tasks, create_task]
"""
    )
    return cfg
