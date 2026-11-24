"""Long-term memory: notes about decisions, people, preferences and context."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from gabe.integrations._store import JsonStore
from gabe.tools.registry import Risk, ToolRegistry, schema


def setup(registry: ToolRegistry, ctx) -> None:
    store = JsonStore(ctx.path("notes.json"), {"notes": []})

    @registry.add(
        "remember",
        "Save a durable note (a decision, commitment, preference, or fact about a person, "
        "project, or process) so it can be recalled in later sessions.",
        schema(
            {
                "topic": {"type": "string", "description": "Short subject, e.g. a person, project or client name."},
                "note": {"type": "string", "description": "The fact to remember, self-contained."},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            required=["topic", "note"],
        ),
        Risk.WRITE,
        "memory",
    )
    def remember(topic: str, note: str, tags: list[str] | None = None) -> str:
        entry = {
            "id": uuid.uuid4().hex[:8],
            "topic": topic,
            "note": note,
            "tags": tags or [],
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        store.update(lambda d: d["notes"].append(entry))
        return f"Saved note {entry['id']} under '{topic}'."

    @registry.add(
        "recall",
        "Search saved notes. Matches every word of the query against topic, note text and tags "
        "(case-insensitive). Use an empty query to list the most recent notes.",
        schema(
            {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "Max notes to return (default 20)."},
            },
            required=["query"],
        ),
        Risk.READ,
        "memory",
    )
    def recall(query: str, limit: int = 20):
        words = query.lower().split()
        notes = store.load()["notes"]
        hits = [
            n
            for n in notes
            if all(w in f"{n['topic']} {n['note']} {' '.join(n['tags'])}".lower() for w in words)
        ]
        hits = hits[-limit:]
        return hits or "No matching notes."

    @registry.add(
        "forget",
        "Delete a saved note by id (use when a note is wrong or superseded).",
        schema({"id": {"type": "string"}}),
        Risk.WRITE,
        "memory",
    )
    def forget(id: str) -> str:
        def rm(d):
            before = len(d["notes"])
            d["notes"] = [n for n in d["notes"] if n["id"] != id]
            return before != len(d["notes"])

        return f"Deleted note {id}." if store.update(rm) else f"No note with id {id}."
