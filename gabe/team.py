"""Gabe's team: a hierarchy of AI assistants, like an org chart.

Gabe (the chief of staff) sits at the top. Every other team member reports to
Gabe or to another member, and can hand work down to its own direct reports.
The team lives in a small YAML file (`team_file` in config) that the Agents
screen in the app edits, so it can be changed without touching code.

Each member has:
- a name and a one-line job description (its manager uses this to pick who to ask),
- instructions on how to do the job,
- skills: which tools it may use,
- a freedom level that can only make it *more* careful than company policy,
- an on/off switch. A paused member and everyone under it are left out of the team.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gabe.policy import Decision
from gabe.tools.registry import Risk

CHIEF_ID = "chief"  # reserved id for Gabe, the top of the hierarchy
MAX_DEPTH = 4       # levels below Gabe; keeps chains of hand-offs short and understandable

_A, _Q, _D = Decision.ALLOW, Decision.ASK, Decision.DENY


@dataclass(frozen=True)
class FreedomLevel:
    key: str
    label: str
    explanation: str
    ceiling: dict[Risk, Decision]


FREEDOM_LEVELS: dict[str, FreedomLevel] = {
    lvl.key: lvl
    for lvl in [
        FreedomLevel(
            "look_only",
            "Look, don't touch",
            "Can look things up and report back, but can't change or send anything.",
            {Risk.READ: _A, Risk.WRITE: _D, Risk.EXTERNAL: _D},
        ),
        FreedomLevel(
            "ask_first",
            "Check with me first",
            "Looks things up freely. Asks you before changing anything or contacting anyone.",
            {Risk.READ: _A, Risk.WRITE: _Q, Risk.EXTERNAL: _Q},
        ),
        FreedomLevel(
            "standard",
            "Normal",
            "Keeps tasks, notes and drafts up to date on its own. Asks you before contacting anyone outside.",
            {Risk.READ: _A, Risk.WRITE: _A, Risk.EXTERNAL: _Q},
        ),
        FreedomLevel(
            "trusted",
            "Trusted",
            "Follows the company rules only, with no extra checks. It can never do more than company policy allows.",
            {Risk.READ: _A, Risk.WRITE: _A, Risk.EXTERNAL: _A},
        ),
    ]
}
DEFAULT_FREEDOM = "standard"


class TeamError(ValueError):
    """A team change that would leave the team broken. The message is shown to the user."""


@dataclass
class Member:
    id: str
    name: str
    job: str = ""
    instructions: str = ""
    reports_to: str = CHIEF_ID
    skills: list[str] | None = None  # None = every skill Gabe has
    freedom: str = DEFAULT_FREEDOM
    active: bool = True

    @property
    def freedom_level(self) -> FreedomLevel:
        return FREEDOM_LEVELS[self.freedom]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_id(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "member"
    if base == CHIEF_ID:
        base = "chief_helper"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}_{n}", n + 1
    return out


@dataclass
class Team:
    members: list[Member] = field(default_factory=list)

    # ---- lookups ---------------------------------------------------------------
    def get(self, member_id: str) -> Member | None:
        return next((m for m in self.members if m.id == member_id), None)

    def reports(self, manager_id: str, active_only: bool = True) -> list[Member]:
        """Direct reports of a manager (or of Gabe, with CHIEF_ID)."""
        return [
            m for m in self.members
            if m.reports_to == manager_id and (m.active or not active_only)
        ]

    def chain(self, member_id: str) -> list[Member]:
        """The member and its managers, bottom-up (Gabe not included)."""
        out: list[Member] = []
        cur = self.get(member_id)
        while cur is not None:
            out.append(cur)
            cur = self.get(cur.reports_to)
        return out

    def is_working(self, member_id: str) -> bool:
        """Active and not under a paused manager."""
        chain = self.chain(member_id)
        return bool(chain) and all(m.active for m in chain)

    def depth(self, member_id: str) -> int:
        return len(self.chain(member_id))

    # ---- validation --------------------------------------------------------------
    def validate(self, known_skills: set[str] | None = None) -> None:
        ids = [m.id for m in self.members]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise TeamError(f"Two team members share the id '{sorted(dupes)[0]}'.")
        for m in self.members:
            if m.id == CHIEF_ID:
                raise TeamError(f"'{CHIEF_ID}' is reserved for the chief of staff.")
            if not re.fullmatch(r"[a-z0-9_]+", m.id):
                raise TeamError(f"'{m.id}' isn't a valid id (use lowercase letters, numbers and _).")
            if not m.name.strip():
                raise TeamError("Every team member needs a name.")
            if m.freedom not in FREEDOM_LEVELS:
                raise TeamError(f"{m.name}: unknown freedom level '{m.freedom}'.")
            if m.reports_to != CHIEF_ID and self.get(m.reports_to) is None:
                raise TeamError(f"{m.name} reports to '{m.reports_to}', who isn't on the team.")
            if known_skills is not None and m.skills is not None:
                unknown = sorted(set(m.skills) - known_skills)
                if unknown:
                    raise TeamError(f"{m.name} has skills that don't exist: {', '.join(unknown)}.")
        for m in self.members:
            seen = {m.id}
            cur = self.get(m.reports_to)
            while cur is not None:
                if cur.id in seen:
                    raise TeamError(f"{m.name} would end up managing themselves. Pick a different manager.")
                seen.add(cur.id)
                cur = self.get(cur.reports_to)
            if len(seen) > MAX_DEPTH:
                raise TeamError(
                    f"{m.name} would be {len(seen)} levels below the chief of staff; the limit is {MAX_DEPTH}."
                )

    # ---- edits (used by the Agents screen) ------------------------------------------
    def upsert(self, member: Member, known_skills: set[str] | None = None) -> None:
        updated = Team([member if m.id == member.id else m for m in self.members])
        if self.get(member.id) is None:
            updated.members.append(member)
        updated.validate(known_skills)
        self.members = updated.members

    def remove(self, member_id: str) -> Member:
        """Remove a member. Their direct reports move up to the removed member's manager."""
        gone = self.get(member_id)
        if gone is None:
            raise TeamError(f"No team member with id '{member_id}'.")
        self.members = [m for m in self.members if m.id != member_id]
        for m in self.members:
            if m.reports_to == member_id:
                m.reports_to = gone.reports_to
        return gone


