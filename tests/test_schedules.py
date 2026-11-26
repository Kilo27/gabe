from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import gabe.web.state as web_state
from gabe import schedules
from gabe.config import load_config
from gabe.providers.base import StepResult, ToolCall
from gabe.web.server import create_app
from tests.conftest import ScriptedProvider
from tests.test_web import _wait

UTC = ZoneInfo("UTC")


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_every_half_hour():
    s = {"every_minutes": 30, "days": schedules.DAYS}
    assert schedules.next_after(s, at("2026-11-25T10:07:12")) == at("2026-11-25T10:37:00")


def test_interval_respects_hours_and_days():
    s = schedules.normalise(30, None, ["mon", "tue", "wed", "thu", "fri"], "08:00-18:00")
    assert schedules.next_after(s, at("2026-11-25T17:45:00")) == at("2026-11-26T08:00:00")  # Wed -> Thu
    assert schedules.next_after(s, at("2026-11-27T17:45:00")) == at("2026-11-30T08:00:00")  # Fri -> Mon
    assert schedules.next_after(s, at("2026-11-25T06:00:00")) == at("2026-11-25T08:00:00")
    assert schedules.describe(s) == "every 30 minutes, 08:00-18:00, weekdays"


def test_daily_at():
    s = schedules.normalise(None, "9:00", ["mon"], None)
    assert schedules.next_after(s, at("2026-11-25T10:00:00")) == at("2026-11-30T09:00:00")


@pytest.mark.parametrize("args", [
    (None, None, None, None), (30, "09:00", None, None), (2, None, None, None),
    (30, None, ["someday"], None), (30, None, None, "18:00-08:00"), (None, "25:00", None, None),
])
def test_bad_timetables_are_refused(args):
    with pytest.raises(ValueError):
        schedules.normalise(*args)


def test_due_jobs_are_claimed_once(config_file):
    cfg = load_config(config_file)
    s = schedules.create(cfg, "Email check", "Check my email.", every_minutes=30)
    first = datetime.fromisoformat(s["next_run"])
    assert schedules.claim_due(cfg, first - timedelta(minutes=1)) == []
    # Missed several runs (app closed): it catches up once, then moves on.
    late = first + timedelta(hours=3)
    assert [x["id"] for x in schedules.claim_due(cfg, late)] == [s["id"]]
    assert schedules.claim_due(cfg, late) == []
    schedules.update(cfg, s["id"], paused=True)
    assert schedules.claim_due(cfg, late + timedelta(days=1)) == []


def _client(config_file, monkeypatch, script):
    provider = ScriptedProvider(script)
    orig = web_state.build_gabe
    monkeypatch.setattr(web_state, "build_gabe", lambda cfg, **kw: orig(cfg, provider=provider, **kw))
    return TestClient(create_app(config_file, run_schedules=False)), provider


def test_gabe_schedules_a_job_from_chat_and_it_runs(config_file, monkeypatch):
    c, provider = _client(config_file, monkeypatch, [
        StepResult("", [ToolCall("1", "schedule_job", {
            "name": "Email check", "instruction": "Check my inbox for anything urgent.", "every_minutes": 30})], "tool_use"),
        StepResult("Done: I'll check every 30 minutes.", [], "done"),
        # first scheduled run: nothing new
        StepResult("NOTHING TO REPORT", [], "done"),
        # second run: something urgent
        StepResult("Urgent: the board wants the deck today.", [], "done"),
    ])
    sid = c.post("/api/sessions", json={"text": "check my email every half hour"}).json()["id"]
    _wait(lambda: c.get(f"/api/sessions/{sid}").json()["messages"][-1]["status"] == "done")
    [job] = c.get("/api/schedules").json()
    assert job["when"] == "every 30 minutes, every day" and job["name"] == "Email check"

    state = c.app.state.gabe
    due = datetime.fromisoformat(job["next_run"])
    assert state.run_due_schedules(due) == [job["id"]]
    job = c.get("/api/schedules").json()[0]
    run_sid = job["session_id"]
    _wait(lambda: c.get(f"/api/sessions/{run_sid}").json()["messages"][-1]["status"] == "done")
    sent = [x for k, x in provider.log if k == "user"][-1]
    assert sent.startswith("Check my inbox for anything urgent.") and "NOTHING TO REPORT" in sent
    notices = lambda: [n["title"] for n in state.notices.load()["notices"]]
    assert "Email check" not in notices()  # quiet run

    c.post(f"/api/schedules/{job['id']}/run")
    _wait(lambda: c.get(f"/api/sessions/{run_sid}").json()["messages"][-1]["text"].startswith("Urgent"))
    _wait(lambda: "Email check" in notices())
    assert "previous run was at" in [x for k, x in provider.log if k == "user"][-1]
    assert c.get("/api/schedules").json()[0]["runs"] == 2


def test_schedule_api_pause_and_delete(config_file, monkeypatch):
    c, _ = _client(config_file, monkeypatch, [])
    s = schedules.create(load_config(config_file), "Chase", "Chase overdue tasks.", at="09:00", days=["mon"])
    assert c.patch(f"/api/schedules/{s['id']}", json={"paused": True}).json()["paused"] is True
    assert c.patch(f"/api/schedules/{s['id']}", json={"every_minutes": 1}).status_code == 400
    assert c.delete(f"/api/schedules/{s['id']}").json() == {"ok": True}
    assert c.get("/api/schedules").json() == []
