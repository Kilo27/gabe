"""Company configuration. Everything that differs between companies lives here
(or in playbooks/integrations it points to), never in Gabe's code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class IntegrationSpec:
    name: str  # built-in name ("tasks") or dotted module path ("acme_gabe.crm")
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class DelegateSpec:
    name: str
    description: str
    instructions: str
    tools: list[str] | None = None  # None = every tool Gabe has (except delegation)


@dataclass
class Config:
    root: Path
    assistant_name: str = "Gabe"
    company: dict[str, Any] = field(default_factory=dict)
    provider: dict[str, Any] = field(default_factory=lambda: {"kind": "anthropic"})
    data_dir: Path = Path("data")
    playbooks_dir: Path | None = None
    integrations: list[IntegrationSpec] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)
    delegates: list[DelegateSpec] = field(default_factory=list)
    briefing: dict[str, Any] = field(default_factory=dict)
    pricing: dict[str, dict[str, float]] = field(default_factory=dict)  # model -> $/M tokens

    def playbooks(self) -> list[tuple[str, str]]:
        if not self.playbooks_dir or not self.playbooks_dir.is_dir():
            return []
        return [(p.stem, p.read_text().strip()) for p in sorted(self.playbooks_dir.glob("*.md"))]


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    path = Path(path or os.environ.get("GABE_CONFIG", "config/gabe.yaml"))
    raw = yaml.safe_load(path.read_text()) or {}
    root = path.resolve().parent

    def rel(p: str | None) -> Path | None:
        if p is None:
            return None
        q = Path(p)
        return q if q.is_absolute() else (root / q).resolve()

    integrations = []
    for item in raw.get("integrations") or []:
        if isinstance(item, str):
            integrations.append(IntegrationSpec(item))
        else:
            integrations.append(IntegrationSpec(item["name"], item.get("settings") or {}))

    delegates = [
        DelegateSpec(
            name=name,
            description=spec.get("description", ""),
            instructions=spec.get("instructions", ""),
            tools=spec.get("tools"),
        )
        for name, spec in (raw.get("delegates") or {}).items()
    ]

    return Config(
        root=root,
        assistant_name=(raw.get("assistant") or {}).get("name", "Gabe"),
        company=raw.get("company") or {},
        provider=raw.get("provider") or {"kind": "anthropic"},
        data_dir=rel(raw.get("data_dir", "../data")),
        playbooks_dir=rel(raw.get("playbooks_dir")),
        integrations=integrations,
        policy=raw.get("policy") or {},
        delegates=delegates,
        briefing=raw.get("briefing") or {},
        pricing=raw.get("pricing") or {},
    )
