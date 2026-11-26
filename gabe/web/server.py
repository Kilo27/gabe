"""HTTP API for the Gabe web app, plus the built front end.

Run with `gabe web`. Everything is local JSON files for now; see docs/UI_PLAN.md.
"""

from __future__ import annotations

import csv
import io
import logging
import time
import threading
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from gabe import schedules
from gabe.app import team_skills
from gabe.briefing import DAYS, BriefingSettings, build_briefer, company_tz, email_briefing, next_run, run_briefing
from gabe.claude_code import find_claude, open_in_terminal
from gabe.faq.slack import BOT_NAME as FAQ_BOT, REPLY_TOOL, SlackService
from gabe.integrations import IntegrationContext, load_integrations
from gabe.integrations._store import JsonStore
from gabe.policy import Declined, Policy
from gabe.providers import make_provider
from gabe.tools.registry import Risk, Tool, ToolRegistry
from gabe.updater import TOKEN_NAME, UpdateError, Updater, UpdateSettings
from gabe.web.state import WebState, check_args, friendly_error, new_id, now

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)
PERSON = "You (web app)"
DECISIONS = ("allow", "ask", "deny")


def faq_provider(cfg):
    """The model the FAQ bot answers with (tests swap this out)."""
    return make_provider(cfg.provider)


