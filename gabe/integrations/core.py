"""Always-useful basics: the current time in the company's timezone."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from gabe.tools.registry import Risk, ToolRegistry, schema


def setup(registry: ToolRegistry, ctx) -> None:
    tz_name = ctx.settings.get("timezone") or ctx.config.company.get("timezone") or "UTC"
    tz = ZoneInfo(tz_name)

    @registry.add(
        "get_current_time",
        "Get the current date, time and weekday in the company's timezone. "
        "Use this before reasoning about deadlines, 'today', or 'next week'.",
        schema({}),
        Risk.READ,
        "core",
    )
    def get_current_time() -> str:
        now = datetime.now(tz)
        return now.strftime(f"%A %Y-%m-%d %H:%M ({tz_name})")
