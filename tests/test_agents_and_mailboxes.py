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


def test_paused_specialist_is_not_run(config_file):
    _edit(config_file, lambda raw: raw["delegates"]["scheduler"].__setitem__("paused", True))
    provider = ScriptedProvider([
        StepResult("", [ToolCall("1", "delegate", {"specialist": "scheduler", "task": "x"})], "tool_use"),
        StepResult("ok", [], "done"),
    ])
    build_gabe(load_config(config_file), provider=provider).send("go")
    results = [x for k, x in provider.log if k == "tool_results"][0]
    assert "paused" in results[0].content
    assert len(provider.systems) == 1  # no specialist conversation was started


def test_specialist_rules_override_company_policy(config_file):
    _edit(config_file, lambda raw: raw["delegates"]["scheduler"].__setitem__("policy", {"create_task": "deny"}))
    sub = build_gabe(load_config(config_file), provider=ScriptedProvider([]), specialist="scheduler")
    assert sub.policy.decide(sub.registry.get("create_task")).value == "deny"
    assert "delegate" not in [t.name for t in sub.registry.tools()]


def test_agents_api_pause_rules_and_request(config_file, monkeypatch):
    def fake_briefer(cfg, **kw):
        return build_gabe(cfg, provider=ScriptedProvider([StepResult(
            '{"name": "invoice_chaser", "description": "Chases invoices.", "instructions": "Be polite.", "tools": ["list_tasks", "nope"]}',
            [], "done")]))
    monkeypatch.setattr(server, "build_briefer", fake_briefer)
    c = TestClient(server.create_app(config_file))
    ids = [a["id"] for a in c.get("/api/agents").json()["agents"]]
    assert ids == ["chief", "briefing", "scheduler"]

    c.post("/api/agents/scheduler/pause", json={"paused": True})
    assert c.post("/api/agents/scheduler/jobs", json={"text": "x"}).status_code == 409
    c.post("/api/agents/scheduler/pause", json={"paused": False})

    c.put("/api/agents/scheduler/rules", json={"rules": {"create_task": "ask"}})
    sched = next(a for a in c.get("/api/agents").json()["agents"] if a["id"] == "scheduler")
    assert sched["decisions"]["create_task"] == "ask" and not sched["paused"]

    c.post("/api/agents/request", json={"role": "chase invoices"})
    end = time.time() + 5
    while (req := c.get("/api/agents").json()["request"])["status"] == "running" and time.time() < end:
        time.sleep(0.05)
    assert req["result"]["tools"] == ["list_tasks"]  # unknown actions dropped
    assert c.post("/api/agents", json=req["result"]).status_code == 200
    assert "invoice_chaser" in [a["id"] for a in c.get("/api/agents").json()["agents"]]


def test_brief_recipients_list(config_file, monkeypatch):
    c = TestClient(server.create_app(config_file))
    r = c.put("/api/settings/briefing", json={"time": "08:00", "days": ["mon"], "email_to": ["me", "a@x.co", "b@x.co"]})
    assert r.json()["briefing"]["email_to"] == ["me", "a@x.co", "b@x.co"]
    assert c.put("/api/settings/briefing", json={"time": "08:00", "days": ["mon"], "email_to": ["nope"]}).status_code == 400
