import json
import time

import pytest

from gabe.app import build_gabe
from gabe.config import load_config
from gabe.providers.base import StepResult, ToolCall
from gabe.team import Member, Team, TeamError, load_team, save_team
from tests.conftest import ScriptedProvider
from tests.test_agent import done, results, tool_step

TEAM = """
members:
  - {id: ops, name: Operations lead, job: Runs ops, reports_to: chief, skills: [list_tasks]}
  - {id: scheduler, name: Scheduler, job: Owns tasks, reports_to: ops, skills: [list_tasks, create_task]}
  - {id: watcher, name: Watcher, job: Looks only, reports_to: chief, skills: [create_task], freedom: look_only}
  - {id: careful, name: Careful, job: Asks first, reports_to: chief, skills: [create_task], freedom: ask_first}
  - {id: wild, name: Wild, job: Trusted, reports_to: chief, skills: [forget, send_email], freedom: trusted}
"""


@pytest.fixture
def team_config(tmp_path):
    (tmp_path / "team.yaml").write_text(TEAM)
    cfg = tmp_path / "gabe.yaml"
    cfg.write_text(
        """
company: {name: TestCo, timezone: UTC}
data_dir: data
integrations: [core, memory, tasks, {name: email, settings: {backend: outbox, from_address: g@test.co}}]
policy: {tools: {forget: deny}}
team_file: team.yaml
"""
    )
    return load_config(cfg)


def delegate(to, task="do it"):
    return tool_step(("delegate", {"specialist": to, "task": task}))


def test_two_level_handoff_carries_the_chain(team_config):
    events = []
    p = ScriptedProvider([
        delegate("ops"),
        delegate("scheduler"),                                   # ops hands down a level
        tool_step(("create_task", {"title": "Chase", "owner": "Sam"})),
        done("Created."),                                        # scheduler
        done("Scheduler created it."),                           # ops
        done("All done."),                                       # Gabe
    ])
    gabe = build_gabe(team_config, provider=p, on_event=lambda k, d: events.append((k, d)))
    assert gabe.send("chase Sam") == "All done."
    # Gabe sees only direct reports; ops sees only the scheduler.
    tools_by_agent = [t for _, t in p.systems]
    assert sorted(gabe.registry.get("delegate").input_schema["properties"]["specialist"]["enum"]) == [
        "careful", "ops", "watcher", "wild"]
    assert tools_by_agent[1] == ["delegate", "list_tasks"]
    assert tools_by_agent[2] == ["create_task", "list_tasks"]
    assert "You report to Operations lead" in p.systems[2][0]
    tool_ev = next(d for k, d in events if k == "tool" and d["tool"] == "create_task")
    assert tool_ev["path"] == ["Gabe", "Operations lead", "Scheduler"]
    audit = [json.loads(line) for line in (team_config.data_dir / "audit.jsonl").read_text().splitlines()]
    assert {"Gabe", "Gabe › Operations lead", "Gabe › Operations lead › Scheduler"} == {e["chain"] for e in audit}


def test_paused_manager_takes_their_whole_branch_offline(team_config):
    team = team_config.team()
    team.get("ops").active = False
    save_team(team_config.team_file, team)
    p = ScriptedProvider([done("hi")])
    gabe = build_gabe(team_config, provider=p)
    enum = gabe.registry.get("delegate").input_schema["properties"]["specialist"]["enum"]
    assert "ops" not in enum and "scheduler" not in enum
    assert not team.is_working("scheduler")


@pytest.mark.parametrize("member,expect_asked,expect", [
    ("watcher", False, "policy"),        # look_only: changes are blocked
    ("careful", True, "did not approve"),  # ask_first: changes need a person
])
def test_freedom_level_tightens_policy(team_config, member, expect_asked, expect):
    asked = []
    p = ScriptedProvider([
        delegate(member),
        tool_step(("create_task", {"title": "x", "owner": "y"})),
        done("tried"),
        done("ok"),
    ])
    gabe = build_gabe(team_config, provider=p, approver=lambda t, a: asked.append(t.name) or False)
    gabe.send("go")
    r = results(p)[0][0]
    assert r.is_error and expect in r.content
    assert bool(asked) is expect_asked


def test_trusted_never_loosens_company_rules(team_config):
    asked = []
    p = ScriptedProvider([
        delegate("wild"),
        tool_step(("forget", {"id": "n1"}), ("send_email", {"to": ["a@b.c"], "subject": "s", "body": "b"})),
        done("tried"),
        done("ok"),
    ])
    gabe = build_gabe(team_config, provider=p, approver=lambda t, a: asked.append(t.name) or False)
    gabe.send("go")
    forget, send = results(p)[0]
    assert "policy" in forget.content         # company says deny
    assert asked == ["send_email"]             # company says ask for external


