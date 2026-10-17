"""Artist endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_db
from db import queries
from scraper.platforms.soundcloud import validate_artist_url

router = APIRouter(prefix="/api/artists", tags=["artists"])


class ArtistCreate(BaseModel):
    url: str = Field(max_length=2_000)
    name: Optional[str] = Field(None, max_length=200)
    priority: int = Field(0, ge=0, le=4)


class ArtistPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    priority: Optional[int] = Field(None, ge=0, le=4)
    is_active: Optional[int] = Field(None, ge=0, le=1)
    is_deleted: Optional[int] = Field(None, ge=0, le=1)
    include_reposts: Optional[int] = Field(None, ge=-1, le=1)  # -1 = inherit
    repost_limit: Optional[int] = Field(None, ge=-1, le=10_000)  # -1 = inherit, 0 = no limit
    notes: Optional[str] = Field(None, max_length=10_000)
    aliases: Optional[list[str]] = Field(None, max_length=50)


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
    try:
        url = validate_artist_url(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    name = body.name or url.rstrip("/").split("/")[-1]
    artist = await queries.add_artist(db, name=name, url=url, priority=body.priority)
    return artist


@router.patch("/{artist_id}")
async def patch_artist(artist_id: int, body: ArtistPatch, db=Depends(get_db)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "name" in fields and not fields["name"].strip():
        raise HTTPException(400, "Artist name can't be empty")
    if "aliases" in fields:
        fields["aliases"] = [a.strip()[:200] for a in fields["aliases"] if a.strip()]
    artist = await queries.update_artist(db, artist_id, fields)
    if not artist:
        raise HTTPException(404, "artist not found")
    return artist


@router.delete("/{artist_id}")
async def delete_artist(artist_id: int, db=Depends(get_db)):
    await queries.soft_delete_artist(db, artist_id)
    return {"ok": True}
