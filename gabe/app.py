"""Wire config, provider, integrations, policy and the team into a ready Gabe."""

from __future__ import annotations

from gabe.agent import Agent, EventHandler, _ignore
from gabe.audit import AuditLog
from gabe.config import Config
from gabe.integrations import load_integrations
from gabe.policy import Approver, Policy, deny_all
from gabe.prompts import chief_of_staff_prompt, delegate_prompt
from gabe.providers import Provider, make_provider
from gabe.team import (CHIEF_ID, DEFAULT_FREEDOM, FREEDOM_ORDER, Member, Team, TeamError,
                       ensure_team_file, load_team, make_id, save_team)
from gabe.tools.registry import Risk, Tool, ToolRegistry, schema
from gabe.usage import UsageLog, resolve_user


HIRE = "add_team_member"  # the action that creates a new bot; a skill like any other


def team_skills(cfg: Config) -> list[str]:
    """Every action a team member can be given."""
    return sorted([t.name for t in base_registry(cfg).tools()] + [HIRE])


def base_registry(cfg: Config) -> ToolRegistry:
    """Every tool the company's integrations provide (no delegation)."""
    registry = ToolRegistry()
    load_integrations(cfg, registry)
    return registry


def team_coverage(team: Team, available: set[str]) -> set[str]:
    """Every tool some working team member (at any level under Gabe) can use."""
    covered: set[str] = set()
    todo = [CHIEF_ID]
    while todo:
        for m in team.reports(todo.pop()):
            covered |= available if m.skills is None else set(m.skills) & available
            todo.append(m.id)
    return covered


def chief_tools(cfg: Config, team: Team, available: set[str]) -> list[str] | None:
    """With a team, Gabe is a dispatcher: it keeps its own lookup tools (assistant.own_tools)
    and hands the rest to the team. Anything no working member can do (say their manager is
    paused) stays with Gabe, so nothing becomes impossible. Without a team, Gabe has it all."""
    if not team.reports(CHIEF_ID):
        return None
    covered = team_coverage(team, available)
    return sorted(n for n in available if n in cfg.own_tools or n not in covered)