def test_validation_catches_broken_teams():
    with pytest.raises(TeamError, match="managing themselves"):
        Team([Member("a", "A", reports_to="b"), Member("b", "B", reports_to="a")]).validate()
    with pytest.raises(TeamError, match="levels below"):
        Team([Member(f"m{i}", f"M{i}", reports_to=f"m{i-1}" if i else "chief") for i in range(5)]).validate()
    with pytest.raises(TeamError, match="don't exist"):
        Team([Member("a", "A", skills=["nope"])]).validate({"list_tasks"})
    with pytest.raises(TeamError, match="reserved"):
        Team([Member("chief", "X")]).validate()
    with pytest.raises(TeamError, match="isn't on the team"):
        Team([Member("a", "A", reports_to="ghost")]).validate()


def test_remove_moves_reports_up_and_round_trips(tmp_path):
    team = Team([Member("a", "A"), Member("b", "B", reports_to="a"), Member("c", "C", reports_to="b", skills=[])])
    team.remove("b")
    assert team.get("c").reports_to == "a"
    path = tmp_path / "team.yaml"
    save_team(path, team)
    again = load_team(path)
    assert again.members == team.members and again.get("a").skills is None and again.get("c").skills == []


def test_config_rejects_both_team_file_and_delegates(tmp_path):
    cfg = tmp_path / "gabe.yaml"
    cfg.write_text("team_file: team.yaml\ndelegates: {x: {description: d}}\n")
    with pytest.raises(ValueError, match="not both"):
        load_config(cfg)


# ---- the team page ------------------------------------------------------------

def wait_for(pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    raise AssertionError("timed out")


def _client(team_config, script):
    import gabe.web.state as web_state
    from fastapi.testclient import TestClient

    from gabe.web.server import create_app

    provider = ScriptedProvider(script)
    orig = web_state.build_gabe
    web_state.build_gabe = lambda cfg, **kw: orig(cfg, provider=provider, **kw)
    try:
        return TestClient(create_app(team_config.path)), lambda: setattr(web_state, "build_gabe", orig)
    except Exception:
        web_state.build_gabe = orig
        raise


def test_app_run_pauses_for_a_members_approval_and_resumes(team_config):
    c, restore = _client(team_config, [
        delegate("careful"),
        tool_step(("create_task", {"title": "Book room", "owner": "Sam"})),
        done("Booked."),
        done("Careful booked it."),
    ])
    try:
        sid = c.post("/api/agents/chief/jobs", json={"text": "book a room"}).json()["session_id"]
        wait_for(lambda: any(a["status"] == "pending" for a in c.get("/api/approvals").json()))
        approval = next(a for a in c.get("/api/approvals").json() if a["status"] == "pending")
        assert approval["agent"] == "Careful" and approval["tool"] == "create_task"
        careful = next(a for a in c.get("/api/agents").json()["agents"] if a["id"] == "careful")
        assert careful["status"] == "waiting" and careful["waiting"] == [approval["id"]]
        c.post(f"/api/approvals/{approval['id']}", json={"decision": "approve"})
        wait_for(lambda: c.get(f"/api/sessions/{sid}").json()["messages"][-1]["status"] == "done")
        assert c.get(f"/api/sessions/{sid}").json()["messages"][-1]["text"] == "Careful booked it."
    finally:
        restore()


def test_app_edits_the_team_file(team_config):
    c, restore = _client(team_config, [])
    try:
        ids = {a["id"] for a in c.get("/api/agents").json()["agents"]}
        assert {"chief", "briefing", "ops", "scheduler", "watcher", "careful", "wild"} <= ids

        r = c.post("/api/agents", json={"name": "Hiring helper", "description": "Hiring", "reports_to": "ops", "tools": ["list_tasks"]})
        assert r.status_code == 200 and r.json()["id"] == "hiring_helper"
        assert team_config.team().get("hiring_helper").reports_to == "ops"

        r = c.put("/api/agents/ops", json={"reports_to": "scheduler"})
        assert r.status_code == 400 and "managing themselves" in r.json()["detail"]

        assert c.put("/api/agents/careful", json={"freedom": "look_only"}).status_code == 200
        assert team_config.team().get("careful").freedom == "look_only"

        assert c.post("/api/agents/ops/pause", json={"paused": True}).status_code == 200
        sched = next(a for a in c.get("/api/agents").json()["agents"] if a["id"] == "scheduler")
        assert sched["status"] == "paused"  # its manager is paused
        assert c.post("/api/agents/scheduler/jobs", json={"text": "x"}).status_code == 409
        c.post("/api/agents/ops/pause", json={"paused": False})

        assert c.delete("/api/agents/ops").status_code == 200
        assert team_config.team().get("scheduler").reports_to == "chief"
    finally:
        restore()


def test_skill_from_an_unconnected_integration_is_skipped_not_fatal(team_config):
    team = team_config.team()
    team.get("ops").skills = ["list_tasks", "search_email"]  # outbox mode has no search_email
    save_team(team_config.team_file, team)
    p = ScriptedProvider([delegate("ops"), done("ok"), done("ok")])
    build_gabe(team_config, provider=p).send("go")
    assert p.systems[1][1] == ["delegate", "list_tasks"]
