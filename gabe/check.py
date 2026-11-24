"""`gabe check`: is this config folder one Gabe can run with?

Run after any hand (or Claude Code) edit. Problems stop Gabe from working as intended;
warnings are worth a look but Gabe runs regardless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tools: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def text(self) -> str:
        lines = [f"Problem: {p}" for p in self.problems] + [f"Warning: {w}" for w in self.warnings]
        if self.ok:
            lines.append(f"OK: {self.tools} actions available.")
        return "\n".join(lines)


def check_config(path: str | Path) -> Report:
    from gabe.app import HIRE
    from gabe.config import load_config
    from gabe.integrations import load_integrations
    from gabe.policy import Policy
    from gabe.tools.registry import ToolRegistry

    report = Report()
    try:
        cfg = load_config(path)
    except Exception as e:
        report.problems.append(f"{Path(path).name} can't be read: {type(e).__name__}: {e}")
        return report

    try:
        Policy.from_config(cfg.policy)
    except Exception as e:
        report.problems.append(f"policy: {type(e).__name__}: {e} (decisions are allow, ask or deny; "
                               "risks are read, write or external)")

    registry, errors = ToolRegistry(), {}
    load_integrations(cfg, registry, errors)
    for name, err in errors.items():
        report.problems.append(f"integration {name}: {err}")
    available = {t.name for t in registry.tools()}
    report.tools = len(available)

    try:
        team = cfg.team()
    except Exception as e:
        report.problems.append(f"team: {type(e).__name__}: {e}")
    else:
        for m in team.members:
            unknown = sorted(set(m.skills or []) - available - {HIRE})
            if unknown:
                report.warnings.append(f"team member {m.id} has actions that aren't available now: "
                                       f"{', '.join(unknown)}")

    for name in list((cfg.policy or {}).get("tools") or {}):
        if name not in available and name not in ("delegate", HIRE):
            report.warnings.append(f"policy.tools names an action that isn't available: {name}")

    try:
        from gabe.integrations import mcp

        for name, st in mcp.status().items():
            if st["state"] != "connected" and name in _mcp_servers(cfg):
                report.warnings.append(f"MCP server {name} isn't connected: {st['error']}")
    except Exception:
        pass
    return report


def _mcp_servers(cfg) -> set[str]:
    for spec in cfg.integrations:
        if spec.name == "mcp":
            return set((spec.settings.get("servers") or {}))
    return set()
