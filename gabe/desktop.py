"""Gabe as a desktop app (macOS and Windows).

Runs the web API on a random local port, locked to this window by a per-launch token,
inside a native window (WKWebView on macOS, WebView2 on Windows). Settings and data live
in the user's app-data folder, not next to the code:

    macOS:   ~/Library/Application Support/Gabe/{config,data}
    Windows: %APPDATA%\\Gabe\\{config,data}
    (override with GABE_HOME)

The daily brief is written on schedule while the app is open.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

APP_NAME = "Gabe"
KEYRING_SERVICE = "Gabe"
API_KEY_NAMES = ("ANTHROPIC_API_KEY",)

log = logging.getLogger("gabe.desktop")


def app_home() -> Path:
    if os.environ.get("GABE_HOME"):
        return Path(os.environ["GABE_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gabe"


def bundled(*parts: str) -> Path:
    """A file shipped with the app (inside the PyInstaller bundle, or the source tree)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def ensure_config(home: Path) -> Path:
    """First launch: copy the starter config and playbooks into the app-data folder."""
    cfg_dir = home / "config"
    cfg = cfg_dir / "gabe.yaml"
    (home / "data").mkdir(parents=True, exist_ok=True)
    if not cfg.exists():
        cfg_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(bundled("gabe", "defaults", "gabe.yaml"), cfg)
        shutil.copy(bundled("gabe", "defaults", "team.yaml"), cfg_dir / "team.yaml")
        books = bundled("config", "playbooks")
        if books.is_dir():
            shutil.copytree(books, cfg_dir / "playbooks", dirs_exist_ok=True)
    else:
        _upgrade_starter_team(cfg)
    try:
        from gabe.claude_code import ensure_guide

        ensure_guide(cfg)  # how to change this installation, for Claude Code
    except Exception as e:
        log.warning("couldn't write the Claude Code guide: %s", e)
    return cfg


def check_main() -> int:
    """`Gabe check` from the packaged app: check the app's config folder."""
    from gabe.check import check_config

    report = check_config(ensure_config(app_home()))
    print(report.text())
    return 0 if report.ok else 1


def _upgrade_starter_team(cfg: Path) -> None:
    """Configs created by the first app build list two specialists under `delegates:`. If they
    are still exactly the starter ones, move to the starter team file (org chart, notes keeper)."""
    import yaml

    raw = yaml.safe_load(cfg.read_text()) or {}
    starter = yaml.safe_load(bundled("gabe", "defaults", "gabe_v0_delegates.yaml").read_text())
    if raw.get("team_file") or raw.get("delegates") != starter:
        return
    team = cfg.parent / "team.yaml"
    if not team.exists():
        shutil.copy(bundled("gabe", "defaults", "team.yaml"), team)
    raw.pop("delegates")
    raw["team_file"] = "team.yaml"
    raw.setdefault("assistant", {}).setdefault("own_tools", ["get_current_time", "recall"])
    shutil.copy(cfg, cfg.with_name(cfg.name + ".bak"))
    cfg.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    log.info("moved the starter specialists to %s", team)


# -- API key in the OS keychain (macOS Keychain / Windows Credential Manager) -----

def get_secret(name: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:  # no keychain available: fall back to the environment only
        return None


def set_secret(name: str, value: str | None) -> None:
    import keyring

    if value:
        keyring.set_password(KEYRING_SERVICE, name, value)
        os.environ[name] = value
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, name)
        except Exception:
            pass
        os.environ.pop(name, None)


def load_secrets() -> None:
    for name in API_KEY_NAMES:
        if not os.environ.get(name):
            value = get_secret(name)
            if value:
                os.environ[name] = value


# -- Native bits the page can call (window.pywebview.api.*) ---------------------

class NativeApi:
    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self.window = None

    def save_text(self, filename: str, content: str) -> bool:
        """Show a Save dialog and write `content` there."""
        import webview

        target = self.window.create_file_dialog(webview.FileDialog.SAVE, save_filename=filename)
        if not target:
            return False
        path = target if isinstance(target, str) else target[0]
        Path(path).write_text(content)
        return True

    def open_url(self, url: str) -> None:
        if url.startswith(("https://", "http://")):
            import webbrowser

            webbrowser.open(url)


# -- Notifications: questions and approvals never wait silently ------------------

def describe_request(rec: dict) -> tuple[str, str]:
    """Title and body for a notification about something waiting for the person in charge."""
    who = rec.get("agent") or APP_NAME
    args = rec.get("args") or {}
    if rec.get("kind") == "question":
        return f"{who} has a question", str(args.get("question", ""))
    tool = rec.get("tool", "")
    if tool in ("send_email", "draft_email"):
        to = ", ".join(args.get("to") or [])
        body = f"Send an email to {to}: {args.get('subject', '')}"
    elif tool == "add_team_member":
        body = f"Add {args.get('name', 'a new bot')} to the team"
    elif tool == "change_gabe":
        body = f"Change Gabe's setup: {args.get('request', '')}"
    else:
        body = tool.replace("_", " ").capitalize()
    return f"{who} needs your OK", body


