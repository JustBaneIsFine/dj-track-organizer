"""'Tracks I already own' - folder checks.

Receives file *names* (native dialog walk, or the browser's folder picker) and
fuzzily matches them against the library. The matches are saved as a check and
decided one at a time, so a review survives closing the app. Files are never
modified; /open hands a matched file to the OS default player on request.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from typing import Literal, Optional

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

# A native /pick's result, held until the client matches it via /scan-pending. Keeps
# folder-picking and matching as two steps so the UI can show each separately.
PENDING_SCANS: dict[str, dict] = {}


class ScanBody(BaseModel):
    filenames: list[str]
    root: str = "Folder"  # the picked folder's name (the browser picker has no path)
    floor: Optional[int] = None  # lowest score to keep; defaults to settings floor
    artist_id: Optional[int] = None  # scope the match to a single artist's tracks
    token: Optional[str] = None  # progress-poll token (see /progress)


async def _match_into_check(
    db, root: str, filenames: list[str], paths: dict[str, str], floor: Optional[int],
    artist_id: Optional[int] = None, token: Optional[str] = None,
) -> dict:
    """Match filenames vs not-yet-owned tracks and save the hits as a check.

    Rechecking a folder that still has an open check adds to that check. With
    ``artist_id`` only that artist's tracks are candidates (the per-artist check,
    which runs at a more lenient floor). Matching is CPU-bound, so it runs in a
    worker thread and reports progress via ``SCAN_PROGRESS[token]``.
    """
    if floor is None:
        floor = int(await queries.get_setting(db, "owned_match_floor", 90))
    floor = min(100, max(0, floor))
    exact_at = int(await queries.get_setting(db, "owned_match_threshold", 98))
    tracks = await queries.list_tracks(
        db, include_owned=True, limit=100000,
        artist_ids=[artist_id] if artist_id else None,
    )
    candidates = [t for t in tracks if not t["is_owned"]]
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
            match.match_filenames, candidates, filenames, floor, progress_cb, rejected, exact_at
        )
    finally:
        if token:
            SCAN_PROGRESS.pop(token, None)
    for m in matches:
        m["path"] = paths.get(m["filename"])

    scanned = sum(1 for f in filenames if match.is_audio(f))
    check_id = await queries.find_open_check(db, root, artist_id)
    if check_id is None:
        if not matches:
            return {"scanned": scanned, "check_id": None, "added": 0}
        check_id = await queries.create_check(db, root=root, artist_id=artist_id, file_count=scanned)
    added = await queries.add_check_items(db, check_id, matches)
    return {"scanned": scanned, "check_id": check_id, "added": added}


@router.get("/progress")
async def progress(token: str):
    """Poll matching progress (0-100) for a scan token. 100 once it's gone/done."""
    return {"percent": SCAN_PROGRESS.get(token, 100)}


@router.post("/scan")
async def scan(body: ScanBody, db=Depends(get_db)):
    """Match a list of file names from the browser folder picker (names only)."""
    return await _match_into_check(
        db, body.root, body.filenames, {}, body.floor, body.artist_id, body.token
    )


@router.post("/pick")
async def pick():
    """Open a native OS folder dialog and list audio names (read-only). Returns a
    token to match via /scan-pending. Paths are kept so a match can be played."""
    try:
        path = await asyncio.get_event_loop().run_in_executor(None, folder_pick.ask_directory)
    except Exception as e:  # noqa: BLE001 - e.g. no dialog available
        raise HTTPException(500, f"Could not open folder dialog: {e}")
    if not path:
        return {"cancelled": True}
    names, paths = folder_pick.walk_audio_paths(path)
    PENDING_SCANS.clear()
    pending = uuid.uuid4().hex
    PENDING_SCANS[pending] = {"root": os.path.normpath(path), "names": names, "paths": paths}
    return {"cancelled": False, "pending": pending, "count": len(names)}


class PendingBody(BaseModel):
    pending: str
    floor: Optional[int] = None
    artist_id: Optional[int] = None
    token: Optional[str] = None  # progress-poll token (see /progress)


@router.post("/scan-pending")
async def scan_pending(body: PendingBody, db=Depends(get_db)):
    """Match the names from a native /pick (by its pending token) against the library."""
    p = PENDING_SCANS.pop(body.pending, None)
    if p is None:
        raise HTTPException(404, "Scan expired; choose the folder again")
    return await _match_into_check(
        db, p["root"], p["names"], p["paths"], body.floor, body.artist_id, body.token
    )


@router.get("/checks")
async def list_checks(db=Depends(get_db)):
    return await queries.list_checks(db)


@router.get("/checks/{check_id}")
async def get_check(check_id: int, db=Depends(get_db)):
    c = await queries.get_check(db, check_id)
    if not c:
        raise HTTPException(404, "Check not found")
    c["items"] = await queries.list_check_items(db, check_id)
    return c


@router.delete("/checks/{check_id}")
async def delete_check(check_id: int, db=Depends(get_db)):
    await queries.delete_check(db, check_id)
    return {"deleted": True}


class DecideBody(BaseModel):
    item_ids: list[int]
    decision: Literal["same", "different", "skipped"]


@router.post("/decide")
async def decide(body: DecideBody, db=Depends(get_db)):
    return {"decided": await queries.decide_check_items(db, body.item_ids, body.decision)}


class UndoBody(BaseModel):
    item_ids: list[int]


@router.post("/undo")
async def undo(body: UndoBody, db=Depends(get_db)):
    return {"undone": await queries.undo_check_items(db, body.item_ids)}


class OpenBody(BaseModel):
    item_id: int


@router.post("/open")
async def open_file(body: OpenBody, db=Depends(get_db)):
    """Open a matched file in the OS default player. Only a path a native scan stored
    for a check item can be opened, never one sent by the client."""
    path = await queries.check_item_path(db, body.item_id)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "File not found. It may have moved since the check.")
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
