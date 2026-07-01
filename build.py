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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BROWSERS = ROOT / "pw-browsers"
SEP = os.pathsep  # ';' on Windows, ':' elsewhere
IS_WIN = sys.platform.startswith("win")


def run(cmd: list[str], **env_extra) -> None:
    env = {**os.environ, **env_extra}
    print(">", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)


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
        f"{BROWSERS}{SEP}pw-browsers",
    ]
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

    exe = "DJOrganizer.exe" if IS_WIN else "DJOrganizer"
    out = ROOT / "dist" / "DJOrganizer" / exe
    print(f"\nDone. App folder: {out.parent}\nLaunch: {out}")


if __name__ == "__main__":
    main()