class Notifier:
    """macOS: Notification Center (in the packaged app) plus a Dock badge with the number
    waiting. Clicking a notification brings Gabe forward on the Approvals page.
    Elsewhere, or when running from source, falls back to `osascript` / nothing."""

    def __init__(self, window_getter) -> None:
        self._window = window_getter
        self._center = None
        self._delegate = None
        if sys.platform == "darwin" and getattr(sys, "frozen", False):
            try:
                self._setup_center()
            except Exception as e:  # never let notifications stop the app
                log.warning("notification center unavailable: %s", e)
                self._center = None

    def _setup_center(self) -> None:
        from Foundation import NSObject
        from UserNotifications import (UNAuthorizationOptionAlert, UNAuthorizationOptionBadge,
                                       UNAuthorizationOptionSound, UNUserNotificationCenter)

        notifier = self

        class _Delegate(NSObject):
            def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, note, handler):
                # In front already: the in-app banner shows it, so no system banner (just the sound).
                handler(0 if notifier._is_active() else (1 << 4) | (1 << 3) | (1 << 1))

            def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, center, resp, handler):
                notifier.open_approvals()
                handler()

        self._center = UNUserNotificationCenter.currentNotificationCenter()
        self._delegate = _Delegate.alloc().init()
        self._center.setDelegate_(self._delegate)
        self._center.requestAuthorizationWithOptions_completionHandler_(
            UNAuthorizationOptionAlert | UNAuthorizationOptionSound | UNAuthorizationOptionBadge,
            lambda granted, err: log.info("notifications allowed: %s", bool(granted)))

    def _main(self, fn) -> None:
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(fn)
        except Exception:
            pass

    def _is_active(self) -> bool:
        try:
            from AppKit import NSApplication

            return bool(NSApplication.sharedApplication().isActive())
        except Exception:
            return False

    def notify(self, ident: str, title: str, body: str) -> None:
        if self._center is not None:
            from UserNotifications import (UNMutableNotificationContent, UNNotificationRequest,
                                           UNNotificationSound)

            content = UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(body[:240])
            content.setSound_(UNNotificationSound.defaultSound())
            req = UNNotificationRequest.requestWithIdentifier_content_trigger_(ident, content, None)
            self._center.addNotificationRequest_withCompletionHandler_(req, None)
        elif sys.platform == "darwin":
            import subprocess

            esc = lambda t: t.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display notification "{esc(body[:240])}" with title "{esc(title)}" sound name "default"'],
                           check=False, capture_output=True)
        if sys.platform == "darwin" and not self._is_active():
            def bounce():
                from AppKit import NSApplication

                NSApplication.sharedApplication().requestUserAttention_(10)  # NSInformationalRequest
            self._main(bounce)

    def badge(self, count: int) -> None:
        if sys.platform != "darwin":
            return

        def set_badge():
            from AppKit import NSApplication

            NSApplication.sharedApplication().dockTile().setBadgeLabel_(str(count) if count else None)
        self._main(set_badge)

    def open_approvals(self) -> None:
        def front():
            try:
                from AppKit import NSApplication

                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                pass
        self._main(front)
        w = self._window()
        if w is not None:
            w.restore()
            w.evaluate_js("history.pushState(null, '', '/approvals'); dispatchEvent(new PopStateEvent('popstate'))")


def watch_requests(state, notifier: Notifier, stop: threading.Event) -> None:
    """Announce every new question or approval once, and keep the Dock badge current."""
    seen = {a["id"] for a in state.approvals.all() if a["status"] == "pending"}
    last = None
    while not stop.wait(1.5):
        try:
            pending = [a for a in state.approvals.all() if a["status"] == "pending"]
        except Exception:
            continue
        for rec in pending:
            if rec["id"] not in seen:
                seen.add(rec["id"])
                title, body = describe_request(rec)
                notifier.notify(rec["id"], title, body)
                log.info("notified: %s", title)
        if len(pending) != last:
            last = len(pending)
            notifier.badge(last)


# -- Startup --------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app, port: int) -> None:
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
    uvicorn.Server(config).run()


def _wait_for(port: int, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("The Gabe service didn't start.")


def _scheduler(config_path: Path) -> None:
    from gabe.briefing import run_scheduler
    from gabe.config import load_config

    run_scheduler(
        load_config(config_path),
        on_brief=lambda text, path: log.info("brief written to %s", path),
        on_error=lambda e: log.error("brief failed: %s: %s", type(e).__name__, e),
    )


def main() -> int:
    home = app_home()
    home.mkdir(parents=True, exist_ok=True)
    logs = home / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=logs / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stdout is None or sys.stderr is None:  # windowed build: nowhere to print
        stream = open(logs / "app.out.log", "a", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream

    import webview

    from gabe.cli import load_dotenv
    from gabe.web.server import create_app

    config_path = ensure_config(home)
    load_dotenv(str(home / ".env"))  # optional, for people who prefer a file

    token = secrets.token_urlsafe(32)
    port = _free_port()
    app = create_app(config_path, token=token, desktop=True)
    threading.Thread(target=_serve, args=(app, port), daemon=True, name="api").start()

    def secrets_in_background():
        # macOS may ask the person to allow Keychain access (e.g. after an update). Waiting for
        # that must never hold up the window, so the key loads here and agents pick it up after.
        load_secrets()
        app.state.gabe.sessions.reset_idle()
        log.info("API key %s", "loaded" if os.environ.get("ANTHROPIC_API_KEY") else "not set")

    threading.Thread(target=secrets_in_background, daemon=True, name="keychain").start()
    threading.Thread(target=_scheduler, args=(config_path,), daemon=True, name="brief-scheduler").start()
    _wait_for(port)

    api = NativeApi(config_path)
    window = webview.create_window(
        APP_NAME, f"http://127.0.0.1:{port}/#t={token}", js_api=api,
        width=1320, height=860, min_size=(960, 640), background_color="#1e1f22",
    )
    api.window = window
    notifier = Notifier(lambda: window)
    stop = threading.Event()
    threading.Thread(target=watch_requests, args=(app.state.gabe, notifier, stop), daemon=True,
                     name="needs-you").start()
    log.info("Gabe started on port %s with config %s", port, config_path)
    webview.start(private_mode=False, storage_path=str(home / "webview"))
    stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
