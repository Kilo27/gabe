"""Integrations plug capabilities into Gabe.

An integration is any Python module that defines:

    def setup(registry: ToolRegistry, ctx: IntegrationContext) -> None

and registers its tools on the registry. Built-in integrations live in this
package and are referenced by short name ("tasks"); a company's own
integrations are referenced by dotted module path ("acme_gabe.crm") and only
need to be importable. That is how Gabe adapts to each company's existing
systems without changes to the core.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gabe.config import Config
from gabe.tools.registry import ToolRegistry


@dataclass
class IntegrationContext:
    name: str
    settings: dict[str, Any]
    data_dir: Path
    config: Config
    state: dict[str, Any] = field(default_factory=dict)

    def path(self, *parts: str) -> Path:
        """A per-integration storage location under the data dir."""
        p = self.data_dir.joinpath(self.name, *parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def load_integrations(cfg: Config, registry: ToolRegistry) -> None:
    for spec in cfg.integrations:
        module_path = spec.name if "." in spec.name else f"gabe.integrations.{spec.name}"
        module = importlib.import_module(module_path)
        if not hasattr(module, "setup"):
            raise TypeError(f"integration '{spec.name}' has no setup(registry, ctx) function")
        ctx = IntegrationContext(
            name=spec.name.rsplit(".", 1)[-1],
            settings=spec.settings,
            data_dir=cfg.data_dir,
            config=cfg,
        )
        module.setup(registry, ctx)
