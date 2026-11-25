"""Recurring jobs: things Gabe does on a timetable ("check my email every half hour",
"every Monday at 9, chase overdue tasks").

Gabe sets them up with the `schedule_job` tools; the app (gabe/web) runs each one when it
is due, in its own conversation, under the same approval policy as any other job. Each run
starts with a fresh memory, so the instruction must stand on its own. A run that finds
nothing worth telling the user ends with NOTHING_TO_REPORT and stays quiet.

Schedules live in data/schedules.json. Times are in the company timezone.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from gabe.config import Config
from gabe.integrations._store import JsonStore
from gabe.tools.registry import Risk, Tool, schema

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MIN_INTERVAL = 5  # minutes
NOTHING_TO_REPORT = "NOTHING TO REPORT"

RUN_NOTE = """

(This is a scheduled run of "{name}", {when}. {last}Nobody is watching it live. Do the job.
If there is nothing the user needs to know, reply with exactly: {quiet}
Otherwise lead with what needs their attention.)"""

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def store(cfg: Config) -> JsonStore:
    return JsonStore(cfg.data_dir / "schedules.json", {"schedules": []})


def company_tz(cfg: Config) -> ZoneInfo:
    return ZoneInfo(cfg.company.get("timezone") or "UTC")


def _clock(s: str) -> time:
    m = _HHMM.match(s.strip())
    if not m:
        raise ValueError(f"'{s}' isn't a time like 09:30.")
    return time(int(m.group(1)), int(m.group(2)))


def _window(between: str | None) -> tuple[time, time] | None:
    if not between:
        return None
    parts = [p.strip() for p in re.split(r"\s*[-–]\s*", between.strip())]
    if len(parts) != 2:
        raise ValueError(f"'{between}' isn't a time range like 08:00-18:00.")
    start, end = _clock(parts[0]), _clock(parts[1])
    if start >= end:
        raise ValueError("The time range has to start before it ends, on the same day.")
    return start, end


def _days(days: list[str] | None) -> list[str]:
    if not days:
        return list(DAYS)
    out = []
    for d in days:
        key = str(d).strip().lower()[:3]
        if key not in DAYS:
            raise ValueError(f"'{d}' isn't a day of the week.")
        if key not in out:
            out.append(key)
    return sorted(out, key=DAYS.index)


def normalise(every_minutes: int | None, at: str | None, days: list[str] | None,
              between: str | None) -> dict[str, Any]:
    """Check a timetable and return it in stored form. Raises ValueError with a readable reason."""
    if (every_minutes is None) == (not at):
        raise ValueError("Give either every_minutes (repeat through the day) or at (once a day), not both.")
    if every_minutes is not None:
        every_minutes = int(every_minutes)
        if every_minutes < MIN_INTERVAL:
            raise ValueError(f"The shortest interval is {MIN_INTERVAL} minutes.")
        if every_minutes > 7 * 24 * 60:
            raise ValueError("The longest interval is a week; use `at` and `days` instead.")
        window = _window(between)
        return {"every_minutes": every_minutes, "at": None, "days": _days(days),
                "between": f"{window[0]:%H:%M}-{window[1]:%H:%M}" if window else None}
    if between:
        raise ValueError("`between` only applies to every_minutes.")
    return {"every_minutes": None, "at": f"{_clock(at):%H:%M}", "days": _days(days), "between": None}


def next_after(s: dict[str, Any], now: datetime) -> datetime:
    """When schedule `s` next runs, strictly after `now` (tz-aware, company timezone)."""
    tz = now.tzinfo
    days = s.get("days") or DAYS
    if s.get("at"):
        t = _clock(s["at"])
        candidate = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        while DAYS[candidate.weekday()] not in days:
            candidate += timedelta(days=1)
        return candidate
    window = _window(s.get("between"))
    candidate = (now + timedelta(minutes=s["every_minutes"])).replace(second=0, microsecond=0)
    if DAYS[candidate.weekday()] in days and (window is None or window[0] <= candidate.time() < window[1]):
        return candidate
    # Outside the allowed days or hours: the next time the window opens.
    opens = window[0] if window else time(0)
    for d in range(8):
        day = candidate.date() + timedelta(days=d)
        start = datetime.combine(day, opens, tzinfo=tz)
        if start >= candidate and DAYS[day.weekday()] in days:
            return start
    raise ValueError("This schedule never runs.")  # unreachable: days is never empty


def describe(s: dict[str, Any]) -> str:
    """'every 30 minutes, 08:00-18:00, Mon-Fri' / 'at 09:00, every day'."""
    days = s.get("days") or DAYS
    if days == DAYS:
        on = "every day"
    elif days == DAYS[:5]:
        on = "weekdays"
    elif days == DAYS[5:]:
        on = "weekends"
    else:
        on = ", ".join(d.capitalize() for d in days)
    if s.get("at"):
        return f"at {s['at']}, {on}"
    n = s["every_minutes"]
    every = (f"every {n // 60} hours" if n % 60 == 0 and n > 60 else "every hour" if n == 60
             else f"every {n} minutes")
    return ", ".join(x for x in (every, s.get("between"), on) if x)


def run_prompt(s: dict[str, Any]) -> str:
    last = f"The previous run was at {s['last_run'][:16].replace('T', ' ')}. " if s.get("last_run") else ""
    return s["instruction"] + RUN_NOTE.format(name=s["name"], when=describe(s), last=last, quiet=NOTHING_TO_REPORT)


def is_quiet(answer: str) -> bool:
    return answer.strip().strip(".").upper().startswith(NOTHING_TO_REPORT)


def _now(cfg: Config) -> datetime:
    return datetime.now(company_tz(cfg))


def _public(s: dict[str, Any]) -> dict[str, Any]:
    return {**s, "when": describe(s)}


def create(cfg: Config, name: str, instruction: str, every_minutes: int | None = None, at: str | None = None,
           days: list[str] | None = None, between: str | None = None, created_by: str = "") -> dict[str, Any]:
    if not instruction.strip():
        raise ValueError("Say what to do on each run.")
    timing = normalise(every_minutes, at, days, between)
    now = _now(cfg)
    s = {"id": uuid.uuid4().hex[:8], "name": (name.strip() or instruction.strip())[:60], "instruction": instruction.strip(),
         **timing, "paused": False, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "created_by": created_by, "last_run": None, "runs": 0, "session_id": None}
    s["next_run"] = next_after(s, now).isoformat(timespec="seconds")
    store(cfg).update(lambda d: d["schedules"].append(s))
    return _public(s)


def list_all(cfg: Config) -> list[dict[str, Any]]:
    return [_public(s) for s in store(cfg).load()["schedules"]]


def update(cfg: Config, id: str, **changes: Any) -> dict[str, Any]:
    """Change a schedule. Timing fields given replace the timetable (the rest keep theirs)."""
    def upd(d):
        s = next((x for x in d["schedules"] if x["id"] == id), None)
        if s is None:
            raise KeyError(f"No schedule with id {id}.")
        timing_keys = ("every_minutes", "at", "days", "between")
        if any(changes.get(k) is not None for k in timing_keys):
            every, at = changes.get("every_minutes"), changes.get("at")
            if every is None and at is None:
                every, at = s.get("every_minutes"), s.get("at")
            between = changes.get("between") if changes.get("between") is not None else (s.get("between") if every else None)
            s.update(normalise(every, at, changes.get("days") or s.get("days"), between or None))
            s["next_run"] = next_after(s, _now(cfg)).isoformat(timespec="seconds")
        for k in ("name", "instruction"):
            if changes.get(k):
                s[k] = str(changes[k]).strip()
        if changes.get("paused") is not None:
            was = s.get("paused")
            s["paused"] = bool(changes["paused"])
            if was and not s["paused"]:  # resuming: no burst of catch-up for the paused time
                s["next_run"] = next_after(s, _now(cfg)).isoformat(timespec="seconds")
        return _public(s)

    return store(cfg).update(upd)


def remove(cfg: Config, id: str) -> dict[str, Any]:
    def upd(d):
        s = next((x for x in d["schedules"] if x["id"] == id), None)
        if s is None:
            raise KeyError(f"No schedule with id {id}.")
        d["schedules"].remove(s)
        return s

    return store(cfg).update(upd)


def claim_due(cfg: Config, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every active schedule that is due, each moved on to its next run (so a run that fails
    or is skipped never fires again in a loop). Missed runs (app closed) catch up once."""
    now = now or _now(cfg)

    def upd(d):
        out = []
        for s in d["schedules"]:
            if s.get("paused") or datetime.fromisoformat(s["next_run"]) > now:
                continue
            s["last_run_due"] = s["next_run"]
            s["next_run"] = next_after(s, now).isoformat(timespec="seconds")
            out.append(dict(s))
        return out

    return store(cfg).update(upd)


