"""Wire config, provider, integrations, policy and delegates into a ready Gabe."""

from __future__ import annotations

from gabe.agent import Agent, EventHandler, _ignore
from gabe.audit import AuditLog
from gabe.config import Config
from gabe.integrations import load_integrations
from gabe.policy import Approver, Policy, deny_all
from gabe.prompts import chief_of_staff_prompt, delegate_prompt
from gabe.providers import Provider, make_provider
from gabe.tools.registry import Risk, Tool, ToolRegistry, schema


def build_gabe(
    cfg: Config,
    approver: Approver = deny_all,
    on_event: EventHandler = _ignore,
    provider: Provider | None = None,
) -> Agent:
    provider = provider or make_provider(cfg.provider)
    policy = Policy.from_config(cfg.policy)
    audit = AuditLog(cfg.data_dir / "audit.jsonl")

    registry = ToolRegistry()
    load_integrations(cfg, registry)
    # With specialists, Gabe is a dispatcher: it keeps a few lookup tools and the
    # delegate tool, and every other action goes through a specialist.
    own = registry.subset([n for n in cfg.own_tools if registry.get(n)]) if cfg.delegates else registry

    if cfg.delegates:
        specs = {d.name: d for d in cfg.delegates}

        def delegate(specialist: str, task: str) -> str:
            spec = specs[specialist]
            sub = Agent(
                name=spec.name,
                provider=provider,
                system=delegate_prompt(cfg, spec),
                registry=registry.subset(spec.tools),
                policy=policy,
                approver=approver,
                audit=audit,
                on_event=on_event,
            )
            return sub.send(task)

        own.register(
            Tool(
                name="delegate",
                description=(
                    "Hand a task to a specialist assistant and get its report back. This is how "
                    "you get almost everything done. The specialist cannot see this conversation: "
                    "include every detail it needs."
                ),
                input_schema=schema(
                    {
                        "specialist": {"type": "string", "enum": sorted(specs)},
                        "task": {"type": "string", "description": "Complete instructions and context."},
                    }
                ),
                handler=delegate,
                # Delegation itself is safe; the specialist's own tool calls are policy-gated.
                risk=Risk.READ,
                integration="delegation",
            )
        )

    return Agent(
        name=cfg.assistant_name,
        provider=provider,
        system=chief_of_staff_prompt(cfg, dispatcher=bool(cfg.delegates)),
        registry=own,
        policy=policy,
        approver=approver,
        audit=audit,
        on_event=on_event,
    )
