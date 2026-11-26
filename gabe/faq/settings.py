"""FAQ bot settings: the `faq` integration's settings in gabe.yaml.

    integrations:
      - name: faq
        settings:
          folder: faq                  # FAQ documents, relative to gabe.yaml
          model: haiku                 # haiku | sonnet | opus, or a model id
          tone: Friendly and brief.
          where_to_look: |             # where to send people when the FAQ has no answer
            Help centre: https://help.example.com
            Email support@example.com
          retriever: keyword           # or "package.module:Class" (see gabe/faq/search.py)
          slack:
            enabled: true
            channels: []               # empty: every channel the bot has been added to
            direct_messages: true
            answer: everyone           # everyone | external (guests and other workspaces)
            staff: [U0123ABC]          # never answered
            follow_ups: true
            unanswered: {say_so: true, notify_channel: "#support", create_task: true, task_owner: Support}

Slack tokens come from SLACK_BOT_TOKEN / SLACK_APP_TOKEN (the desktop app keeps them in
the keychain), or `bot_token` / `app_token` here, which may be "${ENV_VAR}".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gabe.config import Config

INTEGRATION = "faq"
BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"
APP_TOKEN_ENV = "SLACK_APP_TOKEN"
ANSWER_MODES = ("everyone", "external")


def expand(value: Any) -> str:
    """"${VAR}" placeholders filled from the environment."""
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), str(value or ""))


def _list(v: Any) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,\s]+", v)
    return [str(x).strip() for x in (v or []) if str(x).strip()]


@dataclass
class SlackSettings:
    enabled: bool = False
    channels: list[str] = field(default_factory=list)
    direct_messages: bool = True
    answer: str = "everyone"
    staff: list[str] = field(default_factory=list)
    follow_ups: bool = True
    per_user_per_hour: int = 10
    notify_channel: str = ""
    create_task: bool = False
    task_owner: str = ""
    say_so: bool = True  # tell the customer when the FAQ has no answer (and where to look)
    holding_reply: str = ""  # a fixed message to use instead of Gabe's own; empty: Gabe writes it
    bot_token: str = ""
    app_token: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SlackSettings":
        un = raw.get("unanswered") or {}
        answer = str(raw.get("answer", "everyone"))
        return cls(
            enabled=bool(raw.get("enabled", False)),
            channels=_list(raw.get("channels")),
            direct_messages=bool(raw.get("direct_messages", True)),
            answer=answer if answer in ANSWER_MODES else "everyone",
            staff=_list(raw.get("staff")),
            follow_ups=bool(raw.get("follow_ups", True)),
            per_user_per_hour=int(raw.get("per_user_per_hour", 10) or 0),
            notify_channel=str(un.get("notify_channel") or "").strip(),
            create_task=bool(un.get("create_task", False)),
            task_owner=str(un.get("task_owner") or "").strip(),
            say_so=bool(un.get("say_so", True)),
            holding_reply=str(un.get("reply") or "").strip(),
            bot_token=expand(raw.get("bot_token")) or os.environ.get(BOT_TOKEN_ENV, ""),
            app_token=expand(raw.get("app_token")) or os.environ.get(APP_TOKEN_ENV, ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """As written to gabe.yaml (tokens are never written there by the app)."""
        return {
            "enabled": self.enabled, "channels": self.channels, "direct_messages": self.direct_messages,
            "answer": self.answer, "staff": self.staff, "follow_ups": self.follow_ups,
            "per_user_per_hour": self.per_user_per_hour,
            "unanswered": {"notify_channel": self.notify_channel, "create_task": self.create_task,
                           "task_owner": self.task_owner, "say_so": self.say_so, "reply": self.holding_reply},
        }


@dataclass
class FaqSettings:
    folder: Path
    model: str = "haiku"
    tone: str = ""
    where_to_look: str = ""  # places to point people to when the FAQ can't answer
    retriever: str = "keyword"
    max_matches: int = 6
    # A library up to this size goes to the model whole (cached); bigger ones are searched first.
    full_context_chars: int = 40_000
    slack: SlackSettings = field(default_factory=SlackSettings)

    @classmethod
    def from_config(cls, cfg: Config) -> "FaqSettings | None":
        """None when the faq integration isn't turned on."""
        spec = next((i for i in cfg.integrations if i.name == INTEGRATION), None)
        if spec is None:
            return None
        return cls.from_dict(spec.settings, cfg.root)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], root: Path) -> "FaqSettings":
        folder = Path(str(raw.get("folder") or "faq")).expanduser()
        return cls(
            folder=folder if folder.is_absolute() else (root / folder).resolve(),
            model=str(raw.get("model") or "haiku"),
            tone=str(raw.get("tone") or "").strip(),
            where_to_look=str(raw.get("where_to_look") or "").strip(),
            retriever=str(raw.get("retriever") or "keyword"),
            max_matches=int(raw.get("max_matches") or 6),
            full_context_chars=int(raw.get("full_context_chars", 40_000)),
            slack=SlackSettings.from_dict(raw.get("slack") or {}),
        )
