# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the client app: the .exe on Windows, the .app on a Mac.

One-folder, not one-file, and that is a decision rather than a default. The
bundle carries Playwright's driver -- `node.exe` alone is 92MB -- plus Qt, and
one-file re-extracts the whole lot to %TEMP% on *every* launch. This app is
meant to sit open all day waiting for a schedule to come due, so paying a
multi-second unpack each time it starts buys nothing and loses the "it just
opens" feel the client is being sold. One-folder plus a real installer also
gives them a Start Menu entry and an uninstaller, which is what "not technical"
actually needs.

Build with:  pyinstaller packaging/fbposter.spec --noconfirm

On a Mac the same spec also wraps the folder into FacebookAutoPoster.app
(`BUNDLE`, below). Build it through packaging/build_mac.sh, which makes the
.icns icon this needs and the .pkg installer around it.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).resolve().parent
MAC = sys.platform == "darwin"
# Windows takes the .ico; a Mac app takes an .icns, which build_mac.sh makes
# from app.png into the build folder and names here.
ICON = os.environ.get("FBPOSTER_ICNS") if MAC else str(ROOT / "packaging" / "app.ico")

# Playwright ships its driver -- a node runtime plus the JS package -- inside
# the pip package, and the app needs it even though it never launches a
# browser: it attaches to the user's real Chrome over CDP through that driver.
# collect_all is what brings driver/node.exe along; without it the app imports
# fine and fails the moment it tries to connect.
playwright_datas, playwright_binaries, playwright_hidden = collect_all("playwright")

datas = [
    # The checkbox tick, loaded by path from the Qt stylesheet. PyInstaller
    # does not pick up .svg on its own, and when it is missing the failure is
    # silent -- the box still fills with the accent colour and only the tick
    # disappears, on the one screen whose whole job is picking groups.
    (str(ROOT / "fbposter" / "qtui" / "assets"), "fbposter/qtui/assets"),
    *playwright_datas,
    # Windows ships no IANA time zone database. Without this Asia/Jerusalem
    # does not resolve, clock.posting_zone() quietly falls back to the
    # machine's own zone, and every posting-window decision moves by hours.
    *collect_data_files("tzdata", include_py_files=True),
]

hiddenimports = [
    *playwright_hidden,
    "tzdata",
    # Imported for its side effect of registering the zone data.
    "zoneinfo",
]

# Nothing in the app imports these. Tkinter is in the standard library and
# Pillow is installed for the tests, so both are named here to keep them out of
# the bundle whatever a hook might pull in.
excludes = [
    "tkinter",
    "_tkinter",
    "PIL",
    "pytest",
    # Qt modules this app has no use for. QtSvg is deliberately NOT here: no
    # Python code imports it, but Qt needs its image plugin to draw the SVG the
    # stylesheet references, and excluding it loses the tick silently.
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtNetworkAuth",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=list(playwright_binaries),
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FacebookAutoPoster",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # No console. Every message that used to go to stdout now goes to the
    # window: the single-instance refusal is a dialog and the setup wizard
    # explains the rest.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FacebookAutoPoster",
)

if MAC:
    # The Mac's double-clickable app. Its folder inside is exactly the
    # one-folder build above, for the same reasons.
    app = BUNDLE(
        coll,
        name="FacebookAutoPoster.app",
        icon=ICON,
        bundle_identifier="com.fbautomation.autoposter",
        version=os.environ.get("FBPOSTER_VERSION", "0"),
        info_plist={
            "CFBundleDisplayName": "Facebook Auto-Poster",
            "CFBundleName": "Facebook Auto-Poster",
            "NSHighResolutionCapable": True,
            # The oldest macOS the bundled Qt runs on.
            "LSMinimumSystemVersion": "12.0",
            # Not a document app: one window, opened from the Dock.
            "LSMultipleInstancesProhibited": True,
            # The build number beside the release number; most Mac tools and
            # the Installer's own version check read this one.
            "CFBundleVersion": os.environ.get("FBPOSTER_VERSION", "0"),
            # The app's normal state is its window closed to the Dock, posting
            # on a timer: App Nap would slow exactly that (macos.keep_running
            # asks the same while it runs).
            "LSAppNapIsDisabled": True,
            # Light only (fbposter/qtui/CLAUDE.md), the Mac's own parts too.
            "NSRequiresAquaSystemAppearance": True,
            "LSApplicationCategoryType": "public.app-category.productivity",
        },
    )
