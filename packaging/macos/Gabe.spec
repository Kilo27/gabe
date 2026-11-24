# PyInstaller spec for Gabe.app. Build with packaging/macos/build.sh.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
VERSION = os.environ.get("GABE_VERSION", "0.1.0")

hidden = (
    collect_submodules("gabe")            # integrations are imported by name at runtime
    + collect_submodules("uvicorn")
    + collect_submodules("keyring.backends")
    + ["googleapiclient.discovery", "google_auth_oauthlib.flow",
       "UserNotifications", "AppKit", "Foundation", "PyObjCTools.AppHelper"]  # notifications, Dock badge
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
    excludes=["tkinter", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Gabe", console=False,
          argv_emulation=False, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Gabe")
app = BUNDLE(
    coll,
    name="Gabe.app",
    icon=os.path.join(ROOT, "packaging", "build", "icons", "Gabe.icns"),
    bundle_identifier="app.gabe.desktop",
    version=VERSION,
    info_plist={
        "CFBundleName": "Gabe",
        "CFBundleDisplayName": "Gabe",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSRequiresAquaSystemAppearance": False,
    },
)
