import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from gabe.app import build_gabe
from gabe.config import load_config
from gabe.providers.base import StepResult, ToolCall
from gabe.team import Member, Team, TeamError, load_team, save_team
from gabe.ui.server import Workspace, make_server
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


def test_workspace_run_pauses_for_approval_and_resumes(team_config):
    p = ScriptedProvider([
        delegate("careful"),
        tool_step(("create_task", {"title": "Book room", "owner": "Sam"})),
        done("Booked."),
        done("Careful booked it."),
    ])
    ws = Workspace(team_config, provider=p)
    ws.ask("book a room")
    wait_for(lambda: ws.run_view()["status"] == "waiting")
    view = ws.run_view()
    approval = view["approvals"][0]
    assert approval["path"] == ["Gabe", "Careful"]
    assert approval["summary"] == 'Careful wants to create a task "Book room" for Sam'
    assert any(e["kind"] == "delegate" and e["depth"] == 0 for e in view["events"])
    ws.decide(approval["id"], True)
    wait_for(lambda: ws.run_view()["status"] == "done")
    view = ws.run_view()
    assert view["answer"] == "Careful booked it."
    assert any(e["text"].startswith("Careful created a task") for e in view["events"])
    history = ws.history_view()
    # Newest first; the hand-off is logged when it finishes, after Careful's own action.
    assert [h["who"] for h in history] == ["Gabe", "Gabe › Careful"]
    assert history[0]["text"] == "handed work to Careful"


@pytest.fixture
def server(team_config):
    srv = make_server(Workspace(team_config, provider=ScriptedProvider([])), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, team_config
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, headers=None):
    port = srv.server_address[1]
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}") if "json" in r.headers["Content-Type"] else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_api_edits_the_team_file(server):
    srv, cfg = server
    status, page = call(srv, "GET", "/")
    assert status == 200 and b"Gabe's team" in page
    status, team = call(srv, "GET", "/api/team")
    assert {m["id"] for m in team["members"]} == {"ops", "scheduler", "watcher", "careful", "wild"}
    assert any(s["name"] == "send_email" and s["company_rule"] == "ask" for s in team["skills"])

    status, out = call(srv, "PUT", "/api/team/members/new",
                       {"name": "Hiring helper", "job": "Hiring", "reports_to": "ops", "skills": ["list_tasks"]})
    assert status == 200 and out["member"]["id"] == "hiring_helper"
    assert cfg.team().get("hiring_helper").reports_to == "ops"

    status, out = call(srv, "PUT", "/api/team/members/ops", {"name": "Ops", "reports_to": "scheduler"})
    assert status == 400 and "managing themselves" in out["error"]

    status, _ = call(srv, "DELETE", "/api/team/members/ops", {})
    assert status == 200
    assert cfg.team().get("scheduler").reports_to == "chief"


def test_api_refuses_other_websites(server):
    srv, cfg = server
    status, out = call(srv, "PUT", "/api/team/members/new", {"name": "Evil"}, {"Origin": "https://evil.example"})
    assert status == 403
    status, out = call(srv, "GET", "/api/team", headers={"Host": "evil.example"})
    assert status == 403
    assert cfg.team().get("evil") is None


def test_skill_from_an_unconnected_integration_is_skipped_not_fatal(team_config):
    team = team_config.team()
    team.get("ops").skills = ["list_tasks", "search_email"]  # outbox mode has no search_email
    save_team(team_config.team_file, team)
    p = ScriptedProvider([delegate("ops"), done("ok"), done("ok")])
    build_gabe(team_config, provider=p).send("go")
    assert p.systems[1][1] == ["delegate", "list_tasks"]
    view = Workspace(team_config, provider=p).team_view()
    ops = next(m for m in view["members"] if m["id"] == "ops")
    assert ops["missing_skills"] == [{"name": "search_email", "label": "Search email"}]
