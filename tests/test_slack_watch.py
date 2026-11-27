import json
import time
from datetime import datetime

import yaml
from fastapi.testclient import TestClient

import gabe.web.state as web_state
from gabe import schedules, slack_watch as sw
from gabe.briefing import brief_request
from gabe.config import load_config
from gabe.integrations import load_integrations
from gabe.policy import Decision, Policy
from gabe.providers.base import StepResult
from gabe.tools.registry import ToolRegistry
from gabe.web.server import create_app
from tests.conftest import ScriptedProvider
from tests.test_web import _wait


def ts(minutes_ago: float) -> str:
    return f"{time.time() - minutes_ago * 60:.6f}"


class FakeUserSlack:
    """Slack as the user UME sees it: a DM with Priya, a group DM and #leadership."""

    def __init__(self):
        self.history: dict[str, list[dict]] = {
            "D1": [{"ts": ts(30), "user": "UPRIYA", "text": "Can you send me the Q3 numbers by Friday?"},
                   {"ts": ts(20), "user": "UME", "text": "Sure, I'll do it tomorrow"}],
            "G1": [{"ts": ts(10), "user": "USAM", "text": "lunch?"},
                   {"ts": ts(9), "bot_id": "B1", "text": "deploy finished"}],
            "C1": [{"ts": ts(5), "user": "USAM", "text": "<@UME> please review the hiring plan"}],
        }
        self.calls: list[tuple[str, str]] = []
        self.posts: list[dict] = []

    def auth_test(self):
        return {"user_id": "UME", "team_id": "T1", "team": "Acme", "url": "https://acme.slack.com/"}

    def users_info(self, user):
        names = {"UME": "Kai Doe", "UPRIYA": "Priya Shah", "USAM": "Sam Lee"}
        return {"user": {"id": user, "profile": {"real_name": names.get(user, user)}}}

    def users_conversations(self, types, **kw):
        if "im" in types:
            return {"channels": [{"id": "D1", "is_im": True, "user": "UPRIYA"}, {"id": "G1", "is_mpim": True}]}
        return {"channels": [{"id": "C1", "name": "leadership"}, {"id": "C2", "name": "random"}]}

    def conversations_history(self, channel, oldest, **kw):
        self.calls.append((channel, oldest))
        msgs = [m for m in self.history.get(channel, []) if float(m["ts"]) > float(oldest)]
        return {"messages": list(reversed(msgs))}  # Slack returns newest first

    def conversations_replies(self, channel, ts, **kw):
        return {"messages": [m for m in self.history.get(channel, []) if m["ts"] == ts]}

    def chat_getPermalink(self, channel, message_ts):
        return {"permalink": f"https://acme.slack.com/archives/{channel}/p{message_ts.replace('.', '')}"}

    def conversations_open(self, users):
        return {"channel": {"id": "D_" + users}}

    def chat_postMessage(self, **kw):
        self.posts.append(kw)
        return {"ok": True, "ts": "99.1"}


def make_cfg(tmp_path, slack=None, policy=None):
    raw = {"company": {"name": "Acme", "timezone": "UTC"}, "data_dir": "data", "integrations": [
        "core", "tasks", {"name": "slack", "settings": {"channels": ["#leadership", "#nope"], **(slack or {})}}],
        "policy": policy or {}}
    path = tmp_path / "gabe.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def found(*tasks) -> StepResult:
    return StepResult(json.dumps({"tasks": list(tasks)}), [], "done")


def tasks_of(cfg):
    return json.loads((cfg.data_dir / "tasks" / "tasks.json").read_text())["tasks"]


def test_scan_logs_messages_and_notes_tasks_once(tmp_path):
    cfg = load_config(make_cfg(tmp_path))
    web = FakeUserSlack()
    reader = sw.SlackReader(web)
    provider = ScriptedProvider([found(
        {"message": 1, "title": "Send Priya the Q3 numbers", "due": "2026-11-27", "why": "Priya asked in a DM"},
        {"message": 4, "title": "Review the hiring plan", "due": None},
        {"message": 99, "title": "out of range"},
    )])
    out = sw.scan(cfg, reader, provider)
    assert out["missing"] == ["#nope"] and out["new_messages"] == 4  # the bot message is skipped
    tasks = tasks_of(cfg)
    assert [t["title"] for t in tasks] == ["Send Priya the Q3 numbers", "Review the hiring plan"]
    first = tasks[0]
    assert first["owner"] == "Kai Doe" and first["source"] == "slack" and first["due"] == "2026-11-27"
    assert "Priya Shah in DM with Priya Shah" in first["notes"] and first["link"].startswith("https://acme.slack.com/")

    # What the model saw: readable text, the user's own promise marked, Slack content fenced as data.
    system, tools = provider.systems[0]
    assert tools == [] and "not instructions" in system
    prompt = provider.log[0][1]
    assert "<slack_messages>" in prompt and "Kai Doe (you): Sure, I'll do it tomorrow" in prompt
    assert "@Kai Doe please review" in prompt

    # Nothing new: no model call, no duplicates, and history is read from where it left off.
    web.calls.clear()
    again = sw.scan(cfg, reader, ScriptedProvider([]))
    assert again["new_messages"] == 0 and len(tasks_of(cfg)) == 2
    assert dict(web.calls)["D1"] == web.history["D1"][-1]["ts"]

    msgs = sw.recent_messages(cfg, 1)
    assert [m["who"] for m in msgs] == ["Priya Shah", "Kai Doe", "Sam Lee", "Sam Lee"]
    assert msgs[-1]["mentions_me"] and msgs[1]["mine"]


