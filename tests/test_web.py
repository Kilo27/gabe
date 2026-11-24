import time

from fastapi.testclient import TestClient

import gabe.web.state as web_state
from gabe.providers.base import StepResult, ToolCall
from gabe.web.server import create_app
from tests.conftest import ScriptedProvider


def _client(config_file, monkeypatch, script):
    provider = ScriptedProvider(script)
    orig = web_state.build_gabe
    monkeypatch.setattr(web_state, "build_gabe", lambda cfg, **kw: orig(cfg, provider=provider, **kw))
    return TestClient(create_app(config_file)), provider


def _wait(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    raise AssertionError("timed out")


EMAIL = {"to": ["a@b.co"], "subject": "Hi", "body": "Hello"}


def test_edit_then_approve_sends_edited_email(config_file, monkeypatch):
    c, _ = _client(config_file, monkeypatch, [
        StepResult("", [ToolCall("1", "send_email", EMAIL)], "tool_use"),
        StepResult("Sent.", [], "done"),
    ])
    sid = c.post("/api/sessions", json={"text": "email them"}).json()["id"]
    pending = _wait(lambda: [a for a in c.get("/api/approvals").json() if a["status"] == "pending"])
    r = c.post(f"/api/approvals/{pending[0]['id']}", json={"decision": "approve", "args": {**EMAIL, "subject": "Edited"}})
    assert r.json()["edited"] is True
    msg = _wait(lambda: (m := c.get(f"/api/sessions/{sid}").json()["messages"][-1])["status"] == "done" and m)
    assert msg["text"] == "Sent."
    outbox = (config_file.parent / "data" / "email" / "outbox")
    assert '"subject": "Edited"' in next(outbox.glob("*.json")).read_text()


def test_reject_passes_note_to_model(config_file, monkeypatch):
    c, provider = _client(config_file, monkeypatch, [
        StepResult("", [ToolCall("1", "send_email", EMAIL)], "tool_use"),
        StepResult("OK.", [], "done"),
    ])
    c.post("/api/sessions", json={"text": "email them"})
    pending = _wait(lambda: [a for a in c.get("/api/approvals").json() if a["status"] == "pending"])
    c.post(f"/api/approvals/{pending[0]['id']}", json={"decision": "reject", "note": "not on Fridays"})
    results = _wait(lambda: [x for k, x in provider.log if k == "tool_results"])
    assert results[0][0].is_error and "not on Fridays" in results[0][0].content


def test_invalid_edit_is_refused(config_file, monkeypatch):
    c, _ = _client(config_file, monkeypatch, [
        StepResult("", [ToolCall("1", "send_email", EMAIL)], "tool_use"),
        StepResult("OK.", [], "done"),
    ])
    c.post("/api/sessions", json={"text": "x"})
    pending = _wait(lambda: [a for a in c.get("/api/approvals").json() if a["status"] == "pending"])
    r = c.post(f"/api/approvals/{pending[0]['id']}", json={"decision": "approve", "args": {"to": "nope"}})
    assert r.status_code == 400
    c.post(f"/api/approvals/{pending[0]['id']}", json={"decision": "reject"})


def test_tasks_and_policy_settings(config_file, monkeypatch):
    c, _ = _client(config_file, monkeypatch, [])
    t = c.post("/api/tasks", json={"title": "Do it", "owner": "Sam", "due": "2026-01-01"}).json()
    assert c.patch(f"/api/tasks/{t['id']}", json={"status": "done"}).json()["status"] == "done"
    s = c.put("/api/settings/policy", json={"defaults": {"read": "allow", "write": "allow", "external": "deny"}, "tools": {}}).json()
    assert next(r for r in s["policy"]["tools"] if r["name"] == "send_email")["decision"] == "deny"
    assert (config_file.parent / "gabe.yaml.bak").exists()


def test_token_locks_the_api(config_file):
    c = TestClient(create_app(config_file, token="secret-token"))
    assert c.get("/api/overview").status_code == 403
    assert c.get("/api/overview", headers={"X-Gabe-Token": "wrong"}).status_code == 403
    assert c.get("/api/overview", headers={"X-Gabe-Token": "secret-token"}).status_code == 200


def test_desktop_first_run_copies_starter_config(tmp_path, monkeypatch):
    from gabe.config import load_config
    from gabe.desktop import ensure_config

    cfg = ensure_config(tmp_path)
    assert cfg == tmp_path / "config" / "gabe.yaml"
    assert (tmp_path / "config" / "playbooks" / "daily-brief.md").exists()
    assert load_config(cfg).data_dir == (tmp_path / "data").resolve()
    cfg.write_text(cfg.read_text().replace("Your company", "Kept"))
    ensure_config(tmp_path)  # never overwrites
    assert "Kept" in cfg.read_text()


def test_first_build_config_moves_to_the_starter_team(tmp_path):
    import shutil

    import yaml

    from gabe.config import load_config
    from gabe.desktop import bundled, ensure_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    old = yaml.safe_load(bundled("gabe", "defaults", "gabe.yaml").read_text())
    old.pop("team_file")
    old["delegates"] = yaml.safe_load(bundled("gabe", "defaults", "gabe_v0_delegates.yaml").read_text())
    old["company"]["name"] = "Kept Co"
    (cfg_dir / "gabe.yaml").write_text(yaml.safe_dump(old))
    shutil.copytree(bundled("config", "playbooks"), cfg_dir / "playbooks")
    cfg = load_config(ensure_config(tmp_path))
    assert cfg.company["name"] == "Kept Co" and cfg.team_file == (cfg_dir / "team.yaml").resolve()
    assert cfg.team().get("notes") is not None and not cfg.delegates


def test_edited_specialists_are_left_alone(tmp_path):
    import yaml

    from gabe.desktop import bundled, ensure_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    old = yaml.safe_load(bundled("gabe", "defaults", "gabe.yaml").read_text())
    old.pop("team_file")
    old["delegates"] = {"mine": {"description": "x", "instructions": "y", "tools": ["recall"]}}
    (cfg_dir / "gabe.yaml").write_text(yaml.safe_dump(old))
    ensure_config(tmp_path)
    assert "mine" in yaml.safe_load((cfg_dir / "gabe.yaml").read_text())["delegates"]


def test_bot_question_waits_for_an_answer_from_the_app(config_file, monkeypatch):
    c, _ = _client(config_file, monkeypatch, [
        StepResult("", [ToolCall("1", "ask_user", {"question": "Which client first?", "choices": ["Acme", "Beta"]})], "tool_use"),
        StepResult("Starting with Beta.", [], "done"),
    ])
    sid = c.post("/api/sessions", json={"text": "chase invoices"}).json()["id"]
    q = _wait(lambda: [a for a in c.get("/api/approvals").json() if a["status"] == "pending"])[0]
    assert q["kind"] == "question" and q["args"]["choices"] == ["Acme", "Beta"] and q["agent"] == "Gabe"
    gabe = next(a for a in c.get("/api/agents").json()["agents"] if a["id"] == "chief")
    assert gabe["status"] == "waiting" and gabe["asking"]
    assert c.post(f"/api/approvals/{q['id']}", json={"answer": ""}).status_code == 400
    assert c.post(f"/api/approvals/{q['id']}", json={"answer": "Beta"}).json()["status"] == "answered"
    msg = _wait(lambda: (m := c.get(f"/api/sessions/{sid}").json()["messages"][-1])["status"] == "done" and m)
    assert msg["text"] == "Starting with Beta."


def test_unattended_questions_get_told_to_assume():
    from gabe.app import NOBODY_TO_ASK, no_one_to_ask

    assert no_one_to_ask("Gabe", "x?", []) == NOBODY_TO_ASK


def test_notification_text_says_who_and_what():
    from gabe.desktop import describe_request

    assert describe_request({"kind": "question", "agent": "Scheduler", "args": {"question": "Move the deadline?"}}) == (
        "Scheduler has a question", "Move the deadline?")
    title, body = describe_request({"agent": "Communications", "tool": "send_email",
                                    "args": {"to": ["a@b.co"], "subject": "Hi"}})
    assert title == "Communications needs your OK" and body == "Send an email to a@b.co: Hi"


def test_task_attached_to_a_bot_job(config_file, monkeypatch):
    c, provider = _client(config_file, monkeypatch, [StepResult("On it.", [], "done")])
    t = c.post("/api/tasks", json={"title": "Renew lease", "owner": "Sam", "due": "2026-12-01", "notes": "Landlord is Pat"}).json()
    r = c.post("/api/agents/scheduler/jobs", json={"text": "Sort this out", "task_id": t["id"]})
    sid = r.json()["session_id"]
    _wait(lambda: c.get(f"/api/sessions/{sid}").json()["messages"][-1]["status"] == "done")
    sent = next(x for k, x in provider.log if k == "user")
    assert sent.startswith("Sort this out") and f"Attached task (id {t['id']}): Renew lease" in sent
    assert "Landlord is Pat" in sent and "update_task" in sent
    s = c.get(f"/api/sessions/{sid}").json()
    assert s["messages"][0]["task"] == {"id": t["id"], "title": "Renew lease"} and s["messages"][0]["text"] == "Sort this out"
    task = next(x for x in c.get("/api/tasks").json()["tasks"] if x["id"] == t["id"])
    assert task["handled_by"]["agent_id"] == "scheduler" and task["handled_by"]["session_id"] == sid


def test_task_alone_is_enough_for_a_job(config_file, monkeypatch):
    c, provider = _client(config_file, monkeypatch, [StepResult("ok", [], "done")])
    t = c.post("/api/tasks", json={"title": "Book offsite", "owner": "Sam"}).json()
    sid = c.post("/api/agents/chief/jobs", json={"text": "", "task_id": t["id"]}).json()["session_id"]
    assert c.get(f"/api/sessions/{sid}").json()["title"] == "Book offsite"
    before = len(c.get("/api/sessions").json())
    assert c.post("/api/agents/chief/jobs", json={"text": "", "task_id": "nope"}).status_code == 404
    assert len(c.get("/api/sessions").json()) == before  # no empty conversation left behind


def _request(config_file, monkeypatch, reply, **choices):
    import gabe.briefing as briefing
    import gabe.web.server as server

    provider = ScriptedProvider([StepResult(reply, [], "done")])
    orig = briefing.build_briefer
    monkeypatch.setattr(server, "build_briefer", lambda cfg, **kw: orig(cfg, provider=provider, **kw))
    c = TestClient(create_app(config_file))
    r = c.post("/api/agents/request", json={"role": "Chases unpaid invoices", **choices})
    assert r.status_code == 200, r.text
    job = _wait(lambda: (j := c.get("/api/agents").json()["request"]) and j["status"] != "running" and j)
    sent = next(x for k, x in provider.log if k == "user")
    return job["result"], sent


PROPOSAL = ('{"name": "Invoice chaser", "description": "Chases invoices", "instructions": "Be polite.", '
            '"tools": [], "reports_to": "chief", "model": "opus"}')


def test_request_an_agent_uses_gabes_choices_by_default(config_file, monkeypatch):
    result, _ = _request(config_file, monkeypatch, PROPOSAL)
    assert result["reports_to"] == "chief" and result["model"] == "opus" and result["freedom"] == "standard"


def test_request_an_agent_keeps_the_persons_choices(config_file, monkeypatch):
    c = TestClient(create_app(config_file))
    member = next(a["id"] for a in c.get("/api/agents").json()["agents"] if a["kind"] == "member")
    result, sent = _request(config_file, monkeypatch, PROPOSAL, reports_to=member, model="haiku", freedom="ask_first")
    assert result["reports_to"] == member and result["model"] == "haiku" and result["freedom"] == "ask_first"
    assert f"reports to '{member}'" in sent and "runs on haiku" in sent


def test_request_an_agent_refuses_bad_choices(config_file):
    c = TestClient(create_app(config_file))
    assert c.post("/api/agents/request", json={"role": "x", "model": "gpt"}).status_code == 400
    assert c.post("/api/agents/request", json={"role": "x", "reports_to": "nobody"}).status_code == 400


# ---- job outcomes are announced; failures go to Gabe to fix --------------------------

def _run(c, text, agent=None):
    sid = c.post("/api/sessions", json={"agent": agent} if agent else {}).json()["id"]
    c.post(f"/api/sessions/{sid}/messages", json={"text": text})
    _wait(lambda: c.get(f"/api/sessions/{sid}").json()["status"] == "idle")
    return sid


def _notices(c, n):
    return _wait(lambda: len(x := c.get("/api/notices").json()) >= n and x)


def test_finished_job_is_announced(config_file, monkeypatch):
    c, _ = _client(config_file, monkeypatch, [StepResult("All done.", [], "done")])
    sid = _run(c, "Tidy the task list")
    [n] = _notices(c, 1)
    assert n["kind"] == "done" and n["session_id"] == sid and n["title"] == "Gabe finished"


def test_failed_job_is_announced_and_handed_to_gabe(config_file, monkeypatch):
    c, provider = _client(config_file, monkeypatch, [RuntimeError("the mail server said no"),
                                                     StepResult("Retried; it worked.", [], "done")])
    sid = _run(c, "Send the invoices")
    notices = _notices(c, 2)
    failed = next(n for n in notices if n["kind"] == "failed")
    assert "mail server said no" in failed["body"] and "Gabe is looking into it" in failed["body"]
    msg = c.get(f"/api/sessions/{sid}").json()["messages"][-1]
    assert msg["status"] == "error" and msg["fix_session"] == failed["session_id"]
    fix = _wait(lambda: (s := c.get(f"/api/sessions/{msg['fix_session']}").json())["status"] == "idle" and s)
    assert fix["title"].startswith("Fixing: Send the invoices") and fix["messages"][0]["auto"]
    asked = fix["messages"][0]["text"]
    assert "Send the invoices" in asked and "mail server said no" in asked
    assert fix["messages"][-1]["text"] == "Retried; it worked."
    assert any(n["kind"] == "done" and n["session_id"] == fix["id"] for n in notices)


def test_a_failed_fix_is_not_fixed_again(config_file, monkeypatch):
    c, _ = _client(config_file, monkeypatch, [RuntimeError("first"), RuntimeError("second")])
    _run(c, "Do the thing")
    notices = _notices(c, 2)
    time.sleep(0.3)
    notices = c.get("/api/notices").json()
    assert [n["kind"] for n in notices] == ["failed", "failed"]
    assert len(c.get("/api/sessions").json()) == 2  # the job and one fix, no more
    assert any(n["title"].startswith("Gabe couldn't fix") for n in notices)


def test_no_fix_when_gabe_cannot_reach_its_model(config_file, monkeypatch):
    class AuthenticationError(Exception):
        pass

    c, _ = _client(config_file, monkeypatch, [AuthenticationError("bad key")])
    _run(c, "Do the thing")
    [n] = _notices(c, 1)
    assert n["kind"] == "failed" and "looking into it" not in n["body"]
    assert len(c.get("/api/sessions").json()) == 1


def test_agents_show_the_model_they_run_on(config_file):
    import json as _json

    from gabe.config import load_config
    from gabe.team import Member, Team, save_team

    cfg = load_config(config_file)
    team_file = cfg.root / "team.yaml"
    save_team(team_file, Team([Member(id="fixed", name="Fixed", model="sonnet"),
                               Member(id="picked", name="Picked"), Member(id="fresh", name="Fresh")]))
    import yaml

    raw = yaml.safe_load(config_file.read_text())
    raw.pop("delegates", None)
    raw["team_file"] = "team.yaml"
    config_file.write_text(yaml.safe_dump(raw))
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "usage.jsonl").write_text(
        _json.dumps({"agent": "Picked", "model": "claude-sonnet-5-5"}) + "\n"
        + _json.dumps({"agent": "Picked", "model": "claude-haiku-5-5"}) + "\n")
    rows = {a["id"]: a for a in TestClient(create_app(config_file)).get("/api/agents").json()["agents"]}
    assert rows["chief"]["model_in_use"] == "opus" and rows["briefing"]["model_in_use"] == "opus"
    assert rows["fixed"]["model_in_use"] == "sonnet"
    assert rows["picked"]["model"] is None and rows["picked"]["model_in_use"] == "haiku"  # its last job
    assert rows["fresh"]["model_in_use"] is None


