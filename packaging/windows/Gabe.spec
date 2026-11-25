# PyInstaller spec for Gabe on Windows. Build with packaging/windows/build.ps1.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))

hidden = (
    collect_submodules("gabe")            # integrations are imported by name at runtime
    + collect_submodules("uvicorn")
    + collect_submodules("keyring.backends")
    + collect_submodules("mcp", filter=lambda n: not n.startswith("mcp.cli"))  # MCP client (lazy); its CLI needs typer
    + ["googleapiclient.discovery", "google_auth_oauthlib.flow",
       "webview.platforms.winforms", "webview.platforms.edgechromium", "clr"]  # WebView2 via pythonnet
)
datas = (
    [(os.path.join(ROOT, "gabe", "web", "static"), "gabe/web/static"),
     (os.path.join(ROOT, "gabe", "defaults"), "gabe/defaults"),
     (os.path.join(ROOT, "config", "playbooks"), "config/playbooks")]
    # Gabe's Google OAuth client (Desktop app type) for "Sign in with Google". Not in git.
    + ([(os.path.join(ROOT, "packaging", "google_oauth_client.json"), "gabe/defaults")]
       if os.path.exists(os.path.join(ROOT, "packaging", "google_oauth_client.json")) else [])
    + collect_data_files("googleapiclient")   # Gmail API discovery documents
)

a = Analysis(
    [os.path.join(ROOT, "packaging", "gabe_app.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "PyInstaller", "mcp.cli"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts,
    [("X utf8_mode=1", None, "OPTION")],  # files are UTF-8 everywhere, not the Windows code page
    exclude_binaries=True, name="Gabe", console=False,
    icon=os.path.join(ROOT, "packaging", "windows", "Gabe.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Gabe")
