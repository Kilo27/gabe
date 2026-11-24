"""Outbound email.

Ships with an "outbox" backend that writes messages to disk instead of
sending them, so Gabe can be trialled safely. Replace or extend this with a
company-specific integration (Gmail, Microsoft 365, SMTP) when going live;
keep send_email at Risk.EXTERNAL so policy still gates it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from gabe.tools.registry import Risk, ToolRegistry, schema


def setup(registry: ToolRegistry, ctx) -> None:
    backend = ctx.settings.get("backend", "outbox")
    if backend != "outbox":
        raise ValueError(f"email backend '{backend}' is not implemented yet")
    sender = ctx.settings.get("from_address", "gabe@example.com")
    outbox = ctx.path("outbox", ".keep").parent

    @registry.add(
        "send_email",
        "Send an email on the company's behalf. Write the full final text; "
        "this may require human approval depending on company policy.",
        schema(
            {
                "to": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cc": {"type": "array", "items": {"type": "string"}},
            },
            required=["to", "subject", "body"],
        ),
        Risk.EXTERNAL,
        "email",
    )
    def send_email(to: list[str], subject: str, body: str, cc: list[str] | None = None) -> str:
        msg_id = uuid.uuid4().hex[:10]
        record = {
            "id": msg_id,
            "from": sender,
            "to": to,
            "cc": cc or [],
            "subject": subject,
            "body": body,
            "queued": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (outbox / f"{msg_id}.json").write_text(json.dumps(record, indent=2))
        return f"Email {msg_id} placed in the outbox (outbox backend: not actually delivered)."
