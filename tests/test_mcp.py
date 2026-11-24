"""MCP servers plugged in as integrations, against the stdio fixture in mcp_fixture_server.py."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from gabe.app import base_registry, build_gabe  # noqa: E402
from gabe.config import load_config  # noqa: E402
from gabe.integrations import mcp as m  # noqa: E402
from gabe.policy import Decision, Policy  # noqa: E402
from gabe.tools.registry import Risk, validate_input  # noqa: E402
from tests.conftest import ScriptedProvider  # noqa: E402
from tests.test_agent import done, results, tool_step  # noqa: E402

FIXTURE = str(Path(__file__).with_name("mcp_fixture_server.py"))


@pytest.fixture(autouse=True)
def fresh_manager():
    """Each test starts with no servers open and closes what it opened."""
    yield
    if m._manager is not None:
        for srv in list(m._manager.servers.values()):
            m._manager._close(srv)
        m._manager.servers.clear()


def make_config(tmp_path, servers: str, extra: str = "", call_timeout: float = 10):
    cfg = tmp_path / "gabe.yaml"
    cfg.write_text(f"""
company: {{name: TestCo, timezone: UTC}}
data_dir: data
integrations:
  - core
  - tasks
  - name: mcp
    settings:
      call_timeout: {call_timeout}
      servers:
{servers}
{extra}
""")
    return load_config(cfg)


def fx(name="fx", **opts) -> str:
    lines = [f"        {name}:", f"          command: {sys.executable}", f"          args: [{FIXTURE}]"]
    lines += [f"          {k}: {v}" for k, v in opts.items()]
    return "\n".join(lines)


def test_tools_register_with_prefixed_names_and_external_risk(tmp_path):
    reg = base_registry(make_config(tmp_path, fx()))
    names = [t.name for t in reg.tools() if t.integration == "fx"]
    assert names == ["fx__broken", "fx__die", "fx__lookup", "fx__pid", "fx__save_note", "fx__slow"]
    lookup = reg.get("fx__lookup")
    assert lookup.risk is Risk.EXTERNAL  # annotations aren't trusted by default
    assert lookup.description == "[fx] Look something up."
    assert lookup.input_schema["type"] == "object" and "query" in lookup.input_schema["properties"]
    assert reg.get("get_current_time") is not None  # the rest of Gabe still loads


def test_risk_overrides_and_trusted_annotations(tmp_path):
    servers = "\n".join([
        fx("a", risk="write"),
        fx("b", risk="write", tools="{save_note: external, slow: read}"),
        fx("c", trust_annotations="true"),
    ])
    reg = base_registry(make_config(tmp_path, servers))
    assert reg.get("a__lookup").risk is Risk.WRITE
    assert reg.get("b__save_note").risk is Risk.EXTERNAL
    assert reg.get("b__slow").risk is Risk.READ
    assert reg.get("b__lookup").risk is Risk.WRITE
    assert reg.get("c__lookup").risk is Risk.READ          # readOnlyHint, trusted
    assert reg.get("c__save_note").risk is Risk.EXTERNAL   # no hint: never lowered


def test_include_and_exclude(tmp_path):
    servers = "\n".join([fx("inc", include="[lookup, pid]"), fx("exc", exclude="[die, slow]")])
    reg = base_registry(make_config(tmp_path, servers))
    assert sorted(t.name for t in reg.tools() if t.integration == "inc") == ["inc__lookup", "inc__pid"]
    assert sorted(t.name for t in reg.tools() if t.integration == "exc") == [
        "exc__broken", "exc__lookup", "exc__pid", "exc__save_note"]
    assert m.status()["inc"] == {"state": "connected", "error": None, "tools": 2, "transport": "stdio"}


def test_call_returns_text_and_nested_input_validates(tmp_path):
    reg = base_registry(make_config(tmp_path, fx()))
    tool = reg.get("fx__lookup")
    args = {"query": "rent", "filters": {"year": 2026}}
    assert validate_input(tool.input_schema, args) is None
    assert tool.run(args) == "found rent [('year', 2026)]"
    assert tool.run({"query": "x"}) == "found x"


def test_is_error_and_timeout_raise(tmp_path):
    reg = base_registry(make_config(tmp_path, fx(), call_timeout=1))
    with pytest.raises(RuntimeError, match="broken"):
        reg.get("fx__broken").run({})
    with pytest.raises(TimeoutError, match="within 1s"):
        reg.get("fx__slow").run({"seconds": 5})
    assert reg.get("fx__lookup").run({"query": "still ok"}) == "found still ok"


def test_bad_server_is_skipped_and_gabe_still_builds(tmp_path):
    servers = "\n".join([
        "        nope:\n          command: /definitely/not/a/real/binary",
        fx(),
    ])
    cfg = make_config(tmp_path, servers)
    reg = base_registry(cfg)
    assert not [t for t in reg.tools() if t.integration == "nope"]
    assert reg.get("fx__lookup") is not None
    st = m.status()
    assert st["nope"]["state"] == "error" and st["nope"]["error"]
    assert st["fx"]["state"] == "connected"
    build_gabe(cfg, provider=ScriptedProvider([]))


def test_registry_rebuild_reuses_the_session(tmp_path):
    cfg = make_config(tmp_path, fx())
    pid = base_registry(cfg).get("fx__pid").run({})
    spawned = m.manager().spawned
    assert base_registry(cfg).get("fx__pid").run({}) == pid
    assert m.manager().spawned == spawned


def test_dead_server_reconnects_and_removed_server_closes(tmp_path):
    cfg = make_config(tmp_path, fx())
    reg = base_registry(cfg)
    pid = reg.get("fx__pid").run({})
    with pytest.raises(RuntimeError, match="Lost the connection"):
        reg.get("fx__die").run({})
    assert base_registry(cfg).get("fx__pid").run({}) != pid
    base_registry(make_config(tmp_path, "        {}"))
    assert "fx" not in m.status()


def test_env_is_expanded_and_config_change_reconnects(tmp_path, monkeypatch):
    notes = tmp_path / "notes.txt"
    monkeypatch.setenv("NOTES_PATH", str(notes))
    cfg = make_config(tmp_path, fx(env="{FIXTURE_NOTES: '${NOTES_PATH}'}"))
    base_registry(cfg).get("fx__save_note").run({"text": "hello"})
    assert notes.read_text() == "hello\n"
    spawned = m.manager().spawned
    monkeypatch.setenv("NOTES_PATH", str(tmp_path / "other.txt"))
    base_registry(cfg).get("fx__save_note").run({"text": "again"})
    assert m.manager().spawned == spawned + 1
    assert (tmp_path / "other.txt").read_text() == "again\n"


def test_policy_by_server_name(tmp_path):
    reg = base_registry(make_config(tmp_path, fx()))
    tool = reg.get("fx__lookup")
    assert Policy.from_config({}).decide(tool) is Decision.ASK  # external by default
    assert Policy.from_config({"integrations": {"fx": "allow"}}).decide(tool) is Decision.ALLOW
    assert Policy.from_config({"integrations": {"fx": "deny"}}).decide(tool) is Decision.DENY


def test_policy_gates_the_call_end_to_end(tmp_path):
    cfg = make_config(tmp_path, fx(), extra="policy: {integrations: {fx: deny}}")
    p = ScriptedProvider([tool_step(("fx__lookup", {"query": "x"})), done("ok")])
    build_gabe(cfg, provider=p).send("look it up")
    assert "Blocked by company policy" in results(p)[0][0].content


def test_mcp_tool_as_a_team_members_skill(tmp_path):
    (tmp_path / "team.yaml").write_text(
        "members:\n  - {id: researcher, name: Researcher, job: Looks things up, reports_to: chief, "
        "skills: [fx__lookup]}\n")
    cfg = make_config(tmp_path, fx(), extra="team_file: team.yaml\npolicy: {integrations: {fx: allow}}")
    from gabe.app import team_skills

    assert "fx__lookup" in team_skills(cfg)
    p = ScriptedProvider([
        tool_step(("delegate", {"specialist": "researcher", "task": "look up rent"})),
        tool_step(("fx__lookup", {"query": "rent"})),
        done("Found it."),
        done("Researcher found it."),
    ])
    asked = []
    gabe = build_gabe(cfg, provider=p, approver=lambda t, a: asked.append(t.name) or True)
    assert gabe.send("find rent") == "Researcher found it."
    assert asked == ["fx__lookup"]  # external, so the member's freedom level still asks first
    assert p.systems[1][1] == ["ask_user", "fx__lookup"]
    assert results(p)[0][0].content == "found rent"
    assert "fx__lookup" not in p.systems[0][1]  # Gabe dispatches; the member does it


def test_names_are_safe_and_stable():
    assert m.tool_name("my server", "do.thing") == "my_server__do_thing"
    long = m.tool_name("s", "x" * 100)
    assert len(long) == 64 and long == m.tool_name("s", "x" * 100)
    assert m.input_schema_of({"properties": {"b": {}, "a": {}}}) == {"properties": {"a": {}, "b": {}}, "type": "object"}
    assert list(m.input_schema_of({"properties": {"b": {}, "a": {}}})["properties"]) == ["a", "b"]
    assert m.input_schema_of(None) == {"properties": {}, "type": "object"}


def test_settings_show_status_without_secrets(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from gabe.web.server import create_app

    monkeypatch.setenv("SECRET_TOKEN", "s3cr3t")
    servers = "\n".join([fx(env="{TOKEN: '${SECRET_TOKEN}'}"),
                         "        remote:\n          url: http://127.0.0.1:9/mcp\n"
                         "          headers: {Authorization: 'Bearer ${SECRET_TOKEN}'}"])
    cfg = make_config(tmp_path, servers, extra="team_file: team.yaml", call_timeout=5)
    (tmp_path / "team.yaml").write_text("members: []\n")
    client = TestClient(create_app(cfg.path))
    body = client.get("/api/settings").json()
    entry = next(i for i in body["integrations"] if i["name"] == "mcp")
    by_name = {s["name"]: s for s in entry["servers"]}
    assert by_name["fx"]["state"] == "connected" and by_name["fx"]["tools"] == 6
    assert by_name["remote"]["state"] == "error" and by_name["remote"]["transport"] == "http"
    assert "s3cr3t" not in str(body) and "SECRET_TOKEN" not in str(entry)
