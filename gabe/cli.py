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

# No colour codes when output goes to a log file (e.g. under launchd).
DIM, BOLD, RESET = ("\033[2m", "\033[1m", "\033[0m") if sys.stderr.isatty() else ("", "", "")


def terminal_approver(tool: Tool, args: dict[str, Any]) -> bool:
    print(f"\n{BOLD}Approval needed:{RESET} {tool.name} ({tool.risk.value})")
    print(json.dumps(args, indent=2))
    try:
        return input("Allow? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def print_event(kind: str, data: dict[str, Any]) -> None:
    indent = "  " * len(data.get("path") or [None])
    if kind == "tool" and data["tool"] != "delegate":
        print(f"{DIM}{indent}· {data['agent']} → {data['tool']} [{data['outcome']}]{RESET}", file=sys.stderr)
    elif kind == "thought":
        print(f"{DIM}{indent}· {data['agent']}: {data['text'].strip()[:200]}{RESET}", file=sys.stderr)
    elif kind == "delegate":
        print(f"{DIM}{indent}↳ {data['agent']} asked {data['to_name']}: {data['task'].strip()[:200]}{RESET}", file=sys.stderr)
    elif kind == "approval_needed":
        print(f"\n{BOLD}{' › '.join(data['path'])}{RESET} wants to use {data['tool']}.", file=sys.stderr)


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
        user=args.user,
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

    from gabe.briefing import email_briefing

    text, path = run_briefing(cfg, on_event=print_event)
    _show_brief(text, path)
    if not args.no_email:
        sent_to = email_briefing(cfg, text)
        if sent_to:
            print(f"{DIM}Emailed to {', '.join(sent_to)}{RESET}", file=sys.stderr)
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


def _usage(cfg, args) -> int:
    from gabe.usage import format_table, read_events, summarize

    path = cfg.data_dir / "usage.jsonl"
    events = read_events(path, since=args.since)
    if args.user:
        events = [e for e in events if e.get("user") == args.user]
    if not events:
        print(f"No usage recorded{' since ' + args.since if args.since else ''} in {path}.")
        return 0
    by = [f.strip() for f in args.by.split(",") if f.strip()]
    first, last = events[0]["ts"][:10], events[-1]["ts"][:10]
    print(f"Usage {first} to {last} ({len(events)} model calls)\n")
    print(format_table(summarize(events, by, cfg.pricing), by))
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


def _ui(cfg, args) -> int:
    from gabe.ui.server import serve

    serve(cfg, port=args.port, open_browser=not args.no_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="gabe", description="Gabe, your AI chief of staff.")
    ap.add_argument("-c", "--config", help="Path to company config (default: $GABE_CONFIG or config/gabe.yaml)")
    ap.add_argument("--user", help="Who this run is for, in usage logs (default: $GABE_USER or your login)")
    sub = ap.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="Talk to Gabe (default)")
    for p in (ap, chat):
        p.add_argument("-m", "--message", help="Run a single request and exit")
        p.add_argument("--unattended", action="store_true", help="Refuse every action that needs approval")
    brief = sub.add_parser("brief", help="Produce today's daily briefing now (and email it if configured)")
    brief.add_argument("--no-email", action="store_true", help="Don't email it, even if briefing.email_to is set")
    sub.add_parser("schedule", help="Run forever, producing the daily briefing at the configured time")
    ui = sub.add_parser("ui", help="Open the team page in your browser: see, change and watch Gabe's team")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true", help="Don't open a browser window")
    usage = sub.add_parser("usage", help="Token usage and estimated cost from data/usage.jsonl")
    usage.add_argument("--since", help="Only calls on or after this date (YYYY-MM-DD, UTC)")
    usage.add_argument("--by", default="user,task,model", help="Group by any of user,task,model,agent (default: user,task,model)")
    auth = sub.add_parser("auth", help="Authorise an integration")
    auth.add_argument("service", choices=["gmail"])

    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    handler = {"brief": _brief, "schedule": _schedule, "usage": _usage, "auth": _auth, "ui": _ui}.get(args.command, _chat)
    return handler(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
