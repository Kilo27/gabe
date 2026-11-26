"""Gabe keeps itself up to date from its GitHub repository.

Each build records the commit it was built from (gabe/_build.py, written by the build
scripts). The updater compares that with the repository and, when `main` has moved on,
reinstalls Gabe on this device:

    macOS app      downloads Gabe-mac.zip from the "mac-latest" release (the "Mac app"
                   workflow replaces it on every push to `main`), swaps it in where the running
                   app lives once Gabe has closed, and reopens it.
    Windows app    downloads GabeSetup.exe from the "Windows app" workflow's latest
                   successful run on `main`, installs it silently and reopens Gabe.
    source         a git checkout (`gabe app`, the CLI): `git pull --ff-only` and
                   `pip install -e .`, then restart Gabe yourself.

Macs need no GitHub token while the repository is public. Windows downloads a workflow
artifact, which GitHub only hands out with a token (Contents: read and Actions: read). A token is
found in the OS keychain (Settings → Updates), GABE_GITHUB_TOKEN / GITHUB_TOKEN / GH_TOKEN, or the
GitHub CLI's login.

Settings (gabe.yaml):

    updates:
      repo: Kilo27/gabe
      branch: main
      auto: true            # install by itself when nothing is running
      check_every_hours: 1
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

log = logging.getLogger("gabe.updater")

DEFAULT_REPO = "Kilo27/gabe"
DEFAULT_BRANCH = "main"
WINDOWS_WORKFLOW = "windows.yml"
WINDOWS_ARTIFACT = "GabeSetup"
MAC_RELEASE = "mac-latest"
MAC_ASSET = "Gabe-mac.zip"
TOKEN_NAME = "GITHUB_TOKEN"  # keychain entry
API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent.parent  # the source tree when running from a checkout


class UpdateError(Exception):
    """Something a person can act on, in plain words."""


@dataclass
class UpdateSettings:
    repo: str = DEFAULT_REPO
    branch: str = DEFAULT_BRANCH
    auto: bool = True
    check_every_hours: float = 1.0

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "UpdateSettings":
        u = (raw or {}).get("updates") or {}
        return cls(
            repo=str(u.get("repo") or DEFAULT_REPO),
            branch=str(u.get("branch") or DEFAULT_BRANCH),
            auto=bool(u.get("auto", True)),
            check_every_hours=max(0.25, float(u.get("check_every_hours", 1.0))),
        )


# -- What's installed ------------------------------------------------------------

def install_kind() -> str:
    """mac_app, windows_app, source (a git checkout) or package (no way to update)."""
    if getattr(sys, "frozen", False):
        return {"darwin": "mac_app", "win32": "windows_app"}.get(sys.platform, "package")
    return "source" if (ROOT / ".git").exists() else "package"


def installed_commit() -> str | None:
    try:
        from gabe import _build  # written by the build scripts

        if getattr(_build, "COMMIT", ""):
            return _build.COMMIT
    except ImportError:
        pass
    if install_kind() == "source":
        try:
            return _git("rev-parse", "HEAD").strip() or None
        except UpdateError:
            return None
    return None


def built_at() -> str | None:
    try:
        from gabe import _build

        return getattr(_build, "BUILT", None)
    except ImportError:
        return None


def app_bundle() -> Path | None:
    """The running Gabe.app (macOS) or the folder holding Gabe.exe (Windows)."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for p in exe.parents:
            if p.suffix == ".app":
                return p
        return None
    return exe.parent


# -- GitHub ------------------------------------------------------------------------

def github_token() -> str | None:
    return find_github_token()[0]