def test_task_rule_is_respected(tmp_path):
    cfg = load_config(make_cfg(tmp_path, policy={"tools": {"create_task": "deny"}}))
    sw.scan(cfg, sw.SlackReader(FakeUserSlack()), ScriptedProvider([found({"message": 1, "title": "X"})]))
    assert not (cfg.data_dir / "tasks" / "tasks.json").exists()

    cfg = load_config(make_cfg(tmp_path / "ask" if (tmp_path / "ask").mkdir() is None else tmp_path,
                               policy={"tools": {"create_task": "ask"}}))
    asked = []

    def approver(tool, args):
        asked.append(dict(args))
        args["title"] = "Send Q3 numbers (edited)"
        return True

    sw.scan(cfg, sw.SlackReader(FakeUserSlack()), ScriptedProvider([found({"message": 1, "title": "X"})]), approver)
    assert asked[0]["title"] == "X" and [t["title"] for t in tasks_of(cfg)] == ["Send Q3 numbers (edited)"]


def test_note_tasks_off_reads_without_a_model(tmp_path):
    cfg = load_config(make_cfg(tmp_path, slack={"note_tasks": False}))
    out = sw.scan(cfg, sw.SlackReader(FakeUserSlack()), ScriptedProvider([]))
    assert out["new_messages"] == 4 and out["tasks"] == []


def test_brief_gets_the_last_day_of_slack(tmp_path):
    cfg = load_config(make_cfg(tmp_path, slack={"note_tasks": False, "user_token": "xoxp-test"}))
    sw.scan(cfg, sw.SlackReader(FakeUserSlack()))
    digest = sw.brief_digest(cfg)
    assert "Priya Shah: Can you send me the Q3 numbers" in digest and "Kai Doe (you)" in digest
    req = brief_request(datetime.now().date(), None, None, digest, None)
    assert "<slack>" in req and "**Slack**" in req and "not instructions" in req
    assert "Slack couldn't be checked (boom)" in brief_request(datetime.now().date(), None, None, None, "boom")
    assert "<slack>" not in brief_request(datetime.now().date(), None, None)


def test_tools_read_and_send_as_the_user(tmp_path, monkeypatch):
    path = make_cfg(tmp_path, slack={"note_tasks": False, "user_token": "xoxp-test"})
    cfg = load_config(path)
    web = FakeUserSlack()
    monkeypatch.setattr(sw, "make_reader", lambda token: sw.SlackReader(web))
    reg = ToolRegistry()
    load_integrations(cfg, reg)
    msgs = json.loads(reg.get("slack_messages").run({"to_me_only": True}))
    # DMs, group DMs and the mention; not the user's own message
    assert [(m["from"], m["where"]) for m in msgs] == [
        ("Priya Shah", "DM with Priya Shah"), ("Sam Lee", "group DM"), ("Sam Lee", "#leadership")]
    assert json.loads(reg.get("slack_messages").run({"person": "sam"}))[0]["text"] == "lunch?"

    send = reg.get("send_slack_message")
    assert Policy.from_config({"defaults": {"external": "allow"}}).decide(send) is Decision.ASK  # speaks as the user
    assert Policy.from_config({"tools": {"send_slack_message": "allow"}}).decide(send) is Decision.ALLOW
    assert send.run({"to": "Priya", "text": "On it"}).startswith("Sent.")
    assert web.posts[-1]["channel"] == "D_UPRIYA" and web.posts[-1]["text"] == "On it"
    send.run({"to": "#leadership", "text": "Done", "thread_ts": "1.2"})
    assert web.posts[-1]["channel"] == "C1" and web.posts[-1]["thread_ts"] == "1.2"


