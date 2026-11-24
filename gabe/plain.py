"""Plain-English wording for skills and actions, so people who don't read code can
see what each assistant may do and what it actually did."""

from __future__ import annotations

from typing import Any

from gabe.tools.registry import Risk, Tool

RISK_LABELS = {
    Risk.READ: ("Looks things up", "Only reads. Never changes anything."),
    Risk.WRITE: ("Changes our records", "Changes Gabe's own records: tasks, notes, drafts, read/unread."),
    Risk.EXTERNAL: ("Acts in the outside world", "Does something other people will see, like sending an email."),
}

# tool name -> (skill label, one use in the past tense, one use as a request)
SKILLS: dict[str, tuple[str, str, str]] = {
    "get_current_time": ("Check the date and time", "checked the date and time", "check the date and time"),
    "remember": ("Save notes", "saved a note", "save a note"),
    "recall": ("Search saved notes", "searched saved notes", "search saved notes"),
    "forget": ("Delete notes", "deleted a note", "delete a note"),
    "create_task": ("Create tasks", "created a task", "create a task"),
    "list_tasks": ("See the task list", "looked at the task list", "look at the task list"),
    "update_task": ("Update tasks", "updated a task", "update a task"),
    "search_email": ("Search email", "searched email", "search email"),
    "read_email": ("Read emails", "read an email", "read an email"),
    "draft_email": ("Write email drafts", "saved an email draft", "save an email draft"),
    "send_email": ("Send emails", "sent an email", "send an email"),
    "mark_email_read": ("Mark emails as read", "marked an email as read", "mark an email as read"),
    "delegate": ("Hand work to their team", "handed work to a team member", "hand work to a team member"),
}

OUTCOMES = {
    "ok": "",
    "denied_by_policy": "blocked by company rules",
    "declined_by_user": "not approved",
    "invalid_input": "failed (bad request)",
    "unknown_tool": "failed (no such skill)",
    "error": "failed",
}


def _humanize(name: str) -> str:
    return name.replace("_", " ").capitalize()


def skill_label(tool_name: str) -> str:
    return SKILLS[tool_name][0] if tool_name in SKILLS else _humanize(tool_name)


def describe_skill(tool: Tool) -> dict[str, Any]:
    risk_label, risk_help = RISK_LABELS[tool.risk]
    return {
        "name": tool.name,
        "label": skill_label(tool.name),
        "what": tool.description,
        "risk": tool.risk.value,
        "risk_label": risk_label,
        "risk_help": risk_help,
        "area": tool.integration,
    }


def _short(value: Any, limit: int = 80) -> str:
    text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_action(tool_name: str, args: dict[str, Any] | None, request: bool = False) -> str:
    """One line such as: sent an email to sam@example.com: "Board pack".
    With request=True it reads as something to approve: send an email to ..."""
    args = args or {}
    name = _humanize(tool_name).lower()
    fallback = ("", f"use {name}" if request else f"used {name}", f"use {name}")
    verb = SKILLS.get(tool_name, fallback)[2 if request else 1]
    detail = ""
    if tool_name in ("send_email", "draft_email"):
        detail = f" to {_short(args.get('to', '?'))}: \"{_short(args.get('subject', ''))}\""
    elif tool_name in ("create_task",):
        detail = f" \"{_short(args.get('title', ''))}\" for {args.get('owner', 'someone')}"
    elif tool_name == "update_task":
        changes = {k: v for k, v in args.items() if k != "id"}
        detail = f" ({_short(', '.join(f'{k}: {v}' for k, v in changes.items()))})" if changes else ""
    elif tool_name in ("search_email", "recall"):
        detail = f" for \"{_short(args.get('query', ''))}\""
    elif tool_name == "remember":
        detail = f" about \"{_short(args.get('topic', args.get('note', '')))}\""
    elif tool_name == "list_tasks":
        filters = [f"{k} {v}" for k, v in args.items() if v]
        detail = f" ({', '.join(filters)})" if filters else ""
    return verb + detail


def describe_outcome(outcome: str) -> str:
    return OUTCOMES.get(outcome, outcome.replace("_", " "))