def record_run(cfg: Config, id: str, session_id: str | None, ran: bool, now: datetime | None = None) -> None:
    now = now or _now(cfg)

    def upd(d):
        for s in d["schedules"]:
            if s["id"] == id:
                if session_id:
                    s["session_id"] = session_id
                if ran:
                    s["last_run"] = now.isoformat(timespec="seconds")
                    s["runs"] = s.get("runs", 0) + 1
                else:
                    s["skipped"] = s.get("skipped", 0) + 1
    store(cfg).update(upd)


# ---------------------------------------------------------------------------
# Gabe's tools

_TIMING = {
    "every_minutes": {"type": "integer", "minimum": MIN_INTERVAL,
                      "description": "Repeat every N minutes (e.g. 30). Use this or `at`."},
    "at": {"type": "string", "description": "Run once a day at this time, HH:MM 24-hour. Use this or `every_minutes`."},
    "days": {"type": "array", "items": {"type": "string", "enum": DAYS},
             "description": "Days it runs on. Leave out for every day."},
    "between": {"type": "string",
                "description": "With every_minutes only: hours it runs in, HH:MM-HH:MM (e.g. 08:00-18:00)."},
}


def schedule_tools(cfg: Config, created_by: str) -> list[Tool]:
    def schedule_job(name: str, instruction: str, every_minutes: int | None = None, at: str | None = None,
                     days: list[str] | None = None, between: str | None = None):
        try:
            s = create(cfg, name, instruction, every_minutes, at, days, between, created_by=created_by)
        except ValueError as e:
            return f"Not scheduled: {e}"
        return {**s, "note": f"Scheduled {s['when']} (company timezone). First run: {s['next_run']}."}

    def list_schedules():
        return list_all(cfg) or "No recurring jobs."

    def update_schedule(id: str, **changes):
        try:
            return update(cfg, id, **changes)
        except (KeyError, ValueError) as e:
            return str(e).strip("'\"")

    def cancel_schedule(id: str):
        try:
            s = remove(cfg, id)
        except KeyError as e:
            return str(e).strip("'\"")
        return f"Cancelled '{s['name']}'."

    return [
        Tool(
            name="schedule_job",
            description=(
                "Set up a recurring job that runs on a timetable, e.g. 'check my email every half hour' "
                "or 'every Monday at 9, chase overdue tasks'. Use it whenever the user wants something "
                "done repeatedly or at set times; do it yourself, don't delegate it. Each run starts "
                "fresh without this conversation, so write `instruction` as a complete, standalone "
                "request (what to do, what counts as worth reporting). Runs are quiet when there's "
                "nothing to report. Times are in the company timezone. Runs while the Gabe app is open; "
                "a missed run happens once when it next opens."
            ),
            input_schema=schema(
                {"name": {"type": "string", "description": "Short name, e.g. 'Email check'."},
                 "instruction": {"type": "string", "description": "What to do on every run, standalone."},
                 **_TIMING},
                required=["name", "instruction"],
            ),
            handler=schedule_job, risk=Risk.WRITE, integration="schedules",
        ),
        Tool(
            name="list_schedules",
            description="List recurring jobs: what each does, when it runs, next run, and whether it's paused.",
            input_schema=schema({}), handler=list_schedules, risk=Risk.READ, integration="schedules",
        ),
        Tool(
            name="update_schedule",
            description=("Change a recurring job: pause or resume it, change what it does, or change "
                         "when it runs. Look up its id with list_schedules."),
            input_schema=schema(
                {"id": {"type": "string"}, "paused": {"type": "boolean"}, "name": {"type": "string"},
                 "instruction": {"type": "string"}, **_TIMING},
                required=["id"],
            ),
            handler=update_schedule, risk=Risk.WRITE, integration="schedules",
        ),
        Tool(
            name="cancel_schedule",
            description="Stop and delete a recurring job. Look up its id with list_schedules.",
            input_schema=schema({"id": {"type": "string"}}, required=["id"]),
            handler=cancel_schedule, risk=Risk.WRITE, integration="schedules",
        ),
    ]