def _interrupted_session(config_file, **reply_extra):
    import json as _json

    from gabe.config import load_config

    d = load_config(config_file).data_dir / "web" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    reply = {"id": "r1", "role": "assistant", "text": "", "ts": "2026-11-24T10:00:00+00:00", "status": "working",
             "prompt": "Email Alex about the board pack",
             "activity": [{"kind": "tool", "agent": "Gabe", "tool": "send_email",
                           "input": {"to": ["alex@x.co"], "subject": "Board pack"}, "outcome": "ok"}],
             **reply_extra}
    s = {"id": "s1", "title": "Board pack", "created": "x", "updated": "x", "status": "running", "agent": None,
         "messages": [{"id": "u1", "role": "user", "text": "Email Alex about the board pack", "ts": "x",
                       "status": "done", "activity": []}, reply]}
    (d / "s1.json").write_text(_json.dumps(s))


def test_interrupted_work_is_picked_up_without_repeating_done_steps(config_file, monkeypatch):
    _interrupted_session(config_file)
    c, provider = _client(config_file, monkeypatch, [StepResult("Already sent; nothing more to do.", [], "done")])
    msg = _wait(lambda: (m := c.get("/api/sessions/s1").json()["messages"][-1])["status"] == "done" and m)
    assert msg["text"] == "Already sent; nothing more to do." and msg["resumes"] == 1
    assert any(a["kind"] == "resumed" for a in msg["activity"])
    sent = next(x for k, x in provider.log if k == "user")
    assert sent.startswith("Email Alex about the board pack")
    assert "picking it back up" in sent and 'done: send_email {"to": ["alex@x.co"], "subject": "Board pack"}' in sent
    assert c.app.state.gabe.resumed == [{"session_id": "s1", "title": "Board pack", "agent": None}]


