"""Settings + data-management endpoints (stats, export, import, purge)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_db
from db import queries
import config

router = APIRouter(tags=["settings"])


class SettingsPatch(BaseModel):
    updates: dict[str, Any]


@router.get("/api/settings")
async def get_settings(db=Depends(get_db)):
    return await queries.get_settings(db)


@router.patch("/api/settings")
async def patch_settings(body: SettingsPatch, db=Depends(get_db)):
    await queries.set_settings(db, {k: str(v) for k, v in body.updates.items()})
    return await queries.get_settings(db)


def _parse_ids(value):
    if not value:
        return None
    out = [int(p) for p in str(value).split(",") if p.strip().isdigit()]
    return out or None


@router.get("/api/stats")
async def stats(artist_id: str | None = None, db=Depends(get_db)):
    return await queries.stats(db, artist_ids=_parse_ids(artist_id))


@router.get("/api/export")
async def export_all(db=Depends(get_db)):
    return await queries.export_all(db)


@router.post("/api/import")
async def import_all(data: dict, db=Depends(get_db)):
    counts = await queries.import_all(db, data)
    return {"restored": counts}


@router.delete("/api/purge/deleted")
async def purge_deleted(db=Depends(get_db)):
    return await queries.purge_deleted(db)


@router.post("/api/reset")
async def reset_all(db=Depends(get_db)):
    await queries.reset_all(db)
    # Re-seed default settings so the app stays usable.
    await queries.set_settings(db, config.DEFAULT_SETTINGS)
    return {"ok": True}
