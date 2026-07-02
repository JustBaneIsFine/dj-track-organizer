"""All database reads/writes live here. No raw SQL anywhere else in the app.

Every function takes an open ``aiosqlite.Connection`` as its first argument so
the caller controls connection lifetime (the API holds one shared connection;
the CLI/scraper opens its own). Adding a new field = add/extend a function here.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import aiosqlite

from db.schema import now_iso


def _norm(s: Optional[str]) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"\s+", " ", s.lower().strip())


_COMMA_WS_RE = re.compile(r"\s*,\s*")


def norm_commas(s: Optional[str]) -> str:
    """Collapse whitespace around commas so 'Baile, House' matches 'Baile,House'.

    Only touches spaces adjacent to a comma - non-comma spaces are preserved
    (",Baile house," keeps its "Baile house"). Used for comma-tag notes search.
    """
    return _COMMA_WS_RE.sub(",", s or "")


def dedup_key(artist_name: Optional[str], title: str) -> str:
    """Normalized 'artist - title' identity for cross-platform grouping (future).

    Deliberately light: lowercase + whitespace/unicode normalize. Does NOT strip
    'remix'/'feat.' - a remix is a genuinely different track we must keep distinct.
    """
    a, t = _norm(artist_name), _norm(title)
    return f"{a} - {t}" if a else t


def canonical_url_key(url: Optional[str]) -> Optional[str]:
    """Normalized canonical track URL for cross-artist repost merging.

    A SoundCloud repost points at the *original* track's permalink, so the same
    song reposted by different artists shares one canonical URL. We lowercase and
    strip scheme/``www.``/query/fragment/trailing slash so trivially different
    spellings of the same link collapse. Returns ``None`` for empty/missing URLs
    (those rows never merge with anything - see the COALESCE in list/count).
    """
    if not url:
        return None
    s = url.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("?", 1)[0].split("#", 1)[0]
    s = s.rstrip("/")
    return s or None


async def backfill_group_keys(conn: aiosqlite.Connection) -> None:
    """Populate group_key for rows that don't have one yet (idempotent)."""
    async with conn.execute(
        "SELECT id, url FROM tracks WHERE group_key IS NULL AND url IS NOT NULL"
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return
    for r in rows:
        gk = canonical_url_key(r["url"])
        if gk:
            await conn.execute(
                "UPDATE tracks SET group_key = ? WHERE id = ?", (gk, r["id"])
            )
    await conn.commit()


async def _expand_group_ids(conn: aiosqlite.Connection, ids: list[int]) -> list[int]:
    """Expand track ids to every row sharing their group_key (for group-wide
    actions on a merged row). Rows with NULL group_key expand to just themselves."""
    if not ids:
        return ids
    ph = ",".join("?" * len(ids))
    async with conn.execute(
        f"SELECT DISTINCT group_key FROM tracks WHERE id IN ({ph}) AND group_key IS NOT NULL",
        ids,
    ) as cur:
        keys = [r["group_key"] for r in await cur.fetchall()]
    if not keys:
        return list(ids)
    kph = ",".join("?" * len(keys))
    async with conn.execute(
        f"SELECT id FROM tracks WHERE group_key IN ({kph})", keys
    ) as cur:
        members = [r["id"] for r in await cur.fetchall()]
    return list(set(ids) | set(members))


async def artist_ids_for_tracks(
    conn: aiosqlite.Connection, track_ids: list[int], *, propagate_group: bool = False
) -> list[int]:
    """Distinct artist ids owning the given tracks (group-expanded if a merged-row
    action propagated to other artists' copies). Lets the caller refresh exactly
    the affected sidebar rows instead of all of them."""
    if not track_ids:
        return []
    ids = await _expand_group_ids(conn, track_ids) if propagate_group else list(track_ids)
    ph = ",".join("?" * len(ids))
    async with conn.execute(
        f"SELECT DISTINCT artist_id FROM tracks WHERE id IN ({ph})", ids
    ) as cur:
        return [r["artist_id"] for r in await cur.fetchall()]


async def track_urls_for_artist(conn: aiosqlite.Connection, artist_id: int) -> set[str]:
    """Canonical track URLs already saved for an artist (for scrape early-stop)."""
    async with conn.execute(
        "SELECT url FROM tracks WHERE artist_id = ? AND url IS NOT NULL", (artist_id,)
    ) as cur:
        return {canonical_url_key(r["url"]) for r in await cur.fetchall() if r["url"]}


def _row(r: Optional[aiosqlite.Row]) -> Optional[dict]:
    return dict(r) if r is not None else None


def _rows(rs: Iterable[aiosqlite.Row]) -> list[dict]:
    return [dict(r) for r in rs]


def _parse_aliases(d: Optional[dict]) -> Optional[dict]:
    """Turn an artist row's `aliases` JSON string into a Python list."""
    if d is None:
        return None
    raw = d.get("aliases")
    if raw:
        try:
            d["aliases"] = json.loads(raw)
        except (TypeError, ValueError):
            d["aliases"] = []
    else:
        d["aliases"] = []
    return d


def _artist_row(r: Optional[aiosqlite.Row]) -> Optional[dict]:
    return _parse_aliases(_row(r))


def _artist_rows(rs: Iterable[aiosqlite.Row]) -> list[dict]:
    return [_parse_aliases(dict(r)) for r in rs]


# Settings
async def get_settings(conn: aiosqlite.Connection) -> dict[str, str]:
    async with conn.execute("SELECT key, value FROM settings") as cur:
        rows = await cur.fetchall()
    return {r["key"]: r["value"] for r in rows}


async def get_setting(conn: aiosqlite.Connection, key: str, default: Any = None) -> Any:
    async with conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else default


async def set_settings(conn: aiosqlite.Connection, updates: dict[str, str]) -> None:
    for key, value in updates.items():
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    await conn.commit()


# Artists
async def list_artists(
    conn: aiosqlite.Connection,
    *,
    include_deleted: bool = False,
    search: Optional[str] = None,
    sort: str = "priority",
    artist_ids: Optional[list[int]] = None,
    unscraped: bool = False,
) -> list[dict]:
    where = []
    params: list[Any] = []
    if not include_deleted:
        where.append("a.is_deleted = 0")
    if unscraped:
        # Added but never scraped (e.g. via "Add only").
        where.append("a.last_scraped IS NULL")
    if artist_ids:
        # Targeted refresh: recompute counts for just these artists (used after a
        # mark so the sidebar updates without rescanning every artist).
        where.append(f"a.id IN ({','.join('?' * len(artist_ids))})")
        params.extend(artist_ids)
    if search:
        # find by name, alias, or notes. Notes are matched comma-insensitively so
        # "Baile, House" finds notes written "Baile,House" (and vice-versa) - we
        # normalize the query and REPLACE spaces *around commas* in the column.
        nsearch = norm_commas(search)
        where.append(
            "(a.name LIKE ? OR a.aliases LIKE ? "
            "OR REPLACE(REPLACE(a.notes, ', ', ','), ' ,', ',') LIKE ?)"
        )
        params.extend([f"%{search}%", f"%{search}%", f"%{nsearch}%"])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    order = {
        "priority": "a.priority DESC, a.name COLLATE NOCASE ASC",
        "priority_old": "a.priority DESC, a.added_at ASC",
        "name": "a.name COLLATE NOCASE ASC",
        "added": "a.added_at DESC",
        "last_scraped": "a.last_scraped DESC",
        "new": "(new_count + revisit_count) DESC, a.priority DESC, a.name COLLATE NOCASE ASC",
    }.get(sort, "a.priority DESC, a.name COLLATE NOCASE ASC")

    # Live counts (cheap counts; fine for thousands of artists locally).
    # Originals vs reposts are tracked separately so the sidebar can show each
    # one's listened progress. (is_repost 0 = original, 1 = repost.)
    sql = f"""
        SELECT a.*,
               (SELECT COUNT(*) FROM tracks t
                  WHERE t.artist_id = a.id AND t.is_deleted = 0) AS tracks_total,
               (SELECT COUNT(*) FROM tracks t
                  WHERE t.artist_id = a.id AND t.is_deleted = 0 AND t.is_repost = 0) AS originals_total,
               (SELECT COUNT(*) FROM tracks t
                  WHERE t.artist_id = a.id AND t.is_deleted = 0 AND t.is_repost = 0 AND t.is_checked = 1) AS originals_listened,
               (SELECT COUNT(*) FROM tracks t
                  WHERE t.artist_id = a.id AND t.is_deleted = 0 AND t.is_repost = 1) AS reposts_total,
               (SELECT COUNT(*) FROM tracks t
                  WHERE t.artist_id = a.id AND t.is_deleted = 0 AND t.is_repost = 1 AND t.is_checked = 1) AS reposts_listened,
               (SELECT COUNT(*) FROM tracks t
                  WHERE t.artist_id = a.id AND t.is_deleted = 0 AND t.is_checked = 0 AND t.is_revisit = 0) AS new_count,
               (SELECT COUNT(*) FROM tracks t
                  WHERE t.artist_id = a.id AND t.is_deleted = 0 AND t.is_revisit = 1) AS revisit_count,
               (SELECT COUNT(*) FROM tracks t
                  WHERE t.artist_id = a.id AND t.is_deleted = 0 AND t.is_owned = 1) AS owned_count,
               (SELECT COUNT(*) FROM tracks t
                  WHERE t.artist_id = a.id AND t.is_deleted = 0 AND t.is_checked = 1) AS listened_count
        FROM artists a
        {where_sql}
        ORDER BY {order}
    """
    async with conn.execute(sql, params) as cur:
        return _artist_rows(await cur.fetchall())


async def get_artist(conn: aiosqlite.Connection, artist_id: int) -> Optional[dict]:
    async with conn.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)) as cur:
        return _artist_row(await cur.fetchone())


