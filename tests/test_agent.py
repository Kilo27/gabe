from gabe.app import build_gabe
from gabe.config import load_config
from gabe.providers.base import StepResult, ToolCall
from tests.conftest import ScriptedProvider


def tool_step(*calls):
    return StepResult(text="", tool_calls=[ToolCall(f"id{i}", n, a) for i, (n, a) in enumerate(calls)], stop="tool_use")


def done(text):
    return StepResult(text=text, tool_calls=[], stop="done")


def results(provider):
    return [entry[1] for entry in provider.log if entry[0] == "tool_results"]


def test_system_prompt_includes_company_playbooks_and_roster(config_file):
    p = ScriptedProvider([done("hi")])
    gabe = build_gabe(load_config(config_file), provider=p)
    gabe.send("hello")
    system, tools = p.systems[0]
    assert "TestCo" in system and "Triage inbox daily." in system and "scheduler" in system
    assert tools == sorted(tools)
    assert "dispatcher" in system


def test_gabe_only_keeps_lookup_tools_and_delegates_the_rest(config_file):
    p = ScriptedProvider([done("hi")])
    gabe = build_gabe(load_config(config_file), provider=p)
    gabe.send("hello")
    assert p.systems[0][1] == ["delegate", "get_current_time", "recall"]


def test_own_tools_is_configurable(config_file):
    cfg = load_config(config_file)
    cfg.own_tools = ["list_tasks", "not_loaded"]
    p = ScriptedProvider([done("hi")])
    build_gabe(cfg, provider=p).send("hello")
    assert p.systems[0][1] == ["delegate", "list_tasks"]


def test_without_delegates_gabe_keeps_every_tool(config_file):
    cfg = load_config(config_file)
    cfg.delegates = []
    p = ScriptedProvider([done("hi")])
    build_gabe(cfg, provider=p).send("hello")
    system, tools = p.systems[0]
    assert "send_email" in tools and "delegate" not in tools and "dispatcher" not in system


def test_specialist_without_tool_list_gets_everything_but_delegation(config_file):
    cfg = load_config(config_file)
    cfg.delegates[0].tools = None
    p = ScriptedProvider([
        tool_step(("delegate", {"specialist": "scheduler", "task": "x"})),
        done("done"),
        done("ok"),
    ])
    build_gabe(cfg, provider=p).send("go")
    sub_tools = p.systems[1][1]
    assert "send_email" in sub_tools and "delegate" not in sub_tools


def hands_on(config_file):
    cfg = load_config(config_file)
    cfg.delegates = []
    return cfg


def test_tool_loop_creates_and_lists_tasks(config_file):
    p = ScriptedProvider([
        tool_step(("create_task", {"title": "Ship v1", "owner": "Sam", "due": "2026-12-01"})),
        tool_step(("list_tasks", {"owner": "sam"})),
        done("Done."),
    ])
    gabe = build_gabe(hands_on(config_file), provider=p)
    assert gabe.send("track it") == "Done."
    listed = results(p)[1][0]
    assert not listed.is_error and "Ship v1" in listed.content


def test_policy_deny_ask_and_invalid_input(config_file):
    asked = []
    p = ScriptedProvider([
        tool_step(
            ("forget", {"id": "x"}),                                   # denied by tools override
            ("send_email", {"to": ["a@b.c"], "subject": "s", "body": "b"}),  # external -> ask
            ("create_task", {"title": "no owner"}),                    # missing required field
            ("nope", {}),                                              # unknown tool
        ),
        done("ok"),
    ])
    gabe = build_gabe(
        hands_on(config_file),
        provider=p,
        approver=lambda tool, args: asked.append(tool.name) or False,
    )
    gabe.send("go")
    r = results(p)[0]
    assert all(x.is_error for x in r)
    assert "policy" in r[0].content
    assert "did not approve" in r[1].content and asked == ["send_email"]
    assert "owner" in r[2].content
    assert "Unknown tool" in r[3].content


def test_approved_email_goes_to_outbox(config_file):
    p = ScriptedProvider([
        tool_step(("send_email", {"to": ["a@b.c"], "subject": "Hi", "body": "Body"})),
        done("sent"),
    ])
    cfg = hands_on(config_file)
    gabe = build_gabe(cfg, provider=p, approver=lambda t, a: True)
    gabe.send("email them")
    assert not results(p)[0][0].is_error
    assert len(list((cfg.data_dir / "email" / "outbox").glob("*.json"))) == 1
    assert (cfg.data_dir / "audit.jsonl").read_text().count("send_email") == 1


def test_delegate_runs_specialist_with_restricted_tools(config_file):
    p = ScriptedProvider([
        tool_step(("delegate", {"specialist": "scheduler", "task": "list Sam's tasks"})),
        # specialist's turn:
        tool_step(("list_tasks", {"owner": "Sam"})),
        done("Sam has no tasks."),
        # back to Gabe:
        done("Sam is clear."),
    ])
    gabe = build_gabe(load_config(config_file), provider=p)
    assert gabe.send("what is Sam doing?") == "Sam is clear."
    assert p.systems[1][1] == ["create_task", "list_tasks"]
    assert "Sam has no tasks." in results(p)[1][0].content


def test_truncated_turn_does_not_run_tools(config_file):
    p = ScriptedProvider([
        StepResult(text="partial", tool_calls=[ToolCall("1", "create_task", {"title": "x"})], stop="max_tokens"),
    ])
    gabe = build_gabe(load_config(config_file), provider=p)
    assert "truncated" in gabe.send("go")
    assert results(p) == []
