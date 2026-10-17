"""Scraping endpoints: start runs, control them, stream progress via SSE."""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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
    threshold: Optional[int] = Field(None, ge=0, le=4)
    notes_query: Optional[str] = None  # for mode='notes': only artists whose notes match


class DecisionBody(BaseModel):
    action: str = "add"            # 'add' | 'skip'
    priority: int = Field(0, ge=0, le=4)
    aliases: list[str] = []
    notes: str = Field("", max_length=10_000)
    scrape: bool = True            # False = add to library now, scrape later
    skip_all_unstarred: bool = False


@router.post("/import")
async def start_import(body: ImportBody, db=Depends(get_db)):
    try:
        sid = await manager.start_import(
            db, mode=body.mode, source_url=body.source_url,
            artist_urls=body.artist_urls, rescan=body.rescan,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"session_id": sid}


@router.post("/update")
async def start_update(body: UpdateBody, db=Depends(get_db)):
    sid = await manager.start_update(
        db, mode=body.mode, artist_ids=body.artist_ids, threshold=body.threshold,
        notes_query=body.notes_query,
    )
    return {"session_id": sid}


@router.get("/results")
async def run_results(db=Depends(get_db)):
    """Recent saved run results (what each run found new), newest first."""
    return await queries.list_run_results(db, limit=50)


@router.delete("/results/{run_id}")
async def delete_run_result(run_id: int, db=Depends(get_db)):
    """Retire a saved run (nothing left to review)."""
    await queries.delete_run_result(db, run_id)
    return {"deleted": True}


class EnqueueBody(BaseModel):
    artist_ids: list[int]


@router.post("/enqueue/{sid}")
async def enqueue(sid: int, body: EnqueueBody, db=Depends(get_db)):
    """Append artists to a running batch update's queue."""
    return await manager.enqueue_update(db, sid, body.artist_ids)


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
async def log(limit: int = Query(100, ge=1, le=10_000), db=Depends(get_db)):
    return await queries.list_logs(db, limit=limit)
