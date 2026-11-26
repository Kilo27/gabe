import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

import gabe.updater as upd
from gabe.updater import UpdateError, Updater, UpdateSettings

OLD, NEW = "a" * 40, "b" * 40


def commit(sha, msg):
    return {"sha": sha, "commit": {"message": msg + "\n\nbody", "committer": {"date": "2026-11-26T10:00:00Z"}}}


def github(routes, seen=None):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req)
        for prefix, body in routes.items():
            if req.url.path.startswith(prefix):
                if isinstance(body, int):
                    return httpx.Response(body, json={"message": "nope"})
                if isinstance(body, bytes):
                    return httpx.Response(200, content=body)
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.MockTransport(handler)


API = "https://api.github.com/repos/o/r"
ROUTES = {
    "/repos/o/r/commits/main": commit(NEW, "Add the thing"),
    "/repos/o/r/releases/tags/mac-latest": {"assets": [
        {"name": "Gabe.dmg", "url": f"{API}/releases/assets/7"},
        {"name": "Gabe-mac.zip", "url": f"{API}/releases/assets/2"},
        {"name": "build.json", "url": f"{API}/releases/assets/1"}]},
    "/repos/o/r/releases/assets/1": {"sha": NEW, "message": "Add the thing", "date": "2026-11-26T10:00:00Z"},
    f"/repos/o/r/compare/{OLD}...{NEW}": {"ahead_by": 2, "behind_by": 0,
                                         "commits": [commit("c" * 40, "First"), commit(NEW, "Add the thing")]},
}


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setenv("GABE_GITHUB_TOKEN", "t" * 30)
    monkeypatch.setattr("gabe.desktop.get_secret", lambda name: None)  # not this computer's keychain
    notes = []

    def _make(kind="mac_app", routes=ROUTES, installed=OLD, auto=True, seen=None):
        monkeypatch.setattr(upd, "installed_commit", lambda: installed)
        u = Updater(tmp_path / "updates", lambda: UpdateSettings(repo="o/r", auto=auto),
                    notify=lambda *a: notes.append(a), kind=kind, transport=github(routes, seen))
        u.notes = notes
        return u
    return _make


def test_check_finds_new_commits(make):
    seen = []
    st = make(seen=seen).check()
    assert st["state"] == "available" and st["behind"] == 2
    assert [c["message"] for c in st["commits"]] == ["Add the thing", "First"]  # newest first
    assert st["latest"]["sha"] == NEW and "_download" not in st
    assert seen[0].headers["authorization"] == "Bearer " + "t" * 30


def test_check_up_to_date(make):
    assert make(installed=NEW).check()["state"] == "current"


def test_installed_ahead_of_main_is_not_an_update(make):
    routes = {**ROUTES, f"/repos/o/r/compare/{OLD}...{NEW}": {"ahead_by": 0, "behind_by": 3, "commits": []}}
    assert make(routes=routes).check()["state"] == "current"


def test_unknown_build_offers_update_but_not_automatically(make):
    u = make(installed=None)
    st = u.check()
    assert st["state"] == "available" and st["behind"] is None and not u.status["known"]


def test_bad_token_is_a_plain_error(make):
    st = make(routes={"/repos/o/r/releases/tags/mac-latest": 401}).check()
    assert st["state"] == "error" and "token" in st["error"]


def test_windows_uses_latest_successful_build(make):
    routes = {
        "/repos/o/r/actions/workflows/windows.yml/runs": {"workflow_runs": [
            {"id": 2, "head_sha": NEW, "head_commit": {"message": "Built"}, "updated_at": "x"}]},
        "/repos/o/r/actions/runs/2/artifacts": {"artifacts": [
            {"name": "Gabe-portable", "archive_download_url": "https://api.github.com/x/1/zip"},
            {"name": "GabeSetup", "expired": False, "archive_download_url": "https://api.github.com/x/2/zip"}]},
        f"/repos/o/r/compare/{OLD}...{NEW}": ROUTES[f"/repos/o/r/compare/{OLD}...{NEW}"],
    }
    u = make(kind="windows_app", routes=routes)
    st = u.check()
    assert st["state"] == "available" and st["latest"]["message"] == "Built"
    assert u.status["_download"].endswith("/2/zip")


