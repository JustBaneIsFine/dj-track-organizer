"""Settings + data-management endpoints (stats, export, import, purge)."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_db
from db import queries
from db.schema import connect
import config

router = APIRouter(tags=["settings"])


class SettingsPatch(BaseModel):
    updates: dict[str, Any]


@router.get("/api/settings")
async def get_settings(db=Depends(get_db)):
    return await queries.get_settings(db)


# Whole-number settings: (lowest, highest, label shown in the error).
_INT_SETTINGS = {
    "scrape_delay_min_ms": (0, 600_000, "Delay min (ms)"),
    "scrape_delay_max_ms": (0, 600_000, "Delay max (ms)"),
    "scroll_pause_ms": (0, 60_000, "Scroll pause (ms)"),
    "scroll_step_px": (50, 5_000, "Scroll step (px)"),
    "scrape_stop_known_count": (1, 1_000, "Stop after N known in a row"),
    "repost_limit_default": (0, 10_000, "Default repost limit"),
    "priority_update_threshold": (1, 4, "Priority threshold"),
    "max_tracks_per_artist": (0, 100_000, "Max tracks per artist"),
    "owned_match_threshold": (0, 100, "Match strictness"),
    "owned_match_floor": (0, 100, "Match floor"),
    "owned_match_floor_artist": (0, 100, "Artist match floor"),
    "owned_fuzzy_from": (0, 100, "Show fuzzy matches from"),
    "saved_runs_keep": (1, 100, "Saved runs to keep"),
    "track_page_size": (1, 5_000, "Tracks per view"),
    "ui_scale": (50, 300, "UI scale"),
}


def _clean_settings(updates: dict[str, Any], current: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in updates.items():
        if not k:
            continue
        v = "" if v is None else str(v).strip()
        if k in _INT_SETTINGS:
            lo, hi, label = _INT_SETTINGS[k]
            try:
                n = int(v)
            except ValueError:
                n = None
            if n is None or not lo <= n <= hi:
                raise HTTPException(400, f"{label} must be a whole number from {lo} to {hi}.")
            v = str(n)
        out[k] = v
    merged = {**current, **out}
    try:
        lo_ms = int(merged.get("scrape_delay_min_ms", 0))
        hi_ms = int(merged.get("scrape_delay_max_ms", 0))
    except ValueError:
        lo_ms = hi_ms = 0
    if lo_ms > hi_ms:
        raise HTTPException(400, "Delay min can't be larger than Delay max.")
    return out


@router.patch("/api/settings")
async def patch_settings(body: SettingsPatch, db=Depends(get_db)):
    current = await queries.get_settings(db)
    await queries.set_settings(db, _clean_settings(body.updates, current))
    return await queries.get_settings(db)


def _parse_ids(value):
    if not value:
        return None
    out = [int(p) for p in str(value).split(",") if p.strip().isdigit()]
    return out or None


@router.get("/api/stats")
async def stats(artist_id: str | None = None, run_id: int | None = None, db=Depends(get_db)):
    track_ids = None
    if run_id:
        run = await queries.get_run_result(db, run_id)
        track_ids = (run["track_ids"] if run else None) or [-1]
    return await queries.stats(db, artist_ids=_parse_ids(artist_id), track_ids=track_ids)


@router.get("/api/export")
async def export_all(db=Depends(get_db)):
    return await queries.export_all(db)


@router.post("/api/import")
async def import_all(data: dict):
    # Own connection: a commit on the shared one mid-restore would make it partial.
    conn = await connect()
    try:
        counts = await queries.import_all(conn, data)
    except (ValueError, sqlite3.Error) as e:
        raise HTTPException(400, f"Nothing was changed. The backup could not be read ({e}).")
    finally:
        await conn.close()
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
