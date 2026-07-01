"""Artist endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_db
from db import queries
from scraper.platforms.soundcloud import normalize_artist_url

router = APIRouter(prefix="/api/artists", tags=["artists"])


class ArtistCreate(BaseModel):
    url: str
    name: Optional[str] = None
    priority: int = 0


class ArtistPatch(BaseModel):
    name: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[int] = None
    is_deleted: Optional[int] = None
    include_reposts: Optional[int] = None
    repost_limit: Optional[int] = None
    notes: Optional[str] = None
    aliases: Optional[list[str]] = None


def _parse_ids(value: Optional[str]) -> Optional[list[int]]:
    if not value:
        return None
    out = [int(p) for p in value.split(",") if p.strip().isdigit()]
    return out or None


@router.get("")
async def list_artists(
    include_deleted: bool = False,
    search: Optional[str] = None,
    sort: str = "priority",
    artist_id: Optional[str] = None,  # comma-separated: refresh just these
    unscraped: bool = False,
    db=Depends(get_db),
):
    return await queries.list_artists(
        db, include_deleted=include_deleted, search=search, sort=sort,
        artist_ids=_parse_ids(artist_id), unscraped=unscraped,
    )


@router.post("")
async def add_artist(body: ArtistCreate, db=Depends(get_db)):
    url = normalize_artist_url(body.url)
    name = body.name or url.rstrip("/").split("/")[-1]
    artist = await queries.add_artist(db, name=name, url=url, priority=body.priority)
    return artist


@router.patch("/{artist_id}")
async def patch_artist(artist_id: int, body: ArtistPatch, db=Depends(get_db)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    artist = await queries.update_artist(db, artist_id, fields)
    if not artist:
        raise HTTPException(404, "artist not found")
    return artist


@router.delete("/{artist_id}")
async def delete_artist(artist_id: int, db=Depends(get_db)):
    await queries.soft_delete_artist(db, artist_id)
    return {"ok": True}
