"""'Tracks I already own' - folder scan endpoints.

Receives file *names* (native dialog walk, or the browser's folder picker) and
fuzzily matches them against the library. Files are never modified; /open hands a
matched file to the OS default player on explicit request. Applying the result
changes nothing here - it reuses PATCH /tracks/bulk.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import folder_pick
import match
from api.deps import get_db
from db import queries

router = APIRouter(prefix="/api/owned", tags=["owned"])

# Live matching progress keyed by a client-supplied token, so the UI can poll a
# determinate progress bar while a (potentially large) scan runs off the event loop.
# Values are 0-100; an absent token means "no scan / done".
SCAN_PROGRESS: dict[str, int] = {}

# basename -> abspath from the most recent native folder scan, so a match can be
# opened in the default player. Only /open reads it, and /open only accepts names
# from this map (never a path from the client). Browser-picker scans clear it.
LAST_SCAN_PATHS: dict[str, str] = {}

# Names from a native /pick, held until the client matches them via /scan-pending.
# Keeps folder-picking and matching as two steps so the UI can show each separately.
PENDING_SCANS: dict[str, list[str]] = {}


class ScanBody(BaseModel):
    filenames: list[str]
    floor: Optional[int] = None  # lowest score to return; defaults to settings floor
    artist_id: Optional[int] = None  # scope the match to a single artist's tracks
    token: Optional[str] = None  # progress-poll token (see /progress)


async def _match_against_library(
    db, filenames: list[str], floor: Optional[int],
    artist_id: Optional[int] = None, token: Optional[str] = None,
) -> dict:
    """Shared by /scan and /pick: match filenames vs not-yet-owned tracks.

    When ``artist_id`` is given, only that artist's tracks are candidates - used by
    the per-artist "Check folder" which can match at a more lenient floor since the
    pool is small and already artist-scoped. Matching is CPU-bound, so it runs in a
    worker thread (keeps the server + SSE responsive) and reports progress via
    ``SCAN_PROGRESS[token]``.
    """
    if floor is None:
        floor = int(await queries.get_setting(db, "owned_match_floor", 90))
    tracks = await queries.list_tracks(
        db, include_owned=True, limit=100000,
        artist_ids=[artist_id] if artist_id else None,
    )
    candidates = [t for t in tracks if not t["is_owned"]]
    # Attach each artist's aliases so alias-prefixed filenames ("Alias - Title") match.
    artists = await queries.list_artists(db, include_deleted=True)
    alias_map = {a["id"]: a.get("aliases") or [] for a in artists}
    for t in candidates:
        t["artist_aliases"] = alias_map.get(t["artist_id"], [])

    # (track, filename) pairs the user said aren't the same song - never re-match them.
    rejected = await queries.get_owned_rejections(db, [t["id"] for t in candidates])

    progress_cb = None
    if token:
        SCAN_PROGRESS[token] = 0
        def progress_cb(done, total):  # noqa: E306 - called from the worker thread
            SCAN_PROGRESS[token] = int(100 * done / total) if total else 100
    try:
        matches = await asyncio.to_thread(
            match.match_filenames, candidates, filenames, floor, progress_cb, rejected
        )
    finally:
        if token:
            SCAN_PROGRESS.pop(token, None)
    return {
        "scanned": sum(1 for f in filenames if match.is_audio(f)),
        "matches": matches,
    }


@router.get("/progress")
async def progress(token: str):
    """Poll matching progress (0-100) for a scan token. 100 once it's gone/done."""
    return {"percent": SCAN_PROGRESS.get(token, 100)}


@router.post("/scan")
async def scan(body: ScanBody, db=Depends(get_db)):
    """Match a list of file names (from the browser folder picker) - read-only."""
    LAST_SCAN_PATHS.clear()  # browser picker gives names only, no paths to open
    result = await _match_against_library(
        db, body.filenames, body.floor, body.artist_id, body.token
    )
    result["has_paths"] = False
    return result


@router.post("/pick")
async def pick():
    """Open a native OS folder dialog and list audio names (read-only). Returns a
    token to match via /scan-pending, so the UI shows 'choosing' and 'matching' as
    separate steps. Also remembers name->path so a match can be opened in a player."""
    try:
        path = await asyncio.get_event_loop().run_in_executor(None, folder_pick.ask_directory)
    except Exception as e:  # noqa: BLE001 - e.g. no dialog available
        raise HTTPException(500, f"Could not open folder dialog: {e}")
    if not path:
        return {"cancelled": True}
    names, paths = folder_pick.walk_audio_paths(path)
    LAST_SCAN_PATHS.clear()
    LAST_SCAN_PATHS.update(paths)
    PENDING_SCANS.clear()
    pending = uuid.uuid4().hex
    PENDING_SCANS[pending] = names
    return {"cancelled": False, "pending": pending, "count": len(names), "has_paths": True}


class PendingBody(BaseModel):
    pending: str
    floor: Optional[int] = None
    artist_id: Optional[int] = None
    token: Optional[str] = None  # progress-poll token (see /progress)


@router.post("/scan-pending")
async def scan_pending(body: PendingBody, db=Depends(get_db)):
    """Match the names from a native /pick (by its pending token) against the library."""
    names = PENDING_SCANS.pop(body.pending, None)
    if names is None:
        raise HTTPException(404, "Scan expired; please choose the folder again")
    result = await _match_against_library(db, names, body.floor, body.artist_id, body.token)
    result["has_paths"] = True
    return result


class RejectItem(BaseModel):
    track_id: int
    filename: str


class RejectBody(BaseModel):
    pairs: list[RejectItem]


@router.post("/reject")
async def reject(body: RejectBody, db=Depends(get_db)):
    """Remember (track, filename) pairs as 'not the same song', so future scans skip
    those exact pairs. The track can still match a different file."""
    n = await queries.add_owned_rejections(db, [(p.track_id, p.filename) for p in body.pairs])
    return {"rejected": n}


class OpenBody(BaseModel):
    filename: str


@router.post("/open")
async def open_file(body: OpenBody):
    """Open a scanned file in the OS default player. Only accepts basenames from
    the last native scan, so no arbitrary path can be opened."""
    path = LAST_SCAN_PATHS.get(body.filename)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "File not known from the last folder scan")
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 - user-chosen folder, OS default handler
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Could not open the file: {e}")
    return {"ok": True}
