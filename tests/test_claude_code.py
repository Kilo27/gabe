"""Changing an installation: local integrations, `gabe check`, the CLAUDE.md guide,
"Open in Claude Code", and the change_gabe action (against a fake `claude`)."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from gabe.app import base_registry, build_gabe
from gabe.check import check_config
from gabe.config import load_config
from gabe.policy import Decision, Policy
from tests.conftest import ScriptedProvider
from tests.test_agent import done, results, tool_step

GOOD = """
company: {name: TestCo, timezone: UTC}
data_dir: ../data
playbooks_dir: playbooks
integrations: [core, tasks, {name: claude_code, settings: {binary: %(claude)s}}]
policy: {defaults: {read: allow, write: allow, external: ask}}
team_file: team.yaml
"""

# Stands in for Claude Code: acts on keywords in the prompt (read from stdin) and records
# how it was called.
FAKE = r'''#!%(python)s
import json, os, sys
from pathlib import Path
prompt = sys.stdin.read()
log = Path(os.environ["FAKE_LOG"])
calls = json.loads(log.read_text()) if log.exists() else []
calls.append({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(),
              "has_key": "ANTHROPIC_API_KEY" in os.environ, "guide": Path("CLAUDE.md").exists()})
log.write_text(json.dumps(calls))
cfg = Path("gabe.yaml")
if "please fail" in prompt:
    Path("playbooks/half.md").write_text("half done")
    print(json.dumps({"type": "result", "is_error": True, "result": "Not logged in"})); sys.exit(1)
if "playbook" in prompt:
    Path("playbooks/onboarding.md").write_text("Welcome new hires.")
if "break" in prompt:
    cfg.write_text(cfg.read_text().replace("external: ask", "external: maybe"))
if "fix these" in prompt.lower() and "stubborn" not in Path("gabe.yaml").read_text():
    cfg.write_text(cfg.read_text().replace("external: maybe", "external: ask"))
if "stubborn" in prompt:
    cfg.write_text(cfg.read_text().replace("external: ask", "external: maybe") + "# stubborn\n")
print(json.dumps({"type": "result", "is_error": False, "session_id": "sess-1",
                  "result": "Added the onboarding playbook."}))
'''


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A config folder like the app's, with a fake Claude Code."""
    claude = tmp_path / "bin" / "claude"
    claude.parent.mkdir()
    claude.write_text(FAKE % {"python": sys.executable})
    claude.chmod(0o755)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    cfg_dir = tmp_path / "config"
    (cfg_dir / "playbooks").mkdir(parents=True)
    (cfg_dir / "playbooks" / "daily.md").write_text("Brief me.")
    (cfg_dir / "team.yaml").write_text("members: []\n")
    (cfg_dir / "gabe.yaml").write_text(GOOD % {"claude": claude})
    return cfg_dir / "gabe.yaml"


def calls(tmp_path):
    return json.loads((tmp_path / "calls.json").read_text())


# -- local integrations -------------------------------------------------------------------

INVOICES = '''
from gabe.tools.registry import Risk, schema

def setup(registry, ctx):
    @registry.add("list_invoices", "List unpaid invoices.", schema({}), Risk.READ, "invoices")
    def list_invoices():
        return "%s"
'''


def test_local_integration_loads_and_reloads_when_edited(install):
    folder = install.parent / "integrations"
    folder.mkdir()
    (folder / "invoices.py").write_text(INVOICES % "INV-1")
    install.write_text(install.read_text().replace("[core, tasks,", "[core, tasks, invoices,"))
    cfg = load_config(install)
    assert base_registry(cfg).get("list_invoices").run({}) == "INV-1"
    (folder / "invoices.py").write_text(INVOICES % "INV-2")
    os.utime(folder / "invoices.py", (time.time() + 5, time.time() + 5))
    assert base_registry(cfg).get("list_invoices").run({}) == "INV-2"


def test_broken_local_integration_is_skipped_and_reported(install):
    folder = install.parent / "integrations"
    folder.mkdir()
    (folder / "crm.py").write_text("def setup(registry, ctx):\n    raise ValueError('no CRM_URL')\n")
    install.write_text(install.read_text().replace("[core, tasks,", "[core, tasks, crm,"))
    reg = base_registry(load_config(install))
    assert reg.get("get_current_time") is not None
    report = check_config(install)
    assert not report.ok and "integration crm: ValueError: no CRM_URL" in report.text()


# -- gabe check -----------------------------------------------------------------------------


def test_check_passes_and_catches_problems(install):
    assert check_config(install).ok
    install.write_text(install.read_text().replace("external: ask", "external: maybe"))
    assert "policy" in check_config(install).problems[0]
    install.write_text("company: [unclosed")
    assert "can't be read" in check_config(install).problems[0]


