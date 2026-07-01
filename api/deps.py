"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Request


def get_db(request: Request):
    """Return the process-wide aiosqlite connection held on app.state."""
    return request.app.state.db
