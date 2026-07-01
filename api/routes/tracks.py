"""Track endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_db
from db import queries

router = APIRouter(prefix="/api/tracks", tags=["tracks"])


class TrackPatch(BaseModel):
    is_checked: Optional[int] = None
    is_revisit: Optional[int] = None
    is_deleted: Optional[int] = None
    is_owned: Optional[int] = None
    notes: Optional[str] = None
    group: Optional[bool] = None  # apply to all copies in the merged group


class BulkPatch(BaseModel):
    ids: list[int]
    is_checked: Optional[int] = None
    is_revisit: Optional[int] = None
    is_deleted: Optional[int] = None
    is_owned: Optional[int] = None
    notes: Optional[str] = None
    group: Optional[bool] = None  # apply to all copies in the merged group


def _parse_ids(value: Optional[str]) -> Optional[list[int]]:
    if not value:
        return None
    out = []
    for part in value.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out or None


@router.get("")
async def list_tracks(
    artist_id: Optional[str] = Query(None, description="comma-separated artist ids"),
    is_checked: Optional[int] = None,
    is_revisit: Optional[int] = None,
    include_deleted: bool = False,
    is_deleted: Optional[int] = None,
    include_owned: bool = False,
    is_owned: Optional[int] = None,
    is_repost: Optional[int] = None,
    priority_min: Optional[int] = None,
    priority_in: Optional[str] = Query(None, description="comma-separated exact star levels (0-4)"),
    search: Optional[str] = None,
    sort: str = "priority_new_first",
    limit: int = 500,
    offset: int = 0,
    db=Depends(get_db),
):
    artist_ids = _parse_ids(artist_id)
    stars = _parse_ids(priority_in)  # "0,1,2" → [0,1,2]; level 0 = no stars
    # Merge cross-artist repost duplicates only in the unfiltered (no specific
    # artist) view; per-artist views keep every row intact.
    merge = not artist_ids
    items = await queries.list_tracks(
        db, artist_ids=artist_ids, is_checked=is_checked, is_revisit=is_revisit,
        include_deleted=include_deleted, is_deleted=is_deleted,
        include_owned=include_owned, is_owned=is_owned,
        is_repost=is_repost, priority_min=priority_min, priority_in=stars,
        search=search, sort=sort, merge=merge, limit=limit, offset=offset,
    )
    total = await queries.count_tracks(
        db, artist_ids=artist_ids, is_checked=is_checked, is_revisit=is_revisit,
        include_deleted=include_deleted, is_deleted=is_deleted,
        include_owned=include_owned, is_owned=is_owned,
        is_repost=is_repost, priority_min=priority_min, priority_in=stars,
        search=search, merge=merge,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset, "merged": merge}


async def _affected_artists(db, track_ids: list[int], propagate_group: bool) -> list[dict]:
    """Refreshed sidebar rows (with recomputed counts) for the artists a track
    action touched - lets the client update just those instead of all artists."""
    ids = await queries.artist_ids_for_tracks(db, track_ids, propagate_group=propagate_group)
    return await queries.list_artists(db, artist_ids=ids) if ids else []


@router.patch("/bulk")
async def bulk_patch(body: BulkPatch, db=Depends(get_db)):
    fields = {k: v for k, v in body.model_dump(exclude={"ids", "group"}).items() if v is not None}
    n = await queries.bulk_set_tracks(db, body.ids, fields, propagate_group=bool(body.group))
    artists = await _affected_artists(db, body.ids, bool(body.group))
    return {"updated": n, "affected_artists": artists}


@router.patch("/{track_id}")
async def patch_track(track_id: int, body: TrackPatch, db=Depends(get_db)):
    fields = {k: v for k, v in body.model_dump(exclude={"group"}).items() if v is not None}
    track = await queries.set_track_fields(db, track_id, fields, propagate_group=bool(body.group))
    if not track:
        raise HTTPException(404, "track not found or no valid fields")
    artists = await _affected_artists(db, [track_id], bool(body.group))
    return {**track, "affected_artists": artists}
