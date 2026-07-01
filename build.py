"""Package DJ Organizer into a single-file binary with PyInstaller.

Run:  python build.py

Outputs (a folder build for fast startup - no per-launch unpacking):
  dist/DJOrganizer/DJOrganizer.exe   (Windows)
  dist/DJOrganizer/DJOrganizer       (Mac/Linux)

The build is windowed (no console window) and starts fast because it's --onedir
rather than --onefile (a single .exe re-extracts ~350MB on every launch).
Distribute the whole dist/DJOrganizer/ folder (zip it).

What it does:
  1. Installs Chromium into a local ``pw-browsers/`` folder (so it can be bundled
     instead of relying on the user's machine).
  2. Invokes PyInstaller, bundling: the Python runtime, all deps, ``frontend/``,
     ``db/migrations/``, and the Playwright Chromium folder.

At runtime, ``main.py`` points ``PLAYWRIGHT_BROWSERS_PATH`` at the bundled folder
when frozen, so the app is fully self-contained and offline.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent
BROWSERS = ROOT / "pw-browsers"
SEP = os.pathsep  # ';' on Windows, ':' elsewhere
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def run(cmd: list[str], **env_extra) -> None:
    env = {**os.environ, **env_extra}
    print(">", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)


def write_win_version_file() -> Path:
    """Generate a Windows version resource from APP_VERSION so the .exe Properties
    show a real product name and version instead of blanks."""
    parts = [int(x) for x in config.APP_VERSION.split(".")[:3]]
    while len(parts) < 4:
        parts.append(0)
    v = config.APP_VERSION
    vers = tuple(parts)
    path = ROOT / "version_info.txt"
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers}, prodvers={vers},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Tezej'),
      StringStruct('FileDescription', 'DJ Track Organizer'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'DJOrganizer'),
      StringStruct('OriginalFilename', 'DJOrganizer.exe'),
      StringStruct('ProductName', 'DJ Track Organizer'),
      StringStruct('ProductVersion', '{v}'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Tezej'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return path


def main() -> None:
    py = sys.executable

    # 1. Ensure build tooling.
    run([py, "-m", "pip", "install", "--quiet", "pyinstaller"])

    # 2. Install Chromium into a bundle-local folder.
    BROWSERS.mkdir(exist_ok=True)
    run([py, "-m", "playwright", "install", "chromium"],
        PLAYWRIGHT_BROWSERS_PATH=str(BROWSERS))

    # 3. Build.
    add_data = [
        f"frontend{SEP}frontend",
        f"db/migrations{SEP}db/migrations",
    ]
    # On macOS, PyInstaller ad-hoc codesigns every Mach-O it collects, which fails
    # on Chromium's nested "Google Chrome for Testing.app" bundle. So keep the
    # browser out of the collector here and copy it into the .app after the build.
    if not IS_MAC:
        add_data.append(f"{BROWSERS}{SEP}pw-browsers")
    cmd = [
        py, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir",  # folder build = fast startup
        "--windowed",                          # no console window
        "--name", "DJOrganizer",
        "--collect-all", "playwright",
        "--collect-all", "webview",            # pywebview + backend
    ]
    if IS_WIN:
        cmd += ["--hidden-import", "clr"]      # pythonnet (WebView2 backend), Windows only
        cmd += ["--version-file", str(write_win_version_file())]
    # App/window/taskbar icon. Windows uses .ico, macOS uses .icns.
    icon = ROOT / "frontend" / "assets" / ("icon.ico" if IS_WIN else "icon.icns")
    if icon.exists():
        cmd += ["--icon", str(icon)]
    else:
        print(f"[build] note: {icon} not found - using the default icon.")
    for d in add_data:
        cmd += ["--add-data", d]
    cmd.append("main.py")
    run(cmd)

    if IS_MAC:
        # Copy Chromium into the built .app (see the add_data note above). main.py
        # reads it from Contents/Resources/pw-browsers when frozen on macOS.
        dest = ROOT / "dist" / "DJOrganizer.app" / "Contents" / "Resources" / "pw-browsers"
        print(f"[build] copying bundled Chromium into {dest}")
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(BROWSERS, dest, symlinks=True)
        out = ROOT / "dist" / "DJOrganizer.app"
        print(f"\nDone. App bundle: {out}")
    else:
        exe = "DJOrganizer.exe" if IS_WIN else "DJOrganizer"
        out = ROOT / "dist" / "DJOrganizer" / exe
        print(f"\nDone. App folder: {out.parent}\nLaunch: {out}")


if __name__ == "__main__":
    main()