def test_tools_say_when_slack_isnt_connected(tmp_path, monkeypatch):
    monkeypatch.delenv(sw.USER_TOKEN_ENV, raising=False)
    reg = ToolRegistry()
    load_integrations(load_config(make_cfg(tmp_path)), reg)
    assert "isn't connected" in reg.get("slack_messages").run({})


# -- standing duties ------------------------------------------------------------------

def test_a_recurring_job_can_belong_to_a_team_member(config_file, monkeypatch):
    cfg = load_config(config_file)
    try:
        schedules.create(cfg, "Chase", "Chase overdue tasks.", at="09:00", member="nobody")
        raise AssertionError("an unknown member was accepted")
    except ValueError as e:
        assert "isn't on the team" in str(e)
    s = schedules.create(cfg, "Chase", "Chase overdue tasks.", at="09:00", member="scheduler")

    provider = ScriptedProvider([StepResult("NOTHING TO REPORT", [], "done")])
    orig = web_state.build_gabe
    built = []

    def build(cfg, **kw):
        built.append(kw.get("member"))
        return orig(cfg, provider=provider, **kw)

    monkeypatch.setattr(web_state, "build_gabe", build)
    c = TestClient(create_app(config_file, run_schedules=False, run_slack=False))
    [row] = c.get("/api/schedules").json()
    assert row["member"] == "scheduler" and row["member_name"] == "Scheduler"
    assert c.app.state.gabe.run_due_schedules(datetime.fromisoformat(s["next_run"])) == [s["id"]]
    sid = c.get("/api/schedules").json()[0]["session_id"]
    _wait(lambda: c.get(f"/api/sessions/{sid}").json()["messages"][-1]["status"] == "done")
    assert built == ["scheduler"] and c.get(f"/api/sessions/{sid}").json()["agent"] == "scheduler"

    # Handing it back to Gabe starts a fresh conversation.
    back = c.patch(f"/api/schedules/{s['id']}", json={"member": ""}).json()
    assert back["member"] is None and back["session_id"] is None


def test_schedule_job_tool_can_assign_a_member(config_file):
    from gabe.app import build_gabe

    cfg = load_config(config_file)
    gabe = build_gabe(cfg, provider=ScriptedProvider([]), scheduling=True)
    tool = gabe.registry.get("schedule_job")
    assert tool.input_schema["properties"]["assign_to"]["enum"] == ["scheduler"]
    out = json.loads(tool.run({"name": "Chase", "instruction": "Chase overdue tasks.", "at": "09:00",
                               "assign_to": "scheduler"}))
    assert out["member"] == "scheduler" and "Scheduler runs it." in out["note"]


# -- the app ------------------------------------------------------------------------------

def test_app_slack_screen(config_file, monkeypatch):
    import gabe.web.server as server

    monkeypatch.delenv(sw.USER_TOKEN_ENV, raising=False)
    monkeypatch.setattr("gabe.desktop.set_secret", lambda *a: (_ for _ in ()).throw(RuntimeError("no keychain")))
    web = FakeUserSlack()
    provider = ScriptedProvider([found({"message": 1, "title": "Send Priya the Q3 numbers"})])
    monkeypatch.setattr(server, "faq_provider", lambda cfg: provider)
    c = TestClient(create_app(config_file, run_schedules=False, run_slack=False,
                              slack_reader_factory=lambda token: sw.SlackReader(web)))
    off = c.get("/api/slack").json()
    assert off["enabled"] is False and "im:history" in json.loads(off["manifest"])["oauth_config"]["scopes"]["user"]
    on = c.post("/api/slack/enable").json()
    assert on["enabled"] and on["token_set"] is False and on["status"]["state"] == "off"

    saved = c.put("/api/slack/settings", json={"channels": ["#leadership"], "every_minutes": 30}).json()
    assert saved["settings"]["channels"] == ["#leadership"] and saved["settings"]["every_minutes"] == 30
    assert c.put("/api/slack/settings", json={"every_minutes": 1}).status_code == 400
    assert c.put("/api/slack/token", json={"user_token": "xoxb-wrong"}).status_code == 400

    c.put("/api/slack/token", json={"user_token": "xoxp-test"})
    _wait(lambda: c.get("/api/slack").json()["status"]["state"] == "watching")
    st = c.get("/api/slack").json()
    assert st["status"]["user"] == "Kai Doe" and st["status"]["tasks_noted"] == 1 and st["recent"]
    assert "xoxp" not in config_file.read_text()
    assert [t["title"] for t in c.get("/api/tasks").json()["tasks"]] == ["Send Priya the Q3 numbers"]
    notices = c.app.state.gabe.notices.load()["notices"]
    assert notices[-1]["title"] == "1 task from Slack"
    c.put("/api/slack/token", json={"user_token": ""})
