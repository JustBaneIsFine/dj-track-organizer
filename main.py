"""Entry point: initialize the DB, start the FastAPI server, show the app window.

The app runs a local FastAPI/uvicorn server and presents it in a **native desktop
window** via pywebview (WebView2/Edge Chromium on Windows) - no browser chrome, no
address bar. If a native window can't be created (pywebview/WebView2 missing) or
``--browser`` / ``DJ_UI=browser`` is set, it falls back to the default browser.

The default mode is the persisted ``open_mode`` setting (Settings → General;
defaults to native). A CLI flag or env var overrides it for one launch.

Run:  python main.py                 # use the saved open_mode (native by default)
      python main.py --browser       # force the browser this launch (dev/debug)
      python main.py --native        # force the native window this launch
      python main.py --init-only     # just run migrations + seed, then exit
      DJ_UI=browser python main.py   # force the browser via env (or DJ_UI=native)
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

# When frozen by PyInstaller, use the Chromium bundled alongside the binary so
# the app is fully self-contained and offline. Must be set before Playwright
# is imported anywhere.
if getattr(sys, "frozen", False):
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(__import__("pathlib").Path(sys._MEIPASS) / "pw-browsers"),  # type: ignore[attr-defined]
    )

import config

# In a windowed (no-console) build there is no stdout/stderr; redirect them to a
# log file so uvicorn/logging don't crash writing to a missing console.
if getattr(sys, "frozen", False) and sys.stdout is None:
    try:
        _logf = open(config.app_dir() / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = _logf
        sys.stderr = _logf
    except Exception:
        pass

from db.schema import connect, init_db


def _stored_open_mode() -> str:
    """Read the persisted 'open_mode' setting (native | browser); default native."""
    async def _read() -> str:
        conn = await connect()
        try:
            from db import queries  # local import keeps --init-only lean
            return await queries.get_setting(conn, "open_mode", "native")
        finally:
            await conn.close()
    try:
        return (asyncio.run(_read()) or "native").lower()
    except Exception:
        return "native"


def _find_free_port() -> int:
    for port in range(config.PORT, config.PORT_FALLBACK_MAX + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((config.HOST, port)) != 0:  # nothing listening
                return port
    raise RuntimeError(
        f"No free port in range {config.PORT}-{config.PORT_FALLBACK_MAX}"
    )


def _splash_html() -> str:
    """Branded loading screen shown instantly, before the server is up.

    Read from the bundled frontend/splash.html (no server dependency); a minimal
    inline fallback covers a missing/unreadable file.
    """
    try:
        return (config.frontend_dir() / "splash.html").read_text(encoding="utf-8")
    except Exception:
        return (
            "<html><body style='background:#0e0e10;color:#d4d4d4;margin:0;height:100vh;"
            "display:flex;align-items:center;justify-content:center;"
            "font-family:sans-serif'>Starting DJ Organizer…</body></html>"
        )


def _wait_until_ready(url: str, timeout: float = 30.0) -> bool:
    """Poll the local server until it answers 200 (replaces a fixed delay)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def _boot_server():
    """Import the API, pick a port, and build the uvicorn server (not started).

    Imported lazily/here so the splash window can paint before this heavier work.
    Returns (server, url).
    """
    import uvicorn  # noqa: WPS433
    from api.server import app  # noqa: WPS433

    asyncio.run(init_db())
    port = _find_free_port()
    url = f"http://{config.HOST}:{port}"
    print(f"[startup] serving on {url}")
    server = uvicorn.Server(uvicorn.Config(app, host=config.HOST, port=port, log_level="info"))
    return server, url


def _run_browser() -> None:
    """Fallback presentation: serve and open the default browser. A heartbeat
    watchdog shuts the server down when the tab stops pinging (tab closed)."""
    server, url = _boot_server()

    import api.routes.system as system  # noqa: WPS433
    system.arm()

    def _watch() -> None:
        # Generous timeout so a backgrounded (throttled) tab isn't killed; the
        # unload sendBeacon gives a fast exit in the normal close case.
        while not server.should_exit:
            time.sleep(5)
            if system.is_stale(120):
                print("[startup] no heartbeat for 120s - shutting down")
                server.should_exit = True
                break

    def _open() -> None:
        if _wait_until_ready(url):
            webbrowser.open(url)

    threading.Thread(target=_watch, daemon=True).start()
    threading.Thread(target=_open, daemon=True).start()
    server.run()  # blocks on the main thread


def main() -> None:
    init_only = "--init-only" in sys.argv

    print(f"[startup] data dir: {config.app_dir()}")

    if init_only:
        asyncio.run(init_db())
        print("[startup] --init-only: done.")
        return

    # Presentation mode precedence: CLI flag > env var > persisted setting > native.
    # (_stored_open_mode runs migrations + reads the setting; cheap, subsecond.)
    if "--browser" in sys.argv:
        mode = "browser"
    elif "--native" in sys.argv:
        mode = "native"
    else:
        mode = os.environ.get("DJ_UI", "").lower() or _stored_open_mode()
    force_browser = mode == "browser"
    print(f"[startup] open mode: {'browser' if force_browser else 'native window'}")

    if force_browser:
        print("[startup] opening default browser")
        _run_browser()
        return

    # Native window. pywebview's GUI loop must own the main thread, so we create
    # the splash window FIRST (instant paint) and do all heavy work - importing
    # the API, init_db, starting the server - in the start() worker thread.
    try:
        import webview  # noqa: WPS433
    except Exception as e:  # pywebview/its backend not installed
        print(f"[startup] pywebview unavailable ({e}); using browser")
        _run_browser()
        return

    window = webview.create_window(
        "DJ Organizer",
        html=_splash_html(),       # paints immediately, no server dependency
        width=1280,
        height=820,
        min_size=(900, 600),
    )
    holder: dict = {}

    def _boot_and_swap() -> None:
        # Runs after the GUI loop starts (splash already visible).
        server, url = _boot_server()
        holder["server"] = server
        threading.Thread(target=server.run, daemon=True).start()
        _wait_until_ready(url)
        window.load_url(url)           # swap splash -> the real app

    try:
        webview.start(_boot_and_swap)  # blocks until the window is closed
    finally:
        if holder.get("server"):
            holder["server"].should_exit = True  # stop the server thread


if __name__ == "__main__":
    main()
