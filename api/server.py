"""FastAPI application: lifespan-managed DB connection, static frontend, routes."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
from db import queries
from db.schema import connect, init_db
from api.routes import artists, owned, scrape, sessions, settings, system, tracks


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.db = await connect()
    # No scrape run can be live at process start, so any session left
    # "in_progress" by a previous hard exit is orphaned - flip it to "paused"
    # so the resume banner / "Continue last import" can recover it (and drain
    # any artists that were queued for background scraping).
    await queries.reconcile_orphaned_sessions(app.state.db)
    # Repair artist names an older scraper saved as "Visit <name>'s profile"
    # (it grabbed the followed-card title tooltip instead of the link text).
    await queries.repair_visit_profile_names(app.state.db)
    # One-time bump: the per-artist Check-folder floor shipped at 80 and was
    # raised to 88. Only update the stored value if it's still the old default
    # (never clobber a value the user has since chosen themselves).
    if str(await queries.get_setting(app.state.db, "owned_match_floor_artist", "88")) == "80":
        await queries.set_settings(app.state.db, {"owned_match_floor_artist": "88"})
    try:
        yield
    finally:
        await app.state.db.close()


app = FastAPI(title="DJ Organizer", lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """Local app served from disk - never let the browser cache stale assets so a
    new app version always loads fresh frontend code."""
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


app.include_router(artists.router)
app.include_router(tracks.router)
app.include_router(scrape.router)
app.include_router(sessions.router)
app.include_router(settings.router)
app.include_router(owned.router)
app.include_router(system.router)


def get_db(request: Request):
    return request.app.state.db


# Mount the SPA. API routers are registered first so they take precedence.
_frontend = config.frontend_dir()
if _frontend.exists():
    app.mount("/static", StaticFiles(directory=str(_frontend)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(str(_frontend / "index.html"))
