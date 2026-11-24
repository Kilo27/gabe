"""`gabe ui`: a local web page to see, change and watch Gabe's team.

Standard library only (no web framework), bound to 127.0.0.1. It serves one
page (index.html) and a small JSON API:

  GET    /api/team                  the org chart, skills, freedom levels, company rules
  PUT    /api/team/members/<id>     add or change a team member ("new" creates one)
  DELETE /api/team/members/<id>     remove a member (their reports move up a level)
  POST   /api/ask       {text}      give Gabe a request; runs in the background
  GET    /api/run                   live progress of the current request, in plain English
  POST   /api/approvals/<id> {approve}  approve or decline an action waiting for a person
  POST   /api/reset                 start a fresh conversation
  GET    /api/history               recent actions from the audit log

Approvals pause the run until someone clicks Approve or Decline on the page
(or APPROVAL_TIMEOUT passes, which counts as declined).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from gabe.agent import Agent
from gabe.app import base_registry, build_gabe
from gabe.config import Config
from gabe.plain import describe_action, describe_outcome, describe_skill, skill_label
from gabe.policy import Policy
from gabe.providers import Provider, make_provider
from gabe.team import (
    CHIEF_ID, DEFAULT_FREEDOM, FREEDOM_LEVELS, MAX_DEPTH, Member, TeamError,
    make_id, member_from_dict, save_team,
)
from gabe.tools.registry import Tool

APPROVAL_TIMEOUT = 30 * 60  # seconds; an unanswered approval counts as declined
PAGE = Path(__file__).with_name("index.html")


@dataclass
class Approval:
    id: str
    path: list[str]
    tool: str
    label: str
    summary: str
    input: dict[str, Any]
    status: str = "waiting"  # waiting | approved | declined | expired
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


@dataclass
class Run:
    id: str
    request: str
    status: str = "working"  # working | waiting | done | failed
    answer: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "request": self.request, "status": self.status, "answer": self.answer,
            "events": self.events, "approvals": [a.public() for a in self.approvals],
            "seconds": round(time.time() - self.started),
        }


class Workspace:
    """Everything the page talks to: the team file, one conversation with Gabe, its current run."""

    def __init__(self, cfg: Config, provider: Provider | None = None,
                 provider_factory: Callable[[], Provider] | None = None) -> None:
        self.cfg = cfg
        self._provider = provider
        self._provider_factory = provider_factory or (lambda: make_provider(cfg.provider))
        self.lock = threading.RLock()
        self.run: Run | None = None
        self.history: list[dict[str, str]] = []  # past requests and answers in this conversation
        self._agent: Agent | None = None
        self._team_stamp: Any = None
        self._pending: dict[str, Any] = {}

    # ---- team ------------------------------------------------------------------
    @property
    def editable(self) -> bool:
        return self.cfg.team_file is not None

    def _skills(self) -> list[Tool]:
        return base_registry(self.cfg).tools()

    def team_view(self) -> dict[str, Any]:
        team = self.cfg.team()
        tools = self._skills()
        policy = Policy.from_config(self.cfg.policy)
        members = []
        for m in team.members:
            d = m.to_dict()
            d["working"] = team.is_working(m.id)
            d["manager_name"] = self.cfg.assistant_name if m.reports_to == CHIEF_ID else team.get(m.reports_to).name
            d["missing_skills"] = [
                {"name": s, "label": skill_label(s)} for s in (m.skills or []) if s not in {t.name for t in tools}
            ]
            members.append(d)
        return {
            "chief": {"id": CHIEF_ID, "name": self.cfg.assistant_name,
                      "company": self.cfg.company.get("name", "your company")},
            "members": members,
            "skills": [{**describe_skill(t), "company_rule": policy.decide(t).value} for t in tools],
            "freedom_levels": [
                {"key": f.key, "label": f.label, "explanation": f.explanation,
                 "ceiling": {r.value: d.value for r, d in f.ceiling.items()}}
                for f in FREEDOM_LEVELS.values()
            ],
            "default_freedom": DEFAULT_FREEDOM,
            "max_depth": MAX_DEPTH,
            "editable": self.editable,
            "team_file": str(self.cfg.team_file) if self.cfg.team_file else None,
        }

    def save_member(self, member_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.editable:
            raise TeamError("This setup keeps the team in gabe.yaml. Add `team_file: team.yaml` to edit it here.")
        with self.lock:
            team = self.cfg.team()
            if member_id == "new":
                member_id = make_id(str(body.get("name", "")), {m.id for m in team.members})
            elif team.get(member_id) is None:
                raise TeamError("That team member no longer exists. Reload the page.")
            member = member_from_dict({**body, "id": member_id})
            # Skills come from checkboxes; ones not available right now are kept, not rejected.
            team.upsert(member)
            save_team(self.cfg.team_file, team)
            return member.to_dict()

    def remove_member(self, member_id: str) -> None:
        if not self.editable:
            raise TeamError("This setup keeps the team in gabe.yaml. Add `team_file: team.yaml` to edit it here.")
        with self.lock:
            team = self.cfg.team()
            team.remove(member_id)
            save_team(self.cfg.team_file, team)

    # ---- running requests ----------------------------------------------------------
    def _stamp(self) -> Any:
        f = self.cfg.team_file
        return f.stat().st_mtime_ns if f and f.exists() else None

    def _gabe(self) -> Agent:
        """Gabe with the current team. A team change starts a fresh conversation."""
        stamp = self._stamp()
        if self._agent is None or stamp != self._team_stamp:
            if self._provider is None:
                self._provider = self._provider_factory()
            self._agent = build_gabe(self.cfg, approver=self._approve, on_event=self._on_event,
                                     provider=self._provider)
            self._team_stamp = stamp
            self.history = []
        return self._agent

    def ask(self, text: str) -> Run:
        text = text.strip()
        if not text:
            raise TeamError("Type a request first.")
        with self.lock:
            if self.run and self.run.status in ("working", "waiting"):
                raise TeamError("Gabe is still working on the last request.")
            gabe = self._gabe()
            run = self.run = Run(uuid.uuid4().hex[:8], text)
        threading.Thread(target=self._work, args=(gabe, run), daemon=True).start()
        return run

    def _work(self, gabe: Agent, run: Run) -> None:
        try:
            answer = gabe.send(run.request)
            with self.lock:
                run.answer, run.status = answer, "done"
                self.history.append({"request": run.request, "answer": answer})
        except Exception as e:  # shown on the page instead of killing the server
            with self.lock:
                run.answer, run.status = f"Something went wrong: {type(e).__name__}: {e}", "failed"

    def reset(self) -> None:
        with self.lock:
            if self.run and self.run.status in ("working", "waiting"):
                raise TeamError("Wait for the current request to finish first.")
            self._agent, self.run, self.history = None, None, []

    def _add(self, path: list[str], kind: str, text: str, detail: str = "") -> None:
        with self.lock:
            if self.run:
                self.run.events.append({"kind": kind, "who": path[-1], "path": path,
                                        "depth": len(path) - 1, "text": text, "detail": detail,
                                        "at": round(time.time() - self.run.started)})

    def _on_event(self, kind: str, data: dict[str, Any]) -> None:
        path, who = data.get("path") or [data.get("agent", "?")], data.get("agent", "?")
        if kind == "delegate":
            self._add(path, "delegate", f"{who} asked {data['to_name']} to help", data["task"])
        elif kind == "report":
            self._add(path, "report", f"{who} reported back", data["text"])
        elif kind == "thought":
            self._add(path, "thought", f"{who} is working it out", data["text"])
        elif kind == "approval_needed":
            self._pending = data
        elif kind == "tool" and data["tool"] != "delegate":
            outcome = describe_outcome(data["outcome"])
            text = f"{who} {describe_action(data['tool'], data.get('input'))}"
            self._add(path, "problem" if outcome else "action", text + (f" ({outcome})" if outcome else ""))

    def _approve(self, tool: Tool, args: dict[str, Any]) -> bool:
        ctx = self._pending if self._pending.get("tool") == tool.name else {}
        path = ctx.get("path") or [self.cfg.assistant_name]
        approval = Approval(uuid.uuid4().hex[:8], path, tool.name, skill_label(tool.name),
                            f"{path[-1]} wants to {describe_action(tool.name, args, request=True)}",
                            args)
        with self.lock:
            run = self.run
            if run is None:
                return False
            run.approvals.append(approval)
            run.status = "waiting"
        answered = approval._done.wait(APPROVAL_TIMEOUT)
        with self.lock:
            if not answered:
                approval.status = "expired"
            if run.status == "waiting" and not any(a.status == "waiting" for a in run.approvals):
                run.status = "working"
        return approval.status == "approved"

    def decide(self, approval_id: str, approve: bool) -> None:
        with self.lock:
            run = self.run
            approval = next((a for a in (run.approvals if run else []) if a.id == approval_id), None)
            if approval is None or approval.status != "waiting":
                raise TeamError("That approval has already been handled.")
            approval.status = "approved" if approve else "declined"
            approval._done.set()

    def conversation(self) -> list[dict[str, str]]:
        with self.lock:
            return list(self.history)

    def run_view(self) -> dict[str, Any] | None:
        with self.lock:
            return self.run.public() if self.run else None

    # ---- history -------------------------------------------------------------------
    def history_view(self, limit: int = 200) -> list[dict[str, Any]]:
        path = self.cfg.data_dir / "audit.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().splitlines()[-limit:]
        team = self.cfg.team()
        out = []
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            who = e.get("chain") or e.get("agent", "?")
            text = describe_action(e.get("tool", ""), e.get("input"))
            if e.get("tool") == "delegate":
                to = str((e.get("input") or {}).get("specialist", ""))
                text = f"handed work to {team.get(to).name if team.get(to) else to}"
            out.append({
                "ts": e.get("ts", ""), "who": who, "text": text,
                "outcome": describe_outcome(e.get("outcome", "ok")),
                "problem": bool(e.get("error")),
            })
        return out


# ---- HTTP -----------------------------------------------------------------------------

def make_handler(ws: Workspace, port: int) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "GabeUI"

        def log_message(self, fmt: str, *args: Any) -> None:  # keep the terminal quiet
            pass

        def _send(self, status: int, body: Any, content_type: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _guard(self, writing: bool) -> bool:
            """Only this machine's browser, on this page, may use the API (blocks other websites)."""
            if self.headers.get("Host") not in allowed_hosts:
                self._send(HTTPStatus.FORBIDDEN, {"error": "Unknown host."})
                return False
            if writing:
                origin = self.headers.get("Origin")
                if origin is not None and origin.split("://", 1)[-1] not in allowed_hosts:
                    self._send(HTTPStatus.FORBIDDEN, {"error": "Requests from other websites are not allowed."})
                    return False
                if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                    self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send JSON."})
                    return False
            return True

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            raw = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(raw, dict):
                raise TeamError("Expected a JSON object.")
            return raw

        def _handle(self, method: str) -> None:
            if not self._guard(method != "GET"):
                return
            parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
            try:
                if method == "GET" and parts == []:
                    return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                if parts[:1] != ["api"]:
                    return self._send(404, {"error": "Not found."})
                route = parts[1:]
                if method == "GET" and route == ["team"]:
                    return self._send(200, ws.team_view())
                if method == "GET" and route == ["run"]:
                    return self._send(200, {"run": ws.run_view(), "conversation": ws.conversation()})
                if method == "GET" and route == ["history"]:
                    return self._send(200, {"entries": ws.history_view()})
                if method == "PUT" and route[:2] == ["team", "members"] and len(route) == 3:
                    return self._send(200, {"member": ws.save_member(route[2], self._body())})
                if method == "DELETE" and route[:2] == ["team", "members"] and len(route) == 3:
                    ws.remove_member(route[2])
                    return self._send(200, {"ok": True})
                if method == "POST" and route == ["ask"]:
                    return self._send(200, {"run": ws.ask(str(self._body().get("text", ""))).public()})
                if method == "POST" and route == ["reset"]:
                    ws.reset()
                    return self._send(200, {"ok": True})
                if method == "POST" and route[:1] == ["approvals"] and len(route) == 2:
                    ws.decide(route[1], bool(self._body().get("approve")))
                    return self._send(200, {"ok": True})
                return self._send(404, {"error": "Not found."})
            except (TeamError, ValueError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

    return Handler


def make_server(ws: Workspace, port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), None)  # type: ignore[arg-type]
    server.RequestHandlerClass = make_handler(ws, server.server_address[1])
    server.daemon_threads = True
    return server


def serve(cfg: Config, port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(Workspace(cfg), port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Gabe's team page is at {url}  (Ctrl-C to stop)")
    if open_browser:
        import webbrowser

        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
