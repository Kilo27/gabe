"""Server-side state for the web app: chat sessions, the approval queue, background
jobs and config editing. Everything is kept in JSON files under `data/web/` so a
single-process deployment needs nothing else.

Approvals: a chat run executes on a worker thread. When the policy says `ask`, the
web approver records a pending approval and blocks that thread until a person decides
in the app (approve, edit then approve, or reject with a note). Approvals still waiting
when the server stops are marked expired on the next start; Gabe was told no.
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from gabe.agent import Agent
from gabe.app import build_gabe
from gabe.audit import AuditLog
from gabe.config import Config, load_config
from gabe.integrations._store import JsonStore
from gabe.policy import Declined
from gabe.tools.registry import Tool, validate_input


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def friendly_error(e: BaseException) -> str:
    """Turn an exception into something a non-technical person can act on."""
    name = type(e).__name__
    text = str(e)
    if name == "AuthenticationError" or "api_key" in text.lower() or "auth" in name.lower():
        return (
            "Gabe couldn't sign in to its AI model. The API key is missing or wrong. "
            "Whoever set Gabe up needs to check the key (ANTHROPIC_API_KEY)."
        )
    if name in ("APIConnectionError", "ConnectError", "ConnectTimeout"):
        return "Gabe couldn't reach its AI model. Check the internet connection, then try again."
    if name == "RateLimitError":
        return "Gabe's AI model is busy right now (rate limit). Wait a minute and try again."
    if "invalid_grant" in text or "Token has been expired" in text:
        return "The Gmail connection has expired. Reconnect Gmail in Settings, then try again."
    return f"Something went wrong: {text or name}"


# ---------------------------------------------------------------------------
# Approvals


class ApprovalQueue:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, {"approvals": []})
        self._events: dict[str, threading.Event] = {}
        self._guard = threading.Lock()
        # Anything still pending belongs to a run that died with the last server.
        def expire(d):
            for a in d["approvals"]:
                if a["status"] == "pending":
                    a["status"] = "expired"
                    a["decided"] = now()
                    a["note"] = "The app closed before this was answered."
        self.store.update(expire)

    def all(self) -> list[dict[str, Any]]:
        return sorted(self.store.load()["approvals"], key=lambda a: a["created"], reverse=True)

    def get(self, approval_id: str) -> dict[str, Any] | None:
        return next((a for a in self.store.load()["approvals"] if a["id"] == approval_id), None)

    def pending_count(self) -> int:
        return sum(1 for a in self.store.load()["approvals"] if a["status"] == "pending")

    def create(self, **fields: Any) -> dict[str, Any]:
        rec = {"id": new_id(), "kind": "approval", "status": "pending", "created": now(), "decided": None,
               "note": "", "edited": False, **fields}
        with self._guard:
            self._events[rec["id"]] = threading.Event()
        self.store.update(lambda d: d["approvals"].append(rec))
        return rec

    def wait(self, approval_id: str) -> dict[str, Any]:
        ev = self._events[approval_id]
        ev.wait()
        with self._guard:
            self._events.pop(approval_id, None)
        return self.get(approval_id) or {"status": "rejected"}

    def is_live(self, approval_id: str) -> bool:
        with self._guard:
            return approval_id in self._events

    def answer(self, approval_id: str, text: str, by: str) -> dict:
        """Answer a question from Gabe or a bot; the waiting run carries on with it."""
        def upd(d):
            for a in d["approvals"]:
                if a["id"] == approval_id:
                    if a["status"] != "pending":
                        raise ValueError("This question has already been answered.")
                    a.update(status="answered", answer=text, decided=now(), decided_by=by)
                    return a
            raise KeyError(approval_id)

        rec = self.store.update(upd)
        with self._guard:
            ev = self._events.get(approval_id)
        if ev:
            ev.set()
        return rec

    def decide(self, approval_id: str, approve: bool, args: dict | None, note: str, by: str) -> dict:
        def upd(d):
            for a in d["approvals"]:
                if a["id"] == approval_id:
                    if a["status"] != "pending":
                        raise ValueError("This request has already been decided.")
                    a["status"] = "approved" if approve else "rejected"
                    a["decided"] = now()
                    a["note"] = note
                    a["decided_by"] = by
                    if approve and args is not None and args != a["args"]:
                        a["original_args"] = a["args"]
                        a["args"] = args
                        a["edited"] = True
                    return a
            raise KeyError(approval_id)

        rec = self.store.update(upd)
        with self._guard:
            ev = self._events.get(approval_id)
        if ev:
            ev.set()
        return rec


# ---------------------------------------------------------------------------
# Chat sessions


@dataclass
class _Live:
    agent: Agent
    message_id: str = ""
    last_thought: str = ""
    asking_agent: str = ""
    running: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class Sessions:
    def __init__(self, root: Path, app: "WebState") -> None:
        self.root = root
        self.app = app
        root.mkdir(parents=True, exist_ok=True)
        self._live: dict[str, _Live] = {}
        self._guard = threading.Lock()
        # Replies cut off by a restart or crash are picked up again by resume_interrupted().
        self.interrupted: list[tuple[str, str]] = []  # (session id, reply message id)
        for p in root.glob("*.json"):
            store = JsonStore(p, {})

            def fix(s, sid=p.stem):
                for m in s.get("messages", []):
                    if m.get("status") in ("working", "waiting", "interrupted"):
                        m["status"] = "interrupted"
                        self.interrupted.append((sid, m["id"]))
                s["status"] = "idle"

            store.update(fix)

    def _store(self, sid: str) -> JsonStore:
        return JsonStore(self.root / f"{sid}.json", {})

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in self.root.glob("*.json"):
            s = JsonStore(p, {}).load()
            if not s:
                continue
            last = s["messages"][-1] if s.get("messages") else None
            out.append({
                "id": s["id"], "title": s["title"], "agent": s.get("agent"), "created": s["created"], "updated": s["updated"],
                "status": s.get("status", "idle"), "count": len(s.get("messages", [])),
                "preview": (last or {}).get("text", "")[:140],
            })
        return sorted(out, key=lambda s: s["updated"], reverse=True)

    def get(self, sid: str) -> dict[str, Any] | None:
        p = self.root / f"{sid}.json"
        if not p.exists():
            return None
        s = self._store(sid).load()
        live = self._live.get(sid)
        s["memory_reset"] = bool(s.get("messages")) and live is None
        return s

    def create(self, title: str = "New conversation", agent: str | None = None) -> dict[str, Any]:
        s = {"id": new_id(), "title": title, "created": now(), "updated": now(),
             "status": "idle", "messages": [], "agent": agent}
        self._store(s["id"]).save(s)
        return s

    def rename(self, sid: str, title: str) -> None:
        self._store(sid).update(lambda s: s.update(title=title.strip() or s["title"]))

    def delete(self, sid: str) -> None:
        live = self._live.get(sid)
        if live and live.running:
            raise RuntimeError("Gabe is still working in this conversation.")
        (self.root / f"{sid}.json").unlink(missing_ok=True)
        with self._guard:
            self._live.pop(sid, None)

    def _patch_message(self, sid: str, mid: str, fn: Callable[[dict], None]) -> None:
        def upd(s):
            for m in s["messages"]:
                if m["id"] == mid:
                    fn(m)
            s["updated"] = now()
        self._store(sid).update(upd)

    def _live_for(self, sid: str) -> _Live:
        with self._guard:
            if sid in self._live:
                return self._live[sid]
            cfg = self.app.config()
            member = self._store(sid).load().get("agent")  # a team member's id, or None for Gabe
            holder: dict[str, _Live] = {}

            def on_event(kind: str, data: dict[str, Any]) -> None:
                live = holder["live"]
                self.app.track(kind, data, sid)
                if kind == "approval_needed":
                    live.asking_agent = data.get("agent", "")
                elif kind == "step":
                    self.app.record_usage(data.get("usage") or {})
                elif kind == "thought":
                    live.last_thought = data.get("text", "").strip()
                    self._patch_message(sid, live.message_id, lambda m: m["activity"].append(
                        {"kind": "thought", "agent": data.get("agent"), "text": live.last_thought, "ts": now()}))
                elif kind == "tool":
                    self._patch_message(sid, live.message_id, lambda m: m["activity"].append(
                        {"kind": "tool", "agent": data.get("agent"), "tool": data.get("tool"),
                         "input": data.get("input"), "outcome": data.get("outcome"), "ts": now()}))

            def approver(tool: Tool, args: dict[str, Any]):
                live = holder["live"]
                session = self._store(sid).load()
                rec = self.app.approvals.create(
                    session_id=sid, message_id=live.message_id, session_title=session.get("title", ""),
                    agent=live.asking_agent or cfg.assistant_name,
                    tool=tool.name, risk=tool.risk.value, integration=tool.integration,
                    description=tool.description, args=dict(args), reason=live.last_thought,
                )

                def waiting(m):
                    m["status"] = "waiting"
                    m["activity"].append({"kind": "approval", "approval_id": rec["id"], "tool": tool.name, "ts": now()})
                self._patch_message(sid, live.message_id, waiting)
                decided = self.app.approvals.wait(rec["id"])
                self._patch_message(sid, live.message_id, lambda m: m.update(status="working"))
                if decided.get("status") == "approved":
                    args.clear()
                    args.update(decided["args"])
                    return True
                return Declined(decided.get("note", ""))

            def asker(agent_name: str, question: str, choices: list) -> str:
                """A question from Gabe or a bot: queued with the approvals, waits for an answer."""
                live = holder["live"]
                session = self._store(sid).load()
                rec = self.app.approvals.create(
                    kind="question", session_id=sid, message_id=live.message_id,
                    session_title=session.get("title", ""), agent=agent_name, tool="ask_user",
                    risk="read", integration="people", description="",
                    args={"question": question, "choices": choices}, reason="",
                )

                def waiting(m):
                    m["status"] = "waiting"
                    m["activity"].append({"kind": "approval", "approval_id": rec["id"], "tool": "ask_user", "ts": now()})
                self._patch_message(sid, live.message_id, waiting)
                answered = self.app.approvals.wait(rec["id"])
                self._patch_message(sid, live.message_id, lambda m: m.update(status="working"))
                if answered.get("status") == "answered":
                    return answered.get("answer", "")
                return "The user didn't answer. Make a reasonable assumption, say what you assumed, and carry on."

            agent = build_gabe(cfg, approver=approver, on_event=on_event, member=member, asker=asker)
            live = _Live(agent=agent)
            holder["live"] = live
            self._live[sid] = live
            return live

    def send(self, sid: str, text: str, task: dict[str, Any] | None = None) -> dict[str, Any]:
        """Give the agent a message. With `task` (a task-list entry), the agent gets its details
        and is asked to keep it up to date; the task records who is working on it."""
        text = text.strip()
        if not text and task:
            text = "Please work on this task."
        if not text:
            raise ValueError("Type a message first.")
        self.app.check_not_paused(self._store(sid).load().get("agent"))
        live = self._live_for(sid)
        with live.lock:
            if live.running:
                raise RuntimeError("Gabe is still working on your last message.")
            live.running = True
        user_msg = {"id": new_id(), "role": "user", "text": text, "ts": now(), "status": "done", "activity": []}
        prompt = text
        if task:
            user_msg["task"] = {"id": task["id"], "title": task["title"]}
            prompt = f"{text}\n\n{task_brief(task)}"
        reply = {"id": new_id(), "role": "assistant", "text": "", "ts": now(), "status": "working", "activity": [],
                 "prompt": prompt}  # kept so the work can be picked up again after a restart

        def add(s):
            if not s["messages"] and s["title"] == "New conversation":
                label = task["title"] if task and text == "Please work on this task." else text
                s["title"] = label[:60] + ("…" if len(label) > 60 else "")
            s["messages"] += [user_msg, reply]
            s["status"] = "running"
            s["updated"] = now()
        self._store(sid).update(add)
        self._run(sid, live, reply["id"], prompt)
        return reply

    def _run(self, sid: str, live: "_Live", reply_id: str, prompt: str) -> None:
        """Run the agent on `prompt` in the background, filling in reply `reply_id`."""
        live.message_id = reply_id
        live.last_thought = ""
        root = live.agent.name
        self.app.track("start", {"agent": root}, sid)

        def run():
            try:
                answer = live.agent.send(prompt)
                patch = {"text": answer, "status": "done"}
            except Exception as e:  # shown in the chat, not raised
                patch = {"text": friendly_error(e), "status": "error"}
            self._patch_message(sid, reply_id, lambda m: m.update(patch, ts=now()))
            self._store(sid).update(lambda s: s.update(status="idle"))
            live.running = False
            self.app.track("end", {"agent": root}, sid)

        threading.Thread(target=run, daemon=True, name=f"chat-{sid}").start()

    MAX_RESUMES = 2  # a job that keeps taking the app down is stopped, not retried forever

    def resume_interrupted(self) -> list[dict[str, Any]]:
        """Pick up every reply that a restart or crash cut off. The agent gets the original
        request plus the steps already done, so finished work (a sent email) isn't repeated."""
        picked = []
        for sid, mid in self.interrupted:
            s = self._store(sid).load()
            msgs = s.get("messages", [])
            idx = next((i for i, m in enumerate(msgs) if m["id"] == mid), None)
            if idx is None or msgs[idx].get("status") != "interrupted":
                continue
            reply = msgs[idx]
            prompt = reply.get("prompt") or next(
                (m["text"] for m in reversed(msgs[:idx]) if m["role"] == "user"), "")
            attempts = reply.get("resumes", 0) + 1

            def stop(text: str, m_id=mid, sid=sid):
                self._patch_message(sid, m_id, lambda m: m.update(status="error", text=text, ts=now()))

            if not prompt:
                stop("This reply was cut off when the Gabe app closed. Send your message again to continue.")
                continue
            if attempts > self.MAX_RESUMES:
                stop("This job was cut off by the app closing several times in a row, so Gabe stopped "
                     "retrying it. Check what was done above, then send your message again if needed.")
                continue
            try:
                self.app.check_not_paused(s.get("agent"))
            except RuntimeError as e:
                stop(f"This job was cut off when the Gabe app closed and wasn't restarted: {e}")
                continue
            live = self._live_for(sid)
            with live.lock:
                if live.running:
                    continue
                live.running = True

            def mark(m, n=attempts):
                m.update(status="working", resumes=n)
                m["activity"].append({"kind": "resumed", "ts": now()})
            self._patch_message(sid, mid, mark)
            self._store(sid).update(lambda x: x.update(status="running"))
            self._run(sid, live, mid, prompt + resume_note(reply.get("activity", [])))
            picked.append({"session_id": sid, "title": s.get("title", ""), "agent": s.get("agent")})
        self.interrupted = []
        return picked

    def reset_idle(self) -> None:
        """Drop every idle conversation's agent so the next message rebuilds it (e.g. new API key)."""
        with self._guard:
            for sid in [k for k, v in self._live.items() if not v.running]:
                self._live.pop(sid)

    def forget(self, sid: str) -> None:
        """Start Gabe's working memory of this conversation afresh (history stays on screen)."""
        with self._guard:
            live = self._live.get(sid)
            if live and live.running:
                raise RuntimeError("Gabe is still working in this conversation.")
            self._live.pop(sid, None)