async def get_artist_by_url(conn: aiosqlite.Connection, url: str) -> Optional[dict]:
    async with conn.execute("SELECT * FROM artists WHERE url = ?", (url,)) as cur:
        return _artist_row(await cur.fetchone())


async def add_artist(
    conn: aiosqlite.Connection,
    *,
    name: str,
    url: str,
    platform: str = "soundcloud",
    priority: int = 0,
    aliases: Optional[list[str]] = None,
) -> dict:
    """Insert an artist, or return the existing one (and un-delete it) by URL."""
    name = clean_artist_name(name)  # never persist a "Visit … profile" tooltip as the name
    existing = await get_artist_by_url(conn, url)
    if existing:
        if existing["is_deleted"]:
            await conn.execute(
                "UPDATE artists SET is_deleted = 0, deleted_at = NULL WHERE id = ?",
                (existing["id"],),
            )
            await conn.commit()
        return await get_artist(conn, existing["id"])  # type: ignore[return-value]

    await conn.execute(
        "INSERT INTO artists (name, url, platform, priority, added_at, aliases) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, url, platform, priority, now_iso(),
         json.dumps(aliases) if aliases else None),
    )
    await conn.commit()
    return await get_artist_by_url(conn, url)  # type: ignore[return-value]


_ARTIST_PATCHABLE = {
    "name", "priority", "is_active", "is_deleted",
    "include_reposts", "repost_limit", "notes", "aliases",
}