def find_github_token() -> tuple[str | None, str | None]:
    """The token and where it came from: keychain, env or gh (the GitHub CLI's login)."""
    try:
        from gabe.desktop import get_secret

        value = get_secret(TOKEN_NAME)
        if value:
            return value, "keychain"
    except Exception:
        pass
    for name in ("GABE_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(name):
            return os.environ[name], "env"
    gh = shutil.which("gh") or next((p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh") if os.path.exists(p)), None)
    if gh:
        try:
            out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip(), "gh"
        except Exception:
            pass
    return None, None


class GitHub:
    def __init__(self, repo: str, token: str | None, transport: httpx.BaseTransport | None = None) -> None:
        self.repo = repo
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "gabe-updater"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=API, headers=headers, timeout=30, follow_redirects=True,
                                 transport=transport)

    def get(self, path: str, **params) -> Any:
        try:
            r = self.http.get(f"/repos/{self.repo}{path}", params=params or None)
        except httpx.HTTPError as e:
            raise UpdateError(f"Couldn't reach GitHub: {e}") from e
        if r.status_code in (401, 403) and "rate limit" not in r.text.lower():
            raise UpdateError("GitHub refused the token. It needs read access to the repository.")
        if r.status_code == 404:
            raise LookupError(path)
        if r.status_code >= 400:
            raise UpdateError(f"GitHub said {r.status_code}: {r.text[:200]}")
        return r.json()

    def head(self, branch: str) -> dict[str, Any]:
        try:
            c = self.get(f"/commits/{branch}")
        except LookupError:
            raise UpdateError(f"Can't see {self.repo} on GitHub. Check the token can read it.") from None
        return {"sha": c["sha"], "message": c["commit"]["message"].splitlines()[0],
                "date": c["commit"]["committer"]["date"]}

    def windows_build(self, branch: str) -> dict[str, Any]:
        """The newest successful Windows build of `branch` that still has its installer."""
        try:
            runs = self.get(f"/actions/workflows/{WINDOWS_WORKFLOW}/runs", branch=branch, event="push",
                            status="success", per_page=10)["workflow_runs"]
        except LookupError:
            raise UpdateError("No Windows builds on GitHub yet.") from None
        for run in runs:
            arts = self.get(f"/actions/runs/{run['id']}/artifacts")["artifacts"]
            art = next((a for a in arts if a["name"] == WINDOWS_ARTIFACT and not a.get("expired")), None)
            if art:
                message = (run.get("head_commit") or {}).get("message") or run.get("display_title") or ""
                return {"sha": run["head_sha"], "message": message.splitlines()[0] if message else "",
                        "date": run.get("updated_at") or run.get("created_at"), "download": art["archive_download_url"]}
        raise UpdateError("No finished Windows build of the latest changes yet.")

    def mac_build(self) -> dict[str, Any]:
        """The build on the mac-latest release: build.json says which commit it is."""
        try:
            assets = {a["name"]: a for a in self.get(f"/releases/tags/{MAC_RELEASE}")["assets"]}
        except LookupError:
            raise UpdateError("No Mac builds on GitHub yet.") from None
        if "build.json" not in assets or MAC_ASSET not in assets:
            raise UpdateError("No finished Mac build of the latest changes yet.")
        try:
            r = self.http.get(assets["build.json"]["url"], headers={"Accept": "application/octet-stream"})
            r.raise_for_status()
            info = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise UpdateError(f"Couldn't read the Mac build's details: {e}") from e
        return {"sha": info["sha"], "message": info.get("message", ""), "date": info.get("date"),
                "download": assets[MAC_ASSET]["url"]}

    def compare(self, base: str, head: str) -> dict[str, Any] | None:
        """Commits `head` has that `base` doesn't. None when GitHub doesn't know `base`."""
        try:
            c = self.get(f"/compare/{base}...{head}")
        except LookupError:
            return None
        return {"behind": c["ahead_by"], "ahead": c["behind_by"],
                "commits": [{"sha": x["sha"], "message": x["commit"]["message"].splitlines()[0],
                             "date": x["commit"]["committer"]["date"]} for x in c["commits"]][::-1][:30]}

    def download(self, url: str, dest: Path) -> Path:
        # GitHub redirects to its file storage; httpx drops the token on the way (other host).
        # Release assets come as files only when asked for octet-stream.
        try:
            with self.http.stream("GET", url, headers={"Accept": "application/octet-stream"}) as r:
                if r.status_code >= 400:
                    raise UpdateError(f"Download failed ({r.status_code}).")
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as e:
            raise UpdateError(f"Download failed: {e}") from e
        return dest


# -- The updater ---------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(*args: str, cwd: Path = ROOT) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError(f"git failed: {e}") from e
    if out.returncode != 0:
        raise UpdateError((out.stderr or out.stdout).strip() or f"git {args[0]} failed")
    return out.stdout


SCRIPT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


class Updater:
    """Checks for and installs updates. `work_dir` holds downloads, the build source and the
    state of the last attempt; `quit_app` closes Gabe so an installer can replace it."""

    def __init__(self, work_dir: Path, settings: Callable[[], UpdateSettings],
                 notify: Callable[[str, str, str], None] | None = None,
                 quit_app: Callable[[], None] | None = None,
                 kind: str | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self.dir = work_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.notify = notify or (lambda kind, title, body: None)
        self.quit_app = quit_app
        self.kind = kind or install_kind()
        self._transport = transport
        self._lock = threading.Lock()
        self.status: dict[str, Any] = {"state": "idle", "checked": None, "error": None}
        self._announced: str | None = None

    # files
    @property
    def attempt_file(self) -> Path:
        return self.dir / "attempt.json"

    @property
    def log_file(self) -> Path:
        return self.dir / "update.log"

    def _attempt(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.attempt_file.read_text())
        except (OSError, ValueError):
            return None

    def _github(self) -> GitHub:
        s = self.settings()
        return GitHub(s.repo, github_token(), self._transport)

    def info(self) -> dict[str, Any]:
        s = self.settings()
        commit = installed_commit()
        return {
            **self.status,
            "kind": self.kind, "supported": self.kind != "package",
            "installed": {"commit": commit, "built": built_at()},
            "repo": s.repo, "branch": s.branch, "auto": s.auto and self.kind != "source",
            "token": find_github_token()[1],
        }

    # check
    def check(self) -> dict[str, Any]:
        s = self.settings()
        if self.kind == "package":
            self.status = {"state": "unsupported", "checked": _now(), "error": None}
            return self.info()
        try:
            gh = self._github()
            latest = {"windows_app": lambda: gh.windows_build(s.branch), "mac_app": gh.mac_build}.get(
                self.kind, lambda: gh.head(s.branch))()
            commit = installed_commit()
            diff = gh.compare(commit, latest["sha"]) if commit and commit != latest["sha"] else None
            if commit == latest["sha"]:
                behind, commits, known = 0, [], True
            elif diff is None:  # no build stamp, or a commit GitHub doesn't have
                behind, commits, known = None, [], False
            else:
                behind, commits, known = diff["behind"], diff["commits"], True
            available = behind is None or behind > 0
            state = self.status.get("state")
            self.status = {
                "state": state if state == "installing" else ("available" if available else "current"),
                "checked": _now(), "error": None,
                "latest": {k: v for k, v in latest.items() if k != "download"},
                "behind": behind, "commits": commits, "known": known,
                "_download": latest.get("download"),
            }
        except UpdateError as e:
            self.status = {**self.status, "state": "error" if self.status.get("state") != "installing" else "installing",
                           "checked": _now(), "error": str(e)}
        return self.public()

    def public(self) -> dict[str, Any]:
        return {k: v for k, v in self.info().items() if not k.startswith("_")}

    # install
    def install(self) -> dict[str, Any]:
        """Start installing the latest version. Returns at once for the apps (the install runs
        in a separate process that outlives Gabe); a source checkout is updated in place."""
        with self._lock:
            if self.status.get("state") == "installing":
                return self.public()
            if not self.status.get("latest"):
                self.check()
            if self.status.get("state") == "error":
                raise UpdateError(self.status["error"])
            if self.status.get("state") == "current":
                return self.public()
            target = self.status["latest"]
            self.status["state"] = "installing"
            self.status["error"] = None
            try:
                {"mac_app": self._install_mac, "windows_app": self._install_windows,
                 "source": self._install_source}.get(self.kind, self._unsupported)(target)
            except Exception as e:
                self.status["state"] = "error"
                self.status["error"] = str(e)
                log.exception("update failed")
                raise UpdateError(str(e)) from e
            return self.public()

    def _unsupported(self, target):
        raise UpdateError("This copy of Gabe can't update itself.")

    def _record_attempt(self, target: dict[str, Any]) -> None:
        self.attempt_file.write_text(json.dumps({"sha": target["sha"], "message": target.get("message", ""),
                                                 "from": installed_commit(), "started": _now()}))
        (self.dir / "result.json").unlink(missing_ok=True)

    def _install_source(self, target: dict[str, Any]) -> None:
        s = self.settings()
        if _git("status", "--porcelain").strip():
            raise UpdateError("The checkout has uncommitted changes. Commit or stash them first.")
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch != s.branch:
            raise UpdateError(f"The checkout is on {branch}, not {s.branch}.")
        _git("pull", "--ff-only", "origin", s.branch)
        pip = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", str(ROOT)],
                             capture_output=True, text=True, timeout=600)
        if pip.returncode != 0:
            raise UpdateError("pip install failed: " + (pip.stderr or pip.stdout).strip()[-400:])
        self.status.update(state="restart", behind=0, commits=[])
        self.notify("done", "Gabe updated", "Restart Gabe to use the new version.")

    def _install_mac(self, target: dict[str, Any]) -> None:
        url = self.status.get("_download")
        if not url:
            raise UpdateError("No build to download.")
        app = app_bundle()
        if app is None:
            raise UpdateError("Couldn't find Gabe.app.")
        zpath = self._github().download(url, self.dir / MAC_ASSET)
        new = self.dir / "new"
        shutil.rmtree(new, ignore_errors=True)
        out = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zpath), str(new)], capture_output=True, text=True)
        zpath.unlink(missing_ok=True)
        if out.returncode != 0 or not (new / "Gabe.app").is_dir():
            raise UpdateError("The download didn't contain Gabe.app.")
        script = self.dir / "update.sh"
        script.write_text(mac_script(pid=os.getpid(), new=new / "Gabe.app", app=app, result=self.dir / "result.json"))
        script.chmod(0o755)
        self._record_attempt(target)
        self._spawn(["/bin/sh", str(script)])
        self.notify("done", "Updating Gabe", "Gabe will close and reopen in a moment.")
        if self.quit_app:
            threading.Timer(2.0, self.quit_app).start()

    def _install_windows(self, target: dict[str, Any]) -> None:
        url = self.status.get("_download")
        if not url:
            raise UpdateError("No installer to download.")
        app = app_bundle()
        if app is None:
            raise UpdateError("Couldn't find Gabe.exe.")
        zpath = self._github().download(url, self.dir / "GabeSetup.zip")
        with zipfile.ZipFile(zpath) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".exe")), None)
            if not name:
                raise UpdateError("The download had no installer in it.")
            setup = self.dir / "GabeSetup.exe"
            with z.open(name) as src, open(setup, "wb") as out:
                shutil.copyfileobj(src, out)
        zpath.unlink(missing_ok=True)
        script = self.dir / "update.ps1"
        script.write_text(windows_script(pid=os.getpid(), setup=setup, exe=app / "Gabe.exe", log=self.log_file,
                                         result=self.dir / "result.json"))
        self._record_attempt(target)
        self._spawn(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                     "-File", str(script)])
        self.notify("done", "Updating Gabe", "Gabe will close and reopen in a moment.")
        if self.quit_app:
            threading.Timer(2.0, self.quit_app).start()

    def _spawn(self, argv: list[str]) -> None:
        logf = open(self.log_file, "a")
        logf.write(f"\n=== {_now()} {' '.join(argv)}\n")
        logf.flush()
        kw: dict[str, Any] = {"stdout": logf, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
                              "cwd": str(self.dir), "close_fds": True}
        if sys.platform == "win32":
            kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True  # survives Gabe quitting
            kw["env"] = {**os.environ, "PATH": SCRIPT_PATH}
        subprocess.Popen(argv, **kw)

    # after a restart, and while a build runs
    def settle(self) -> None:
        """Report how the last attempt went: on start (did we come back as the new version?)
        and while a Mac build is running (did it fail?)."""
        attempt = self._attempt()
        if not attempt:
            return
        result = None
        try:
            result = json.loads((self.dir / "result.json").read_text())
        except (OSError, ValueError):
            pass
        if installed_commit() == attempt["sha"]:
            self.attempt_file.unlink(missing_ok=True)
            self.status.update(state="current", error=None)
            self.notify("done", "Gabe updated", attempt.get("message", ""))
        elif result and not result.get("ok"):
            self.attempt_file.unlink(missing_ok=True)
            err = f"The update didn't install. Details are in {self.log_file}."
            self.status.update(state="error", error=err)
            self.notify("failed", "Gabe update failed", err)
        elif self.status.get("state") != "installing":
            # Gabe restarted without the new version and without a result: the installer died.
            started = datetime.fromisoformat(attempt["started"])
            if (datetime.now(timezone.utc) - started).total_seconds() > 1800:
                self.attempt_file.unlink(missing_ok=True)
                self.status.update(state="error", error=f"The last update didn't finish. Details are in {self.log_file}.")

    # background loop
    def run_forever(self, idle: Callable[[], bool], stop: threading.Event | None = None,
                    first_delay: float = 60) -> None:
        stop = stop or threading.Event()
        self.settle()
        if stop.wait(first_delay):
            return
        while True:
            try:
                self.settle()
                if self.status.get("state") != "installing":
                    self.check()
                    s = self.settings()
                    if (s.auto and self.kind in ("mac_app", "windows_app") and self.status.get("state") == "available"
                            and self.status.get("known") and idle()):
                        log.info("installing update %s", self.status["latest"]["sha"][:7])
                        self.install()
                    elif self.status.get("state") == "available" and self._announced != self.status["latest"]["sha"]:
                        self._announced = self.status["latest"]["sha"]
                        if not s.auto or self.kind == "source":
                            self.notify("done", "Gabe update available", self.status["latest"].get("message", ""))
            except Exception:
                log.exception("update check failed")
            wait = 30 if self.status.get("state") == "installing" else self.settings().check_every_hours * 3600
            if stop.wait(wait):
                return