def test_windows_install_unpacks_installer_and_quits(make, monkeypatch, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("GabeSetup.exe", b"MZ fake")
    routes = {
        "/repos/o/r/actions/workflows/windows.yml/runs": {"workflow_runs": [{"id": 2, "head_sha": NEW}]},
        "/repos/o/r/actions/runs/2/artifacts": {"artifacts": [
            {"name": "GabeSetup", "archive_download_url": "https://api.github.com/x/2/zip"}]},
        "/x/2/zip": buf.getvalue(),
        f"/repos/o/r/compare/{OLD}...{NEW}": ROUTES[f"/repos/o/r/compare/{OLD}...{NEW}"],
    }
    u = make(kind="windows_app", routes=routes)
    spawned, quit = [], []
    u.quit_app = lambda: quit.append(1)
    monkeypatch.setattr(upd, "app_bundle", lambda: tmp_path / "Gabe")
    monkeypatch.setattr(u, "_spawn", lambda argv: spawned.append(argv))
    monkeypatch.setattr(upd.threading, "Timer", lambda t, fn: type("T", (), {"start": lambda self: fn()})())
    st = u.install()
    assert st["state"] == "installing" and quit == [1]
    assert (u.dir / "GabeSetup.exe").read_bytes() == b"MZ fake"
    script = (u.dir / "update.ps1").read_text()
    assert "/VERYSILENT" in script and str(tmp_path / "Gabe" / "Gabe.exe") in script
    assert spawned[0][0] == "powershell.exe"
    assert json.loads(u.attempt_file.read_text())["sha"] == NEW


def test_mac_uses_the_published_build(make):
    seen = []
    u = make(seen=seen)
    st = u.check()
    assert st["state"] == "available" and st["latest"]["message"] == "Add the thing" and st["behind"] == 2
    assert u.status["_download"] == f"{API}/releases/assets/2"
    assert seen[1].headers["accept"] == "application/octet-stream"


def test_mac_without_a_build_yet(make):
    st = make(routes={"/repos/o/r/releases/tags/mac-latest": {"assets": []}}).check()
    assert st["state"] == "error" and "Mac build" in st["error"]


def test_mac_install_swaps_in_the_download_and_quits(make, monkeypatch, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Gabe.app/Contents/Info.plist", b"<plist/>")
    routes = {**ROUTES, "/repos/o/r/releases/assets/2": buf.getvalue()}
    app = tmp_path / "Apps" / "Gabe.app"
    monkeypatch.setattr(upd, "app_bundle", lambda: app)
    monkeypatch.setattr(upd.threading, "Timer", lambda t, fn: type("T", (), {"start": lambda self: fn()})())
    u = make(routes=routes)
    spawned, quit = [], []
    u.quit_app = lambda: quit.append(1)
    monkeypatch.setattr(u, "_spawn", lambda argv: spawned.append(argv))
    st = u.install()
    assert st["state"] == "installing" and quit == [1]
    assert (u.dir / "new" / "Gabe.app" / "Contents" / "Info.plist").exists()
    script = (u.dir / "update.sh").read_text()
    assert f"ditto '{u.dir / 'new' / 'Gabe.app'}' '{app}'" in script and f"open '{app}'" in script
    assert spawned == [["/bin/sh", str(u.dir / "update.sh")]]
    assert json.loads(u.attempt_file.read_text())["sha"] == NEW


def test_settle_reports_success_after_restart(make, monkeypatch):
    u = make(installed=NEW)
    u.attempt_file.write_text(json.dumps({"sha": NEW, "message": "Add the thing", "started": upd._now()}))
    u.settle()
    assert u.notes[-1] == ("done", "Gabe updated", "Add the thing")
    assert not u.attempt_file.exists()


def test_settle_reports_failed_build(make):
    u = make()
    u.attempt_file.write_text(json.dumps({"sha": NEW, "started": upd._now()}))
    (u.dir / "result.json").write_text('{"ok": false, "error": "build"}')
    u.status["state"] = "installing"
    u.settle()
    assert u.status["state"] == "error" and u.notes[-1][1] == "Gabe update failed"


def test_auto_install_waits_until_idle(make, monkeypatch):
    u = make()
    installs = []
    monkeypatch.setattr(u, "install", lambda: installs.append(1))
    stop = upd.threading.Event()
    calls = []

    def idle():
        calls.append(1)
        stop.set()  # one round is enough
        return False

    u.run_forever(idle, stop, first_delay=0)
    assert calls and not installs
    assert u.notes == []  # auto is on: no "update available" nag, it installs once idle


def test_api_endpoints(config_file, monkeypatch, tmp_path):
    from gabe.web.server import create_app

    monkeypatch.setenv("GABE_GITHUB_TOKEN", "t" * 30)
    monkeypatch.setattr(upd, "installed_commit", lambda: NEW)
    config_file.write_text(config_file.read_text() + "\nupdates: {repo: o/r}\n")
    app = create_app(config_file, run_schedules=False, run_slack=False)
    app.state.updater.kind = "mac_app"
    app.state.updater._transport = github(ROUTES)
    c = TestClient(app)
    assert c.post("/api/updates/check").json()["state"] == "current"
    assert c.put("/api/updates/settings", json={"auto": False}).json()["auto"] is False
    assert "auto: false" in config_file.read_text()
