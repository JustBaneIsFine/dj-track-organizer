"""System/runtime endpoints - currently just the browser-mode heartbeat.

In browser mode the server has no signal when the user closes the tab (unlike the
native window, which exits on close). The frontend pings ``/api/heartbeat`` while
the page is open; the launcher arms a watchdog that shuts the server down if the
pings stop (tab closed). Native mode never arms the watchdog, so pings are no-ops.
"""
from __future__ import annotations

import sys
import time

from fastapi import APIRouter

import config

router = APIRouter(tags=["system"])

# Release zip name per platform (matches the CI upload names). Linux has no build.
_ASSETS = {
    "win32": "DJOrganizer_windows.zip",
    "darwin": "DJOrganizer_mac_apple-silicon.zip",
}


@router.get("/api/meta")
async def meta():
    repo = config.GITHUB_REPO
    base = f"https://github.com/{repo}" if repo else ""
    plat = "windows" if sys.platform == "win32" else "mac" if sys.platform == "darwin" else "linux"
    asset = _ASSETS.get(sys.platform)
    # GitHub's stable redirect always points at the newest release's asset.
    download_url = f"{base}/releases/latest/download/{asset}" if base and asset else f"{base}/releases/latest"
    return {
        "version": config.APP_VERSION,
        "repo": repo,
        "github_url": base,
        "issues_url": f"{base}/issues/new" if base else "",
        "releases_url": f"{base}/releases/latest" if base else "",
        "download_url": download_url,
        "platform": plat,
        "contact_email": config.CONTACT_EMAIL,
    }

# Module-level shared state: the launcher arms this and runs the watchdog thread.
_state = {"armed": False, "last_ping": 0.0}


@router.post("/api/heartbeat")
async def heartbeat(bye: int = 0):
    """Frontend keep-alive. ``bye=1`` (sent via sendBeacon on unload) expires it
    immediately so the server can exit promptly on tab close."""
    _state["last_ping"] = 0.0 if bye else time.time()
    return {"ok": True}


def arm() -> None:
    _state["armed"] = True
    _state["last_ping"] = time.time()


def is_stale(timeout_s: float) -> bool:
    return _state["armed"] and (time.time() - _state["last_ping"] > timeout_s)
