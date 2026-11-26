"""Calendar backends for the calendar integration.

- `GoogleCalendarClient`: a Google Calendar (Google Workspace or personal) via the Calendar
  API, signed in with the same Google OAuth client as Gmail but its own token.
- `LocalCalendar`: a practice calendar kept in data/calendar/events.json. Nobody is invited
  and nothing leaves the computer.

Both take and return Google-shaped event bodies, so `normalize` and the integration's tools
work the same over either.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from gabe.integrations._store import JsonStore

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",    # read and change events
    "https://www.googleapis.com/auth/calendar.freebusy",  # when colleagues are busy (no details)
]
NOT_SIGNED_IN = "Google Calendar isn't signed in. Use Sign in with Google in Settings → Calendar."
MAX_DESCRIPTION_CHARS = 4_000


# -- Times ----------------------------------------------------------------------

def parse_when(value: str, tz: ZoneInfo) -> datetime | date:
    """'2026-11-27' is a whole day; '2026-11-27T14:00' is a time in the company's timezone
    unless it carries its own offset ('…+01:00' or '…Z')."""
    value = value.strip()
    if "T" not in value and " " not in value:
        return date.fromisoformat(value)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def to_google(when: datetime | date, tz_name: str) -> dict[str, str]:
    if isinstance(when, datetime):
        return {"dateTime": when.isoformat(), "timeZone": tz_name}
    return {"date": when.isoformat()}


def from_google(when: dict[str, Any], tz: ZoneInfo) -> datetime | date:
    if "dateTime" in when:
        return datetime.fromisoformat(when["dateTime"].replace("Z", "+00:00")).astimezone(tz)
    return date.fromisoformat(when["date"])


def as_datetime(when: datetime | date, tz: ZoneInfo) -> datetime:
    """A whole day counts from its midnight, in the company's timezone."""
    if isinstance(when, datetime):
        return when
    return datetime(when.year, when.month, when.day, tzinfo=tz)


def _show(when: datetime | date) -> str:
    return when.strftime("%Y-%m-%dT%H:%M") if isinstance(when, datetime) else when.isoformat()


def normalize(event: dict[str, Any], tz: ZoneInfo) -> dict[str, Any]:
    """The parts of an event Gabe needs, with times in the company's timezone. A whole-day
    event's `end` is its last day (Google's own end date is the day after)."""
    start = from_google(event.get("start") or {}, tz)
    end = from_google(event.get("end") or event.get("start") or {}, tz)
    all_day = not isinstance(start, datetime)
    if all_day and end > start:
        end = end - timedelta(days=1)
    out: dict[str, Any] = {
        "id": event["id"],
        "title": event.get("summary") or "(no title)",
        "start": _show(start),
        "end": _show(end),
        "all_day": all_day,
    }
    if event.get("location"):
        out["location"] = event["location"]
    link = event.get("hangoutLink") or next(
        (e.get("uri") for e in (event.get("conferenceData") or {}).get("entryPoints", []) if e.get("entryPointType") == "video"),
        None,
    )
    if link:
        out["video_link"] = link
    people = [
        {"email": a.get("email"), **({"name": a["displayName"]} if a.get("displayName") else {}),
         "response": a.get("responseStatus", "needsAction")}
        for a in event.get("attendees") or [] if not a.get("resource")
    ]
    if people:
        out["attendees"] = people
    org = event.get("organizer") or {}
    if org.get("email"):
        out["organizer"] = org["email"]
    mine = next((a for a in event.get("attendees") or [] if a.get("self")), None)
    if mine:
        out["my_response"] = mine.get("responseStatus", "needsAction")
    if event.get("description"):
        d = event["description"]
        out["description"] = d[:MAX_DESCRIPTION_CHARS] + ("\n[...truncated]" if len(d) > MAX_DESCRIPTION_CHARS else "")
    if event.get("status") == "cancelled":
        out["cancelled"] = True
    return out


def overlaps(event: dict[str, Any], start: datetime, end: datetime, tz: ZoneInfo) -> bool:
    s = as_datetime(from_google(event["start"], tz), tz)
    e = as_datetime(from_google(event["end"], tz), tz)
    return s < end and e > start


# -- Google Calendar ------------------------------------------------------------

def build_service(creds):
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