# ---------------------------------------------------------------------------
# Config editing


def _str_presenter(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _str_presenter)


class WebState:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path.resolve()
        self._cfg_lock = threading.Lock()
        cfg = self.config()
        self.web_dir = cfg.data_dir / "web"
        self.web_dir.mkdir(parents=True, exist_ok=True)
        self.approvals = ApprovalQueue(self.web_dir / "approvals.json")
        self.sessions = Sessions(self.web_dir / "sessions", self)
        self.usage = JsonStore(self.web_dir / "usage.json", {"days": {}})
        self.resumed: list[dict[str, Any]] = []  # interrupted work picked up on this start
        self.jobs: dict[str, dict[str, Any]] = {}
        self.jobs_lock = threading.Lock()
        # Live view of who is doing what: agent log name -> {"working": n, "now": {...}, "session": id}
        self.live: dict[str, dict[str, Any]] = {}
        self.live_lock = threading.Lock()

    def track(self, kind: str, data: dict[str, Any], sid: str | None = None) -> None:
        """Keep a small live picture of each agent from the agent loop's events.
        Agents are keyed by the name they log under (Gabe, a team member's name)."""
        name = data.get("to_name") if kind == "delegate" else data.get("agent")
        if not name:
            return
        with self.live_lock:
            a = self.live.setdefault(name, {"working": 0, "now": None, "session": None, "from": None})
            if kind in ("start", "delegate"):
                a["working"] += 1
                a["session"] = sid
                a["from"] = data.get("agent") if kind == "delegate" else "you"
                a["now"] = {"text": data.get("task", ""), "ts": now(), "kind": "task"} if kind == "delegate" else None
            elif kind in ("end", "report"):
                a["working"] = max(0, a["working"] - 1)
                if not a["working"]:
                    a["now"] = None
                    a["from"] = None
            elif kind == "tool":
                a["now"] = {"tool": data.get("tool"), "input": data.get("input"), "outcome": data.get("outcome"), "ts": now(), "kind": "tool"}
            elif kind == "thought":
                a["now"] = {"text": data.get("text", ""), "ts": now(), "kind": "thought"}

    def live_view(self) -> dict[str, dict[str, Any]]:
        with self.live_lock:
            return json_copy(self.live)

    def check_not_paused(self, member: str | None) -> None:
        cfg = self.config()
        if member is None:
            if cfg.paused:
                raise RuntimeError(f"{cfg.assistant_name} is paused. Resume it on the Agents page first.")
            return
        team = cfg.team()
        m = team.get(member)
        if m is None:
            raise RuntimeError("That agent doesn't exist any more.")
        if not team.is_working(member):
            who = m.name if not m.active else next(x.name for x in team.chain(member) if not x.active)
            raise RuntimeError(f"{who} is paused. Resume it first.")


    def config(self) -> Config:
        return load_config(self.config_path)

    def audit(self) -> AuditLog:
        return AuditLog(self.config().data_dir / "audit.jsonl")

    def raw_config(self) -> dict[str, Any]:
        return yaml.safe_load(self.config_path.read_text()) or {}

    def edit_config(self, fn: Callable[[dict[str, Any]], None]) -> None:
        """Apply fn to the raw YAML and write it back. The first edit keeps a backup."""
        with self._cfg_lock:
            raw = self.raw_config()
            backup = self.config_path.with_name(self.config_path.name + ".bak")
            if not backup.exists():
                shutil.copy2(self.config_path, backup)
            fn(raw)
            text = yaml.dump(raw, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
            header = "# Gabe company configuration. Edited by the Gabe web app; the original is in gabe.yaml.bak.\n"
            self.config_path.write_text(header + text)
            load_config(self.config_path)  # fail loudly here rather than on the next run

    def record_usage(self, usage: dict[str, Any]) -> None:
        if not usage:
            return
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        inp = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        cached = usage.get("cache_read_input_tokens") or 0

        def upd(d):
            t = d["days"].setdefault(day, {"input": 0, "output": 0, "cached": 0, "steps": 0})
            t["input"] += inp
            t["output"] += out
            t["cached"] += cached
            t["steps"] += 1
        self.usage.update(upd)

    # Background jobs (briefing, inbox sorting, Gmail sign-in): one of each at a time.
    def start_job(self, name: str, fn: Callable[[], Any]) -> dict[str, Any]:
        with self.jobs_lock:
            job = self.jobs.get(name)
            if job and job["status"] == "running":
                return job
            job = {"name": name, "status": "running", "started": now(), "finished": None,
                   "error": None, "result": None}
            self.jobs[name] = job

        def run():
            try:
                job["result"] = fn()
                job["status"] = "done"
            except Exception as e:
                job["error"] = friendly_error(e)
                job["status"] = "error"
            job["finished"] = now()

        threading.Thread(target=run, daemon=True, name=f"job-{name}").start()
        return job

    def job(self, name: str) -> dict[str, Any] | None:
        return self.jobs.get(name)


def task_brief(task: dict[str, Any]) -> str:
    """The attached task, written out for the agent."""
    lines = [f"Attached task (id {task['id']}): {task['title']}"]
    for key, label in (("owner", "Owner"), ("due", "Due"), ("status", "Status"), ("project", "Project"), ("notes", "Notes")):
        if task.get(key):
            lines.append(f"- {label}: {str(task[key]).replace('_', ' ') if key == 'status' else task[key]}")
    lines.append(f"Keep this task up to date as you work: if you can update tasks, set its status and add "
                 f"notes with update_task (id {task['id']}); otherwise say in your reply what should change.")
    return "\n".join(lines)


def resume_note(activity: list[dict[str, Any]]) -> str:
    """What the agent already did before the app closed, so it picks up rather than repeats."""
    lines = []
    for a in activity:
        if a.get("kind") != "tool" or a.get("tool") == "ask_user":
            continue
        who = f"{a.get('agent')}: " if a.get("agent") else ""
        inp = json.dumps(a.get("input"), default=str)[:300]
        if a.get("outcome") == "ok":
            lines.append(f"- {who}done: {a.get('tool')} {inp}")
        elif a.get("outcome") == "declined_by_user":
            lines.append(f"- {who}the user said no to: {a.get('tool')} {inp}")
    done = "\n".join(lines) if lines else "- nothing recorded yet"
    return (
        "\n\n(The Gabe app closed while this was being worked on, so you're picking it back up. "
        "These steps were already done before it closed. Don't repeat them; check the current "
        f"state where it matters, then finish the job.\n{done})"
    )


def json_copy(x):
    import json
    return json.loads(json.dumps(x, default=str))


def check_args(tool: Tool, args: dict[str, Any]) -> str | None:
    return validate_input(tool.input_schema, args)
