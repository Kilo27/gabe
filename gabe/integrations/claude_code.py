"""`change_gabe`: Gabe hands "change how you're set up" requests to Claude Code.

    integrations:
      - name: claude_code
        settings:
          # binary: /opt/homebrew/bin/claude   # found automatically when left out
          # model: sonnet                       # default: the model Gabe runs on
          # base_url: https://api.z.ai/api/anthropic   # an Anthropic-format endpoint, when
          # auth_token: "${ZAI_API_KEY}"               #   it isn't derivable from `provider:`
          # timeout: 600                        # seconds
          # max_budget_usd: 2                   # per change
          # use_claude_login: false             # true: your own Claude login instead

Claude Code runs on the same model and credentials as Gabe (see `endpoint`): with
`provider: anthropic`, Gabe's API key and model; with `provider: openai_compatible` (e.g. GLM
on vLLM), the same server and model through its Anthropic-format API, which vLLM and LiteLLM
serve next to the OpenAI one. Set `base_url` when it lives elsewhere (Z.ai).

Claude Code runs non-interactively and restricted (`--restricted`: no shell or other
code-running tools, file tools confined to the config folder) with the folder's CLAUDE.md
as its guide. A person approves every request first (`ask_first`). The folder is backed up
before the change and checked after it; if the check fails, Claude Code gets one chance to
fix it, then the change is undone.

Claude Code is optional: without it the action isn't offered. Nothing else in Gabe
depends on it.
"""

from __future__ import annotations

import filecmp
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from gabe.tools.registry import Risk, Tool, ToolRegistry, schema

log = logging.getLogger("gabe.claude_code")

TOOL = "change_gabe"
_lock = threading.Lock()  # one change at a time
_SKIP = {".DS_Store"}

APPEND = (
    "You were started by Gabe, the AI chief of staff whose config folder this is, on behalf of a "
    "person who approved this request. Nobody can answer questions during this run: if the request is "
    "unclear or would loosen approvals in a way they may not intend, make no change and say what you "
    "need. You can't run commands here: Gabe runs its check after you finish and will tell you "
    "if anything needs fixing, so don't mention the check. Make only the change asked for. When done, reply in two or three plain sentences: what "
    "changed, and anything the person must do (for example add a variable to ../.env)."
)


def setup(registry: ToolRegistry, ctx) -> None:
    from gabe.claude_code import find_claude

    claude = find_claude(ctx.settings.get("binary"))
    if not claude:
        log.info("Claude Code isn't installed; %s is not offered", TOOL)
        return
    config_path = ctx.config.path
    if config_path is None:
        return

    def change_gabe(request: str) -> str:
        if not _lock.acquire(timeout=5):
            raise RuntimeError("Another change to Gabe's setup is in progress. Try again when it's done.")
        try:
            return run_change(claude, config_path, ctx.path("backups"), request.strip(), ctx.settings,
                              ctx.config.provider)
        finally:
            _lock.release()

    registry.register(Tool(
        name=TOOL,
        description=(
            "Change how Gabe itself is set up: company details, approval rules, the team, playbooks, "
            "connected MCP servers, or a new custom integration. Claude Code makes the edit in Gabe's "
            "config folder, the result is checked, and a failed change is undone. A person approves "
            "first. Give the complete request in plain words, with every detail (names, which actions, "
            "how careful to be). Not for everyday work like tasks, notes or email."
        ),
        input_schema=schema({
            "request": {"type": "string", "description": "What to change, completely and in plain words."},
        }),
        handler=change_gabe,
        risk=Risk.WRITE,
        integration="claude_code",
        ask_first=True,
    ))


# -- one change -------------------------------------------------------------------------


def run_change(claude: str, config_path: Path, backups: Path, request: str, settings: dict,
               provider: dict | None = None) -> str:
    from gabe.check import check_config
    from gabe.claude_code import ensure_guide

    if not request:
        raise ValueError("Say what to change.")
    folder = config_path.parent
    ensure_guide(config_path)
    backup = backups / time.strftime("%Y%m%d-%H%M%S")
    if backup.exists():
        shutil.rmtree(backup)
    shutil.copytree(folder, backup, ignore=shutil.ignore_patterns(*_SKIP))

    env, model = claude_env(provider, settings, os.environ)
    settings = {**settings, "model": model}
    timeout = float(settings.get("timeout") or 600)

    try:
        out = _claude(claude, folder, env, timeout, settings, request)
        report = check_config(config_path)
        if not report.ok and out.get("session_id"):
            out = _claude(claude, folder, env, timeout, settings,
                          "Gabe's check failed after your change. Fix these and nothing else:\n" + report.text(),
                          resume=out["session_id"])
            report = check_config(config_path)
    except Exception as e:
        if _diff(backup, folder):
            _restore(backup, folder)
            raise RuntimeError(f"{str(e).rstrip('.')}. Its partial changes were undone.") from e
        raise

    changed = _diff(backup, folder)
    if not report.ok:
        _restore(backup, folder)
        raise RuntimeError("Claude Code's change didn't pass Gabe's check, so it was undone.\n" + report.text())
    summary = (out.get("result") or "").strip() or "Done."
    lines = [summary, "", "Changed: " + (", ".join(changed) if changed else "nothing")]
    if report.warnings:
        lines.append(report.text())
    if not changed:
        shutil.rmtree(backup, ignore_errors=True)
    else:
        lines.append(f"Backup of the previous version: {backup}")
    return "\n".join(lines)


