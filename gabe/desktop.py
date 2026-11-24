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
        books = bundled("config", "playbooks")
        if books.is_dir():
            shutil.copytree(books, cfg_dir / "playbooks", dirs_exist_ok=True)
    return cfg


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
    load_secrets()

    token = secrets.token_urlsafe(32)
    port = _free_port()
    app = create_app(config_path, token=token, desktop=True)
    threading.Thread(target=_serve, args=(app, port), daemon=True, name="api").start()
    threading.Thread(target=_scheduler, args=(config_path,), daemon=True, name="brief-scheduler").start()
    _wait_for(port)

    api = NativeApi(config_path)
    window = webview.create_window(
        APP_NAME, f"http://127.0.0.1:{port}/#t={token}", js_api=api,
        width=1320, height=860, min_size=(960, 640), background_color="#1e1f22",
    )
    api.window = window
    log.info("Gabe started on port %s with config %s", port, config_path)
    webview.start(private_mode=False, storage_path=str(home / "webview"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
