"""Task and follow-up tracking with owners, due dates and status."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from gabe.integrations._store import JsonStore
from gabe.tools.registry import Risk, ToolRegistry, schema

STATUSES = ["open", "in_progress", "blocked", "done", "cancelled"]


def setup(registry: ToolRegistry, ctx) -> None:
    store = JsonStore(ctx.path("tasks.json"), {"tasks": []})

    @registry.add(
        "create_task",
        "Create a task or follow-up. Every commitment someone makes should become a task "
        "with an owner and, where known, a due date.",
        schema(
            {
                "title": {"type": "string"},
                "owner": {"type": "string", "description": "Who is responsible."},
                "due": {"type": "string", "description": "Due date, ISO format YYYY-MM-DD."},
                "notes": {"type": "string"},
                "project": {"type": "string"},
            },
            required=["title", "owner"],
        ),
        Risk.WRITE,
        "tasks",
    )
    def create_task(title: str, owner: str, due: str | None = None, notes: str = "", project: str = ""):
        task = {
            "id": uuid.uuid4().hex[:8],
            "title": title,
            "owner": owner,
            "due": due,
            "notes": notes,
            "project": project,
            "status": "open",
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        store.update(lambda d: d["tasks"].append(task))
        return task

    @registry.add(
        "list_tasks",
        "List tasks, optionally filtered by owner, status or project. Results are sorted by due date.",
        schema(
            {
                "owner": {"type": "string"},
                "status": {"type": "string", "enum": STATUSES + ["active"]},
                "project": {"type": "string"},
            },
            required=[],
        ),
        Risk.READ,
        "tasks",
    )
    def list_tasks(owner: str | None = None, status: str | None = "active", project: str | None = None):
        tasks = store.load()["tasks"]
        if owner:
            tasks = [t for t in tasks if t["owner"].lower() == owner.lower()]
        if status == "active":
            tasks = [t for t in tasks if t["status"] in ("open", "in_progress", "blocked")]
        elif status:
            tasks = [t for t in tasks if t["status"] == status]
        if project:
            tasks = [t for t in tasks if t.get("project", "").lower() == project.lower()]
        tasks.sort(key=lambda t: t.get("due") or "9999")
        return tasks or "No matching tasks."

    @registry.add(
        "update_task",
        "Update a task's status, owner, due date or notes.",
        schema(
            {
                "id": {"type": "string"},
                "status": {"type": "string", "enum": STATUSES},
                "owner": {"type": "string"},
                "due": {"type": "string"},
                "notes": {"type": "string", "description": "Replaces existing notes."},
            },
            required=["id"],
        ),
        Risk.WRITE,
        "tasks",
    )
    def update_task(id: str, **changes):
        def upd(d):
            for t in d["tasks"]:
                if t["id"] == id:
                    t.update({k: v for k, v in changes.items() if v is not None})
                    return t
            return None

        return store.update(upd) or f"No task with id {id}."