# Everything that points Claude Code at a model; cleared so only what Gabe decides applies.
_ENDPOINT_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
                  "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                  "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")


def endpoint(provider: dict | None, settings: dict, environ) -> dict | None:
    """Where Claude Code's model calls go: {base_url, api_key, auth_token, model}, or None for
    the person's own Claude login. Explicit claude_code settings win, then Gabe's provider."""
    from gabe.integrations.mcp import expand

    if settings.get("use_claude_login"):
        return None
    provider = provider or {"kind": "anthropic"}
    kind = provider.get("kind", "anthropic")
    model = settings.get("model") or provider.get("model")
    if settings.get("base_url"):
        return {"base_url": expand(str(settings["base_url"])),
                "auth_token": expand(str(settings.get("auth_token") or provider.get("api_key") or "")),
                "model": model, "custom": True}
    if kind == "openai_compatible":
        base = re.sub(r"/v1/?$", "", str(provider.get("base_url") or "http://localhost:8000/v1").rstrip("/"))
        return {"base_url": base, "auth_token": str(provider.get("api_key") or "EMPTY"),
                "model": model, "custom": True}
    # Anthropic: the same key (and endpoint, if Gabe uses one) the Anthropic client resolves.
    key, token = environ.get("ANTHROPIC_API_KEY"), environ.get("ANTHROPIC_AUTH_TOKEN")
    if not key and not token:
        return None
    return {"base_url": environ.get("ANTHROPIC_BASE_URL"), "api_key": key, "auth_token": token,
            "model": model, "custom": False}


def claude_env(provider: dict | None, settings: dict, environ) -> tuple[dict, str | None]:
    """The environment and --model for a Claude Code run that matches Gabe's own model setup."""
    from gabe.claude_code import search_path

    env = {k: v for k, v in environ.items() if k not in _ENDPOINT_VARS}
    env["PATH"] = search_path()
    ep = endpoint(provider, settings, environ)
    if ep is None:
        return env, settings.get("model")
    for var, key in (("ANTHROPIC_BASE_URL", "base_url"), ("ANTHROPIC_API_KEY", "api_key"),
                     ("ANTHROPIC_AUTH_TOKEN", "auth_token")):
        if ep.get(key):
            env[var] = ep[key]
    if ep["custom"] and ep.get("model"):
        # A non-Claude model (e.g. GLM): use it for every tier, and keep traffic on that server.
        for var in _ENDPOINT_VARS[3:]:
            env[var] = ep["model"]
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env, ep.get("model")


def _claude(claude: str, folder: Path, env: dict, timeout: float, settings: dict, prompt: str,
            resume: str | None = None) -> dict:
    cmd = [claude, "-p", "--output-format", "json", "--restricted", "--strict-mcp-config",
           "--permission-mode", "acceptEdits", "--append-system-prompt", APPEND]
    if settings.get("model"):
        cmd += ["--model", str(settings["model"])]
    if settings.get("max_budget_usd"):
        cmd += ["--max-budget-usd", str(settings["max_budget_usd"])]
    if resume:
        cmd += ["--resume", resume]
    try:
        proc = subprocess.run(cmd, input=prompt, cwd=folder, env=env, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Claude Code didn't finish within {timeout:g}s.") from None
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        out = {"result": proc.stdout.strip()}
    if proc.returncode != 0 or out.get("is_error"):
        detail = (out.get("result") or proc.stderr or proc.stdout or "").strip()[:500]
        if "/login" in detail or "not logged in" in detail.lower():
            detail = ("Claude Code has no model to use: add Gabe's API key in Settings, or run `claude` "
                      "in Terminal once and sign in.")
        raise RuntimeError(f"Claude Code failed: {detail or f'exit code {proc.returncode}'}")
    return out


def _files(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in sorted(root.rglob("*"))
            if p.is_file() and p.name not in _SKIP and "__pycache__" not in p.parts}


def _diff(before: Path, after: Path) -> list[str]:
    a, b = _files(before), _files(after)
    return sorted(
        [f"{n} (new)" for n in b if n not in a]
        + [f"{n} (removed)" for n in a if n not in b]
        + [n for n in a if n in b and not filecmp.cmp(a[n], b[n], shallow=False)]
    )


def _restore(backup: Path, folder: Path) -> None:
    saved = _files(backup)
    for name, path in _files(folder).items():
        if name not in saved:
            path.unlink()
    for name, path in saved.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
