"""'Tracks I already own' - folder scan endpoint.

READ-ONLY: receives a list of file *names* (collected by the browser's folder
picker) and fuzzily matches them against the library. It never touches files,
and it changes nothing in the DB - applying the result reuses PATCH /tracks/bulk.
"""
from __future__ import annotations

import asyncio
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

    progress_cb = None
    if token:
        SCAN_PROGRESS[token] = 0
        def progress_cb(done, total):  # noqa: E306 - called from the worker thread
            SCAN_PROGRESS[token] = int(100 * done / total) if total else 100
    try:
        matches = await asyncio.to_thread(
            match.match_filenames, candidates, filenames, floor, progress_cb
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
    return await _match_against_library(
        db, body.filenames, body.floor, body.artist_id, body.token
    )


@router.post("/pick")
async def pick(artist_id: Optional[int] = None, floor: Optional[int] = None,
               token: Optional[str] = None, db=Depends(get_db)):
    """Fallback for browsers without the File System Access API: open a native OS
    folder dialog on this machine, list names (read-only), and match them."""
    try:
        path = await asyncio.get_event_loop().run_in_executor(None, folder_pick.ask_directory)
    except Exception as e:  # noqa: BLE001 - e.g. no display / tkinter missing
        raise HTTPException(500, f"Could not open folder dialog: {e}")
    if not path:
        return {"cancelled": True}
    names = folder_pick.walk_audio_names(path)
    result = await _match_against_library(db, names, floor, artist_id, token)
    result["cancelled"] = False
    return result
