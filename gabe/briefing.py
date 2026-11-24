"""Daily briefing: a read-only Gabe run that summarises what needs doing today,
written to the terminal and saved to data/briefings/YYYY-MM-DD.md.

Scheduled with `gabe schedule` (or cron/launchd running `gabe brief`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from gabe.agent import Agent, EventHandler, _ignore
from gabe.audit import AuditLog
from gabe.config import Config
from gabe.integrations import load_integrations
from gabe.policy import Decision, Policy, deny_all
from gabe.prompts import chief_of_staff_prompt
from gabe.providers import Provider, make_provider
from gabe.tools.registry import Risk, ToolRegistry

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

BRIEF_REQUEST = """\
Prepare today's daily briefing for the leadership team. This is an unattended run:
you can only read, so don't try to change anything. Gather the facts with your tools
first (current time, tasks, and email and notes where you have them), then write the
briefing. If a 'daily-brief' playbook exists, follow its structure. Output only the
briefing, in Markdown."""


@dataclass
class BriefingSettings:
    time: str = "08:00"
    days: list[str] = field(default_factory=lambda: DAYS[:5])
    output_dir: Path | None = None
    email_to: list[str] = field(default_factory=list)  # "me" = the connected mailbox

    @classmethod
    def from_config(cls, cfg: Config) -> "BriefingSettings":
        raw: dict[str, Any] = cfg.briefing or {}
        out = raw.get("output_dir")
        return cls(
            time=str(raw.get("time", "08:00")),
            days=[d.lower()[:3] for d in raw.get("days", DAYS[:5])],
            output_dir=(cfg.root / out).resolve() if out else cfg.data_dir / "briefings",
            email_to=list(raw.get("email_to") or []),
        )


def company_tz(cfg: Config) -> ZoneInfo:
    return ZoneInfo(cfg.company.get("timezone") or "UTC")


def build_briefer(cfg: Config, provider: Provider | None = None, on_event: EventHandler = _ignore) -> Agent:
    """Gabe with read-only tools and a policy that blocks everything else."""
    registry = ToolRegistry()
    load_integrations(cfg, registry)
    read_only = [t.name for t in registry.tools() if t.risk is Risk.READ]
    policy = Policy(defaults={Risk.READ: Decision.ALLOW, Risk.WRITE: Decision.DENY, Risk.EXTERNAL: Decision.DENY})
    return Agent(
        name=f"{cfg.assistant_name} (briefing)",
        provider=provider or make_provider(cfg.provider),
        system=chief_of_staff_prompt(cfg),
        registry=registry.subset(read_only),
        policy=policy,
        approver=deny_all,
        audit=AuditLog(cfg.data_dir / "audit.jsonl"),
        on_event=on_event,
    )


def run_briefing(cfg: Config, provider: Provider | None = None, on_event: EventHandler = _ignore) -> tuple[str, Path]:
    settings = BriefingSettings.from_config(cfg)
    text = build_briefer(cfg, provider, on_event).send(BRIEF_REQUEST).strip()
    today = datetime.now(company_tz(cfg)).strftime("%Y-%m-%d")
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    path = settings.output_dir / f"{today}.md"
    path.write_text(text + "\n")
    return text, path


def email_briefing(cfg: Config, text: str, client=None) -> list[str]:
    """Email the briefing to `briefing.email_to`. Returns the addresses sent to.

    This send is driven by code to recipients fixed in config, not chosen by the
    model, so it doesn't go through the approval policy. It is still audited.
    """
    from gabe.markdown_email import to_html

    settings = BriefingSettings.from_config(cfg)
    if not settings.email_to:
        return []
    if client is None:
        from gabe.integrations.email import gmail_client_from_config

        client = gmail_client_from_config(cfg)
    to = [client.address() if a.strip().lower() == "me" else a.strip() for a in settings.email_to]
    day = datetime.now(company_tz(cfg)).strftime("%a %d %b")
    subject = f"Daily brief: {day}"
    msg_id = client.send(to, subject, text, html_body=to_html(text))
    AuditLog(cfg.data_dir / "audit.jsonl").record(
        agent="briefing", tool="send_email", input={"to": to, "subject": subject},
        outcome="ok", error=False, message_id=msg_id,
    )
    return to


def next_run(now: datetime, at: str, days: list[str]) -> datetime:
    """Next datetime at HH:MM on an allowed weekday, strictly after `now`."""
    hour, minute = (int(x) for x in at.split(":"))
    allowed = {DAYS.index(d) for d in days}
    if not allowed:
        raise ValueError("briefing.days is empty")
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() not in allowed:
        candidate += timedelta(days=1)
    return candidate


def run_scheduler(
    cfg: Config,
    on_brief: Callable[[str, Path], None],
    on_error: Callable[[Exception], None],
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run forever, producing a briefing at the configured time."""
    settings = BriefingSettings.from_config(cfg)
    tz = company_tz(cfg)
    while True:
        target = next_run(datetime.now(tz), settings.time, settings.days)
        # Sleep in short chunks so laptop sleep / clock changes don't overshoot much.
        while (remaining := (target - datetime.now(tz)).total_seconds()) > 0:
            sleep(min(remaining, 60))
        try:
            text, path = run_briefing(cfg)
            on_brief(text, path)
            email_briefing(cfg, text)
        except Exception as e:  # keep the scheduler alive across a bad run
            on_error(e)
