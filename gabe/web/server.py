"""HTTP API for the Gabe web app, plus the built front end.

Run with `gabe web`. Everything is local JSON files for now; see docs/UI_PLAN.md.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from gabe.briefing import DAYS, BriefingSettings, build_briefer, company_tz, email_briefing, next_run, run_briefing
from gabe.integrations import IntegrationContext, load_integrations
from gabe.integrations._store import JsonStore
from gabe.policy import Policy
from gabe.tools.registry import Risk, ToolRegistry
from gabe.web.state import WebState, check_args, friendly_error, new_id, now

STATIC = Path(__file__).parent / "static"
PERSON = "You (web app)"
DECISIONS = ("allow", "ask", "deny")


def create_app(config_path: str | Path, token: str | None = None, desktop: bool = False) -> FastAPI:
    """`token`: when set, every /api call must carry it (X-Gabe-Token). The desktop app sets a
    fresh one per launch so other programs and web pages on this computer can't use the API."""
    state = WebState(Path(config_path))
    app = FastAPI(title="Gabe", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.gabe = state

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
        cfg = state.config()
        if name == "delegate":
            from gabe.app import build_gabe
            from gabe.policy import deny_all

            return build_gabe(cfg, approver=deny_all, provider=_NoProvider()).registry.get("delegate")
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

        return state.start_job("brief", go)

    @app.get("/api/jobs/{name}")
    def job(name: str):
        return state.job(name) or {"name": name, "status": "idle"}

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
        s = state.sessions.create(agent=agent)
        if payload.get("text"):
            try:
                state.sessions.send(s["id"], payload["text"])
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
            state.sessions.send(sid, str(payload.get("text", "")))
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

        return state.start_job("triage", go)

    # -- settings --------------------------------------------------------------

    def policy_rows(cfg) -> list[dict[str, Any]]:
        pol = Policy.from_config(cfg.policy)
        tools_cfg = (cfg.policy or {}).get("tools") or {}
        integ_cfg = (cfg.policy or {}).get("integrations") or {}
        reg = registry()
        rows = []
        tools = reg.tools()
        if cfg.delegates:
            t = find_tool("delegate")
            if t:
                tools.append(t)
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
        integrations = []
        for spec in cfg.integrations:
            entry = {"name": spec.name, "settings": {k: v for k, v in spec.settings.items() if "key" not in k.lower()}}
            if spec.name == "email":
                entry["status"] = gmail_status()
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
                "tools": policy_rows(cfg),
                "integrations": (raw.get("policy") or {}).get("integrations") or {},
            },
            "delegates": [{"name": d.name, "description": d.description, "instructions": d.instructions.strip(),
                           "tools": d.tools} for d in cfg.delegates],
            "all_tools": [t.name for t in registry().tools()],
            "provider": provider,
            "integrations": integrations,
            "playbooks": playbooks,
            "usage": [{"date": k, **v} for k, v in sorted(usage_days.items(), reverse=True)[:30]],
            "config_path": str(state.config_path),
            "desktop": desktop,
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

    @app.put("/api/settings/delegates")
    def save_delegates(payload: dict = Body(...)):
        known = set(registry()._tools)
        out = {}
        for d in payload.get("delegates", []):
            name = re.sub(r"[^a-z0-9_]+", "_", str(d.get("name", "")).strip().lower()).strip("_")
            if not name:
                bad("Every specialist needs a name.")
            if name in out:
                bad(f"There are two specialists called '{name}'.")
            tools = d.get("tools")
            if tools is not None:
                unknown = [t for t in tools if t not in known]
                if unknown:
                    bad(f"Unknown action(s): {', '.join(unknown)}")
            out[name] = {"description": str(d.get("description", "")).strip(),
                         "instructions": str(d.get("instructions", "")).strip() + "\n", "tools": tools}

        state.edit_config(lambda raw: raw.__setitem__("delegates", out))
        person_audit("edit_settings", {"section": "specialists"})
        return settings()

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

        return state.start_job("gmail", go)

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

    # -- agents ----------------------------------------------------------------

    def agent_rows(cfg) -> list[dict[str, Any]]:
        """Every agent a person is in charge of: Gabe, the briefing writer, each specialist."""
        reg = registry()
        all_tools = [t.name for t in reg.tools()]
        pol = Policy.from_config(cfg.policy)
        rows = [{
            "id": "chief", "name": cfg.assistant_name, "log_name": cfg.assistant_name, "kind": "chief",
            "role": "Chief of staff. Takes your requests and hands work to the other agents.",
            "instructions": "", "tools": all_tools + (["delegate"] if cfg.delegates else []),
            "paused": cfg.paused, "rules": (cfg.policy or {}).get("tools") or {}, "reports_to": "you",
        }, {
            "id": "briefing", "name": "Briefing writer", "log_name": f"{cfg.assistant_name} (briefing)", "kind": "briefing",
            "role": "Writes the daily brief. Read only.",
            "instructions": "", "tools": [t.name for t in reg.tools() if t.risk is Risk.READ],
            "paused": BriefingSettings.from_config(cfg).paused, "rules": {}, "reports_to": "chief",
        }]
        for d in cfg.delegates:
            rows.append({
                "id": d.name, "name": d.name, "log_name": d.name, "kind": "specialist", "role": d.description,
                "instructions": d.instructions.strip(), "tools": d.tools if d.tools is not None else all_tools,
                "all_tools": d.tools is None, "paused": d.paused, "rules": d.policy, "reports_to": "chief",
            })
        for r in rows:
            r["decisions"] = {}
            for name in r["tools"]:
                t = reg.get(name) or (find_tool("delegate") if name == "delegate" else None)
                if not t:
                    continue
                if r["kind"] == "briefing":
                    r["decisions"][name] = "allow"
                elif r["kind"] == "specialist":
                    spec = next(d for d in cfg.delegates if d.name == r["id"])
                    from gabe.app import specialist_policy

                    r["decisions"][name] = specialist_policy(pol, spec).decide(t).value
                else:
                    r["decisions"][name] = pol.decide(t).value
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
        by_log["briefing"] = "briefing"  # the brief's email send
        timeline: dict[str, list] = {r["id"]: [] for r in rows}
        timeline["you"] = []
        for row in reversed(read_audit()):
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
            r["status"] = "waiting" if waits else "working" if working else "paused" if r["paused"] else "idle"
            r["now"] = lv.get("now") if working else None
            r["handed_by"] = lv.get("from") if working else None
            r["session"] = lv.get("session")
            r["waiting"] = [a["id"] for a in waits]
            items = timeline[r["id"]]
            r["today"] = {"done": sum(1 for i in items if i["outcome"] == "ok"),
                          "problems": sum(1 for i in items if i["outcome"] != "ok"), "total": len(items)}
        return {"day": day, "today": today_str(), "agents": rows, "timeline": timeline,
                "request": state.job("agent_request")}

    @app.post("/api/agents/{aid}/pause")
    def pause_agent(aid: str, payload: dict = Body(...)):
        cfg = state.config()
        row = agent_or_404(cfg, aid)
        paused = bool(payload.get("paused"))

        def fn(raw):
            if row["kind"] == "chief":
                raw.setdefault("assistant", {})["paused"] = paused
            elif row["kind"] == "briefing":
                raw.setdefault("briefing", {})["paused"] = paused
            else:
                raw["delegates"][aid]["paused"] = paused
            for holder in (raw.get("assistant"), raw.get("briefing"), *(raw.get("delegates") or {}).values()):
                if isinstance(holder, dict) and holder.get("paused") is False:
                    holder.pop("paused")

        state.edit_config(fn)
        person_audit("pause_agent" if paused else "resume_agent", {"agent": row["name"]})
        return {"ok": True}

    @app.put("/api/agents/{aid}/rules")
    def agent_rules(aid: str, payload: dict = Body(...)):
        cfg = state.config()
        row = agent_or_404(cfg, aid)
        rules = {k: v for k, v in (payload.get("rules") or {}).items() if v}
        if any(v not in DECISIONS for v in rules.values()):
            bad("Each rule must be allow, ask or deny.")
        if row["kind"] == "briefing":
            bad("The briefing writer is always read only.")

        def fn(raw):
            if row["kind"] == "chief":
                raw.setdefault("policy", {})["tools"] = rules
            else:
                d = raw["delegates"][aid]
                if rules:
                    d["policy"] = rules
                else:
                    d.pop("policy", None)

        state.edit_config(fn)
        person_audit("edit_agent_rules", {"agent": row["name"]})
        return {"ok": True}

    def clean_specialist(payload: dict, known: set[str]) -> tuple[str, dict]:
        name = re.sub(r"[^a-z0-9_]+", "_", str(payload.get("name", "")).strip().lower()).strip("_")
        if not name or name in ("chief", "briefing", "you"):
            bad("Give the agent a different name.")
        tools = payload.get("tools")
        if tools is not None:
            tools = [t for t in tools if t != "delegate"]
            unknown = [t for t in tools if t not in known]
            if unknown:
                bad(f"Unknown action(s): {', '.join(unknown)}")
            if not tools:
                bad("Give the agent at least one thing it can do.")
        return name, {"description": str(payload.get("description", "")).strip(),
                      "instructions": str(payload.get("instructions", "")).strip() + "\n", "tools": tools}

    @app.post("/api/agents")
    def add_agent(payload: dict = Body(...)):
        name, spec = clean_specialist(payload, set(registry()._tools))
        if name in {d.name for d in state.config().delegates}:
            bad(f"There's already an agent called {name}.")
        state.edit_config(lambda raw: raw.__setitem__("delegates", {**(raw.get("delegates") or {}), name: spec}))
        person_audit("add_agent", {"agent": name})
        return {"id": name}

    @app.put("/api/agents/{aid}")
    def edit_agent(aid: str, payload: dict = Body(...)):
        row = agent_or_404(state.config(), aid)
        if row["kind"] != "specialist":
            bad("Only specialist agents can be edited here.")
        name, spec = clean_specialist({**payload, "name": aid}, set(registry()._tools))

        def fn(raw):
            raw["delegates"][aid].update(spec)

        state.edit_config(fn)
        person_audit("edit_agent", {"agent": aid})
        return {"id": aid}

    @app.delete("/api/agents/{aid}")
    def remove_agent(aid: str):
        row = agent_or_404(state.config(), aid)
        if row["kind"] != "specialist":
            bad("This agent can't be removed, only paused.")
        state.edit_config(lambda raw: raw["delegates"].pop(aid, None))
        person_audit("remove_agent", {"agent": aid})
        return {"ok": True}

    @app.post("/api/agents/{aid}/jobs")
    def give_job(aid: str, payload: dict = Body(...)):
        row = agent_or_404(state.config(), aid)
        text = str(payload.get("text", "")).strip()
        if row["kind"] == "briefing":
            return {"job": run_brief()}
        if not text:
            bad("Say what the job is.")
        agent = None if row["kind"] == "chief" else aid
        try:
            state.check_not_paused(agent)
        except RuntimeError as e:
            bad(str(e), 409)
        s = state.sessions.create(title=text[:60] + ("…" if len(text) > 60 else ""), agent=agent)
        state.sessions.send(s["id"], text)
        return {"session_id": s["id"]}

    @app.post("/api/agents/request")
    def request_agent(payload: dict = Body(...)):
        role = str(payload.get("role", "")).strip()
        if not role:
            bad("Describe the role you need.")
        cfg = state.config()
        tools = [{"name": t.name, "what": t.description.split(".")[0]} for t in registry().tools()]
        existing = [{"name": d.name, "role": d.description} for d in cfg.delegates]

        def go():
            prompt = (
                "A person wants a new specialist agent on the team. Design it.\n"
                f"Role they asked for: {role}\n"
                f"Existing specialists: {json.dumps(existing)}\n"
                f"Actions available (pick only what the role needs): {json.dumps(tools)}\n"
                "Reply with ONLY a JSON object: {\"name\": short lowercase_with_underscores, "
                "\"description\": one line, \"instructions\": 3-6 plain sentences on how it works, "
                "\"tools\": [action names]}."
            )
            text = build_briefer(cfg).send(prompt)
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                raise RuntimeError(f"{cfg.assistant_name} didn't come back with a proposal. Try describing the role differently.")
            p = json.loads(m.group(0))
            known = set(registry()._tools)
            return {"role": role, "name": str(p.get("name", "")), "description": str(p.get("description", "")),
                    "instructions": str(p.get("instructions", "")),
                    "tools": [t for t in p.get("tools") or [] if t in known]}

        return state.start_job("agent_request", go)

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
