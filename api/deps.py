"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Request


def get_db(request: Request):
    """Return the process-wide aiosqlite connection held on app.state."""
    return request.app.state.db


async def get_read_db(request: Request):
    """Borrow a read-only connection for the heavy list endpoints. WAL lets these
    run alongside each other and the writer, so the artists, tracks and stats
    calls at startup no longer queue behind one another."""
    pool = request.app.state.read_pool
    conn = await pool.get()
    try:
        yield conn
    finally:
        pool.put_nowait(conn)
