"""Session inspection + resume-on-launch support."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.scrape_manager import manager
from db import queries

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(db=Depends(get_db)):
    return await queries.list_sessions(db)


@router.get("/resumable")
async def resumable(db=Depends(get_db)):
    """The most recent paused session, if any (for the resume banner)."""
    return await queries.get_resumable_session(db)


@router.get("/{sid}")
async def get_session(sid: int, db=Depends(get_db)):
    s = await queries.get_session(db, sid)
    if not s:
        raise HTTPException(404, "session not found")
    s["live"] = manager.get(sid) is not None
    return s


@router.post("/{sid}/resume")
async def resume_session(sid: int, db=Depends(get_db)):
    """Re-launch a paused session as a fresh background job from where it left off."""
    s = await queries.get_session(db, sid)
    if not s:
        raise HTTPException(404, "session not found")
    if manager.get(sid):
        return {"session_id": sid, "already_live": True}
    sid2 = await manager.relaunch(db, sid)
    return {"session_id": sid2}
