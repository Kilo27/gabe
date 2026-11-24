import copy
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
    assert sorted(m.id for m in team_config.team().reports("chief")) == ["careful", "ops", "watcher", "wild"]
    assert tools_by_agent[1] == ["ask_user", "delegate", "list_tasks"]
    assert tools_by_agent[2] == ["ask_user", "create_task", "list_tasks"]
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
    refused = gabe.registry.get("delegate").handler(specialist="ops", task="x")
    assert "isn't one of your active direct reports" in refused
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
    assert p.systems[1][1] == ["ask_user", "delegate", "list_tasks"]


# ---- bots adding bots -------------------------------------------------------------

def hire(**kw):
    args = {"name": "Invoice chaser", "job": "Chases unpaid invoices.", "instructions": "Be polite.",
            "skills": ["list_tasks"], "reports_to": "chief", "why": "Nobody does finance.",
            "first_task": "Chase Acme."}
    args.update(kw)
    return tool_step(("add_team_member", args))


def test_new_bot_starts_its_first_task_straight_away(team_config):
    asked, events = [], []
    p = ScriptedProvider([
        hire(),
        done("Chased Acme: they pay Friday."),   # the new bot, on its first task
        done("Added and chased."),               # Gabe
    ])
    gabe = build_gabe(team_config, provider=p, approver=lambda t, a: asked.append(t.name) or True,
                      on_event=lambda k, d: events.append((k, d)))
    assert gabe.send("chase invoices") == "Added and chased."
    assert asked == ["add_team_member"]  # a person approves every new bot
    new = load_team(team_config.team_file).get("invoice_chaser")
    assert new.reports_to == "chief" and new.skills == ["list_tasks"] and new.freedom == "standard"
    out = results(p)[0][0].content
    assert out.startswith("Added Invoice chaser") and "they pay Friday" in out
    assert p.log[1] == ("user", "Chase Acme.")  # its first task, handed to it
    hand = next(d for k, d in events if k == "delegate")
    assert hand["to"] == "invoice_chaser" and hand["path"] == ["Gabe"]


def test_new_bot_can_be_given_more_work_in_the_same_run(team_config):
    p = ScriptedProvider([
        hire(),
        done("first done"),
        tool_step(("delegate", {"specialist": "invoice_chaser", "task": "Chase Beta"})),
        done("Chased."),
        done("ok"),
    ])
    build_gabe(team_config, provider=p, approver=lambda t, a: True).send("x")
    assert not results(p)[1][0].is_error and results(p)[1][0].content == "Chased."


def test_gabe_adds_a_bot_under_another_bot(team_config):
    events = []
    p = ScriptedProvider([hire(reports_to="ops"), done("first done"), done("ok")])
    build_gabe(team_config, provider=p, approver=lambda t, a: True,
               on_event=lambda k, d: events.append((k, d))).send("x")
    assert load_team(team_config.team_file).get("invoice_chaser").reports_to == "ops"
    hand = next(d for k, d in events if k == "delegate")
    assert hand["path"] == ["Gabe", "Operations lead"]  # handed down from its manager


def test_overlapping_bot_must_go_under_the_member_who_owns_that_area(team_config):
    p = ScriptedProvider([
        hire(name="Scheduler helper", job="Owns tasks and deadlines."),                  # beside the scheduler
        hire(name="Scheduler helper", job="Owns tasks and deadlines.", reports_to="scheduler"),
        done("first done"),
        done("ok"),
    ])
    build_gabe(team_config, provider=p, approver=lambda t, a: True).send("x")
    first, second = results(p)[0][0], results(p)[1][0]
    assert first.is_error and "overlaps Scheduler" in first.content and "reports_to: scheduler" in first.content
    assert not second.is_error
    assert load_team(team_config.team_file).get("scheduler_helper").reports_to == "scheduler"


def test_new_bot_needs_a_first_task(team_config):
    p = ScriptedProvider([hire(first_task=" "), done("ok")])
    build_gabe(team_config, provider=p, approver=lambda t, a: True).send("x")
    assert results(p)[0][0].is_error and "first_task" in results(p)[0][0].content
    assert load_team(team_config.team_file).get("invoice_chaser") is None


