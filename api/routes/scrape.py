"""Scraping endpoints: start runs, control them, stream progress via SSE."""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.deps import get_db
from api.scrape_manager import DONE, manager
from db import queries

router = APIRouter(prefix="/api/scrape", tags=["scrape"])


class ImportBody(BaseModel):
    source_url: Optional[str] = None
    artist_urls: Optional[list[str]] = None
    mode: str = "batch"  # 'batch' | 'interactive'
    rescan: bool = True  # False = reuse last saved list for source_url (skip follow-page scrape)


class UpdateBody(BaseModel):
    mode: str = "full"  # 'full' | 'priority' | 'selected' | 'single' | 'notes'
    artist_ids: Optional[list[int]] = None
    threshold: Optional[int] = None
    notes_query: Optional[str] = None  # for mode='notes': only artists whose notes match


class DecisionBody(BaseModel):
    action: str = "add"            # 'add' | 'skip'
    priority: int = 0
    aliases: list[str] = []
    notes: str = ""
    scrape: bool = True            # False = add to library now, scrape later
    skip_all_unstarred: bool = False


@router.post("/import")
async def start_import(body: ImportBody, db=Depends(get_db)):
    sid = await manager.start_import(
        db, mode=body.mode, source_url=body.source_url,
        artist_urls=body.artist_urls, rescan=body.rescan,
    )
    return {"session_id": sid}


@router.post("/update")
async def start_update(body: UpdateBody, db=Depends(get_db)):
    sid = await manager.start_update(
        db, mode=body.mode, artist_ids=body.artist_ids, threshold=body.threshold,
        notes_query=body.notes_query,
    )
    return {"session_id": sid}


@router.get("/progress/{sid}")
async def progress(sid: int):
    job = manager.get(sid)
    if not job:
        # Session not live (already finished or server restarted).
        async def empty():
            yield f"data: {json.dumps({'type': 'not_live', 'session_id': sid})}\n\n"
        return StreamingResponse(empty(), media_type="text/event-stream")

    async def event_stream():
        while True:
            try:
                event = await asyncio.wait_for(job.events.get(), timeout=20.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"  # comment line keeps the connection open
                continue
            if event is DONE:
                yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/pause/{sid}")
async def pause(sid: int, db=Depends(get_db)):
    ok = manager.pause(sid)
    # Persist 'paused' so the run is resumable even if the app is closed mid-pause.
    await queries.update_session(db, sid, status="paused")
    return {"ok": ok}


@router.post("/resume/{sid}")
async def resume(sid: int, db=Depends(get_db)):
    ok = manager.resume(sid)
    await queries.update_session(db, sid, status="in_progress")
    return {"ok": ok}


@router.post("/skip/{sid}")
async def skip(sid: int):
    return {"ok": manager.skip(sid)}


@router.post("/decide/{sid}")
async def decide(sid: int, body: DecisionBody):
    return {"ok": manager.decide(sid, body.model_dump())}


@router.post("/abandon/{sid}")
async def abandon(sid: int, save: bool = False, db=Depends(get_db)):
    """Stop a run. ``?save=true`` keeps it resumable (paused) instead of abandoned."""
    ok = manager.abandon(sid, save=save)
    if not ok:  # not live; just mark in DB
        await queries.update_session(db, sid, status="paused" if save else "abandoned")
    return {"ok": True}


@router.post("/stop-bg/{sid}")
async def stop_bg(sid: int, db=Depends(get_db)):
    """Stop the background scrape worker after the current artist (interactive
    imports). Queued + un-reviewed artists stay resumable."""
    ok = manager.stop_worker(sid)
    if not ok:  # not live; just ensure it's left resumable
        await queries.update_session(db, sid, status="paused")
    return {"ok": True}


@router.get("/sessions")
async def sessions(db=Depends(get_db)):
    return await queries.list_sessions(db)


@router.get("/log")
async def log(limit: int = 100, db=Depends(get_db)):
    return await queries.list_logs(db, limit=limit)
