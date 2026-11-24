"""Integrations plug capabilities into Gabe.

An integration is any Python module that defines:

    def setup(registry: ToolRegistry, ctx: IntegrationContext) -> None

and registers its tools on the registry. Built-in integrations live in this
package and are referenced by short name ("tasks"); a company's own
integrations are either a file in the config folder (`integrations/crm.py`,
referenced as "crm") or a dotted module path ("acme_gabe.crm") that only needs to
be importable. That is how Gabe adapts to each company's existing
systems without changes to the core.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from gabe.config import Config
from gabe.tools.registry import ToolRegistry

log = logging.getLogger("gabe.integrations")


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


def local_integration(cfg: Config, name: str) -> Path | None:
    """A company's own integration file in the config folder (`integrations/<name>.py`),
    used for short names that aren't built in. It works in the packaged app too."""
    if "." in name:
        return None
    path = cfg.root / "integrations" / f"{name}.py"
    return path if path.is_file() else None


_local_modules: dict[Path, tuple[float, ModuleType]] = {}


def _load_local(path: Path) -> ModuleType:
    """Import a local integration file, again whenever it changes on disk."""
    mtime = path.stat().st_mtime
    cached = _local_modules.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    folder = str(path.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)  # so it can import helper modules next to it
    spec = importlib.util.spec_from_file_location(f"gabe_local_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _local_modules[path] = (mtime, module)
    return module


def _builtin(name: str) -> bool:
    return importlib.util.find_spec(f"gabe.integrations.{name}") is not None


def load_integrations(cfg: Config, registry: ToolRegistry, errors: dict[str, str] | None = None) -> None:
    """Run every configured integration's setup. A local integration (a file in the config
    folder) that fails is logged and skipped, so a bad edit never stops Gabe; with `errors`,
    every failure is collected there instead of raised."""
    for spec in cfg.integrations:
        local = None if "." in spec.name or _builtin(spec.name) else local_integration(cfg, spec.name)
        try:
            if local is not None:
                module = _load_local(local)
            else:
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
        except Exception as e:
            if errors is not None:
                errors[spec.name] = f"{type(e).__name__}: {e}"
            elif local is not None:
                log.warning("integration %s (%s) failed and was skipped: %s", spec.name, local, e)
            else:
                raise
