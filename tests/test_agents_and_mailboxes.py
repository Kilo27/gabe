import time

import yaml
from fastapi.testclient import TestClient

import gabe.integrations.email as email_mod
import gabe.web.server as server
from gabe.app import build_gabe
from gabe.config import load_config
from gabe.integrations import load_integrations
from gabe.providers.base import StepResult, ToolCall
from gabe.tools.registry import ToolRegistry
from tests.conftest import ScriptedProvider


def _edit(config_file, fn):
    raw = yaml.safe_load(config_file.read_text())
    fn(raw)
    config_file.write_text(yaml.safe_dump(raw))


class FakeGmail:
    def __init__(self, name):
        self.name, self.sent = name, []

    def search(self, query, n):
        return [{"id": f"{self.name}-1", "subject": "hi"}]

    def send(self, to, subject, body, cc=None, reply_to_id=None, html_body=None):
        self.sent.append(subject)
        return "m1"


def test_several_mailboxes_share_tools(config_file, monkeypatch):
    boxes = {}
    monkeypatch.setattr(email_mod, "_client", lambda ctx, account=None: boxes.setdefault(account, FakeGmail(account)))

    def fn(raw):
        raw["integrations"][-1] = {"name": "email", "settings": {"backend": "gmail", "accounts": ["a@x.co", "b@x.co"]}}
    _edit(config_file, fn)
    reg = ToolRegistry()
    load_integrations(load_config(config_file), reg)
    search = reg.get("search_email")
    assert search.input_schema["properties"]["account"]["enum"] == ["a@x.co", "b@x.co"]
    assert {m["account"] for m in search.handler(query="x")} == {"a@x.co", "b@x.co"}
    reg.get("send_email").handler(to=["z@y.co"], subject="S", body="B", account="b@x.co")
    assert boxes["b@x.co"].sent == ["S"] and boxes["a@x.co"].sent == []


def test_one_mailbox_keeps_old_schema(config_file, monkeypatch):
    monkeypatch.setattr(email_mod, "_client", lambda ctx, account=None: FakeGmail("only"))
    _edit(config_file, lambda raw: raw["integrations"].__setitem__(-1, {"name": "email", "settings": {"backend": "gmail"}}))
    reg = ToolRegistry()
    load_integrations(load_config(config_file), reg)
    assert "account" not in reg.get("search_email").input_schema["properties"]


def test_agents_api_pause_freedom_and_request(config_file, monkeypatch):
    def fake_briefer(cfg, **kw):
        return build_gabe(cfg, provider=ScriptedProvider([StepResult(
            '{"name": "Invoice chaser", "description": "Chases invoices.", "instructions": "Be polite.", '
            '"tools": ["list_tasks", "nope"], "reports_to": "scheduler"}',
            [], "done")]))
    monkeypatch.setattr(server, "build_briefer", fake_briefer)
    c = TestClient(server.create_app(config_file))
    ids = [a["id"] for a in c.get("/api/agents").json()["agents"]]
    assert ids == ["chief", "briefing", "scheduler"]  # legacy delegates become the team

    c.post("/api/agents/scheduler/pause", json={"paused": True})
    assert (config_file.parent / "team.yaml").exists()  # first edit moves the team into its own file
    assert c.post("/api/agents/scheduler/jobs", json={"text": "x"}).status_code == 409
    c.post("/api/agents/scheduler/pause", json={"paused": False})

    c.put("/api/agents/scheduler", json={"freedom": "ask_first"})
    sched = next(a for a in c.get("/api/agents").json()["agents"] if a["id"] == "scheduler")
    assert sched["decisions"]["create_task"] == "ask" and not sched["paused"]

    c.post("/api/agents/request", json={"role": "chase invoices"})
    end = time.time() + 5
    while (req := c.get("/api/agents").json()["request"])["status"] == "running" and time.time() < end:
        time.sleep(0.05)
    assert req["result"]["tools"] == ["list_tasks"] and req["result"]["reports_to"] == "scheduler"
    assert c.post("/api/agents", json=req["result"]).json()["id"] == "invoice_chaser"
    chaser = next(a for a in c.get("/api/agents").json()["agents"] if a["id"] == "invoice_chaser")
    assert chaser["reports_to"] == "scheduler"


def test_brief_recipients_list(config_file, monkeypatch):
    c = TestClient(server.create_app(config_file))
    r = c.put("/api/settings/briefing", json={"time": "08:00", "days": ["mon"], "email_to": ["me", "a@x.co", "b@x.co"]})
    assert r.json()["briefing"]["email_to"] == ["me", "a@x.co", "b@x.co"]
    assert c.put("/api/settings/briefing", json={"time": "08:00", "days": ["mon"], "email_to": ["nope"]}).status_code == 400