async def update_artist(conn: aiosqlite.Connection, artist_id: int, fields: dict) -> Optional[dict]:
    sets, params = [], []
    for k, v in fields.items():
        if k not in _ARTIST_PATCHABLE:
            continue
        if k == "aliases":
            # store the list as JSON (accept a list or an already-encoded string)
            v = json.dumps(v) if isinstance(v, list) else v
        if k == "name":
            v = clean_artist_name(v)
        sets.append(f"{k} = ?")
        params.append(v)
        if k == "is_deleted":
            sets.append("deleted_at = ?")
            params.append(now_iso() if v else None)
    if not sets:
        return await get_artist(conn, artist_id)
    params.append(artist_id)
    await conn.execute(f"UPDATE artists SET {', '.join(sets)} WHERE id = ?", params)
    await conn.commit()
    return await get_artist(conn, artist_id)


async def soft_delete_artist(conn: aiosqlite.Connection, artist_id: int) -> None:
    await conn.execute(
        "UPDATE artists SET is_deleted = 1, deleted_at = ? WHERE id = ?",
        (now_iso(), artist_id),
    )
    await conn.commit()


async def refresh_artist_cache(conn: aiosqlite.Connection, artist_id: int) -> None:
    """Recompute cached track_count and stamp last_scraped."""
    await conn.execute(
        """
        UPDATE artists
           SET track_count = (SELECT COUNT(*) FROM tracks
                                WHERE artist_id = ? AND is_deleted = 0),
               last_scraped = ?
         WHERE id = ?
        """,
        (artist_id, now_iso(), artist_id),
    )
    await conn.commit()


