"""Wire config, provider, integrations, policy and the team into a ready Gabe."""

from __future__ import annotations

from typing import Callable

from gabe.agent import Agent, EventHandler, _ignore
from gabe.audit import AuditLog
from gabe.config import Config
from gabe.integrations import load_integrations
from gabe.policy import Approver, Policy, deny_all
from gabe.prompts import chief_of_staff_prompt, delegate_prompt
from gabe.providers import Provider, make_provider
from gabe.team import (CHIEF_ID, DEFAULT_FREEDOM, DEFAULT_TIER, FREEDOM_ORDER, MODEL_TIERS, Member, Team,
                       TeamError, ensure_team_file, load_team, make_id, save_team)
from gabe.tools.registry import Risk, Tool, ToolRegistry, schema
from gabe.usage import UsageLog, resolve_user


HIRE = "add_team_member"  # the action that creates a new bot; a skill like any other


def team_skills(cfg: Config) -> list[str]:
    """Every action a team member can be given."""
    return sorted([t.name for t in base_registry(cfg).tools()] + [HIRE])


_GENERIC = {"helper", "assistant", "agent", "team", "member", "specialist", "lead", "manager", "keeper",
            "writer", "mapper", "scoper", "owner", "with", "that", "from", "into", "their", "they", "them",
            "about", "which", "what", "keeps", "handles", "work", "tasks", "project", "using", "including",
            "plans", "makes", "sure", "each", "every", "other", "company"}


def _words(text: str) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 4 and w not in _GENERIC}


def overlaps(name: str, job: str, member: Member) -> bool:
    """Rough check that a proposed member covers the same ground as an existing one."""
    a, b = _words(name), _words(member.name)
    if a and b and len(a & b) / min(len(a), len(b)) >= 0.5:
        return True
    ja, jb = _words(f"{name} {job}"), _words(f"{member.name} {member.job}")
    return bool(ja and jb) and len(ja & jb) / len(ja | jb) >= 0.35


# Called when an agent asks the user something: (agent name, question, choices) -> the answer.
Asker = Callable[[str, str, list], str]

NOBODY_TO_ASK = ("Nobody is available to answer right now. Make a reasonable assumption, say what you "
                 "assumed, and carry on.")


def no_one_to_ask(agent: str, question: str, choices: list) -> str:
    """Asker for unattended runs."""
    return NOBODY_TO_ASK


def run_handed_off(agent: Agent, task: str, on_event: EventHandler, by: tuple[str, ...]) -> str:
    """Run a job handed down to a team member. A failure is announced (so it is never silent)
    and raised, so the manager gets it back as an error to deal with."""
    try:
        report = agent.send(task)
    except Exception as e:
        on_event("failed", {"agent": agent.name, "path": list(agent.path), "by": by[-1], "task": task,
                            "error": f"{e}" or type(e).__name__})
        raise
    on_event("report", {"agent": agent.name, "path": list(agent.path), "text": report})
    return report