class GoogleCalendarClient:
    """Thin wrapper over the Calendar API. `service_factory` is called lazily so Gabe
    starts before the calendar is signed in (its actions then return an error)."""

    def __init__(self, service_factory, calendar_id: str = "primary") -> None:
        self._factory = service_factory
        self._service = None
        self.calendar_id = calendar_id

    @property
    def svc(self):
        if self._service is None:
            self._service = self._factory()
        return self._service

    def address(self) -> str:
        """The signed-in account (the primary calendar is named after it)."""
        return self.svc.events().list(calendarId="primary", maxResults=1).execute().get("summary", "")

    def events(self, start: datetime, end: datetime, query: str | None = None, max_results: int = 50) -> list[dict]:
        kw: dict[str, Any] = dict(
            calendarId=self.calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=max_results,
        )
        if query:
            kw["q"] = query
        return self.svc.events().list(**kw).execute().get("items", [])

    def get(self, event_id: str) -> dict:
        return self.svc.events().get(calendarId=self.calendar_id, eventId=event_id).execute()

    def busy(self, start: datetime, end: datetime, emails: list[str]) -> dict[str, list[tuple[str, str]]]:
        """Busy times per calendar ("me" is this calendar). Calendars Google won't share
        come back under `errors`, so the caller can say whose time is unknown."""
        ids = [self.calendar_id] + [e for e in emails if e != self.calendar_id]
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": i} for i in ids]}
        res = self.svc.freebusy().query(body=body).execute().get("calendars", {})
        out: dict[str, Any] = {}
        for i in ids:
            cal = res.get(i, {})
            key = "me" if i == self.calendar_id else i
            if cal.get("errors"):
                out.setdefault("errors", []).append(key)
                continue
            out[key] = [(b["start"], b["end"]) for b in cal.get("busy", [])]
        return out

    def create(self, body: dict, notify: bool, video: bool = False) -> dict:
        kw: dict[str, Any] = {}
        if video:
            body = {**body, "conferenceData": {"createRequest": {
                "requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}}
            kw["conferenceDataVersion"] = 1
        return self.svc.events().insert(calendarId=self.calendar_id, body=body,
                                        sendUpdates="all" if notify else "none", **kw).execute()

    def update(self, event_id: str, changes: dict, notify: bool) -> dict:
        return self.svc.events().patch(calendarId=self.calendar_id, eventId=event_id, body=changes,
                                       sendUpdates="all" if notify else "none").execute()

    def cancel(self, event_id: str, notify: bool) -> None:
        self.svc.events().delete(calendarId=self.calendar_id, eventId=event_id,
                                 sendUpdates="all" if notify else "none").execute()


# -- Practice calendar ----------------------------------------------------------

class LocalCalendar:
    """Events kept in a JSON file. Guests are recorded but never invited."""

    def __init__(self, path: Path, tz: ZoneInfo, owner: str = "me") -> None:
        self.store = JsonStore(path, {"events": []})
        self.tz = tz
        self.owner = owner

    def address(self) -> str:
        return self.owner

    def events(self, start: datetime, end: datetime, query: str | None = None, max_results: int = 50) -> list[dict]:
        q = (query or "").lower()
        found = [
            e for e in self.store.load()["events"]
            if overlaps(e, start, end, self.tz)
            and (not q or q in " ".join(str(e.get(k, "")) for k in ("summary", "description", "location")).lower()
                 or any(q in (a.get("email") or "").lower() for a in e.get("attendees") or []))
        ]
        found.sort(key=lambda e: as_datetime(from_google(e["start"], self.tz), self.tz))
        return found[:max_results]

    def get(self, event_id: str) -> dict:
        for e in self.store.load()["events"]:
            if e["id"] == event_id:
                return e
        raise KeyError(f"No event with id {event_id}.")

    def busy(self, start: datetime, end: datetime, emails: list[str]) -> dict[str, Any]:
        mine = [
            (as_datetime(from_google(e["start"], self.tz), self.tz).isoformat(),
             as_datetime(from_google(e["end"], self.tz), self.tz).isoformat())
            for e in self.events(start, end, max_results=500) if e.get("transparency") != "transparent"
        ]
        out: dict[str, Any] = {"me": mine}
        if emails:
            out["errors"] = list(emails)  # a practice calendar can't see anyone else's
        return out

    def create(self, body: dict, notify: bool, video: bool = False) -> dict:
        event = {**body, "id": uuid.uuid4().hex[:10], "status": "confirmed",
                 "organizer": {"email": self.owner, "self": True},
                 "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.store.update(lambda d: d["events"].append(event))
        return event

    def update(self, event_id: str, changes: dict, notify: bool) -> dict:
        def fn(d):
            for e in d["events"]:
                if e["id"] == event_id:
                    e.update(changes)
                    return dict(e)
            raise KeyError(f"No event with id {event_id}.")

        return self.store.update(fn)

    def cancel(self, event_id: str, notify: bool) -> None:
        def fn(d):
            before = len(d["events"])
            d["events"] = [e for e in d["events"] if e["id"] != event_id]
            if len(d["events"]) == before:
                raise KeyError(f"No event with id {event_id}.")

        self.store.update(fn)