# Tracks
async def list_tracks(
    conn: aiosqlite.Connection,
    *,
    artist_ids: Optional[list[int]] = None,
    is_checked: Optional[int] = None,
    is_revisit: Optional[int] = None,
    include_deleted: bool = False,
    is_deleted: Optional[int] = None,
    include_owned: bool = False,
    is_owned: Optional[int] = None,
    is_repost: Optional[int] = None,
    priority_min: Optional[int] = None,
    priority_in: Optional[list[int]] = None,
    search: Optional[str] = None,
    sort: str = "priority_new_first",
    merge: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    # Tracks of a soft-deleted artist never surface (their counts are excluded too).
    where = ["1=1", "a.is_deleted = 0"]
    params: list[Any] = []
    if is_deleted is not None:
        where.append("t.is_deleted = ?")
        params.append(is_deleted)
    elif not include_deleted:
        where.append("t.is_deleted = 0")
    if is_owned is not None:
        where.append("t.is_owned = ?")
        params.append(is_owned)
    elif not include_owned:
        where.append("t.is_owned = 0")
    if artist_ids:
        where.append(f"t.artist_id IN ({','.join('?' * len(artist_ids))})")
        params.extend(artist_ids)
    if is_checked is not None:
        where.append("t.is_checked = ?")
        params.append(is_checked)
    if is_revisit is not None:
        where.append("t.is_revisit = ?")
        params.append(is_revisit)
    if is_repost is not None:
        where.append("t.is_repost = ?")
        params.append(is_repost)
    if priority_min is not None:
        where.append("a.priority >= ?")
        params.append(priority_min)
    if priority_in:
        where.append(f"a.priority IN ({','.join('?' * len(priority_in))})")
        params.extend(priority_in)
    if search:
        where.append("(t.name LIKE ? OR a.name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    order = {
        "priority_new_first": "a.priority DESC, t.added_at DESC",
        "priority_old_first": "a.priority DESC, t.added_at ASC",
        "artist_az": "a.name COLLATE NOCASE ASC, t.name COLLATE NOCASE ASC",
        "track_az": "t.name COLLATE NOCASE ASC",
        "date_added": "t.added_at DESC",
        "last_seen": "t.last_seen DESC",
        "owned": "t.is_owned DESC, a.priority DESC, t.added_at DESC",
    }.get(sort, "a.priority DESC, t.added_at DESC")

    where_sql = " AND ".join(where)
    if merge:
        # Collapse cross-artist duplicates (same canonical URL) to one row.
        # Representative = the original upload (is_repost=0) if present, else the
        # lowest id. group_size > 1 marks a merged row for the UI.
        gk = "COALESCE(t.group_key, 't' || t.id)"
        sql = f"""
            WITH g AS (
                SELECT t.id AS tid,
                       ROW_NUMBER() OVER (PARTITION BY {gk}
                                          ORDER BY t.is_repost ASC, t.id ASC) AS rn,
                       COUNT(*)     OVER (PARTITION BY {gk}) AS group_size
                FROM tracks t
                JOIN artists a ON a.id = t.artist_id
                WHERE {where_sql}
            )
            SELECT t.*, a.name AS artist_name, a.priority AS artist_priority,
                   g.group_size AS group_size
            FROM g
            JOIN tracks t  ON t.id = g.tid
            JOIN artists a ON a.id = t.artist_id
            WHERE g.rn = 1
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """
    else:
        sql = f"""
            SELECT t.*, a.name AS artist_name, a.priority AS artist_priority,
                   1 AS group_size
            FROM tracks t
            JOIN artists a ON a.id = t.artist_id
            WHERE {where_sql}
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """
    params.extend([limit, offset])
    async with conn.execute(sql, params) as cur:
        return _rows(await cur.fetchall())


async def count_tracks(
    conn: aiosqlite.Connection,
    *,
    artist_ids: Optional[list[int]] = None,
    is_checked: Optional[int] = None,
    is_revisit: Optional[int] = None,
    include_deleted: bool = False,
    is_deleted: Optional[int] = None,
    include_owned: bool = False,
    is_owned: Optional[int] = None,
    is_repost: Optional[int] = None,
    priority_min: Optional[int] = None,
    priority_in: Optional[list[int]] = None,
    search: Optional[str] = None,
    merge: bool = False,
) -> int:
    # Mirror list_tracks: soft-deleted artists' tracks are never counted.
    where = ["1=1", "a.is_deleted = 0"]
    params: list[Any] = []
    if is_deleted is not None:
        where.append("t.is_deleted = ?")
        params.append(is_deleted)
    elif not include_deleted:
        where.append("t.is_deleted = 0")
    if is_owned is not None:
        where.append("t.is_owned = ?")
        params.append(is_owned)
    elif not include_owned:
        where.append("t.is_owned = 0")
    if artist_ids:
        where.append(f"t.artist_id IN ({','.join('?' * len(artist_ids))})")
        params.extend(artist_ids)
    if is_checked is not None:
        where.append("t.is_checked = ?")
        params.append(is_checked)
    if is_revisit is not None:
        where.append("t.is_revisit = ?")
        params.append(is_revisit)
    if is_repost is not None:
        where.append("t.is_repost = ?")
        params.append(is_repost)
    if priority_min is not None:
        where.append("a.priority >= ?")
        params.append(priority_min)
    if priority_in:
        where.append(f"a.priority IN ({','.join('?' * len(priority_in))})")
        params.extend(priority_in)
    if search:
        where.append("(t.name LIKE ? OR a.name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    count_expr = (
        "COUNT(DISTINCT COALESCE(t.group_key, 't' || t.id))" if merge else "COUNT(*)"
    )
    sql = f"""
        SELECT {count_expr} AS n
        FROM tracks t JOIN artists a ON a.id = t.artist_id
        WHERE {' AND '.join(where)}
    """
    async with conn.execute(sql, params) as cur:
        row = await cur.fetchone()
    return row["n"] if row else 0


async def upsert_track(
    conn: aiosqlite.Connection,
    *,
    artist_id: int,
    name: str,
    url: Optional[str],
    is_repost: int = 0,
    purchase_url: Optional[str] = None,
    artist_name: Optional[str] = None,
) -> bool:
    """Insert if new (by artist_id+url), else just bump last_seen.

    Returns True if a NEW track row was created. Never touches is_checked,
    is_deleted or notes on existing rows. ``artist_name`` (optional) is only used
    to compute the cross-platform ``dedup_key``.
    """
    ts = now_iso()
    key = dedup_key(artist_name, name)
    gkey = canonical_url_key(url)  # cross-artist repost merge key
    existing = None
    if url:
        async with conn.execute(
            "SELECT id FROM tracks WHERE artist_id = ? AND url = ?", (artist_id, url)
        ) as cur:
            existing = await cur.fetchone()
    else:
        async with conn.execute(
            "SELECT id FROM tracks WHERE artist_id = ? AND name = ? AND url IS NULL",
            (artist_id, name),
        ) as cur:
            existing = await cur.fetchone()

    if existing:
        # Keep any previously captured purchase_url when a re-scrape doesn't find one
        # (COALESCE keeps the old value when the new one is NULL), so updates never
        # wipe a link that was captured before.
        await conn.execute(
            "UPDATE tracks SET last_seen = ?, name = ?, dedup_key = ?, group_key = ?, "
            "purchase_url = COALESCE(?, purchase_url), purchase_checked = 1 WHERE id = ?",
            (ts, name, key, gkey, purchase_url, existing["id"]),
        )
        return False

    await conn.execute(
        """INSERT INTO tracks (artist_id, name, url, is_repost, added_at, last_seen, dedup_key, group_key,
                               purchase_url, purchase_checked)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (artist_id, name, url, is_repost, ts, ts, key, gkey, purchase_url),
    )
    return True


_TRACK_FIELDS = {"is_checked", "is_deleted", "is_owned", "is_revisit", "notes"}


def _track_update_sets(fields: dict) -> tuple[list[str], list]:
    """Build the SET clause + params for a track update, applying the cross-field
    rules: owned⇒listened (#4), and revisit/listened are mutually exclusive (#5).
    Stamps the matching *_at timestamps."""
    eff = {k: v for k, v in fields.items() if k in _TRACK_FIELDS}
    # #4: marking owned also marks listened (unless caller set is_checked explicitly).
    if eff.get("is_owned") == 1 and "is_checked" not in eff:
        eff["is_checked"] = 1
    # #5: a track is exactly one of new/listened/revisit - keep the two exclusive.
    if eff.get("is_revisit") == 1 and "is_checked" not in eff:
        eff["is_checked"] = 0
    if eff.get("is_checked") == 1 and "is_revisit" not in eff:
        eff["is_revisit"] = 0
    sets, params = [], []
    for k, v in eff.items():
        sets.append(f"{k} = ?")
        params.append(v)
        if k == "is_deleted":
            sets.append("deleted_at = ?")
            params.append(now_iso() if v else None)
        if k == "is_owned":
            sets.append("owned_at = ?")
            params.append(now_iso() if v else None)
        if k == "is_revisit":
            sets.append("revisit_at = ?")
            params.append(now_iso() if v else None)
        if k == "is_checked":
            sets.append("checked_at = ?")
            params.append(now_iso() if v else None)
    return sets, params


async def set_track_fields(
    conn: aiosqlite.Connection, track_id: int, fields: dict, *, propagate_group: bool = False
) -> Optional[dict]:
    sets, params = _track_update_sets(fields)
    if not sets:
        return None
    # A merged row stands in for every copy of the song; apply to the whole group.
    ids = await _expand_group_ids(conn, [track_id]) if propagate_group else [track_id]
    placeholders = ",".join("?" * len(ids))
    await conn.execute(
        f"UPDATE tracks SET {', '.join(sets)} WHERE id IN ({placeholders})", params + ids
    )
    await conn.commit()
    async with conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)) as cur:
        return _row(await cur.fetchone())


async def bulk_set_tracks(
    conn: aiosqlite.Connection, track_ids: list[int], fields: dict, *, propagate_group: bool = False
) -> int:
    if not track_ids:
        return 0
    sets, base_params = _track_update_sets(fields)
    if not sets:
        return 0
    ids = await _expand_group_ids(conn, track_ids) if propagate_group else track_ids
    placeholders = ",".join("?" * len(ids))
    params = base_params + ids
    cur = await conn.execute(
        f"UPDATE tracks SET {', '.join(sets)} WHERE id IN ({placeholders})", params
    )
    await conn.commit()
    return cur.rowcount


# Import sessions
async def create_session(
    conn: aiosqlite.Connection,
    *,
    mode: str,
    source_url: Optional[str],
    queue: list[dict],
) -> int:
    ts = now_iso()
    cur = await conn.execute(
        """INSERT INTO import_sessions
           (created_at, updated_at, mode, status, source_url, total_artists, artist_queue)
           VALUES (?, ?, ?, 'in_progress', ?, ?, ?)""",
        (ts, ts, mode, source_url, len(queue), json.dumps(queue)),
    )
    await conn.commit()
    return cur.lastrowid


async def get_session(conn: aiosqlite.Connection, session_id: int) -> Optional[dict]:
    async with conn.execute(
        "SELECT * FROM import_sessions WHERE id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["artist_queue"] = json.loads(d["artist_queue"]) if d["artist_queue"] else []
    return d


async def update_session(conn: aiosqlite.Connection, session_id: int, **fields) -> None:
    sets, params = ["updated_at = ?"], [now_iso()]
    for k, v in fields.items():
        if k == "artist_queue" and not isinstance(v, str):
            v = json.dumps(v)
        sets.append(f"{k} = ?")
        params.append(v)
    params.append(session_id)
    await conn.execute(
        f"UPDATE import_sessions SET {', '.join(sets)} WHERE id = ?", params
    )
    await conn.commit()


async def list_sessions(conn: aiosqlite.Connection) -> list[dict]:
    async with conn.execute(
        "SELECT * FROM import_sessions ORDER BY updated_at DESC"
    ) as cur:
        return _rows(await cur.fetchall())


async def latest_queue_for_source(conn: aiosqlite.Connection, source_url: str) -> list[dict]:
    """The most recent saved artist_queue scraped from this follow-list URL.

    Lets an import re-use the last discovered list (skip re-scanning the follow
    page) when the user opts out of re-scanning."""
    async with conn.execute(
        "SELECT artist_queue FROM import_sessions "
        "WHERE source_url = ? AND artist_queue IS NOT NULL AND artist_queue != '[]' "
        "ORDER BY updated_at DESC LIMIT 1",
        (source_url,),
    ) as cur:
        row = await cur.fetchone()
    if not row or not row["artist_queue"]:
        return []
    try:
        return json.loads(row["artist_queue"])
    except (TypeError, ValueError):
        return []


async def get_resumable_session(conn: aiosqlite.Connection) -> Optional[dict]:
    async with conn.execute(
        "SELECT * FROM import_sessions WHERE status = 'paused' ORDER BY updated_at DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["artist_queue"] = json.loads(d["artist_queue"]) if d["artist_queue"] else []
    return d


_VISIT_PROFILE_RE = re.compile(r"^Visit\s+(.+?)(?:['’]s)?\s+profile$", re.IGNORECASE)


def clean_artist_name(name: Optional[str]) -> str:
    """Unwrap a SoundCloud "Visit <name>'s profile" accessibility tooltip to the bare
    display name. Server-side safety net at the artist insert/update chokepoint so this
    can never be persisted again regardless of which scrape path produced the name.
    Idempotent; a normal name passes through untouched."""
    s = (name or "").strip()
    m = _VISIT_PROFILE_RE.match(s)
    return m.group(1).strip() if m else s


async def repair_visit_profile_names(conn: aiosqlite.Connection) -> int:
    """Fix artist names saved as "Visit <name>'s profile".

    An older scraper preferred the followed-artist card's `title` attribute, which
    SoundCloud sets to that accessibility tooltip rather than the display name.
    Unwrap the inner name for any affected rows. Idempotent; safe to run each boot."""
    cur = await conn.execute(
        "SELECT id, name FROM artists WHERE name LIKE 'Visit %profile'"
    )
    rows = await cur.fetchall()
    fixed = 0
    for row in rows:
        m = _VISIT_PROFILE_RE.match(row["name"] or "")
        if not m:
            continue
        new = m.group(1).strip()
        if new and new != row["name"]:
            await conn.execute(
                "UPDATE artists SET name = ? WHERE id = ?", (new, row["id"])
            )
            fixed += 1
    if fixed:
        await conn.commit()
    return fixed


async def reconcile_orphaned_sessions(conn: aiosqlite.Connection) -> int:
    """Flip sessions left 'in_progress' by a previous hard exit to 'paused'.

    Called once at startup: no run can be live before the app boots, so any such
    session is orphaned. Marking it paused lets the resume banner recover it
    (and drain any artists that were queued for background scraping)."""
    cur = await conn.execute(
        "UPDATE import_sessions SET status = 'paused' WHERE status = 'in_progress'"
    )
    await conn.commit()
    return cur.rowcount


# Scrape log
async def start_log(
    conn: aiosqlite.Connection,
    *,
    mode: str,
    session_id: Optional[int] = None,
    artist_ids: Optional[list[int]] = None,
) -> int:
    cur = await conn.execute(
        """INSERT INTO scrape_log (session_id, started_at, mode, artist_ids)
           VALUES (?, ?, ?, ?)""",
        (session_id, now_iso(), mode, json.dumps(artist_ids or [])),
    )
    await conn.commit()
    return cur.lastrowid


async def finish_log(
    conn: aiosqlite.Connection,
    log_id: int,
    *,
    tracks_added: int = 0,
    tracks_removed: int = 0,
    errors: Optional[list[dict]] = None,
) -> None:
    await conn.execute(
        """UPDATE scrape_log
              SET finished_at = ?, tracks_added = ?, tracks_removed = ?, errors = ?
            WHERE id = ?""",
        (now_iso(), tracks_added, tracks_removed, json.dumps(errors or []), log_id),
    )
    await conn.commit()


async def list_logs(conn: aiosqlite.Connection, limit: int = 100) -> list[dict]:
    async with conn.execute(
        "SELECT * FROM scrape_log ORDER BY started_at DESC LIMIT ?", (limit,)
    ) as cur:
        return _rows(await cur.fetchall())


# Stats / data management
async def stats(conn: aiosqlite.Connection, *, artist_ids: Optional[list[int]] = None) -> dict:
    out: dict[str, Any] = {}
    # When scoped to an artist selection, the track buckets reflect only those
    # artists (drives the filter-aware chip counts); artist-level meta stays global.
    art_sql, art_params = "", []
    if artist_ids:
        art_sql = f" AND artist_id IN ({','.join('?' * len(artist_ids))})"
        art_params = list(artist_ids)
    # Never count tracks belonging to soft-deleted artists.
    nd = " AND artist_id IN (SELECT id FROM artists WHERE is_deleted = 0)"
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM artists WHERE is_deleted = 0"
    ) as cur:
        out["artists"] = (await cur.fetchone())["n"]
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM artists WHERE is_deleted = 0 AND priority > 0"
    ) as cur:
        out["priority_artists"] = (await cur.fetchone())["n"]
    # Artists added but never scraped - drives the conditional "⊘ Unscraped" filter button.
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM artists WHERE is_deleted = 0 AND last_scraped IS NULL"
    ) as cur:
        out["unscraped_artists"] = (await cur.fetchone())["n"]
    async with conn.execute(
        f"""SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN is_checked = 1 THEN 1 ELSE 0 END) AS listened,
              SUM(CASE WHEN is_checked = 0 AND is_revisit = 0 THEN 1 ELSE 0 END) AS new,
              SUM(CASE WHEN is_revisit = 1 THEN 1 ELSE 0 END) AS revisit,
              SUM(CASE WHEN is_repost = 0 THEN 1 ELSE 0 END) AS originals_total,
              SUM(CASE WHEN is_repost = 0 AND is_checked = 1 THEN 1 ELSE 0 END) AS originals_listened,
              SUM(CASE WHEN is_repost = 1 THEN 1 ELSE 0 END) AS reposts_total,
              SUM(CASE WHEN is_repost = 1 AND is_checked = 1 THEN 1 ELSE 0 END) AS reposts_listened
           FROM tracks WHERE is_deleted = 0{nd}{art_sql}""",
        art_params,
    ) as cur:
        r = await cur.fetchone()
    out["tracks_total"] = r["total"] or 0
    out["listened"] = r["listened"] or 0
    out["new"] = r["new"] or 0
    out["revisit"] = r["revisit"] or 0
    out["originals_total"] = r["originals_total"] or 0
    out["originals_listened"] = r["originals_listened"] or 0
    out["reposts_total"] = r["reposts_total"] or 0
    out["reposts_listened"] = r["reposts_listened"] or 0
    out["listened_pct"] = round(100 * out["listened"] / out["tracks_total"]) if out["tracks_total"] else 0
    out["originals_pct"] = round(100 * out["originals_listened"] / out["originals_total"]) if out["originals_total"] else 0
    out["reposts_pct"] = round(100 * out["reposts_listened"] / out["reposts_total"]) if out["reposts_total"] else 0
    out["revisit_pct"] = round(100 * out["revisit"] / out["tracks_total"]) if out["tracks_total"] else 0
    async with conn.execute(
        f"SELECT COUNT(*) AS n FROM tracks WHERE is_deleted = 0 AND is_owned = 1{nd}{art_sql}",
        art_params,
    ) as cur:
        out["owned"] = (await cur.fetchone())["n"]
    # "Listened today": tracks marked listened since the start of the LOCAL calendar
    # day (now_iso stores UTC, so convert local midnight to UTC for the comparison).
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = local_midnight.astimezone(timezone.utc).isoformat()
    async with conn.execute(
        f"SELECT COUNT(*) AS n FROM tracks WHERE is_deleted = 0 AND is_checked = 1 "
        f"AND checked_at >= ?{nd}{art_sql}",
        [today_start, *art_params],
    ) as cur:
        out["listened_today"] = (await cur.fetchone())["n"]
    async with conn.execute("SELECT MAX(finished_at) AS t FROM scrape_log") as cur:
        out["last_update"] = (await cur.fetchone())["t"]
    return out


async def purge_deleted(conn: aiosqlite.Connection) -> dict:
    async with conn.execute("SELECT COUNT(*) AS n FROM tracks WHERE is_deleted = 1") as cur:
        tracks = (await cur.fetchone())["n"]
    async with conn.execute("SELECT COUNT(*) AS n FROM artists WHERE is_deleted = 1") as cur:
        artists = (await cur.fetchone())["n"]
    await conn.execute("DELETE FROM tracks WHERE is_deleted = 1")
    await conn.execute("DELETE FROM artists WHERE is_deleted = 1")
    await conn.commit()
    return {"tracks_purged": tracks, "artists_purged": artists}


async def export_all(conn: aiosqlite.Connection) -> dict:
    out: dict[str, list] = {}
    for table in ("artists", "tracks", "import_sessions", "scrape_log", "settings"):
        async with conn.execute(f"SELECT * FROM {table}") as cur:
            out[table] = _rows(await cur.fetchall())
    return out


# Child tables before their parents (FK-safe delete order).
_DELETE_ORDER = ("scrape_log", "import_sessions", "tracks", "artists", "settings")
# Parents before children (FK-safe insert order).
_INSERT_ORDER = ("artists", "tracks", "import_sessions", "scrape_log", "settings")


async def import_all(conn: aiosqlite.Connection, data: dict) -> dict:
    """Restore from a JSON export: replace every present table's rows wholesale."""
    counts: dict[str, int] = {}
    for table in _DELETE_ORDER:
        if data.get(table) is not None:
            await conn.execute(f"DELETE FROM {table}")
    for table in _INSERT_ORDER:
        rows = data.get(table)
        if rows is None:
            continue
        for row in rows:
            cols = list(row.keys())
            placeholders = ",".join("?" * len(cols))
            await conn.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                [row[c] for c in cols],
            )
        counts[table] = len(rows)
    await conn.commit()
    return counts


async def reset_all(conn: aiosqlite.Connection) -> None:
    """Wipe all user data. Settings are cleared and re-seeded by the caller."""
    for table in _DELETE_ORDER:
        await conn.execute(f"DELETE FROM {table}")
    await conn.commit()