def test_work_that_keeps_crashing_the_app_is_stopped(config_file, monkeypatch):
    _interrupted_session(config_file, resumes=2)
    c, provider = _client(config_file, monkeypatch, [])
    msg = _wait(lambda: (m := c.get("/api/sessions/s1").json()["messages"][-1])["status"] == "error" and m)
    assert "stopped retrying" in msg["text"] and not provider.log


def test_interrupted_work_for_a_paused_agent_is_not_restarted(config_file, monkeypatch):
    import yaml as _yaml

    raw = _yaml.safe_load(config_file.read_text())
    raw.setdefault("assistant", {})["paused"] = True
    config_file.write_text(_yaml.safe_dump(raw))
    _interrupted_session(config_file)
    c, provider = _client(config_file, monkeypatch, [])
    msg = _wait(lambda: (m := c.get("/api/sessions/s1").json()["messages"][-1])["status"] == "error" and m)
    assert "wasn't restarted" in msg["text"] and "paused" in msg["text"] and not provider.log


def test_missed_brief_is_caught_up(config_file):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from gabe.briefing import BriefingSettings, missed_brief
    from gabe.config import load_config

    cfg = load_config(config_file)
    tue_9am = datetime(2026, 11, 24, 9, 0, tzinfo=ZoneInfo("UTC"))
    assert missed_brief(cfg, tue_9am)                                    # due at 08:00, none written
    assert not missed_brief(cfg, tue_9am.replace(hour=7))                # not due yet
    assert not missed_brief(cfg, datetime(2026, 11, 29, 9, tzinfo=ZoneInfo("UTC")))  # Sunday
    out = BriefingSettings.from_config(cfg).output_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "2026-11-24.md").write_text("done")
    assert not missed_brief(cfg, tue_9am)                                # already written


def test_picked_up_work_is_announced_too(config_file, monkeypatch):
    _interrupted_session(config_file)
    c, _ = _client(config_file, monkeypatch, [StepResult("Done now.", [], "done")])
    [n] = _notices(c, 1)
    assert n["kind"] == "done" and n["session_id"] == "s1"
