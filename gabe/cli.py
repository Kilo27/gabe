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


def terminal_asker(agent: str, question: str, choices: list) -> str:
    print(f"\n{BOLD}{agent} asks:{RESET} {question}")
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    try:
        answer = input("Your answer: ").strip()
    except EOFError:
        return ""
    if answer.isdigit() and 1 <= int(answer) <= len(choices):
        return choices[int(answer) - 1]
    return answer


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
    if cfg.paused:
        print(f"{cfg.assistant_name} is paused. Resume it in the web app, or remove `paused` under assistant: in config.", file=sys.stderr)
        return 1
    gabe = build_gabe(
        cfg,
        approver=deny_all if args.unattended else terminal_approver,
        **({} if args.unattended else {"asker": terminal_asker}),
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

    from gabe.briefing import BriefingSettings, email_briefing

    if BriefingSettings.from_config(cfg).paused:
        print("The briefing writer is paused. Resume it in the web app, or remove `paused` from briefing: in config.", file=sys.stderr)
        return 1
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


def _auth_calendar(cfg) -> int:
    from gabe.integrations.calendar import calendar_ctx, calendar_paths, calendar_spec
    from gabe.integrations.gcal_backend import SCOPES, GoogleCalendarClient, build_service
    from gabe.integrations.gmail_backend import load_credentials

    spec = calendar_spec(cfg)
    if not spec or spec.settings.get("backend", "google") != "google":
        print("The calendar integration isn't set to `backend: google` in config.", file=sys.stderr)
        return 1
    creds_path, token_path = calendar_paths(calendar_ctx(cfg))
    from googleapiclient.errors import HttpError

    creds = load_credentials(creds_path, token_path, interactive=True, scopes=SCOPES)
    try:
        address = GoogleCalendarClient(lambda: build_service(creds)).address()
    except HttpError as e:
        print(f"Signed in and saved the token to {token_path}, but Google Calendar rejected the test call:", file=sys.stderr)
        print(f"  {e.reason}", file=sys.stderr)
        if e.status_code == 403 and "has not been used" in str(e.reason):
            print("Enable the Google Calendar API in your Google Cloud project (APIs & Services → Library),", file=sys.stderr)
            print("wait a minute or two, then run `gabe auth calendar` again.", file=sys.stderr)
        return 1
    print(f"Google Calendar authorised for {address}. Token saved to {token_path}.")
    return 0


def _auth(cfg, args) -> int:
    if args.service == "calendar":
        return _auth_calendar(cfg)
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


def _web(cfg, args) -> int:
    try:
        import uvicorn
    except ImportError:
        print("The web app needs extra packages: pip install -e '.[web]'", file=sys.stderr)
        return 1
    from gabe.web.server import create_app

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"{BOLD}Gabe web app{RESET} running at {url}  (Ctrl-C to stop)")
    if not args.no_open:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(args.config_path), host=args.host, port=args.port, log_level="warning")
    return 0


def _slack(cfg, args) -> int:
    """Run the Slack FAQ bot on its own (a server with no desktop app). Ctrl-C stops it."""
    import logging
    import time

    from gabe.faq.settings import FaqSettings
    from gabe.faq.slack import REPLY_TOOL, SlackService
    from gabe.policy import Decision, Policy

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = FaqSettings.from_config(cfg)
    if settings is None or not settings.slack.enabled:
        print("The Slack FAQ bot is off. Add `- name: faq` under integrations with "
              "`settings: {slack: {enabled: true}}` (see docs/FAQ_BOT.md).", file=sys.stderr)
        return 1
    if Policy.from_config(cfg.policy).decide(REPLY_TOOL) is Decision.ASK:
        print(f"{BOLD}Note:{RESET} answers need a person's OK (policy), and only the app can give it, so they "
              "won't be posted. Set `policy: {tools: {reply_on_slack: allow}}` to post them.", file=sys.stderr)
    service = SlackService(lambda: load_config(args.config_path))
    last = None
    try:
        while True:
            service.sync()
            st = service.status()
            if st != last:
                print(f"Slack FAQ bot: {st.get('state')}"
                      + (f" ({st['workspace']})" if st.get("workspace") else "")
                      + (f": {st['error']}" if st.get("error") else ""), file=sys.stderr)
                last = st
            time.sleep(15)
    except KeyboardInterrupt:
        service.stop()
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
    usage = sub.add_parser("usage", help="Token usage and estimated cost from data/usage.jsonl")
    usage.add_argument("--since", help="Only calls on or after this date (YYYY-MM-DD, UTC)")
    usage.add_argument("--by", default="user,task,model", help="Group by any of user,task,model,agent (default: user,task,model)")
    auth = sub.add_parser("auth", help="Authorise an integration")
    auth.add_argument("service", choices=["gmail", "calendar"])
    sub.add_parser("check", help="Check the config folder (after editing it by hand or with Claude Code)")
    sub.add_parser("slack", help="Run the Slack FAQ bot on its own (no desktop app)")
    sub.add_parser("app", help="Open the Gabe desktop app from source")
    web = sub.add_parser("web", help="Serve the app UI to a browser (for development)")
    web.add_argument("--host", default="127.0.0.1", help="Address to listen on (default: this computer only)")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-open", action="store_true", help="Don't open a browser window")

    args = ap.parse_args(argv)
    if args.command == "app":  # the desktop app keeps its own config in the app-data folder
        from gabe.desktop import main as desktop_main

        return desktop_main()
    if args.command == "check":  # before load_config, which may be what's broken
        from gabe.check import check_config

        report = check_config(args.config or os.environ.get("GABE_CONFIG", "config/gabe.yaml"))
        print(report.text())
        return 0 if report.ok else 1
    cfg = load_config(args.config)
    args.config_path = args.config or os.environ.get("GABE_CONFIG", "config/gabe.yaml")
    handler = {"brief": _brief, "schedule": _schedule, "auth": _auth, "usage": _usage, "web": _web, "slack": _slack}.get(args.command, _chat)
    return handler(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
