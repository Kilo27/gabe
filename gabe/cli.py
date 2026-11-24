"""Interactive terminal interface for Gabe."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import anthropic

from gabe.app import build_gabe
from gabe.config import load_config
from gabe.policy import deny_all
from gabe.tools.registry import Tool

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def terminal_approver(tool: Tool, args: dict[str, Any]) -> bool:
    print(f"\n{BOLD}Approval needed:{RESET} {tool.name} ({tool.risk.value})")
    print(json.dumps(args, indent=2))
    try:
        return input("Allow? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def print_event(kind: str, data: dict[str, Any]) -> None:
    if kind == "tool":
        print(f"{DIM}  · {data['agent']} → {data['tool']} [{data['outcome']}]{RESET}", file=sys.stderr)
    elif kind == "thought":
        print(f"{DIM}  · {data['agent']}: {data['text'].strip()[:200]}{RESET}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gabe", description="Gabe, your AI chief of staff.")
    ap.add_argument("-c", "--config", help="Path to company config (default: $GABE_CONFIG or config/gabe.yaml)")
    ap.add_argument("-m", "--message", help="Run a single request and exit")
    ap.add_argument("--unattended", action="store_true", help="Refuse every action that needs approval")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    gabe = build_gabe(
        cfg,
        approver=deny_all if args.unattended else terminal_approver,
        on_event=print_event,
    )

    def ask(text: str) -> None:
        try:
            print(f"\n{BOLD}{gabe.name}:{RESET} {gabe.send(text)}\n")
        except anthropic.APIError as e:
            print(f"\n[API error] {e}\n", file=sys.stderr)

    if args.message:
        ask(args.message)
        return 0

    company = cfg.company.get("name", "your company")
    print(f"{BOLD}{gabe.name}{RESET} — chief of staff for {company}. /reset clears the conversation, /quit exits.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text == "/reset":
            gabe.reset()
            print("Conversation cleared.")
            continue
        ask(text)


if __name__ == "__main__":
    raise SystemExit(main())