def build_gabe(
    cfg: Config,
    approver: Approver = deny_all,
    on_event: EventHandler = _ignore,
    provider: Provider | None = None,
    team: Team | None = None,
    user: str | None = None,
    task: str = "chat",
    member: str | None = None,
) -> Agent:
    """Gabe, the chief of staff. With `member`, that team member on its own (a job given to
    it directly), still able to hand work to its own reports."""
    provider = provider or make_provider(cfg.provider)
    policy = Policy.from_config(cfg.policy)
    audit = AuditLog(cfg.data_dir / "audit.jsonl")
    tools = base_registry(cfg)
    persist = team is None  # a team passed in (tests, previews) is changed in memory only
    team = team if team is not None else cfg.team()
    available = {t.name for t in tools.tools()}
    usage = UsageLog(cfg.data_dir / "usage.jsonl")
    user = resolve_user(user)
    job = task  # the delegate tool's own `task` argument is the team member's instructions

    def skills_of(member: Member) -> list[str] | None:
        # Skills whose integration isn't set up right now (e.g. email in outbox mode) are skipped,
        # not fatal; they're simply left out.
        return None if member.skills is None else [s for s in member.skills if s in available]

    def can_hire(member: Member | None) -> bool:
        return member is None or member.skills is None or HIRE in member.skills

    def branch(manager_id: str) -> set[str]:
        """Everyone under a manager, at any depth."""
        out, todo = set(), [manager_id]
        while todo:
            for m in team.reports(todo.pop(), active_only=False):
                out.add(m.id)
                todo.append(m.id)
        return out

    def hire_tool(creator: Member | None, path: tuple[str, ...]) -> Tool:
        """`add_team_member` for one agent. It can only add bots under itself or under someone in
        its own branch, give them actions it has itself, and no more freedom than it has."""
        creator_id = creator.id if creator else CHIEF_ID
        if creator is None or creator.skills is None:
            grantable = available | {HIRE}
        else:
            grantable = set(creator.skills) & (available | {HIRE})
        cap = FREEDOM_ORDER.index("standard" if creator is None else min(
            creator.freedom, "standard", key=FREEDOM_ORDER.index))
        levels = FREEDOM_ORDER[: cap + 1]

        def add_team_member(name: str, job: str, instructions: str, skills: list[str],
                            reports_to: str = "", freedom: str = DEFAULT_FREEDOM) -> str:
            manager = reports_to.strip() or creator_id
            # Refusals raise, so they show as failed (not done) in the activity log.
            if manager != creator_id and manager not in branch(creator_id):
                raise ValueError(f"You can only add team members under yourself or someone in your team "
                                 f"({', '.join(sorted(branch(creator_id))) or 'nobody yet'}). Not added.")
            extra = sorted(set(skills) - grantable)
            if extra:
                raise ValueError(f"You can't give actions you don't have yourself: {', '.join(extra)}. Not added.")
            if freedom not in levels:
                raise ValueError(f"Freedom must be one of: {', '.join(levels)}. Not added.")
            new = Member(id="", name=name.strip(), job=job.strip(), instructions=instructions.strip(),
                         reports_to=manager, skills=sorted(set(skills)), freedom=freedom)
            try:
                if persist:
                    file = ensure_team_file(cfg)
                    current = load_team(file)
                else:
                    current = Team(list(team.members))
                new.id = make_id(new.name, {m.id for m in current.members})
                # The new member's actions were checked above; others may list actions from a
                # connection that isn't set up right now, which is allowed.
                current.upsert(new)
                if persist:
                    save_team(file, current)
            except TeamError as e:
                raise ValueError(f"Not added: {e}") from e
            team.members[:] = current.members  # so the new member can get work in this same run
            on_event("hired", {"agent": path[-1], "path": list(path), "member": new.id, "name": new.name,
                               "reports_to": manager})
            boss = "you" if manager == creator_id else (team.get(manager).name if team.get(manager) else manager)
            how = f" Hand it work with delegate (specialist: {new.id})." if manager == creator_id else ""
            return f"Added {new.name} (id {new.id}), reporting to {boss}.{how}"

        return Tool(
            name=HIRE,
            description=(
                "Create a new AI assistant on the team when nobody fits the work. It reports to you, "
                "or to someone in your team (reports_to = their id). Give it only the actions it needs. "
                "A person approves it first."
            ),
            input_schema=schema(
                {
                    "name": {"type": "string", "description": "Short role name, e.g. 'Invoice chaser'."},
                    "job": {"type": "string", "description": "One line: what it's for. Managers use this to pick it."},
                    "instructions": {"type": "string", "description": "How it should do the job, in plain sentences."},
                    "skills": {"type": "array", "items": {"type": "string"},
                               "description": f"Actions it may use, from: {', '.join(sorted(grantable))}."},
                    "reports_to": {"type": "string", "description": "Id of its manager. Default: you."},
                    "freedom": {"type": "string", "enum": levels,
                                "description": "look_only = can't change anything; ask_first = asks before changes; standard = asks before contacting anyone outside."},
                },
                required=["name", "job", "instructions", "skills"],
            ),
            handler=add_team_member,
            risk=Risk.WRITE,
            integration="team",
            ask_first=True,  # the people in charge approve every new bot
        )

    def with_delegation(registry: ToolRegistry, manager_id: str, path: tuple[str, ...],
                        hires: bool = False) -> ToolRegistry:
        """Add a `delegate` tool that reaches this agent's own active direct reports (looked up
        when called, so someone added mid-run can be given work straight away)."""
        reports = team.reports(manager_id)
        if not reports and not hires:
            return registry

        def delegate(specialist: str, task: str) -> str:
            member = team.get(specialist)
            if member is None or member.reports_to != manager_id or not team.is_working(member.id):
                ids = ", ".join(m.id for m in team.reports(manager_id)) or "nobody yet"
                return f"'{specialist}' isn't one of your active direct reports ({ids})."
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
                        "specialist": {"type": "string",
                                       "description": "Id of the team member to hand the task to"
                                       + (f", e.g. {', '.join(m.id for m in reports)}." if reports else ".")},
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
        hires = can_hire(member)
        registry = tools.subset(skills_of(member))
        if hires:
            registry.register(hire_tool(member, path))
        return Agent(
            name=member.name,
            provider=provider,
            system=delegate_prompt(cfg, member, manager, team.reports(member.id), can_hire=hires),
            registry=with_delegation(registry, member.id, path, hires),
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
    if member is not None:
        m = team.get(member)
        if m is None or not team.is_working(member):
            raise ValueError(f"'{member}' isn't an active member of the team.")
        return build_member(m, ("the user",))
    chief_reports = team.reports(CHIEF_ID)
    registry = tools.subset(chief_tools(cfg, team, available))
    registry.register(hire_tool(None, top))  # Gabe can always grow the team, with a person's OK
    return Agent(
        name=cfg.assistant_name,
        provider=provider,
        system=chief_of_staff_prompt(cfg, chief_reports, dispatcher=bool(chief_reports), can_hire=True),
        registry=with_delegation(registry, CHIEF_ID, top, hires=True),
        policy=policy,
        approver=approver,
        audit=audit,
        on_event=on_event,
        path=top,
        usage=usage,
        user=user,
        task=task,
    )