def test_check_warns_about_unknown_skills_and_flags_broken_teams(install):
    team = install.parent / "team.yaml"
    team.write_text("members:\n- {id: a, name: A, job: x, skills: [list_tasks, notion__search]}\n")
    report = check_config(install)
    assert report.ok and "notion__search" in report.warnings[0]
    team.write_text("members:\n- {id: a, name: A}\n- {id: a, name: B}\n")
    assert "team" in check_config(install).problems[0]


def test_check_cli_exit_codes(install, capsys):
    from gabe.cli import main

    assert main(["-c", str(install), "check"]) == 0
    assert "OK:" in capsys.readouterr().out
    install.write_text(install.read_text().replace("external: ask", "external: maybe"))
    assert main(["-c", str(install), "check"]) == 1


# -- the guide ------------------------------------------------------------------------------


def test_guide_is_written_with_a_working_check_command(install):
    from gabe.claude_code import ensure_guide

    guide = ensure_guide(install)
    text = guide.read_text()
    assert "{{" not in text and "check" in text and str(install) in text
    assert ensure_guide(install).read_text() == text  # stable


def test_first_launch_writes_the_guide(tmp_path, monkeypatch):
    from gabe.desktop import ensure_config

    cfg = ensure_config(tmp_path / "home")
    assert (cfg.parent / "CLAUDE.md").exists() and (cfg.parent / "playbooks").is_dir()


# -- change_gabe ----------------------------------------------------------------------------


def change(install, request):
    tool = base_registry(load_config(install)).get("change_gabe")
    return tool.run({"request": request})


def test_change_runs_claude_restricted_in_the_folder_and_reports(install, tmp_path):
    out = change(install, "Add an onboarding playbook")
    assert out.startswith("Added the onboarding playbook.")
    assert "Changed: playbooks/onboarding.md (new)" in out
    c = calls(tmp_path)[0]
    assert c["cwd"] == str(install.parent.resolve()) and c["guide"]
    assert "--restricted" in c["argv"] and "-p" in c["argv"]
    assert c["argv"][c["argv"].index("--permission-mode") + 1] == "acceptEdits"
    assert not c["has_key"]  # uses the person's Claude login, not Gabe's API key
    backups = list((install.parent.parent / "data" / "claude_code" / "backups").iterdir())
    assert len(backups) == 1 and not (backups[0] / "playbooks" / "onboarding.md").exists()


def test_a_broken_change_gets_one_fix_then_passes(install, tmp_path):
    out = change(install, "add a playbook and break something")
    assert "Changed: playbooks/onboarding.md (new)\n" in out  # gabe.yaml ended up as it was
    second = calls(tmp_path)[1]
    assert second["argv"][second["argv"].index("--resume") + 1] == "sess-1"
    assert "policy" in second["prompt"]
    assert check_config(install).ok


def test_a_change_that_stays_broken_is_undone(install, tmp_path):
    before = install.read_text()
    with pytest.raises(RuntimeError, match="undone"):
        change(install, "add a playbook, stubborn")
    assert install.read_text() == before
    assert not (install.parent / "playbooks" / "onboarding.md").exists()
    assert (install.parent / "playbooks" / "daily.md").read_text() == "Brief me."


def test_claude_failure_undoes_partial_edits(install):
    with pytest.raises(RuntimeError, match=r"isn't signed in.*Its partial changes were undone"):
        change(install, "please fail")
    assert not (install.parent / "playbooks" / "half.md").exists()


def test_not_offered_without_claude_code(install):
    install.write_text(install.read_text().replace("binary: ", "binary: /nope/"))
    assert base_registry(load_config(install)).get("change_gabe") is None


def test_every_change_needs_a_persons_ok_and_works_through_gabe(install, tmp_path):
    cfg = load_config(install)
    tool = base_registry(cfg).get("change_gabe")
    assert Policy.from_config(cfg.policy).decide(tool) is Decision.ASK  # write, but ask_first
    asked = []
    p = ScriptedProvider([tool_step(("change_gabe", {"request": "add an onboarding playbook"})), done("Done.")])
    build_gabe(cfg, provider=p, approver=lambda t, a: asked.append(a["request"]) or True).send("x")
    assert asked == ["add an onboarding playbook"]
    assert "onboarding.md" in results(p)[0][0].content


# -- the app --------------------------------------------------------------------------------


def test_settings_and_open_in_claude_code(install, monkeypatch):
    from fastapi.testclient import TestClient

    import gabe.web.server as server
    from gabe.web.server import create_app

    opened = []
    monkeypatch.setattr(server, "find_claude", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(server, "open_in_terminal", lambda path, claude, data: opened.append((path, claude)))
    client = TestClient(create_app(install))
    body = client.get("/api/settings").json()
    assert body["claude_code"] == {"installed": True, "folder": str(install.parent.resolve())}
    assert client.post("/api/claude-code/open").json() == {"ok": True}
    assert opened == [(install.resolve(), "/usr/local/bin/claude")]
    monkeypatch.setattr(server, "find_claude", lambda: None)
    assert client.post("/api/claude-code/open").status_code == 400
