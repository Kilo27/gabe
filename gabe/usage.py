"""Token usage per model call, tagged with who asked and for what.

Every provider step appends one line to data/usage.jsonl. `gabe usage` sums it by
user, task and model, and prices it with the `pricing:` table in config, so model
and hosting choices can be made from real numbers rather than estimates.
"""

from __future__ import annotations

import getpass
import json
import os
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Normalised usage keys every provider reports. `input_tokens` excludes cached input.
TOKEN_KEYS = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")
# Price keys in config, in $ per million tokens, matching TOKEN_KEYS.
PRICE_KEYS = {"input_tokens": "input", "cache_read_tokens": "cache_read",
              "cache_write_tokens": "cache_write", "output_tokens": "output"}


def resolve_user(explicit: str | None = None) -> str:
    """Who the run is for: --user, then $GABE_USER, then the OS login."""
    if explicit:
        return explicit
    if os.environ.get("GABE_USER"):
        return os.environ["GABE_USER"]
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


class UsageLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, *, user: str, task: str, agent: str, usage: dict[str, Any]) -> None:
        if not self.path or not usage:
            return
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "user": user,
            "task": task,
            "agent": agent,
            "model": usage.get("model"),
            **{k: int(usage.get(k) or 0) for k in TOKEN_KEYS},
        }
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(event) + "\n")


def read_events(path: Path, since: str | None = None) -> list[dict[str, Any]]:
    """Events from the log, optionally only those on or after `since` (YYYY-MM-DD, UTC)."""
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if since and e.get("ts", "")[:10] < since:
            continue
        events.append(e)
    return events


def cost(event: dict[str, Any], pricing: dict[str, dict[str, float]]) -> float | None:
    """Dollar cost of one event, or None if its model has no price in config."""
    prices = pricing.get(event.get("model") or "")
    if not prices:
        return None
    total = 0.0
    for key, price_key in PRICE_KEYS.items():
        rate = prices.get(price_key)
        if rate is None and price_key in ("cache_read", "cache_write"):
            rate = prices.get("input", 0)  # no cache price given: bill as plain input
        total += event.get(key, 0) * (rate or 0) / 1_000_000
    return total


@dataclass
class Row:
    key: tuple[str, ...]
    calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    unpriced: bool = False  # at least one call had no price


def summarize(events: Iterable[dict[str, Any]], by: list[str],
              pricing: dict[str, dict[str, float]]) -> list[Row]:
    rows: dict[tuple[str, ...], Row] = {}
    for e in events:
        key = tuple(str(e.get(f) or "-") for f in by)
        row = rows.setdefault(key, Row(key))
        row.calls += 1
        for k in TOKEN_KEYS:
            setattr(row, k, getattr(row, k) + e.get(k, 0))
        c = cost(e, pricing)
        if c is None:
            row.unpriced = True
        else:
            row.cost += c
    return sorted(rows.values(), key=lambda r: -r.cost)


def format_table(rows: list[Row], by: list[str]) -> str:
    def m(n: int) -> str:
        return f"{n / 1_000_000:.2f}M" if n >= 100_000 else f"{n:,}"

    header = [*by, "calls", "input", "cache read", "cache write", "output", "est. cost"]
    body = [
        [*r.key, str(r.calls), m(r.input_tokens), m(r.cache_read_tokens), m(r.cache_write_tokens),
         m(r.output_tokens), f"${r.cost:,.2f}" + ("*" if r.unpriced else "")]
        for r in rows
    ]
    total = Row(("total",) + ("",) * (len(by) - 1))
    for r in rows:
        total.calls += r.calls
        for k in TOKEN_KEYS:
            setattr(total, k, getattr(total, k) + getattr(r, k))
        total.cost += r.cost
        total.unpriced |= r.unpriced
    body.append([*total.key, str(total.calls), m(total.input_tokens), m(total.cache_read_tokens),
                 m(total.cache_write_tokens), m(total.output_tokens),
                 f"${total.cost:,.2f}" + ("*" if total.unpriced else "")])
    widths = [max(len(row[i]) for row in [header, *body]) for i in range(len(header))]
    lines = ["  ".join(c.ljust(w) if i < len(by) else c.rjust(w) for i, (c, w) in enumerate(zip(row, widths)))
             for row in [header, *body]]
    lines.insert(1, "  ".join("-" * w for w in widths))
    lines.insert(-1, "  ".join("-" * w for w in widths))
    if total.unpriced:
        lines.append("* some calls used a model with no entry under `pricing:` in config; their cost is not included.")
    return "\n".join(lines)