# ---- files ---------------------------------------------------------------------

_HEADER = """\
# Gabe's team: who Gabe can hand work to, and who they can hand work to.
# Edited from the Agents screen in the Gabe app; editing by hand is fine too.
# reports_to: "chief" means the member reports straight to Gabe.
# skills: omit to allow every tool Gabe has. freedom: look_only | ask_first | standard | trusted
"""


def member_from_dict(raw: dict[str, Any], fallback_id: str | None = None) -> Member:
    freedom = raw.get("freedom", DEFAULT_FREEDOM)
    skills = raw.get("skills", raw.get("tools"))
    return Member(
        id=str(raw.get("id") or fallback_id or ""),
        name=str(raw.get("name") or raw.get("id") or fallback_id or "").strip(),
        job=str(raw.get("job") or raw.get("description") or "").strip(),
        instructions=str(raw.get("instructions") or "").strip(),
        reports_to=str(raw.get("reports_to") or CHIEF_ID),
        skills=None if skills is None else [str(s) for s in skills],
        freedom=str(freedom),
        active=bool(raw.get("active", True)),
    )


def load_team(path: Path) -> Team:
    if not path.exists():
        return Team()
    raw = yaml.safe_load(path.read_text()) or {}
    team = Team([member_from_dict(m) for m in raw.get("members") or []])
    team.validate()
    return team


def save_team(path: Path, team: Team) -> None:
    team.validate()
    members = []
    for m in team.members:
        d = m.to_dict()
        if d["skills"] is None:
            del d["skills"]
        members.append(d)
    body = yaml.safe_dump({"members": members}, sort_keys=False, allow_unicode=True, width=100)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_HEADER + "\n" + body)
    tmp.replace(path)  # atomic: a crash never leaves a half-written team file