def ask_tool(name: str, asker: Asker) -> Tool:
    """`ask_user`: put a question to the person in charge and wait for their answer."""
    def ask_user(question: str, choices: list | None = None) -> str:
        answer = asker(name, question.strip(), [str(c) for c in (choices or []) if str(c).strip()])
        return answer if answer.strip() else "(The user replied without an answer.)"

    return Tool(
        name="ask_user",
        description=(
            "Ask the user a question and wait for their answer. They are notified. Use it only when "
            "you're blocked on something only a person can decide or tell you; one clear question, "
            "with choices if there are obvious options. Never use it to confirm an action: actions "
            "that need a person's OK (like sending email) already ask for it."
        ),
        input_schema=schema(
            {
                "question": {"type": "string", "description": "The question, complete enough to answer cold."},
                "choices": {"type": "array", "items": {"type": "string"},
                            "description": "Optional quick answers to offer (they can still type their own)."},
            },
            required=["question"],
        ),
        handler=ask_user,
        risk=Risk.READ,
        integration="people",
    )


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
    asker: Asker = no_one_to_ask,
    scheduling: bool = False,
) -> Agent:
    """Gabe, the chief of staff. With `member`, that team member on its own (a job given to
    it directly), still able to hand work to its own reports. With `scheduling`, Gabe can set
    up recurring jobs (gabe/schedules.py); only the app runs them, so only the app turns it on."""
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

    def tiers_up_to(tier: str | None) -> list[str]:
        """Models an agent may hand to others: up to its own (Gabe, with None: any)."""
        return MODEL_TIERS[: MODEL_TIERS.index(tier) + 1] if tier else list(MODEL_TIERS)

    def hire_tool(creator: Member | None, path: tuple[str, ...], creator_tier: str | None = None) -> Tool:
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
        tiers = tiers_up_to(creator_tier)

        def where(manager: str) -> tuple[str, ...]:
            """The chain of names from this agent down to `manager` (for events and the audit)."""
            chain = [m.name for m in reversed(team.chain(manager)) if m.id not in team_ids_above(creator_id)]
            return (*path, *chain) if manager != creator_id else path

        def team_ids_above(member_id: str) -> set[str]:
            return {m.id for m in team.chain(member_id)} if member_id != CHIEF_ID else set()

        def add_team_member(name: str, job: str, instructions: str, skills: list[str], reports_to: str,
                            why: str, first_task: str, freedom: str = DEFAULT_FREEDOM,
                            model: str | None = None) -> str:
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
            model = model or None  # None: its manager picks a model for each job
            if model is not None and model not in tiers:
                raise ValueError(f"Model must be one of: {', '.join(tiers)}. Not added.")
            if not first_task.strip():
                raise ValueError("Give it a first_task to start on now, so it doesn't sit idle. Not added.")
            # Work that belongs to an existing member's area goes under that member, not beside it.
            near = [m for m in team.members if m.active and overlaps(name, job, m)]
            outside = [m for m in near if manager != m.id and manager not in branch(m.id)]
            if outside:
                m = outside[0]
                raise ValueError(
                    f"This overlaps {m.name} ({m.id}: {m.job}). Give {m.name} the work with delegate, "
                    f"or add the new member under them (reports_to: {m.id}). Not added.")
            new = Member(id="", name=name.strip(), job=job.strip(), instructions=instructions.strip(),
                         reports_to=manager, skills=sorted(set(skills)), freedom=freedom, model=model)
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
            team.members[:] = current.members
            on_event("hired", {"agent": path[-1], "path": list(path), "member": new.id, "name": new.name,
                               "reports_to": manager, "model": model})
            boss = "you" if manager == creator_id else (team.get(manager).name if team.get(manager) else manager)

            # It starts work straight away, handed down from its manager, so it never sits idle.
            mpath = where(manager)
            on_event("delegate", {"agent": mpath[-1], "path": list(mpath), "to": new.id,
                                  "to_name": new.name, "task": first_task})
            try:
                report = run_handed_off(build_member(new, mpath, cap=creator_tier), first_task, on_event, mpath)
            except Exception as e:
                raise RuntimeError(f"Added {new.name} (id {new.id}), but its first task failed: {e}") from e
            return (f"Added {new.name} (id {new.id}, on {model or 'a model picked per job'}), reporting to {boss}. It started on its first task "
                    f"and reported back:\n{report}")

        roster = "\n".join(f"- {m.id}: {m.name} (reports to {m.reports_to}, {m.model or 'model picked per job'}). {m.job}"
                           for m in team.members if m.active and (creator is None or m.id in branch(creator_id)))
        return Tool(
            name=HIRE,
            description=(
                "Create a new AI assistant for a lasting ROLE that keeps getting work and that no current "
                "member covers. Not for a single task or a project's to-do list: for those, delegate to the "
                "closest existing member. Put it under the member whose area the work belongs to (e.g. "
                "email work under whoever does communications); report to yourself only when it starts a "
                "genuinely new area. Write instructions it can act on alone (it can ask the user with "
                "ask_user only when truly blocked). It starts on first_task straight away. A person "
                "approves it first.\nCurrent team (id: name (reports to). job):\n" + (roster or "- nobody yet")
            ),
            input_schema=schema(
                {
                    "name": {"type": "string", "description": "Role name, e.g. 'Invoice chaser'. A role, not a task."},
                    "job": {"type": "string", "description": "One line: the ongoing work it owns. Managers use this to pick it."},
                    "instructions": {"type": "string", "description": "How it does the job, in plain sentences it can follow alone."},
                    "skills": {"type": "array", "items": {"type": "string"},
                               "description": f"Actions it may use, from: {', '.join(sorted(grantable))}."},
                    "reports_to": {"type": "string",
                                   "description": f"Id of the member whose area this belongs to, or '{creator_id}' for a new area."},
                    "why": {"type": "string", "description": "For the person approving: why this needs a new member, and why it sits there."},
                    "first_task": {"type": "string", "description": "The work it should start on now, with every detail it needs."},
                    "freedom": {"type": "string", "enum": levels,
                                "description": "look_only = can't change anything; ask_first = asks before changes; standard = asks before contacting anyone outside."},
                    "model": {"type": "string", "enum": tiers,
                              "description": "Fix its model when every job it gets needs the same: haiku for most "
                                             "roles (lookups, sorting, routine updates, simple drafts), sonnet when the "
                                             "role needs judgement or careful writing, opus only for high-priority, "
                                             "genuinely difficult work. Leave out to pick a model each time you delegate."},
                },
                required=["name", "job", "instructions", "skills", "reports_to", "why", "first_task"],
            ),
            handler=add_team_member,
            risk=Risk.WRITE,
            integration="team",
            ask_first=True,  # the people in charge approve every new bot
        )

    def with_delegation(registry: ToolRegistry, manager_id: str, path: tuple[str, ...],
                        hires: bool = False, own_tier: str | None = None) -> ToolRegistry:
        """Add a `delegate` tool that reaches this agent's own active direct reports (looked up
        when called, so someone added mid-run can be given work straight away)."""
        reports = team.reports(manager_id)
        if not reports and not hires:
            return registry

        allowed = tiers_up_to(own_tier)

        def delegate(specialist: str, task: str, model: str | None = None) -> str:
            member = team.get(specialist)
            if member is None or member.reports_to != manager_id or not team.is_working(member.id):
                ids = ", ".join(m.id for m in team.reports(manager_id)) or "nobody yet"
                return f"'{specialist}' isn't one of your active direct reports ({ids})."
            if model and model not in allowed:
                raise ValueError(f"model must be one of: {', '.join(allowed)}.")
            agent = build_member(member, path, tier=model, cap=own_tier)
            on_event("delegate", {"agent": path[-1], "path": list(path), "to": member.id,
                                  "to_name": member.name, "task": task, "model": agent.tier})
            return run_handed_off(agent, task, on_event, path)

        registry.register(
            Tool(
                name="delegate",
                description=(
                    "Hand a self-contained task to a member of your team and get their report back. "
                    "They cannot see this conversation: include every detail they need. If it fails you get "
                    "the error back: work out why and fix it (clearer instructions, a stronger model, someone "
                    "else, or doing that part yourself). Ask the user only when a person has to act."
                ),
                input_schema=schema(
                    {
                        "specialist": {"type": "string",
                                       "description": "Id of the team member to hand the task to"
                                       + (f", e.g. {', '.join(m.id for m in reports)}." if reports else ".")},
                        "task": {"type": "string", "description": "Complete instructions and context."},
                        "model": {"type": "string", "enum": allowed,
                                  "description": "For members whose model is picked per job (ignored when it's fixed): "
                                                 "haiku for most tasks, sonnet when it needs judgement or careful "
                                                 "writing, opus only for high-priority, genuinely difficult work. "
                                                 f"Default {DEFAULT_TIER}."},
                    },
                    required=["specialist", "task"],
                ),
                handler=delegate,
                # Handing off is safe in itself; every action the team member takes is policy-gated.
                risk=Risk.READ,
                integration="delegation",
            )
        )
        return registry

    def build_member(member: Member, manager_path: tuple[str, ...], tier: str | None = None,
                     cap: str | None = None) -> Agent:
        """`tier`: the model its manager picked for this job, used when the member has none fixed.
        Neither set: DEFAULT_TIER. A picked model never goes above the manager's own (`cap`)."""
        path = (*manager_path, member.name)
        manager = manager_path[-1]
        hires = can_hire(member)
        picked = tier if tier in tiers_up_to(cap) else None
        run_on = member.model or picked or DEFAULT_TIER
        registry = tools.subset(skills_of(member))
        registry.register(ask_tool(member.name, asker))
        if hires:
            registry.register(hire_tool(member, path, run_on))
        agent = Agent(
            name=member.name,
            provider=provider.for_tier(run_on),
            system=delegate_prompt(cfg, member, manager, team.reports(member.id), can_hire=hires),
            registry=with_delegation(registry, member.id, path, hires, run_on),
            policy=policy.restricted(member.freedom_level.ceiling),
            approver=approver,
            audit=audit,
            on_event=on_event,
            path=path,
            usage=usage,
            user=user,
            task=job,
            tier=run_on,
        )
        return agent

    top = (cfg.assistant_name,)
    if member is not None:
        m = team.get(member)
        if m is None or not team.is_working(member):
            raise ValueError(f"'{member}' isn't an active member of the team.")
        return build_member(m, ("the user",))
    chief_reports = team.reports(CHIEF_ID)
    registry = tools.subset(chief_tools(cfg, team, available))
    registry.register(ask_tool(cfg.assistant_name, asker))
    registry.register(hire_tool(None, top))  # Gabe can always grow the team, with a person's OK
    if scheduling:
        from gabe.schedules import schedule_tools

        for t in schedule_tools(cfg, created_by=cfg.assistant_name):
            registry.register(t)
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
