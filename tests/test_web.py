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
