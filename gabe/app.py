"""Wire config, provider, integrations, policy and the team into a ready Gabe."""

from __future__ import annotations

from gabe.agent import Agent, EventHandler, _ignore
from gabe.audit import AuditLog
from gabe.config import Config
from gabe.integrations import load_integrations
from gabe.policy import Approver, Policy, deny_all
from gabe.prompts import chief_of_staff_prompt, delegate_prompt
from gabe.providers import Provider, make_provider
from gabe.team import CHIEF_ID, Member, Team
from gabe.tools.registry import Risk, Tool, ToolRegistry, schema
from gabe.usage import UsageLog, resolve_user


def base_registry(cfg: Config) -> ToolRegistry:
    """Every tool the company's integrations provide (no delegation)."""
    registry = ToolRegistry()
    load_integrations(cfg, registry)
    return registry


def build_gabe(
    cfg: Config,
    approver: Approver = deny_all,
    on_event: EventHandler = _ignore,
    provider: Provider | None = None,
    team: Team | None = None,
    user: str | None = None,
    task: str = "chat",
) -> Agent:
    provider = provider or make_provider(cfg.provider)
    policy = Policy.from_config(cfg.policy)
    audit = AuditLog(cfg.data_dir / "audit.jsonl")
    tools = base_registry(cfg)
    team = team if team is not None else cfg.team()
    available = {t.name for t in tools.tools()}
    usage = UsageLog(cfg.data_dir / "usage.jsonl")
    user = resolve_user(user)
    job = task  # the delegate tool's own `task` argument is the team member's instructions

    def skills_of(member: Member) -> list[str] | None:
        # Skills whose integration isn't set up right now (e.g. email in outbox mode) are skipped,
        # not fatal; the Team page flags them as unavailable.
        return None if member.skills is None else [s for s in member.skills if s in available]

    def with_delegation(registry: ToolRegistry, manager_id: str, path: tuple[str, ...]) -> ToolRegistry:
        """Add a `delegate` tool that reaches only this agent's own active direct reports."""
        reports = {m.id: m for m in team.reports(manager_id)}
        if not reports:
            return registry

        def delegate(specialist: str, task: str) -> str:
            member = reports[specialist]
            on_event("delegate", {"agent": path[-1], "path": list(path), "to": member.id,
                                  "to_name": member.name, "task": task})
            report = build_member(member, path).send(task)
            on_event("report", {"agent": member.name, "path": [*path, member.name], "text": report})
            return report

        registry.register(
            Tool(
                name="delegate",
                description=(
                    "Hand a self-contained task to a member of your team and get their report back. "
                    "They cannot see this conversation: include every detail they need."
                ),
                input_schema=schema(
                    {
                        "specialist": {"type": "string", "enum": sorted(reports),
                                       "description": "Id of the team member to hand the task to."},
                        "task": {"type": "string", "description": "Complete instructions and context."},
                    }
                ),
                handler=delegate,
                # Handing off is safe in itself; every action the team member takes is policy-gated.
                risk=Risk.READ,
                integration="delegation",
            )
        )
        return registry

    def build_member(member: Member, manager_path: tuple[str, ...]) -> Agent:
        path = (*manager_path, member.name)
        manager = manager_path[-1]
        return Agent(
            name=member.name,
            provider=provider,
            system=delegate_prompt(cfg, member, manager, team.reports(member.id)),
            registry=with_delegation(tools.subset(skills_of(member)), member.id, path),
            policy=policy.restricted(member.freedom_level.ceiling),
            approver=approver,
            audit=audit,
            on_event=on_event,
            path=path,
            usage=usage,
            user=user,
            task=job,
        )

    top = (cfg.assistant_name,)
    return Agent(
        name=cfg.assistant_name,
        provider=provider,
        system=chief_of_staff_prompt(cfg, team.reports(CHIEF_ID)),
        registry=with_delegation(tools.subset(None), CHIEF_ID, top),
        policy=policy,
        approver=approver,
        audit=audit,
        on_event=on_event,
        path=top,
        usage=usage,
        user=user,
        task=task,
    )