def test_declined_bot_is_not_added(team_config):
    p = ScriptedProvider([hire(), done("ok")])
    build_gabe(team_config, provider=p, approver=lambda t, a: False).send("x")
    assert load_team(team_config.team_file).get("invoice_chaser") is None
    assert results(p)[0][0].is_error


def test_a_bot_can_only_add_bots_in_its_branch_with_what_it_has(team_config):
    team = team_config.team()
    team.get("ops").skills = ["list_tasks", "add_team_member"]
    save_team(team_config.team_file, team)
    p = ScriptedProvider([
        hire(reports_to="careful"),                      # not in its branch
        hire(reports_to="ops", skills=["send_email"]),   # an action it doesn't have
        hire(reports_to="ops", freedom="trusted"),       # more freedom than allowed
        hire(name="Late tasks", reports_to="scheduler"), # fine: under its own report
        done("first done"),
        done("ok"),
    ])
    build_gabe(team_config, provider=p, approver=lambda t, a: True, member="ops").send("grow")
    out = [r[0].content for r in results(p)]
    assert all(r[0].is_error for r in results(p)[:3])
    assert "only add team members under yourself" in out[0]
    assert "don't have yourself: send_email" in out[1]
    assert "must be one of" in out[2] or "Invalid input" in out[2]
    assert out[3].startswith("Added Late tasks")
    assert load_team(team_config.team_file).get("late_tasks").reports_to == "scheduler"


def test_look_only_bots_cannot_add_bots(team_config):
    team = team_config.team()
    team.get("watcher").skills = ["list_tasks", "add_team_member"]
    save_team(team_config.team_file, team)
    p = ScriptedProvider([hire(reports_to="watcher"), done("ok")])
    build_gabe(team_config, provider=p, approver=lambda t, a: True, member="watcher").send("x")
    assert results(p)[0][0].is_error and "policy" in results(p)[0][0].content
    assert load_team(team_config.team_file).get("invoice_chaser") is None


def test_hiring_moves_an_old_delegates_config_to_a_team_file(config_file):
    p = ScriptedProvider([hire(), done("first done"), done("ok")])
    build_gabe(load_config(config_file), provider=p, approver=lambda t, a: True).send("x")
    cfg = load_config(config_file)
    assert cfg.team_file is not None and not cfg.delegates
    assert {m.id for m in cfg.team().members} == {"scheduler", "invoice_chaser"}


def test_adding_a_bot_works_when_others_list_unconnected_actions(team_config):
    team = team_config.team()
    team.get("wild").skills = ["forget", "search_email"]  # outbox mode has no search_email
    save_team(team_config.team_file, team)
    p = ScriptedProvider([hire(), done("first done"), done("ok")])
    build_gabe(team_config, provider=p, approver=lambda t, a: True).send("x")
    assert load_team(team_config.team_file).get("invoice_chaser") is not None


# ---- models per bot -----------------------------------------------------------------

class TieredProvider(ScriptedProvider):
    """Scripted provider that records which model each conversation ran on."""

    def __init__(self, script):
        super().__init__(script)
        self.model = "big"
        self.tiers = {"haiku": "small", "sonnet": "mid", "opus": "big"}
        self.ran_on = []

    def with_model(self, model):
        other = copy.copy(self)
        other.model = model
        return other

    def start(self, system, tools):
        self.ran_on.append(self.model)
        return super().start(system, tools)


def test_each_bot_runs_on_its_own_model(team_config):
    p = TieredProvider([delegate("ops"), done("ok"), done("ok")])
    ops = team_config.team().get("ops")
    team = Team([Member(**{**ops.to_dict(), "model": "sonnet"})])
    build_gabe(team_config, provider=p, team=team).send("go")
    assert p.ran_on == ["big", "mid"]


def test_gabe_picks_the_new_bots_model(team_config):
    p = TieredProvider([hire(model="sonnet"), done("chased"), done("ok")])
    team = Team(list(team_config.team().members))
    build_gabe(team_config, provider=p, team=team, approver=lambda t, a: True).send("go")
    assert team.get("invoice_chaser").model == "sonnet"
    assert p.ran_on == ["big", "mid"]  # Gabe, then the new bot on its first task


def test_unknown_model_is_refused(team_config):
    p = ScriptedProvider([hire(model="gpt"), done("ok")])
    team = Team(list(team_config.team().members))
    build_gabe(team_config, provider=p, team=team, approver=lambda t, a: True).send("go")
    assert results(p)[0][0].is_error and team.get("invoice_chaser") is None