# -- Install scripts (run outside Gabe, so they can replace it) ---------------------

def _sh(s: str | Path) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def mac_script(pid: int, new: Path, app: Path, result: Path) -> str:
    old = Path(str(app) + ".old")
    return f"""#!/bin/sh
# Written by Gabe's updater: wait for Gabe to close, swap in the new version, reopen it.
fail() {{ echo "update failed: $1"; printf '{{"ok": false, "error": "%s"}}' "$1" > {_sh(result)}; open {_sh(app)}; exit 1; }}
for _ in $(seq 60); do kill -0 {pid} 2>/dev/null || break; sleep 1; done
kill {pid} 2>/dev/null || true
codesign --verify --deep --strict {_sh(new)} || fail "signature"
rm -rf {_sh(old)}
mv {_sh(app)} {_sh(old)} || fail "can't replace the app"
if ditto {_sh(new)} {_sh(app)}; then
  rm -rf {_sh(old)} {_sh(new)}
  xattr -dr com.apple.quarantine {_sh(app)} 2>/dev/null || true
  printf '{{"ok": true}}' > {_sh(result)}
  open {_sh(app)}
else
  rm -rf {_sh(app)}; mv {_sh(old)} {_sh(app)}
  fail "copy"
fi
"""


def _ps(s: str | Path) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def windows_script(pid: int, setup: Path, exe: Path, log: Path, result: Path) -> str:
    return f"""# Written by Gabe's updater: wait for Gabe to close, install the new version, reopen it.
$ErrorActionPreference = 'Continue'
Start-Transcript -Append -Path {_ps(log)} | Out-Null
try {{ Wait-Process -Id {pid} -Timeout 60 -ErrorAction SilentlyContinue }} catch {{}}
Get-Process Gabe -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
$p = Start-Process -FilePath {_ps(setup)} -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS' -Wait -PassThru
if ($p.ExitCode -eq 0) {{ Set-Content -Path {_ps(result)} -Value '{{"ok": true}}' }}
else {{ Set-Content -Path {_ps(result)} -Value ('{{"ok": false, "error": "installer exit ' + $p.ExitCode + '"}}') }}
Start-Process -FilePath {_ps(exe)}
Stop-Transcript | Out-Null
"""


def write_build_stamp(commit: str, path: Path = ROOT / "gabe" / "_build.py") -> None:
    """Used by the build scripts (python -m gabe.updater stamp <sha>)."""
    path.write_text(f'"""Written by the build. Not in git."""\nCOMMIT = {commit!r}\nBUILT = {_now()!r}\n')


if __name__ == "__main__":
    if sys.argv[1:2] == ["stamp"]:
        write_build_stamp(sys.argv[2] if len(sys.argv) > 2 else "")
