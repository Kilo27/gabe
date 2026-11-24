"""Wire config, provider, integrations, policy and delegates into a ready Gabe."""

from __future__ import annotations

from gabe.agent import Agent, EventHandler, _ignore
from gabe.audit import AuditLog
from gabe.config import Config, DelegateSpec
from gabe.integrations import load_integrations
from gabe.policy import Approver, Decision, Policy, deny_all
from gabe.prompts import chief_of_staff_prompt, delegate_prompt
from gabe.providers import Provider, make_provider
from gabe.tools.registry import Risk, Tool, ToolRegistry, schema

PAUSED = "The {name} specialist is paused by a person. Don't retry; tell the user it's paused."


def specialist_policy(base: Policy, spec: DelegateSpec) -> Policy:
    """Company policy with the specialist's own per-action rules on top."""
    return Policy(
        defaults=dict(base.defaults),
        tool_overrides={**base.tool_overrides, **{k: Decision(v) for k, v in spec.policy.items()}},
        integration_overrides=dict(base.integration_overrides),
    )


def build_specialist(
    cfg: Config,
    spec: DelegateSpec,
    registry: ToolRegistry,
    provider: Provider,
    policy: Policy,
    approver: Approver,
    audit: AuditLog,
    on_event: EventHandler = _ignore,
) -> Agent:
    return Agent(
        name=spec.name,
        provider=provider,
        system=delegate_prompt(cfg, spec),
        # Skip actions this setup doesn't have (e.g. Gmail-only actions in practice mode).
        registry=registry.subset(
            [t.name for t in registry.tools() if t.name != "delegate"] if spec.tools is None
            else [t for t in spec.tools if registry.get(t)]
        ),
        policy=specialist_policy(policy, spec),
        approver=approver,
        audit=audit,
        on_event=on_event,
    )


def build_gabe(
    cfg: Config,
    approver: Approver = deny_all,
    on_event: EventHandler = _ignore,
    provider: Provider | None = None,
    specialist: str | None = None,
) -> Agent:
    """Gabe, the chief of staff. With `specialist`, that specialist on its own instead."""
    provider = provider or make_provider(cfg.provider)
    policy = Policy.from_config(cfg.policy)
    audit = AuditLog(cfg.data_dir / "audit.jsonl")

    registry = ToolRegistry()
    load_integrations(cfg, registry)
    specs = {d.name: d for d in cfg.delegates}

    if specialist is not None:
        return build_specialist(cfg, specs[specialist], registry, provider, policy, approver, audit, on_event)

    if specs:

        def delegate(specialist: str, task: str) -> str:
            spec = specs[specialist]
            if spec.paused:
                return PAUSED.format(name=spec.name)
            sub = build_specialist(cfg, spec, registry, provider, policy, approver, audit, on_event)
            on_event("delegate_start", {"agent": cfg.assistant_name, "specialist": spec.name, "task": task})
            try:
                return sub.send(task)
            finally:
                on_event("delegate_end", {"agent": cfg.assistant_name, "specialist": spec.name})

        registry.register(
            Tool(
                name="delegate",
                description=(
                    "Hand a self-contained task to a specialist assistant and get its report back. "
                    "The specialist cannot see this conversation: include every detail it needs."
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
        system=chief_of_staff_prompt(cfg),
        registry=registry,
        policy=policy,
        approver=approver,
        audit=audit,
        on_event=on_event,
    )
