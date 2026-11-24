"""Claude Code and Gabe: people change their own installation with it.

Two ways in, both working on the config folder:
- "Open in Claude Code" (Settings): a Terminal running `claude` in that folder.
- The `claude_code` integration: Gabe hands a "change yourself" request to Claude Code
  in restricted, non-interactive mode (see gabe/integrations/claude_code.py).

Either way Claude Code reads the folder's CLAUDE.md (written here from
gabe/defaults/CLAUDE.md) for the config format and the check command.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

GUIDE = "CLAUDE.md"
# Where Claude Code's installers put it. The desktop app starts with a minimal PATH.
_EXTRA_PATH = ["~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin"]


def search_path() -> str:
    extra = [os.path.expanduser(p) for p in _EXTRA_PATH]
    return os.pathsep.join([*extra, os.environ.get("PATH", ""), "/usr/bin", "/bin"])


def find_claude(binary: str | None = None) -> str | None:
    """The `claude` executable, or None when Claude Code isn't installed."""
    if binary:
        b = os.path.expanduser(binary)
        return b if os.path.isfile(b) and os.access(b, os.X_OK) else shutil.which(b, path=search_path())
    return shutil.which("claude", path=search_path())


def check_command(config_path: Path) -> str:
    """The shell command that runs `gabe check` on this config, from any directory."""
    if getattr(sys, "frozen", False):  # the packaged app checks its own config folder
        return f"{shlex.quote(sys.executable)} check"
    return f"{shlex.quote(sys.executable)} -m gabe -c {shlex.quote(str(config_path))} check"


def ensure_guide(config_path: Path) -> Path:
    """Write (or refresh) CLAUDE.md in the config folder. It's Gabe's file: people's own notes
    go in CLAUDE.local.md, which Claude Code also reads."""
    from gabe.desktop import bundled

    template = bundled("gabe", "defaults", GUIDE).read_text()
    text = template.replace("{{CHECK_COMMAND}}", check_command(config_path))
    target = config_path.parent / GUIDE
    if not target.exists() or target.read_text() != text:
        target.write_text(text)
    return target


def open_in_terminal(config_path: Path, claude: str, data_dir: Path) -> None:
    """Open a Terminal window running Claude Code in the config folder (macOS)."""
    if sys.platform != "darwin":
        raise RuntimeError("Opening Claude Code from Gabe works on macOS only for now.")
    folder = config_path.parent
    ensure_guide(config_path)
    # A .command file opens in Terminal and runs, with no automation permission needed.
    data_dir.mkdir(parents=True, exist_ok=True)
    script = data_dir / "claude-code.command"
    script.write_text(
        "#!/bin/zsh -l\n"
        f"cd {shlex.quote(str(folder))} || exit 1\n"
        f"exec {shlex.quote(claude)}\n"
    )
    script.chmod(0o700)
    subprocess.run(["open", "-a", "Terminal", str(script)], check=True, capture_output=True)
