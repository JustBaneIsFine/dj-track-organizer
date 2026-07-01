"""Database connection management + migration runner.

On startup, ``init_db()``:
  1. ensures the ``db_version`` table exists,
  2. applies any migration in ``db/migrations/*.sql`` whose numeric prefix is
     greater than the current version (each in its own transaction),
  3. seeds default settings for any missing keys.

Adding a schema change = drop a new ``NNN_description.sql`` file in the
migrations dir. Nothing else to wire up.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone

import aiosqlite

import config

_VERSION_RE = re.compile(r"^(\d+)_")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def connect() -> aiosqlite.Connection:
    """Open a connection with row dicts and FK enforcement enabled."""
    conn = await aiosqlite.connect(config.db_path())
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA busy_timeout = 5000")  # wait, don't fail, on locks
    return conn


async def init_db() -> None:
    """Run migrations and seed defaults. Safe to call on every startup."""
    conn = await connect()
    try:
        await _ensure_version_table(conn)
        await _run_migrations(conn)
        await _seed_settings(conn)
        from db import queries  # deferred: avoids a circular import at module load
        await queries.backfill_group_keys(conn)
    finally:
        await conn.close()


async def _ensure_version_table(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS db_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL,
            description TEXT
        )
        """
    )
    await conn.commit()


async def _current_version(conn: aiosqlite.Connection) -> int:
    async with conn.execute("SELECT MAX(version) AS v FROM db_version") as cur:
        row = await cur.fetchone()
    return row["v"] if row and row["v"] is not None else 0


async def _run_migrations(conn: aiosqlite.Connection) -> None:
    current = await _current_version(conn)
    pending = []
    for path in sorted(config.migrations_dir().glob("*.sql")):
        m = _VERSION_RE.match(path.name)
        if m and int(m.group(1)) > current:
            pending.append((int(m.group(1)), path))
    if not pending:
        return

    await _backup_db(conn, current)
    for version, path in pending:
        sql = path.read_text(encoding="utf-8")
        try:
            await conn.executescript(sql)
            await conn.execute(
                "INSERT INTO db_version (version, applied_at, description) VALUES (?, ?, ?)",
                (version, now_iso(), path.stem),
            )
            await conn.commit()
            print(f"[db] applied migration {path.name}")
        except Exception:
            await conn.rollback()
            print(
                f"[db] migration {path.name} failed; database left at v{current}. "
                f"A pre-upgrade backup is in {config.app_dir() / 'backups'}."
            )
            raise


async def _backup_db(conn: aiosqlite.Connection, version: int, keep: int = 5) -> None:
    src = config.db_path()
    if not src.exists():
        return
    try:
        await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    bdir = config.app_dir() / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dst = bdir / f"dj_organizer.pre-v{version + 1}.{ts}.db"
    try:
        shutil.copy2(src, dst)
        print(f"[db] backup before migrate: {dst}")
    except Exception as e:
        print(f"[db] backup skipped: {e}")
        return
    backups = sorted(bdir.glob("dj_organizer.pre-v*.db"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)


async def _seed_settings(conn: aiosqlite.Connection) -> None:
    for key, value in config.DEFAULT_SETTINGS.items():
        await conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    await conn.commit()
