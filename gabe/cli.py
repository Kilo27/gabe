"""Interactive terminal interface for Gabe."""

from __future__ import annotations

import argparse
import json
import os
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


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from .env into the environment (existing vars win)."""
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def _chat(cfg, args) -> int:
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


def _show_brief(text: str, path) -> None:
    print(text)
    print(f"\n{DIM}Saved to {path}{RESET}", file=sys.stderr)


def _brief(cfg, args) -> int:
    from gabe.briefing import run_briefing

    _show_brief(*run_briefing(cfg, on_event=print_event))
    return 0


def _schedule(cfg, args) -> int:
    from datetime import datetime

    from gabe.briefing import BriefingSettings, company_tz, next_run, run_scheduler

    s = BriefingSettings.from_config(cfg)
    first = next_run(datetime.now(company_tz(cfg)), s.time, s.days)
    print(f"Daily briefing at {s.time} on {', '.join(s.days)}. Next: {first:%a %Y-%m-%d %H:%M %Z}. Ctrl-C to stop.")
    try:
        run_scheduler(
            cfg,
            on_brief=_show_brief,
            on_error=lambda e: print(f"[briefing failed] {type(e).__name__}: {e}", file=sys.stderr),
        )
    except KeyboardInterrupt:
        print()
    return 0


def _auth(cfg, args) -> int:
    from gabe.integrations import IntegrationContext
    from gabe.integrations.email import gmail_paths
    from gabe.integrations.gmail_backend import build_service, load_credentials

    spec = next((i for i in cfg.integrations if i.name == "email"), None)
    if not spec or spec.settings.get("backend") != "gmail":
        print("The email integration isn't set to `backend: gmail` in config.", file=sys.stderr)
        return 1
    ctx = IntegrationContext("email", spec.settings, cfg.data_dir, cfg)
    creds_path, token_path = gmail_paths(ctx)
    from googleapiclient.errors import HttpError

    creds = load_credentials(creds_path, token_path, interactive=True)
    try:
        profile = build_service(creds).users().getProfile(userId="me").execute()
    except HttpError as e:
        print(f"Signed in and saved the token to {token_path}, but Gmail rejected the test call:", file=sys.stderr)
        print(f"  {e.reason}", file=sys.stderr)
        if e.status_code == 403 and "has not been used" in str(e.reason):
            print("Enable the Gmail API in your Google Cloud project (APIs & Services → Library → Gmail API),", file=sys.stderr)
            print("wait a minute or two, then run `gabe auth gmail` again.", file=sys.stderr)
        return 1
    print(f"Gmail authorised for {profile['emailAddress']}. Token saved to {token_path}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="gabe", description="Gabe, your AI chief of staff.")
    ap.add_argument("-c", "--config", help="Path to company config (default: $GABE_CONFIG or config/gabe.yaml)")
    sub = ap.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="Talk to Gabe (default)")
    for p in (ap, chat):
        p.add_argument("-m", "--message", help="Run a single request and exit")
        p.add_argument("--unattended", action="store_true", help="Refuse every action that needs approval")
    sub.add_parser("brief", help="Produce today's daily briefing now")
    sub.add_parser("schedule", help="Run forever, producing the daily briefing at the configured time")
    auth = sub.add_parser("auth", help="Authorise an integration")
    auth.add_argument("service", choices=["gmail"])

    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    handler = {"brief": _brief, "schedule": _schedule, "auth": _auth}.get(args.command, _chat)
    return handler(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