def create_app(config_path: str | Path, token: str | None = None, desktop: bool = False,
               resume_when: "threading.Event | None" = None, run_schedules: bool = True,
               run_slack: bool = True, run_updates: bool | None = None) -> FastAPI:
    """`token`: when set, every /api call must carry it (X-Gabe-Token). The desktop app sets a
    fresh one per launch so other programs and web pages on this computer can't use the API."""
    state = WebState(Path(config_path))
    app = FastAPI(title="Gabe", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.gabe = state

    def pick_up_interrupted():
        # Work a restart or crash cut off carries on. The desktop app waits until the API key
        # has loaded (`resume_when`), so the resumed runs can reach the model.
        if resume_when is not None:
            resume_when.wait()
        state.resumed = state.sessions.resume_interrupted()

    threading.Thread(target=pick_up_interrupted, daemon=True, name="resume").start()

    def recurring_jobs():
        # Recurring jobs (gabe/schedules.py) run while the app is open. Like resumed work, they
        # wait for the API key first.
        if resume_when is not None:
            resume_when.wait()
        while True:
            try:
                state.run_due_schedules()
            except Exception:  # keep the loop alive across a bad config or store
                log.exception("recurring jobs check failed")
            time.sleep(20)

    if run_schedules:
        threading.Thread(target=recurring_jobs, daemon=True, name="recurring-jobs").start()

    def faq_approver(tool, args):
        """A drafted Slack answer waits on the Approvals screen (policy "ask" for reply_on_slack)."""
        rec = state.approvals.create(
            session_id="", message_id="", session_title="Slack", agent=FAQ_BOT, tool=tool.name,
            risk=tool.risk.value, integration=tool.integration, description=tool.description,
            args=dict(args), reason=f"A customer asked this in {args.get('channel', 'Slack')}.",
        )
        decided = state.approvals.wait(rec["id"])
        if decided.get("status") == "approved":
            args.clear()
            args.update(decided["args"])
            return True
        return Declined(decided.get("note", ""))

    slack = SlackService(state.config, approver=faq_approver, on_usage=state.record_usage,
                         provider_factory=lambda cfg: faq_provider(cfg))
    app.state.slack = slack

    def slack_bot():
        # The Slack FAQ bot stays connected while the app is open, and follows settings changes.
        if resume_when is not None:
            resume_when.wait()
        while True:
            try:
                slack.sync()
            except Exception:
                log.exception("Slack FAQ bot check failed")
            time.sleep(15)

    if run_slack:
        threading.Thread(target=slack_bot, daemon=True, name="slack-faq").start()

    def idle() -> bool:
        """Nothing would be cut off by Gabe closing for an update."""
        with state.live_lock:
            working = any(a.get("working") for a in state.live.values())
        jobs = any(j.get("status") == "running" for j in state.jobs.values())
        return not (working or jobs or state.sessions.busy())

    # Gabe keeps itself up to date from GitHub (gabe/updater.py). The desktop app checks in the
    # background; `quit_app` is set once its window exists, so an installer can replace it.
    updater = Updater(state.config().data_dir / "updates",
                      settings=lambda: UpdateSettings.from_raw(state.raw_config()),
                      notify=lambda kind, title, body: state.notify(kind, title, body))
    app.state.updater = updater
    if desktop if run_updates is None else run_updates:
        threading.Thread(target=updater.run_forever, args=(idle,), daemon=True, name="updates").start()

    if token:
        import hmac

        from fastapi.responses import JSONResponse

        @app.middleware("http")
        async def require_token(request, call_next):
            if request.url.path.startswith("/api") and not hmac.compare_digest(
                request.headers.get("x-gabe-token", ""), token
            ):
                return JSONResponse({"detail": "Not allowed."}, status_code=403)
            return await call_next(request)

    def bad(msg: str, code: int = 400):
        raise HTTPException(code, msg)

    # -- helpers ---------------------------------------------------------------

    def tasks_store() -> JsonStore:
        return JsonStore(state.config().data_dir / "tasks" / "tasks.json", {"tasks": []})

    def notes_store() -> JsonStore:
        return JsonStore(state.config().data_dir / "memory" / "notes.json", {"notes": []})

    def today_str() -> str:
        return datetime.now(company_tz(state.config())).strftime("%Y-%m-%d")

    def registry() -> ToolRegistry:
        cfg = state.config()
        reg = ToolRegistry()
        load_integrations(cfg, reg)
        return reg

    def find_tool(name: str):
        """A tool by name, including Gabe's own (delegate, add_team_member), to check edited input."""
        cfg = state.config()
        if name == REPLY_TOOL.name:
            return REPLY_TOOL
        if name in ("delegate", "add_team_member", "schedule_job", "update_schedule", "cancel_schedule"):
            from gabe.app import build_gabe
            from gabe.policy import deny_all

            return build_gabe(cfg, approver=deny_all, provider=_NoProvider(), team=cfg.team(),
                              scheduling=True).registry.get(name)
        return registry().get(name)

    def brief_dir() -> Path:
        return BriefingSettings.from_config(state.config()).output_dir

    def brief_title(text: str) -> str:
        for line in text.splitlines():
            if line.startswith("#"):
                return line.lstrip("#").strip()
        return ""

    def email_spec():
        return next((i for i in state.config().integrations if i.name == "email"), None)

    def email_ctx():
        cfg = state.config()
        spec = email_spec()
        return IntegrationContext("email", spec.settings if spec else {}, cfg.data_dir, cfg)

    def mailboxes() -> list[dict[str, Any]]:
        """Each mailbox: its address (if known) and whether it has a sign-in token."""
        from gabe.integrations.email import gmail_accounts, gmail_token_path

        ctx = email_ctx()
        accounts = gmail_accounts(ctx.settings)
        if not accounts:
            token = gmail_token_path(ctx)
            return [{"address": _gmail_address.get("value", ""), "connected": True}] if token.exists() else []
        return [{"address": a, "connected": gmail_token_path(ctx, a).exists()} for a in accounts]

    def gmail_status() -> dict[str, Any]:
        spec = email_spec()
        if not spec:
            return {"state": "off", "message": "Email is off.", "mailboxes": []}
        backend = spec.settings.get("backend", "outbox")
        if backend != "gmail":
            return {"state": "practice", "backend": backend, "message": "Outgoing email is saved, not sent.", "mailboxes": []}
        from gabe.integrations.email import gmail_paths

        creds, _ = gmail_paths(email_ctx())
        boxes = mailboxes()
        if not creds.exists():
            return {"state": "missing_file", "backend": "gmail", "mailboxes": boxes,
                    "message": "Google sign-in isn't set up in this copy of Gabe."}
        if not any(m["connected"] for m in boxes):
            return {"state": "disconnected", "backend": "gmail", "message": "Sign in with Google to connect Gmail.", "mailboxes": boxes}
        return {"state": "connected", "backend": "gmail", "mailboxes": boxes,
                "address": ", ".join(m["address"] for m in boxes if m["address"]), "message": "Connected."}

    _gmail_address: dict[str, str] = {}

    def gmail_client(account: str | None = None):
        from gabe.integrations.email import gmail_client_from_config

        return gmail_client_from_config(state.config(), account or None)

    _calendar_address: dict[str, str] = {}

    def calendar_status() -> dict[str, Any]:
        from gabe.integrations.calendar import calendar_ctx, calendar_paths, calendar_spec

        cfg = state.config()
        spec = calendar_spec(cfg)
        if not spec:
            return {"state": "off", "message": "The calendar is off."}
        backend = spec.settings.get("backend", "google")
        if backend == "local":
            return {"state": "practice", "backend": backend, "message": "Practice calendar: nobody is invited."}
        creds, token = calendar_paths(calendar_ctx(cfg))
        if not creds.exists():
            return {"state": "missing_file", "backend": "google", "message": "Google sign-in isn't set up in this copy of Gabe."}
        if not token.exists():
            return {"state": "disconnected", "backend": "google", "message": "Sign in with Google to connect the calendar."}
        return {"state": "connected", "backend": "google", "address": _calendar_address.get("value", ""), "message": "Connected."}

    def person_audit(tool: str, input: dict[str, Any], outcome: str = "ok") -> None:
        state.audit().record(agent=PERSON, tool=tool, input=input, outcome=outcome, error=False)

    # -- overview --------------------------------------------------------------

    @app.get("/api/overview")
    def overview():
        cfg = state.config()
        tasks = tasks_store().load()["tasks"]
        today = today_str()
        active = [t for t in tasks if t["status"] in ("open", "in_progress", "blocked")]
        week_end = (datetime.fromisoformat(today) + timedelta(days=7)).strftime("%Y-%m-%d")
        latest = None
        d = brief_dir()
        if d.is_dir():
            files = sorted(d.glob("*.md"), reverse=True)
            if files:
                text = files[0].read_text()
                latest = {"date": files[0].stem, "title": brief_title(text), "text": text}
        s = BriefingSettings.from_config(cfg)
        try:
            nxt = next_run(datetime.now(company_tz(cfg)), s.time, s.days).isoformat()
        except ValueError:
            nxt = None
        return {
            "assistant": cfg.assistant_name,
            "company": cfg.company.get("name", ""),
            "timezone": cfg.company.get("timezone", "UTC"),
            "today": today,
            "approvals_pending": state.approvals.pending_count(),
            "tasks": {
                "active": len(active),
                "overdue": len([t for t in active if t.get("due") and t["due"] < today]),
                "due_today": len([t for t in active if t.get("due") == today]),
                "due_week": len([t for t in active if t.get("due") and today < t["due"] <= week_end]),
                "blocked": len([t for t in active if t["status"] == "blocked"]),
            },
            "notes": len(notes_store().load()["notes"]),
            "latest_brief": latest,
            "next_brief": nxt,
            "brief_job": state.job("brief"),
            "email": gmail_status(),
            "calendar": calendar_status(),
        }

    # -- briefs ----------------------------------------------------------------

    @app.get("/api/briefs")
    def briefs():
        d = brief_dir()
        if not d.is_dir():
            return []
        return [{"date": p.stem, "title": brief_title(p.read_text())} for p in sorted(d.glob("*.md"), reverse=True)]

    @app.get("/api/briefs/{date}")
    def brief(date: str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            bad("That isn't a date.")
        p = brief_dir() / f"{date}.md"
        if not p.exists():
            bad("There's no brief for that day.", 404)
        return {"date": date, "text": p.read_text()}

    @app.post("/api/briefs/run")
    def run_brief():
        cfg = state.config()

        if BriefingSettings.from_config(cfg).paused:
            bad("The briefing writer is paused. Resume it on the Agents page first.", 409)
        name = f"{cfg.assistant_name} (briefing)"

        def on_event(k, d):
            if k == "step":
                state.record_usage(d.get("usage") or {})
            state.track(k, d)

        def go():
            state.track("start", {"agent": name})
            try:
                text, path = run_briefing(cfg, on_event=on_event)
            finally:
                state.track("end", {"agent": name})
            return {"date": path.stem}

        return state.start_job("brief", go, label="Daily brief", request="Write today's daily brief.", fixable=True)

    @app.get("/api/jobs/{name}")
    def job(name: str):
        return state.job(name) or {"name": name, "status": "idle"}

    @app.get("/api/notices")
    def notices():
        """Jobs that finished or failed, newest first."""
        return list(reversed(state.notices.load().get("notices", [])))[:50]

    @app.post("/api/briefs/{date}/email")
    def email_brief(date: str):
        p = brief_dir() / f"{date}.md"
        if not p.exists():
            bad("There's no brief for that day.", 404)
        try:
            sent = email_briefing(state.config(), p.read_text())
        except Exception as e:
            bad(friendly_error(e))
        if not sent:
            bad("No recipients are set. Add them in Settings → Daily brief.")
        return {"sent_to": sent}

    # -- chat ------------------------------------------------------------------

    @app.get("/api/sessions")
    def sessions():
        return state.sessions.list()

    @app.post("/api/sessions")
    def new_session(payload: dict = Body(default={})):
        agent = payload.get("agent") or None
        try:
            state.check_not_paused(agent)
        except RuntimeError as e:
            bad(str(e), 409)
        require_task(payload.get("task_id"))
        s = state.sessions.create(agent=agent)
        task = attach_task(payload.get("task_id"), agent, s["id"])
        if payload.get("text") or task:
            try:
                state.sessions.send(s["id"], str(payload.get("text") or ""), task)
            except (ValueError, RuntimeError) as e:
                bad(str(e), 409)
            except Exception as e:
                bad(friendly_error(e))
        return state.sessions.get(s["id"])

    @app.get("/api/sessions/{sid}")
    def session(sid: str):
        s = state.sessions.get(sid)
        if not s:
            bad("That conversation doesn't exist any more.", 404)
        return s

    @app.post("/api/sessions/{sid}/messages")
    def send(sid: str, payload: dict = Body(...)):
        if not state.sessions.get(sid):
            bad("That conversation doesn't exist any more.", 404)
        try:
            sess = state.sessions.get(sid) or {}
            task = attach_task(payload.get("task_id"), sess.get("agent"), sid)
            state.sessions.send(sid, str(payload.get("text", "")), task)
        except (ValueError, RuntimeError) as e:
            bad(str(e), 409)
        return state.sessions.get(sid)

    @app.patch("/api/sessions/{sid}")
    def rename(sid: str, payload: dict = Body(...)):
        state.sessions.rename(sid, str(payload.get("title", "")))
        return state.sessions.get(sid)

    @app.post("/api/sessions/{sid}/forget")
    def forget(sid: str):
        try:
            state.sessions.forget(sid)
        except RuntimeError as e:
            bad(str(e), 409)
        return {"ok": True}

    @app.delete("/api/sessions/{sid}")
    def delete_session(sid: str):
        try:
            state.sessions.delete(sid)
        except RuntimeError as e:
            bad(str(e), 409)
        return {"ok": True}

    # -- approvals -------------------------------------------------------------

    @app.get("/api/approvals")
    def approvals():
        out = state.approvals.all()
        for a in out:
            a["live"] = a["status"] == "pending" and state.approvals.is_live(a["id"])
        return out

    @app.post("/api/approvals/{aid}")
    def decide(aid: str, payload: dict = Body(...)):
        rec = state.approvals.get(aid)
        if not rec:
            bad("That request doesn't exist.", 404)
        if rec.get("kind") == "question":
            text = str(payload.get("answer", "")).strip()
            if not text:
                bad("Type an answer first.")
            try:
                return state.approvals.answer(aid, text, PERSON)
            except ValueError as e:
                bad(str(e), 409)
        approve = payload.get("decision") == "approve"
        args = payload.get("args")
        if approve and args is not None:
            tool = find_tool(rec["tool"])
            err = tool and check_args(tool, args)
            if err:
                bad(f"That edit isn't valid: {err}.")
        try:
            return state.approvals.decide(aid, approve, args, str(payload.get("note", "")).strip(), PERSON)
        except ValueError as e:
            bad(str(e), 409)

    # -- tasks attached to a request ------------------------------------------------

    def require_task(task_id) -> None:
        if task_id and not any(t["id"] == task_id for t in tasks_store().load()["tasks"]):
            bad("That task doesn't exist any more.", 404)

    def attach_task(task_id, member: str | None, sid: str) -> dict[str, Any] | None:
        """Look up a task being attached to a request and note on it which agent is working on it."""
        if not task_id:
            return None
        cfg = state.config()
        if member:
            m = cfg.team().get(member)
            who = m.name if m else member
        else:
            who = cfg.assistant_name

        def mark(d):
            for t in d["tasks"]:
                if t["id"] == task_id:
                    t["handled_by"] = {"agent": who, "agent_id": member or "chief", "session_id": sid, "at": now()}
                    return dict(t)
            return None

        task = tasks_store().update(mark)
        if task is None:
            bad("That task doesn't exist any more.", 404)
        return task

    # -- recurring jobs ----------------------------------------------------------

    @app.get("/api/schedules")
    def list_schedules():
        return schedules.list_all(state.config())

    @app.patch("/api/schedules/{sched_id}")
    def edit_schedule(sched_id: str, payload: dict = Body(...)):
        allowed = {k: payload[k] for k in ("paused", "name", "instruction", "every_minutes", "at", "days", "between")
                   if k in payload}
        try:
            s = schedules.update(state.config(), sched_id, **allowed)
        except KeyError:
            bad("That recurring job doesn't exist any more.", 404)
        except ValueError as e:
            bad(str(e))
        person_audit("update_schedule", {"id": sched_id, **allowed})
        return s

    @app.delete("/api/schedules/{sched_id}")
    def delete_schedule(sched_id: str):
        try:
            s = schedules.remove(state.config(), sched_id)
        except KeyError:
            bad("That recurring job doesn't exist any more.", 404)
        person_audit("cancel_schedule", {"id": sched_id, "name": s["name"]})
        return {"ok": True}

    @app.post("/api/schedules/{sched_id}/run")
    def run_schedule_now(sched_id: str):
        s = next((x for x in schedules.list_all(state.config()) if x["id"] == sched_id), None)
        if s is None:
            bad("That recurring job doesn't exist any more.", 404)
        try:
            state.check_not_paused(None)
            if state.run_schedule(s) is None:
                bad("It's still busy with its last run.", 409)
        except RuntimeError as e:
            bad(str(e), 409)
        person_audit("run_schedule", {"id": sched_id, "name": s["name"]})
        return next(x for x in schedules.list_all(state.config()) if x["id"] == sched_id)

    # -- tasks -----------------------------------------------------------------

    @app.get("/api/tasks")
    def tasks():
        return {"today": today_str(), "tasks": tasks_store().load()["tasks"]}

    TASK_FIELDS = ("title", "owner", "due", "notes", "project", "status")

    def clean_task(payload: dict) -> dict:
        out = {k: payload[k] for k in TASK_FIELDS if k in payload}
        for k in ("title", "owner", "notes", "project"):
            if k in out:
                out[k] = str(out[k] or "").strip()
        if "due" in out:
            out["due"] = out["due"] or None
            if out["due"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", out["due"]):
                bad("Due date must be a date.")
        if out.get("status") and out["status"] not in ("open", "in_progress", "blocked", "done", "cancelled"):
            bad("Unknown status.")
        return out

    @app.post("/api/tasks")
    def create_task(payload: dict = Body(...)):
        fields = clean_task(payload)
        if not fields.get("title"):
            bad("Give the task a name.")
        if not fields.get("owner"):
            bad("Say who owns the task.")
        task = {"id": new_id()[:8], "title": "", "owner": "", "due": None, "notes": "", "project": "",
                "status": "open", "created": now(), "source": "Added in the web app", **fields}
        tasks_store().update(lambda d: d["tasks"].append(task))
        person_audit("create_task", fields)
        return task

    @app.patch("/api/tasks/{tid}")
    def update_task(tid: str, payload: dict = Body(...)):
        fields = clean_task(payload)
        if "title" in fields and not fields["title"]:
            bad("A task needs a name.")

        def upd(d):
            for t in d["tasks"]:
                if t["id"] == tid:
                    t.update(fields)
                    return t
            return None

        t = tasks_store().update(upd)
        if not t:
            bad("That task doesn't exist any more.", 404)
        person_audit("update_task", {"id": tid, **fields})
        return t

    # -- notes (memory) --------------------------------------------------------

    @app.get("/api/notes")
    def notes(q: str = ""):
        words = q.lower().split()
        ns = notes_store().load()["notes"]
        ns = [n for n in ns if all(w in f"{n['topic']} {n['note']} {' '.join(n.get('tags', []))}".lower() for w in words)]
        return sorted(ns, key=lambda n: n.get("created", ""), reverse=True)

    def clean_note(payload: dict) -> dict:
        out = {}
        for k in ("topic", "note"):
            if k in payload:
                out[k] = str(payload[k] or "").strip()
        if "tags" in payload:
            tags = payload["tags"]
            if isinstance(tags, str):
                tags = tags.split(",")
            out["tags"] = [str(t).strip() for t in tags if str(t).strip()]
        return out

    @app.post("/api/notes")
    def add_note(payload: dict = Body(...)):
        f = clean_note(payload)
        if not f.get("topic") or not f.get("note"):
            bad("A note needs a topic and some text.")
        n = {"id": new_id()[:8], "topic": f["topic"], "note": f["note"], "tags": f.get("tags", []), "created": now()}
        notes_store().update(lambda d: d["notes"].append(n))
        person_audit("remember", {"topic": n["topic"], "note": n["note"]})
        return n

    @app.patch("/api/notes/{nid}")
    def edit_note(nid: str, payload: dict = Body(...)):
        f = clean_note(payload)

        def upd(d):
            for n in d["notes"]:
                if n["id"] == nid:
                    n.update(f)
                    n["edited"] = now()
                    return n
            return None

        n = notes_store().update(upd)
        if not n:
            bad("That note doesn't exist any more.", 404)
        person_audit("edit_note", {"id": nid, **f})
        return n

    @app.delete("/api/notes/{nid}")
    def delete_note(nid: str):
        def rm(d):
            before = len(d["notes"])
            d["notes"] = [n for n in d["notes"] if n["id"] != nid]
            return before != len(d["notes"])

        if not notes_store().update(rm):
            bad("That note doesn't exist any more.", 404)
        person_audit("forget", {"id": nid})
        return {"ok": True}

    # -- activity --------------------------------------------------------------

    def read_audit() -> list[dict[str, Any]]:
        p = state.config().data_dir / "audit.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        out.reverse()
        return out

    @app.get("/api/activity")
    def activity(limit: int = 200, offset: int = 0, who: str = "", outcome: str = "", tool: str = ""):
        rows = read_audit()
        if who:
            rows = [r for r in rows if r.get("agent") == who]
        if outcome == "problem":
            rows = [r for r in rows if r.get("outcome") != "ok"]
        elif outcome:
            rows = [r for r in rows if r.get("outcome") == outcome]
        if tool:
            rows = [r for r in rows if r.get("tool") == tool]
        all_rows = read_audit()
        return {
            "total": len(rows),
            "rows": rows[offset: offset + limit],
            "agents": sorted({r.get("agent", "") for r in all_rows}),
            "tools": sorted({r.get("tool", "") for r in all_rows}),
        }

    @app.get("/api/activity.csv")
    def activity_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time", "who", "action", "outcome", "details"])
        for r in read_audit():
            w.writerow([r.get("ts"), r.get("agent"), r.get("tool"), r.get("outcome"), json.dumps(r.get("input"))])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="gabe-activity.csv"'})

    # -- inbox -----------------------------------------------------------------

    triage_store = JsonStore(state.web_dir / "triage.json", {"items": {}})

    @app.get("/api/inbox")
    def inbox(q: str = "in:inbox newer_than:7d -category:promotions -category:social"):
        st = gmail_status()
        if st["state"] != "connected":
            return {"status": st, "messages": [], "triage": {}}
        try:
            boxes = [m for m in st["mailboxes"] if m["connected"]]
            msgs = []
            for box in boxes:
                client = gmail_client(box["address"])
                for m in client.search(q, 30):
                    msgs.append({**m, "account": box["address"]})
                if not box["address"] and "value" not in _gmail_address:
                    _gmail_address["value"] = client.address()
            if len(boxes) > 1:
                from email.utils import parsedate_to_datetime

                def when(m):
                    try:
                        return parsedate_to_datetime(m.get("date") or "").timestamp()
                    except Exception:
                        return 0
                msgs.sort(key=when, reverse=True)
        except Exception as e:
            return {"status": {**st, "state": "error", "message": friendly_error(e)}, "messages": [], "triage": {}}
        return {"status": st, "messages": msgs, "triage": triage_store.load()["items"], "job": state.job("triage")}

    @app.get("/api/inbox/{mid}")
    def read_mail(mid: str, account: str = ""):
        try:
            return gmail_client(account).read(mid)
        except Exception as e:
            bad(friendly_error(e))

    @app.post("/api/inbox/{mid}/read")
    def mark_read(mid: str, account: str = ""):
        try:
            gmail_client(account).mark_read(mid)
        except Exception as e:
            bad(friendly_error(e))
        person_audit("mark_email_read", {"id": mid})
        return {"ok": True}

    @app.post("/api/inbox/{mid}/draft")
    def draft_reply(mid: str, payload: dict = Body(default={})):
        hint = str(payload.get("hint", "")).strip()
        subject = payload.get("subject") or "this email"
        sender = payload.get("from") or "the sender"
        text = (
            f"Please draft a reply to the email from {sender} about \"{subject}\" (message id {mid}"
            + (f", in the {payload['account']} mailbox" if payload.get("account") else "") + "). "
            "Read it first, check notes for context, then save the reply as a Gmail draft. Don't send it."
        )
        if hint:
            text += f" What I want to say: {hint}"
        s = state.sessions.create(title=f"Reply: {subject}"[:60])
        state.sessions.send(s["id"], text)
        return {"session_id": s["id"]}

    @app.post("/api/inbox/triage")
    def triage(payload: dict = Body(default={})):
        msgs = payload.get("messages") or []
        if not msgs:
            bad("There are no emails to sort.")
        cfg = state.config()
        listing = json.dumps([{k: m.get(k) for k in ("id", "from", "subject", "date", "snippet")} for m in msgs][:30])

        def go():
            prompt = (
                "Sort these recent emails for the team. For each one decide: 'reply' (someone needs "
                "to answer or decide), 'fyi' (worth knowing, no action) or 'ignore' (notifications, "
                "newsletters, noise). Suggest an owner from the company profile when it's clear, and "
                "give a reason of at most 12 words. Use notes and tasks for context if useful. "
                "Reply with ONLY a JSON array of objects with keys id, category, owner, reason.\n\n"
                f"Emails:\n{listing}"
            )
            text = build_briefer(cfg).send(prompt)
            m = re.search(r"\[.*\]", text, re.S)
            items = json.loads(m.group(0)) if m else []
            found = {}
            for it in items:
                if isinstance(it, dict) and it.get("id") and it.get("category") in ("reply", "fyi", "ignore"):
                    found[it["id"]] = {"category": it["category"], "owner": it.get("owner") or "",
                                       "reason": it.get("reason") or "", "sorted": now()}
            triage_store.update(lambda d: d["items"].update(found))
            return {"sorted": len(found)}

        return state.start_job("triage", go, label="Inbox sorting", fixable=True,
                               request="Sort the unread emails into needs a reply, FYI, or ignore.")

    # -- settings --------------------------------------------------------------

    def policy_rows(cfg) -> list[dict[str, Any]]:
        pol = Policy.from_config(cfg.policy)
        tools_cfg = (cfg.policy or {}).get("tools") or {}
        integ_cfg = (cfg.policy or {}).get("integrations") or {}
        reg = registry()
        rows = []
        tools = reg.tools()
        if cfg.team().members:
            t = find_tool("delegate")
            if t:
                tools.append(t)
        if any(i.name == "faq" for i in cfg.integrations):
            tools.append(REPLY_TOOL)
        for t in tools:
            source = "tool" if t.name in tools_cfg else "integration" if t.integration in integ_cfg else "default"
            rows.append({"name": t.name, "risk": t.risk.value, "integration": t.integration,
                         "description": t.description, "decision": pol.decide(t).value, "source": source})
        return rows

    @app.get("/api/settings")
    def settings():
        cfg = state.config()
        raw = state.raw_config()
        company = dict(cfg.company)
        b = BriefingSettings.from_config(cfg)
        playbooks = [{"name": n, "text": t} for n, t in cfg.playbooks()]
        rules = policy_rows(cfg)  # builds the registry first, so MCP server status is current
        integrations = []
        for spec in cfg.integrations:
            entry = {"name": spec.name, "settings": {k: v for k, v in spec.settings.items() if "key" not in k.lower()}}
            if spec.name == "email":
                entry["status"] = gmail_status()
            if spec.name == "calendar":
                entry["status"] = calendar_status()
            if spec.name == "faq":
                entry["settings"] = {}  # see /api/faq
            if spec.name == "mcp":
                # Server configs carry tokens (env, headers, args): only names and state go out.
                from gabe.integrations import mcp as mcp_integration

                live = mcp_integration.status()
                entry["settings"] = {}
                entry["servers"] = [
                    {"name": name, **live.get(name, {"state": "error", "error": "not connected", "tools": 0,
                                                     "transport": "http" if (s or {}).get("url") else "stdio"})}
                    for name, s in sorted((spec.settings.get("servers") or {}).items())
                ]
            integrations.append(entry)
        provider = {k: v for k, v in (cfg.provider or {}).items() if "key" not in k.lower()}
        provider["key_set"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
        usage_days = state.usage.load()["days"]
        return {
            "assistant_name": cfg.assistant_name,
            "company": {
                "name": company.pop("name", ""), "timezone": company.pop("timezone", "UTC"),
                "profile": str(company.pop("profile", "") or "").strip(),
                "facts": {k: str(v) for k, v in company.items()},
            },
            "briefing": {"time": b.time, "days": b.days, "email_to": b.email_to},
            "policy": {
                "defaults": {r.value: d.value for r, d in Policy.from_config(cfg.policy).defaults.items()},
                "tools": rules,
                "integrations": (raw.get("policy") or {}).get("integrations") or {},
            },
            "all_tools": team_skills(cfg),
            "provider": provider,
            "integrations": integrations,
            "playbooks": playbooks,
            "usage": [{"date": k, **v} for k, v in sorted(usage_days.items(), reverse=True)[:30]],
            "config_path": str(state.config_path),
            "desktop": desktop,
            "claude_code": {"installed": find_claude() is not None,
                            "folder": str(cfg.root).replace(str(Path.home()), "~", 1)},
        }

    @app.put("/api/settings/company")
    def save_company(payload: dict = Body(...)):
        name = str(payload.get("name", "")).strip()
        tz = str(payload.get("timezone", "")).strip() or "UTC"
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(tz)
        except Exception:
            bad(f"'{tz}' isn't a time zone I know. Try something like Europe/Dublin.")
        facts = {str(k).strip(): str(v).strip() for k, v in (payload.get("facts") or {}).items() if str(k).strip()}
        assistant = str(payload.get("assistant_name", "")).strip()

        def fn(raw):
            raw["company"] = {"name": name, "timezone": tz, "profile": str(payload.get("profile", "")).strip() + "\n", **facts}
            if assistant:
                raw.setdefault("assistant", {})["name"] = assistant

        state.edit_config(fn)
        person_audit("edit_settings", {"section": "company"})
        return settings()

    @app.put("/api/settings/briefing")
    def save_briefing(payload: dict = Body(...)):
        t = str(payload.get("time", "08:00"))
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            bad("Time must look like 08:00.")
        days = [d for d in payload.get("days", []) if d in DAYS]
        if not days:
            bad("Pick at least one day.")
        to = [str(a).strip() for a in payload.get("email_to", []) if str(a).strip()]
        for a in to:
            if a.lower() != "me" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a):
                bad(f"'{a}' doesn't look like an email address.")

        def fn(raw):
            b = raw.setdefault("briefing", {}) or {}
            b.update(time=t, days=days, email_to=to)
            raw["briefing"] = b

        state.edit_config(fn)
        person_audit("edit_settings", {"section": "briefing"})
        return settings()

    @app.put("/api/settings/policy")
    def save_policy(payload: dict = Body(...)):
        defaults = payload.get("defaults") or {}
        tools = payload.get("tools") or {}
        for v in list(defaults.values()) + list(tools.values()):
            if v not in DECISIONS:
                bad("Each rule must be allow, ask or deny.")

        def fn(raw):
            p = raw.setdefault("policy", {}) or {}
            p["defaults"] = {r.value: defaults.get(r.value, "ask") for r in Risk}
            p["tools"] = tools
            p.setdefault("integrations", {})
            raw["policy"] = p

        state.edit_config(fn)
        person_audit("edit_settings", {"section": "policy"})
        return settings()

    @app.post("/api/claude-code/open")
    def open_claude_code():
        """A Terminal running Claude Code in the config folder, to change this installation."""
        claude = find_claude()
        if not claude:
            bad("Claude Code isn't installed.")
        cfg = state.config()
        try:
            open_in_terminal(cfg.path, claude, cfg.data_dir)
        except Exception as e:
            bad(f"Couldn't open Claude Code: {e}")
        person_audit("open_claude_code", {"folder": str(cfg.root)})
        return {"ok": True}

    @app.put("/api/settings/api-key")
    def save_api_key(payload: dict = Body(...)):
        """Store the model API key in the OS keychain (desktop app)."""
        key = str(payload.get("key", "")).strip()
        if key and (len(key) < 20 or any(c.isspace() for c in key)):
            bad("That doesn't look like an API key.")
        try:
            from gabe.desktop import set_secret

            set_secret("ANTHROPIC_API_KEY", key or None)
        except Exception as e:
            bad(f"Couldn't save the key to the keychain: {e}")
        state.sessions.reset_idle()  # rebuild agents so they pick up the new key
        person_audit("edit_settings", {"section": "model", "action": "api key " + ("set" if key else "removed")})
        return {"key_set": bool(key)}

    # -- updates -----------------------------------------------------------------

    @app.get("/api/updates")
    def updates():
        return updater.public()

    @app.post("/api/updates/check")
    def check_updates():
        return updater.check()

    @app.post("/api/updates/install")
    def install_update():
        try:
            out = updater.install()
        except UpdateError as e:
            bad(str(e))
        person_audit("install_update", {"commit": (out.get("latest") or {}).get("sha", "")})
        return out

    @app.put("/api/updates/settings")
    def update_settings(payload: dict = Body(...)):
        def fn(raw):
            u = raw.setdefault("updates", {}) or {}
            if "auto" in payload:
                u["auto"] = bool(payload["auto"])
            raw["updates"] = u

        state.edit_config(fn)
        person_audit("edit_settings", {"section": "updates"})
        return updater.public()

    @app.put("/api/updates/token")
    def update_token(payload: dict = Body(...)):
        """A GitHub token that can read the repository, kept in the OS keychain."""
        tok = str(payload.get("token", "")).strip()
        if tok and (len(tok) < 20 or any(c.isspace() for c in tok)):
            bad("That doesn't look like a GitHub token.")
        try:
            from gabe.desktop import set_secret

            set_secret(TOKEN_NAME, tok or None)
        except Exception as e:
            bad(f"Couldn't save the token to the keychain: {e}")
        person_audit("edit_settings", {"section": "updates", "action": "token " + ("set" if tok else "removed")})
        return updater.check() if tok else updater.public()

    @app.put("/api/settings/email-mode")
    def email_mode(payload: dict = Body(...)):
        backend = payload.get("backend")
        if backend not in ("gmail", "outbox"):
            bad("Unknown email mode.")

        def fn(raw):
            items = raw.setdefault("integrations", [])
            for i, it in enumerate(items):
                if it == "email" or (isinstance(it, dict) and it.get("name") == "email"):
                    if not isinstance(it, dict):
                        it = {"name": "email", "settings": {}}
                        items[i] = it
                    it.setdefault("settings", {})["backend"] = backend
                    return
            items.append({"name": "email", "settings": {"backend": backend}})

        state.edit_config(fn)
        person_audit("edit_settings", {"section": "email", "backend": backend})
        return settings()

    def set_accounts(accounts: list[str]) -> None:
        def fn(raw):
            for it in raw.get("integrations", []):
                if isinstance(it, dict) and it.get("name") == "email":
                    it.setdefault("settings", {})["accounts"] = accounts
        state.edit_config(fn)

    @app.post("/api/settings/gmail/connect")
    def gmail_connect():
        """Sign in to one more Gmail mailbox (or the first one)."""
        spec = email_spec()
        if not spec or spec.settings.get("backend") != "gmail":
            bad("Switch email to Gmail first.")
        from gabe.integrations.email import gmail_accounts, gmail_paths, gmail_token_path
        from gabe.integrations.gmail_backend import build_service, load_credentials

        ctx = email_ctx()
        creds, primary = gmail_paths(ctx)
        if not creds.exists():
            bad("The Google sign-in file is missing. See docs/GMAIL_SETUP.md, step 1.")

        def go():
            pending = ctx.path("gmail_token_pending.json")
            pending.unlink(missing_ok=True)
            address = build_service(load_credentials(creds, pending, interactive=True)) \
                .users().getProfile(userId="me").execute()["emailAddress"]
            accounts = gmail_accounts(email_ctx().settings)
            if not accounts and primary.exists():  # record the mailbox that was connected before
                accounts = [gmail_client().address()]
            if address in accounts:  # signing in again refreshes that mailbox's token
                set_accounts(accounts)
                pending.replace(gmail_token_path(email_ctx(), address))
                return {"address": address}
            accounts.append(address)
            set_accounts(accounts)
            pending.replace(gmail_token_path(email_ctx(), address))
            _gmail_address.pop("value", None)
            person_audit("edit_settings", {"section": "email", "action": "connect", "address": address})
            return {"address": address}

        return state.start_job("gmail", go, label="Gmail connection")

    @app.post("/api/settings/gmail/disconnect")
    def gmail_disconnect(payload: dict = Body(default={})):
        from gabe.integrations.email import gmail_accounts, gmail_token_path

        ctx = email_ctx()
        accounts = gmail_accounts(ctx.settings)
        address = str(payload.get("address", ""))
        if not accounts or (len(accounts) == 1 and address in ("", accounts[0])):
            gmail_token_path(ctx).unlink(missing_ok=True)
            if accounts:
                set_accounts([])
        else:
            if address not in accounts:
                bad("That mailbox isn't connected.")
            gmail_token_path(ctx, address).unlink(missing_ok=True)
            rest = [a for a in accounts if a != address]
            if address == accounts[0]:  # the next mailbox takes over the main token file
                gmail_token_path(ctx, rest[0]).replace(gmail_token_path(ctx))
            set_accounts(rest)
        _gmail_address.pop("value", None)
        person_audit("edit_settings", {"section": "email", "action": "disconnect", "address": address})
        return {"ok": True}

    # -- calendar --------------------------------------------------------------

    @app.get("/api/calendar")
    def calendar(date: str = "", days: int = 1):
        """Events from the start of `date` (default today) for `days` days."""
        from gabe.integrations.calendar import calendar_client_from_config, day_events

        st = calendar_status()
        if st["state"] not in ("connected", "practice"):
            return {"status": st, "events": []}
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            bad("That isn't a date.")
        cfg = state.config()
        try:
            client = calendar_client_from_config(cfg)
            events = day_events(cfg, datetime.fromisoformat(date or today_str()).date(), max(1, min(days, 14)), client)
            if st["state"] == "connected" and "value" not in _calendar_address:
                _calendar_address["value"] = client.address()
                st = calendar_status()
        except Exception as e:
            return {"status": {**st, "state": "error", "message": friendly_error(e)}, "events": []}
        return {"status": st, "events": events}

    @app.put("/api/settings/calendar-mode")
    def calendar_mode(payload: dict = Body(...)):
        backend = payload.get("backend")
        if backend not in ("google", "local", "off"):
            bad("Unknown calendar mode.")

        def fn(raw):
            items = raw.setdefault("integrations", [])
            for i, it in enumerate(items):
                if it == "calendar" or (isinstance(it, dict) and it.get("name") == "calendar"):
                    if backend == "off":
                        del items[i]
                        return
                    if not isinstance(it, dict):
                        it = {"name": "calendar", "settings": {}}
                        items[i] = it
                    it.setdefault("settings", {})["backend"] = backend
                    return
            if backend != "off":
                items.append({"name": "calendar", "settings": {"backend": backend}})

        state.edit_config(fn)
        _calendar_address.pop("value", None)
        person_audit("edit_settings", {"section": "calendar", "backend": backend})
        return settings()

    @app.post("/api/settings/calendar/connect")
    def calendar_connect():
        from gabe.integrations.calendar import calendar_ctx, calendar_paths, calendar_spec
        from gabe.integrations.gcal_backend import SCOPES, GoogleCalendarClient, build_service
        from gabe.integrations.gmail_backend import load_credentials

        cfg = state.config()
        spec = calendar_spec(cfg)
        if not spec or spec.settings.get("backend", "google") != "google":
            bad("Switch the calendar to Google Calendar first.")
        ctx = calendar_ctx(cfg)
        creds, token = calendar_paths(ctx)
        if not creds.exists():
            bad("The Google sign-in file is missing. See docs/GMAIL_SETUP.md, step 1.")

        def go():
            pending = ctx.path("google_token_pending.json")
            pending.unlink(missing_ok=True)
            service = build_service(load_credentials(creds, pending, interactive=True, scopes=SCOPES))
            address = GoogleCalendarClient(lambda: service).address()
            pending.replace(token)
            _calendar_address["value"] = address
            person_audit("edit_settings", {"section": "calendar", "action": "connect", "address": address})
            return {"address": address}

        return state.start_job("calendar", go, label="Calendar connection")

    @app.post("/api/settings/calendar/disconnect")
    def calendar_disconnect():
        from gabe.integrations.calendar import calendar_ctx, calendar_paths

        _, token = calendar_paths(calendar_ctx(state.config()))
        token.unlink(missing_ok=True)
        address = _calendar_address.pop("value", "")
        person_audit("edit_settings", {"section": "calendar", "action": "disconnect", "address": address})
        return {"ok": True}

    def playbook_path(name: str) -> Path:
        cfg = state.config()
        if not cfg.playbooks_dir:
            bad("No playbooks folder is set in the config.")
        slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
        if not slug:
            bad("Give the playbook a name.")
        cfg.playbooks_dir.mkdir(parents=True, exist_ok=True)
        return cfg.playbooks_dir / f"{slug}.md"

    @app.put("/api/playbooks/{name}")
    def save_playbook(name: str, payload: dict = Body(...)):
        p = playbook_path(name)
        old = payload.get("rename_from")
        text = str(payload.get("text", "")).strip()
        if not text:
            bad("A playbook can't be empty.")
        p.write_text(text + "\n")
        if old and playbook_path(old) != p:
            playbook_path(old).unlink(missing_ok=True)
        person_audit("edit_playbook", {"name": p.stem})
        return {"name": p.stem, "text": text}

    @app.delete("/api/playbooks/{name}")
    def delete_playbook(name: str):
        p = playbook_path(name)
        if not p.exists():
            bad("That playbook doesn't exist.", 404)
        p.unlink()
        person_audit("delete_playbook", {"name": p.stem})
        return {"ok": True}

    # -- FAQ bot -----------------------------------------------------------------
    # Documents in the FAQ folder (config/faq/), the Slack connection and what customers asked.

    from gabe.faq.answer import FaqResponder
    from gabe.faq.library import FaqLibrary
    from gabe.faq.settings import ANSWER_MODES, APP_TOKEN_ENV, BOT_TOKEN_ENV, FaqSettings, SlackSettings
    from gabe.faq.slack import MANIFEST, question_log

    def faq_settings() -> FaqSettings:
        s = FaqSettings.from_config(state.config())
        if s is None:
            bad("The FAQ bot isn't turned on.", 409)
        return s

    def edit_faq(fn) -> None:
        """Change the faq integration's settings in gabe.yaml (adding it if needed)."""
        def edit(raw):
            items = raw.setdefault("integrations", []) or []
            raw["integrations"] = items
            for i, it in enumerate(items):
                if it == "faq" or (isinstance(it, dict) and it.get("name") == "faq"):
                    if not isinstance(it, dict):
                        it = items[i] = {"name": "faq", "settings": {}}
                    it["settings"] = it.get("settings") or {}
                    fn(it["settings"])
                    return
            entry = {"name": "faq", "settings": {"folder": "faq"}}
            fn(entry["settings"])
            items.append(entry)
        state.edit_config(edit)

    def resync_slack() -> None:
        if run_slack:
            threading.Thread(target=slack.sync, daemon=True, name="slack-faq-sync").start()

    @app.get("/api/faq")
    def faq():
        cfg = state.config()
        s = FaqSettings.from_config(cfg)
        if s is None:
            return {"enabled": False}
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["display_information"]["name"] = cfg.assistant_name
        manifest["features"]["bot_user"]["display_name"] = cfg.assistant_name
        sl = s.slack
        return {
            "enabled": True,
            "model": s.model,
            "tone": s.tone,
            "where_to_look": s.where_to_look,
            "documents": [d.summary() for d in FaqLibrary(s.folder).documents()],
            "slack": {k: v for k, v in vars(sl).items() if k not in ("bot_token", "app_token")}
                     | {"bot_token_set": bool(sl.bot_token), "app_token_set": bool(sl.app_token)},
            "status": slack.status(),
            "reply_rule": Policy.from_config(cfg.policy).decide(REPLY_TOOL).value,
            "questions": question_log(cfg).recent(100),
            "manifest": json.dumps(manifest, indent=2),
            "desktop": desktop,
        }

    @app.post("/api/faq/enable")
    def faq_enable():
        if FaqSettings.from_config(state.config()) is None:
            edit_faq(lambda st: None)
            person_audit("edit_settings", {"section": "faq", "action": "turned on"})
        return faq()

    @app.post("/api/faq/documents")
    def faq_upload(payload: dict = Body(...)):
        import base64
        import binascii

        name = str(payload.get("name", "")).strip()
        try:
            data = base64.b64decode(str(payload.get("content_b64", "")), validate=True)
        except (binascii.Error, ValueError):
            bad("That file didn't upload properly. Try again.")
        if not name or not data:
            bad("Choose a file first.")
        try:
            doc = FaqLibrary(faq_settings().folder).add(name, data)
        except ValueError as e:
            bad(str(e))
        person_audit("upload_faq", {"name": doc.name, "entries": len(doc.entries)})
        return doc.summary()

    @app.get("/api/faq/documents/{name}")
    def faq_document(name: str):
        doc = FaqLibrary(faq_settings().folder).document(name)
        if doc is None:
            bad("That document doesn't exist.", 404)
        return {**doc.summary(), "items": [e.to_dict() for e in doc.entries]}

    @app.delete("/api/faq/documents/{name}")
    def faq_delete(name: str):
        try:
            removed = FaqLibrary(faq_settings().folder).remove(name)
        except ValueError:
            removed = False
        if not removed:
            bad("That document doesn't exist.", 404)
        person_audit("delete_faq", {"name": name})
        return {"ok": True}

    @app.post("/api/faq/try")
    def faq_try(payload: dict = Body(...)):
        """What the bot would say to this message (nothing is posted)."""
        question = str(payload.get("question", "")).strip()
        if not question:
            bad("Type a question first.")
        cfg = state.config()
        responder = FaqResponder(faq_settings(), faq_provider(cfg), assistant=cfg.assistant_name,
                                 company=str(cfg.company.get("name", "")), on_usage=state.record_usage)
        try:
            return responder.answer(question).to_dict()
        except Exception as e:
            bad(friendly_error(e), 502)

    @app.get("/api/faq/channels")
    def faq_channels():
        """The channels the Slack bot can see, and which ones it's in."""
        faq_settings()
        try:
            return slack.bot().channels()
        except RuntimeError as e:
            bad(str(e), 409)
        except Exception as e:
            bad(f"Couldn't list Slack channels: {e}", 502)

    @app.put("/api/faq/settings")
    def faq_save(payload: dict = Body(...)):
        current = faq_settings()
        sl = payload.get("slack") or {}
        answer = sl.get("answer", current.slack.answer)
        if answer not in ANSWER_MODES:
            bad("Unknown answer mode.")
        try:
            per_hour = int(sl.get("per_user_per_hour", current.slack.per_user_per_hour))
        except (TypeError, ValueError):
            bad("Replies per person per hour must be a number.")
        model = str(payload.get("model", current.model))
        if model not in ("haiku", "sonnet", "opus"):
            bad("Unknown model.")

        def ids(v) -> list[str]:
            return [str(x).strip() for x in (v or []) if str(x).strip()]

        new = SlackSettings(
            enabled=bool(sl.get("enabled", current.slack.enabled)),
            channels=ids(sl.get("channels", current.slack.channels)),
            direct_messages=bool(sl.get("direct_messages", current.slack.direct_messages)),
            answer=answer,
            staff=ids(sl.get("staff", current.slack.staff)),
            follow_ups=bool(sl.get("follow_ups", current.slack.follow_ups)),
            per_user_per_hour=max(0, per_hour),
            notify_channel=str(sl.get("notify_channel", current.slack.notify_channel)).strip(),
            create_task=bool(sl.get("create_task", current.slack.create_task)),
            task_owner=str(sl.get("task_owner", current.slack.task_owner)).strip(),
            say_so=bool(sl.get("say_so", current.slack.say_so)),
            holding_reply=str(sl.get("holding_reply", current.slack.holding_reply)).strip(),
        )

        def fn(st):
            st["model"] = model
            st["tone"] = str(payload.get("tone", current.tone)).strip()
            st["where_to_look"] = str(payload.get("where_to_look", current.where_to_look)).strip()
            old = st.get("slack") or {}
            st["slack"] = {**{k: v for k, v in old.items() if k in ("bot_token", "app_token")}, **new.to_dict()}

        edit_faq(fn)
        person_audit("edit_settings", {"section": "faq"})
        joined: dict[str, str] = {}
        if new.enabled and new.channels and slack.status().get("state") == "connected":
            try:
                joined = slack.bot().join(new.channels)  # so it sees the questions asked there
            except Exception as e:
                log.warning("couldn't join Slack channels: %s", e)
        resync_slack()
        return {**faq(), "joined": joined}

    @app.put("/api/faq/tokens")
    def faq_tokens(payload: dict = Body(...)):
        """Slack tokens go in the OS keychain (desktop app), never in gabe.yaml."""
        changes = {}
        for field_name, env, prefix in (("bot_token", BOT_TOKEN_ENV, "xoxb-"), ("app_token", APP_TOKEN_ENV, "xapp-")):
            if field_name not in payload:
                continue
            value = str(payload.get(field_name) or "").strip()
            if value and (not value.startswith(prefix) or any(c.isspace() for c in value)):
                bad(f"The {'bot' if prefix == 'xoxb-' else 'app'} token starts with {prefix}")
            changes[env] = value or None
        for env, value in changes.items():
            try:
                from gabe.desktop import set_secret

                set_secret(env, value)
            except Exception:
                if desktop:
                    bad("Couldn't save the token to the keychain.")
                if value:  # browser/dev mode without a keychain: this run only
                    os.environ[env] = value
                else:
                    os.environ.pop(env, None)
        person_audit("edit_settings", {"section": "faq", "action": "slack tokens " + ", ".join(
            f"{k.split('_')[1].lower()} {'set' if v else 'removed'}" for k, v in changes.items())})
        resync_slack()
        return faq()

    # -- agents ----------------------------------------------------------------
    # Gabe, the briefing writer and every team member (config/team.yaml), as one list.

    from gabe.team import CHIEF_ID, DEFAULT_FREEDOM, FREEDOM_LEVELS, MODEL_TIERS, Member, TeamError, load_team, make_id, save_team

    def team_path() -> Path:
        """The team file, created from legacy `delegates:` the first time the team is edited."""
        cfg = state.config()
        if cfg.team_file is not None:
            return cfg.team_file
        path = cfg.root / "team.yaml"
        if not path.exists():
            save_team(path, cfg.team())
        def move(raw):  # the team now lives in team.yaml; config can't have both
            raw["team_file"] = "team.yaml"
            raw.pop("delegates", None)
        state.edit_config(move)
        return path

    def edit_team(fn) -> None:
        path = team_path()
        team = load_team(path)
        try:
            fn(team)
            team.validate()  # edited members' actions are checked in member_from_payload
        except TeamError as e:
            bad(str(e))
        save_team(path, team)

    def agent_rows(cfg) -> list[dict[str, Any]]:
        from gabe.app import HIRE

        reg = registry()
        reg.register(Tool(HIRE, "", {}, lambda **k: None, Risk.WRITE, "team", ask_first=True))  # for its rule
        all_tools = [t.name for t in reg.tools()]
        pol = Policy.from_config(cfg.policy)
        team = cfg.team()
        from gabe.app import chief_tools

        own = chief_tools(cfg, team, set(all_tools))
        rows = [{
            "id": "chief", "name": cfg.assistant_name, "log_name": cfg.assistant_name, "kind": "chief",
            "role": "Chief of staff. Hands your requests to the team.",
            "instructions": "", "tools": sorted(set((own if own is not None else all_tools) + [HIRE])) + (["delegate"] if team.reports(CHIEF_ID) else []),
            "paused": cfg.paused, "held": False, "rules": (cfg.policy or {}).get("tools") or {}, "reports_to": "you",
            "freedom": None, "model": None,
        }, {
            "id": "briefing", "name": "Briefing writer", "log_name": f"{cfg.assistant_name} (briefing)", "kind": "briefing",
            "role": "Writes the daily brief. Read only.",
            "instructions": "", "tools": [t.name for t in reg.tools() if t.risk is Risk.READ and t.name != HIRE],
            "paused": BriefingSettings.from_config(cfg).paused, "held": False, "rules": {}, "reports_to": "chief",
            "freedom": None, "model": None,
        }]
        # The model each agent runs on: Gabe's own for Gabe and the briefing writer; a member's
        # fixed model, or for one on Default (picked per job), whatever its last job ran on.
        from gabe.providers import own_model, tier_models
        from gabe.usage import read_events

        tier_of = {v: k for k, v in tier_models(cfg.provider).items()}
        last_model = {e.get("agent"): e.get("model") for e in read_events(cfg.data_dir / "usage.jsonl")}
        gabe_model = own_model(cfg.provider)
        for r in rows:
            r["model_in_use"] = tier_of.get(gabe_model, gabe_model)
        for m in team.members:
            used = last_model.get(m.name)
            rows.append({
                "model_in_use": m.model or (tier_of.get(used, used) if used else None),
                "id": m.id, "name": m.name, "log_name": m.name, "kind": "member", "role": m.job,
                "instructions": m.instructions, "tools": [t for t in (m.skills or all_tools) if t in all_tools] + (["delegate"] if team.reports(m.id) else []),
                "all_tools": m.skills is None, "paused": not m.active, "held": m.active and not team.is_working(m.id),
                "rules": {}, "reports_to": m.reports_to, "freedom": m.freedom, "model": m.model,
            })
        for r in rows:
            p = pol
            if r["kind"] == "member":
                p = pol.restricted(FREEDOM_LEVELS[r["freedom"]].ceiling)
            r["decisions"] = {}
            for name in r["tools"]:
                t = reg.get(name) or (find_tool("delegate") if name == "delegate" else None)
                if t:
                    r["decisions"][name] = "allow" if r["kind"] == "briefing" else p.decide(t).value
        return rows

    def agent_or_404(cfg, aid: str) -> dict[str, Any]:
        row = next((r for r in agent_rows(cfg) if r["id"] == aid), None)
        if not row:
            bad("That agent doesn't exist any more.", 404)
        return row

    @app.get("/api/agents")
    def agents(day: str = ""):
        cfg = state.config()
        rows = agent_rows(cfg)
        tz = company_tz(cfg)
        day = day or today_str()
        live = state.live_view()
        pending = [a for a in state.approvals.all() if a["status"] == "pending"]
        by_log = {r["log_name"]: r["id"] for r in rows}
        by_log.update({r["id"]: r["id"] for r in rows if r["kind"] == "member"})  # older logs used ids
        by_log["briefing"] = "briefing"  # the brief's email send
        timeline: dict[str, list] = {r["id"]: [] for r in rows}
        timeline["you"] = []
        audit_rows = read_audit()
        ever = {by_log.get(r.get("agent", "")) for r in audit_rows}
        for row in reversed(audit_rows):
            try:
                local = datetime.fromisoformat(row["ts"]).astimezone(tz)
            except (KeyError, ValueError):
                continue
            if local.strftime("%Y-%m-%d") != day:
                continue
            aid = "you" if row.get("agent") == PERSON else by_log.get(row.get("agent", ""))
            if aid is None:
                continue
            timeline[aid].append({"ts": row["ts"], "minute": local.hour * 60 + local.minute,
                                  "tool": row.get("tool"), "outcome": row.get("outcome"), "input": row.get("input")})
        brief_running = (state.job("brief") or {}).get("status") == "running"
        for r in rows:
            lv = live.get(r["log_name"], {})
            waits = [a for a in pending if a.get("agent") == r["log_name"]]
            working = lv.get("working", 0) > 0 or (r["kind"] == "briefing" and brief_running)
            r["status"] = ("waiting" if waits else "working" if working
                           else "paused" if r["paused"] or r["held"] else "idle")
            r["now"] = lv.get("now") if working else None
            handed = lv.get("from") if working else None
            r["handed_by"] = "you" if handed == "you" else by_log.get(handed) if handed else None
            r["session"] = lv.get("session")
            r["waiting"] = [a["id"] for a in waits]
            r["asking"] = any(a.get("kind") == "question" for a in waits)
            r["never_used"] = r["kind"] == "member" and r["id"] not in ever and not working
            items = timeline[r["id"]]
            r["today"] = {"done": sum(1 for i in items if i["outcome"] == "ok"),
                          "problems": sum(1 for i in items if i["outcome"] != "ok"), "total": len(items)}
        return {"day": day, "today": today_str(), "agents": rows, "timeline": timeline,
                "request": state.job("agent_request"),
                "freedom_levels": [{"key": f.key, "label": f.label, "explanation": f.explanation}
                                   for f in FREEDOM_LEVELS.values()]}

    @app.post("/api/agents/{aid}/pause")
    def pause_agent(aid: str, payload: dict = Body(...)):
        cfg = state.config()
        row = agent_or_404(cfg, aid)
        paused = bool(payload.get("paused"))
        if row["kind"] == "member":
            def fn(team):
                team.get(aid).active = not paused
            edit_team(fn)
        else:
            def fn(raw):
                section = raw.setdefault("assistant" if row["kind"] == "chief" else "briefing", {})
                if paused:
                    section["paused"] = True
                else:
                    section.pop("paused", None)
            state.edit_config(fn)
        person_audit("pause_agent" if paused else "resume_agent", {"agent": row["name"]})
        return {"ok": True}

    @app.put("/api/agents/chief/rules")
    def chief_rules(payload: dict = Body(...)):
        rules = {k: v for k, v in (payload.get("rules") or {}).items() if v}
        if any(v not in DECISIONS for v in rules.values()):
            bad("Each rule must be allow, ask or deny.")
        state.edit_config(lambda raw: raw.setdefault("policy", {}).__setitem__("tools", rules))
        person_audit("edit_agent_rules", {"agent": state.config().assistant_name})
        return {"ok": True}

    def member_from_payload(payload: dict, existing: Member | None, team) -> Member:
        known = set(team_skills(state.config()))
        name = str(payload.get("name", existing.name if existing else "")).strip()
        if not name:
            bad("Give the agent a name.")
        skills = payload.get("tools", existing.skills if existing else None)
        if skills is not None:
            skills = [t for t in skills if t != "delegate"]
            unknown = [t for t in skills if t not in known]
            if unknown:
                bad(f"Unknown action(s): {', '.join(unknown)}")
            if not skills:
                bad("Give the agent at least one thing it can do.")
        freedom = payload.get("freedom", existing.freedom if existing else DEFAULT_FREEDOM)
        if freedom not in FREEDOM_LEVELS:
            bad("Unknown freedom level.")
        model = payload.get("model", existing.model if existing else None) or None
        if model is not None and model not in MODEL_TIERS:
            bad("Unknown model.")
        return Member(
            id=existing.id if existing else make_id(str(payload.get("id") or name), {m.id for m in team.members}),
            name=name,
            job=str(payload.get("description", payload.get("job", existing.job if existing else ""))).strip(),
            instructions=str(payload.get("instructions", existing.instructions if existing else "")).strip(),
            reports_to=str(payload.get("reports_to", existing.reports_to if existing else CHIEF_ID)) or CHIEF_ID,
            skills=skills, freedom=freedom, active=existing.active if existing else True, model=model,
        )

    @app.post("/api/agents")
    def add_agent(payload: dict = Body(...)):
        added = {}

        def fn(team):
            m = member_from_payload(payload, None, team)
            team.upsert(m)
            added["id"] = m.id
        edit_team(fn)
        person_audit("add_agent", {"agent": added["id"]})
        return added

    @app.put("/api/agents/{aid}")
    def edit_agent(aid: str, payload: dict = Body(...)):
        row = agent_or_404(state.config(), aid)
        if row["kind"] != "member":
            bad("This agent can't be edited here.")

        def fn(team):
            team.upsert(member_from_payload(payload, team.get(aid), team))
        edit_team(fn)
        person_audit("edit_agent", {"agent": row["name"]})
        return {"id": aid}

    @app.delete("/api/agents/{aid}")
    def remove_agent(aid: str):
        row = agent_or_404(state.config(), aid)
        if row["kind"] != "member":
            bad("This agent can't be removed, only paused.")
        edit_team(lambda team: team.remove(aid))
        person_audit("remove_agent", {"agent": row["name"]})
        return {"ok": True}

    @app.post("/api/agents/{aid}/jobs")
    def give_job(aid: str, payload: dict = Body(...)):
        row = agent_or_404(state.config(), aid)
        text = str(payload.get("text", "")).strip()
        if row["kind"] == "briefing":
            return {"job": run_brief()}
        if not text and not payload.get("task_id"):
            bad("Say what the job is.")
        member = None if row["kind"] == "chief" else aid
        try:
            state.check_not_paused(member)
        except RuntimeError as e:
            bad(str(e), 409)
        require_task(payload.get("task_id"))
        s = state.sessions.create(agent=member)
        task = attach_task(payload.get("task_id"), member, s["id"])
        state.sessions.send(s["id"], text, task)
        return {"session_id": s["id"]}

    @app.post("/api/agents/request")
    def request_agent(payload: dict = Body(...)):
        role = str(payload.get("role", "")).strip()
        if not role:
            bad("Describe the role you need.")
        cfg = state.config()
        tools = [{"name": t.name, "what": t.description.split(".")[0]} for t in registry().tools()]
        existing = [{"id": m.id, "name": m.name, "job": m.job, "reports_to": m.reports_to} for m in cfg.team().members]
        ids = {x["id"] for x in existing}
        # Choices the person made up front; left empty, Gabe decides.
        want_manager = str(payload.get("reports_to") or "").strip() or None
        if want_manager is not None and want_manager not in ids | {CHIEF_ID}:
            bad("Pick someone on the team to report to.")
        want_model = payload.get("model") or None
        if want_model is not None and want_model not in MODEL_TIERS:
            bad("Unknown model.")
        freedom = payload.get("freedom") or DEFAULT_FREEDOM
        if freedom not in FREEDOM_LEVELS:
            bad("Unknown freedom level.")
        fixed = []
        if want_manager:
            fixed.append(f"It reports to '{want_manager}' (already decided).")
        if want_model:
            fixed.append(f"It runs on {want_model} (already decided).")

        def go():
            prompt = (
                "A person wants a new AI assistant on the team. Design it.\n"
                f"Role they asked for: {role}\n"
                f"Current team (reports_to 'chief' means it reports to you): {json.dumps(existing)}\n"
                f"Actions available (pick only what the role needs): {json.dumps(tools)}\n"
                + "".join(f"{line}\n" for line in fixed) +
                "Reply with ONLY a JSON object: {\"name\": short display name, \"description\": one line, "
                "\"instructions\": 3-6 plain sentences on how it works, \"tools\": [action names], "
                "\"reports_to\": \"chief\" or the id of the team member it should report to, "
                "\"model\": \"default\" to pick a model for each job (best for most roles), or fix one when every "
                "job needs the same: \"haiku\", \"sonnet\" for judgement or careful writing, \"opus\" only for "
                "high-priority, genuinely difficult work}."
            )
            text = build_briefer(cfg).send(prompt)
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                raise RuntimeError(f"{cfg.assistant_name} didn't come back with a proposal. Try describing the role differently.")
            p = json.loads(m.group(0))
            known = set(team_skills(state.config()))
            reports_to = want_manager or str(p.get("reports_to") or CHIEF_ID)
            model = want_model or (p.get("model") if p.get("model") in MODEL_TIERS else None)
            return {"role": role, "name": str(p.get("name", "")).strip(), "description": str(p.get("description", "")),
                    "instructions": str(p.get("instructions", "")),
                    "tools": [t for t in p.get("tools") or [] if t in known],
                    "reports_to": reports_to if reports_to in ids else CHIEF_ID, "freedom": freedom,
                    "model": model}

        return state.start_job("agent_request", go, label="New agent design")

    @app.post("/api/agents/request/dismiss")
    def dismiss_request():
        state.jobs.pop("agent_request", None)
        return {"ok": True}

    # -- front end -------------------------------------------------------------

    if (STATIC / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        f = STATIC / path
        if path and f.is_file() and STATIC in f.resolve().parents:
            return FileResponse(f)
        index = STATIC / "index.html"
        if index.exists():
            return FileResponse(index)
        return HTMLResponse(
            "<h1>The Gabe web app hasn't been built yet</h1>"
            "<p>Run <code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>, then restart <code>gabe web</code>.</p>",
            status_code=503,
        )

    return app


class _NoProvider:
    """Stand-in provider for building a registry without touching a model."""

    def start(self, system, tools):  # pragma: no cover - never called
        raise RuntimeError("not for running")