def test_a_bot_cannot_hire_a_stronger_model_than_its_own(team_config):
    ops = team_config.team().get("ops")
    boss = Member(**{**ops.to_dict(), "model": "sonnet", "skills": ["list_tasks", "add_team_member"],
                     "freedom": "standard"})
    team = Team([boss])
    p = ScriptedProvider([hire(model="opus", reports_to="ops"), done("ok")])
    build_gabe(team_config, provider=p, team=team, member="ops").send("go")
    out = results(p)[0][0]
    assert out.is_error and "opus" not in out.content and "sonnet" in out.content


def test_model_round_trips_through_the_team_file(tmp_path):
    path = tmp_path / "team.yaml"
    save_team(path, Team([Member(id="a", name="A", model="haiku"), Member(id="b", name="B")]))
    assert "model: haiku" in path.read_text() and path.read_text().count("model:") == 2  # header + a
    loaded = load_team(path)
    assert loaded.get("a").model == "haiku" and loaded.get("b").model is None
    with pytest.raises(TeamError):
        Team([Member(id="c", name="C", model="gpt")]).validate()


def test_anthropic_tiers_and_haiku_skips_server_fallback():
    from gabe.providers import make_provider

    p = make_provider({"kind": "anthropic", "client": object(), "tiers": {"sonnet": "claude-sonnet-5"}})
    assert p.for_tier("haiku").model == "claude-haiku-5-5"
    assert p.for_tier("sonnet").model == "claude-sonnet-5"
    assert p.for_tier(None) is p
    assert "fallbacks" not in p.for_tier("haiku").start("s", [])._request()
    assert "fallbacks" in p.start("s", [])._request()


def test_gabe_picks_a_model_per_job_for_default_bots(team_config):
    ops = team_config.team().get("ops")
    team = Team([Member(**{**ops.to_dict(), "model": None})])
    p = TieredProvider([
        tool_step(("delegate", {"specialist": "ops", "task": "hard one", "model": "opus"})), done("ok"),
        tool_step(("delegate", {"specialist": "ops", "task": "easy one"})), done("ok"),
        done("all done"),
    ])
    build_gabe(team_config, provider=p, team=team).send("go")
    assert p.ran_on == ["big", "big", "small"]  # Gabe, opus as asked, then haiku by default


def test_a_fixed_model_wins_over_the_managers_pick(team_config):
    ops = team_config.team().get("ops")
    team = Team([Member(**{**ops.to_dict(), "model": "haiku"})])
    p = TieredProvider([tool_step(("delegate", {"specialist": "ops", "task": "x", "model": "opus"})),
                        done("ok"), done("ok")])
    build_gabe(team_config, provider=p, team=team).send("go")
    assert p.ran_on == ["big", "small"]


def test_new_bot_without_a_model_is_picked_per_job(team_config):
    p = TieredProvider([hire(), done("chased"), done("ok")])
    team = Team(list(team_config.team().members))
    build_gabe(team_config, provider=p, team=team, approver=lambda t, a: True).send("go")
    assert team.get("invoice_chaser").model is None
    assert p.ran_on == ["big", "small"]


# ---- failures are never silent -------------------------------------------------------

def test_failed_hand_off_is_announced_and_goes_back_to_the_manager(team_config):
    events = []
    p = ScriptedProvider([delegate("ops"), RuntimeError("model fell over"), done("I'll retry later.")])
    out = build_gabe(team_config, provider=p, on_event=lambda k, d: events.append((k, d))).send("go")
    assert out == "I'll retry later."
    failed = next(d for k, d in events if k == "failed")
    assert failed["agent"] == "Operations lead" and failed["by"] == "Gabe" and "fell over" in failed["error"]
    assert results(p)[0][0].is_error and "fell over" in results(p)[0][0].content


def test_every_agent_is_told_how_to_report_a_failed_job(team_config):
    p = ScriptedProvider([tool_step(("delegate", {"specialist": "ops", "task": "x"})), done("ok"), done("ok")])
    build_gabe(team_config, provider=p).send("go")
    assert all("JOB FAILED:" in system for system, _ in p.systems)
