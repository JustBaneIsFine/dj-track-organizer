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


def notes_terms(query: Optional[str]) -> list[str]:
    """Split a notes query into lowercased words (on commas and whitespace), so a
    notes search matches artists whose notes contain ALL the words in any order:
    'Baile, Jersey' finds notes written 'Baile,House,Jersey'."""
    return [t for t in re.split(r"[,\s]+", (query or "").lower()) if t]


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


def _in_list(col: str) -> str:
    """`col IN (...)` with the whole list bound as one JSON parameter, so SQLite's
    host-parameter limit (32766) never applies to big selections."""
    return f"{col} IN (SELECT value FROM json_each(?))"


_ESC = "ESCAPE '\\'"


def _like(s: str) -> str:
    """LIKE pattern matching ``s`` literally (% and _ are not wildcards)."""
    s = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{s}%"


async def _expand_group_ids(conn: aiosqlite.Connection, ids: list[int]) -> list[int]:
    """Expand track ids to every row sharing their group_key (for group-wide
    actions on a merged row). Rows with NULL group_key expand to just themselves."""
    if not ids:
        return ids
    async with conn.execute(
        f"SELECT DISTINCT group_key FROM tracks WHERE {_in_list('id')} AND group_key IS NOT NULL",
        (json.dumps(ids),),
    ) as cur:
        keys = [r["group_key"] for r in await cur.fetchall()]
    if not keys:
        return list(ids)
    async with conn.execute(
        f"SELECT id FROM tracks WHERE {_in_list('group_key')}", (json.dumps(keys),)
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
    async with conn.execute(
        f"SELECT DISTINCT artist_id FROM tracks WHERE {_in_list('id')}", (json.dumps(ids),)
    ) as cur:
        return [r["artist_id"] for r in await cur.fetchall()]


async def track_urls_for_artist(conn: aiosqlite.Connection, artist_id: int) -> set[str]:
    """Canonical track URLs already saved for an artist (for scrape early-stop)."""
    async with conn.execute(
        "SELECT url FROM tracks WHERE artist_id = ? AND url IS NOT NULL", (artist_id,)
    ) as cur:
        return {canonical_url_key(r["url"]) for r in await cur.fetchall() if r["url"]}


async def mark_dl_checked(conn: aiosqlite.Connection, artist_id: int) -> None:
    await conn.execute("UPDATE artists SET dl_checked = 1 WHERE id = ?", (artist_id,))
    await conn.commit()


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
    asc: bool = False,
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
        # find by name, alias, or notes. Notes match by WORD: every word in the
        # query must appear (any order, any separators), so "Baile, Jersey" finds
        # notes written "Baile,House,Jersey".
        ors = [f"a.name LIKE ? {_ESC}", f"a.aliases LIKE ? {_ESC}"]
        params.extend([_like(search), _like(search)])
        terms = notes_terms(search)
        if terms:
            ors.append("(" + " AND ".join([f"LOWER(a.notes) LIKE ? {_ESC}"] * len(terms)) + ")")
            params.extend([_like(t) for t in terms])
        where.append("(" + " OR ".join(ors) + ")")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # asc flips the sort's main key. Priority sorts keep high stars on top and flip
    # the key inside each level instead.
    d = "ASC" if asc else "DESC"
    az = "DESC" if asc else "ASC"
    order = {
        "priority": f"a.priority DESC, a.name COLLATE NOCASE {az}",
        "name": f"a.name COLLATE NOCASE {az}",
        "added": f"a.added_at {d}",
        "last_scraped": f"a.last_scraped {d}",
        "new": f"(new_count + revisit_count) {d}, a.priority DESC, a.name COLLATE NOCASE ASC",
        "owned": f"owned_count {d}, a.priority DESC, a.name COLLATE NOCASE ASC",
        "priority_todo": f"a.priority DESC, (new_count + revisit_count) {d}, a.name COLLATE NOCASE ASC",
    }.get(sort, f"a.priority DESC, a.name COLLATE NOCASE {az}")

    # Counts come from artist_counts, which triggers keep current (migration 018).
    # Originals vs reposts are tracked separately so the sidebar can show each
    # one's listened progress. (is_repost 0 = original, 1 = repost.)
    sql = f"""
        SELECT a.*,
               COALESCE(c.tracks_total, 0) AS tracks_total,
               COALESCE(c.originals_total, 0) AS originals_total,
               COALESCE(c.originals_listened, 0) AS originals_listened,
               COALESCE(c.reposts_total, 0) AS reposts_total,
               COALESCE(c.reposts_listened, 0) AS reposts_listened,
               COALESCE(c.new_count, 0) AS new_count,
               COALESCE(c.revisit_count, 0) AS revisit_count,
               COALESCE(c.owned_count, 0) AS owned_count,
               COALESCE(c.listened_count, 0) AS listened_count
        FROM artists a
        LEFT JOIN artist_counts c ON c.artist_id = a.id
        {where_sql}
    """
    # The "most to do" sorts are a work queue: artists with nothing left to go
    # through would just be dead weight at the bottom, so leave them out.
    if sort in ("new", "priority_todo"):
        # Aliased "a" so the ORDER BY below still resolves a.priority / a.name.
        sql = f"SELECT * FROM ({sql}) a WHERE a.new_count + a.revisit_count > 0"
    sql = f"{sql} ORDER BY {order}"
    async with conn.execute(sql, params) as cur:
        return _artist_rows(await cur.fetchall())


async def get_artist(conn: aiosqlite.Connection, artist_id: int) -> Optional[dict]:
    async with conn.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)) as cur:
        return _artist_row(await cur.fetchone())


async def get_artist_by_url(conn: aiosqlite.Connection, url: str) -> Optional[dict]:
    async with conn.execute("SELECT * FROM artists WHERE url = ?", (url,)) as cur:
        return _artist_row(await cur.fetchone())


async def add_skipped_artist(conn: aiosqlite.Connection, url: str, name: str) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO skipped_artists (url, name, skipped_at) VALUES (?, ?, ?)",
        (url, name, now_iso()),
    )
    await conn.commit()


async def skipped_artist_urls(conn: aiosqlite.Connection) -> set[str]:
    async with conn.execute("SELECT url FROM skipped_artists") as cur:
        return {r["url"] for r in await cur.fetchall()}


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
# Same rule as linkKind() in frontend/js/tracks.js: SoundCloud's own download or any
# non-store link is "free", a store host is "buy", nothing at all is "unknown".
# The artist_counts triggers (migration 018) and _COUNTS_FILL repeat these rules.
_BUY_SQL = "(t.purchase_url LIKE '%bandcamp.com%' OR t.purchase_url LIKE '%beatport.com%')"
LINK_SQL = {
    "buy": f"({_BUY_SQL} AND t.sc_download = 0)",
    "free": f"(t.sc_download = 1 OR (COALESCE(t.purchase_url, '') <> '' AND NOT {_BUY_SQL}))",
    "unknown": "(COALESCE(t.purchase_url, '') = '' AND t.sc_download = 0)",
}


def _track_where(f: dict, t: str = "t", a: str = "a") -> tuple[str, list]:
    """WHERE clause for the track list filters, with the table aliases swappable so
    the duplicate-merge probe can apply the same filters to another row."""
    # Tracks of a soft-deleted artist never surface (their counts are excluded too).
    where = [f"{a}.is_deleted = 0"]
    params: list[Any] = []
    if f.get("is_deleted") is not None:
        where.append(f"{t}.is_deleted = ?")
        params.append(f["is_deleted"])
    elif not f.get("include_deleted"):
        where.append(f"{t}.is_deleted = 0")
    if f.get("is_owned") is not None:
        where.append(f"{t}.is_owned = ?")
        params.append(f["is_owned"])
    elif not f.get("include_owned"):
        where.append(f"{t}.is_owned = 0")
    if f.get("artist_ids"):
        where.append(f"{t}.artist_id IN ({','.join('?' * len(f['artist_ids']))})")
        params.extend(f["artist_ids"])
    for col in ("is_checked", "is_revisit", "is_repost"):
        if f.get(col) is not None:
            where.append(f"{t}.{col} = ?")
            params.append(f[col])
    if f.get("link") in LINK_SQL:
        where.append(re.sub(r"\bt\.", f"{t}.", LINK_SQL[f["link"]]))
    if f.get("priority_min") is not None:
        where.append(f"{a}.priority >= ?")
        params.append(f["priority_min"])
    if f.get("priority_in"):
        where.append(f"{a}.priority IN ({','.join('?' * len(f['priority_in']))})")
        params.extend(f["priority_in"])
    if f.get("track_ids"):
        where.append(_in_list(f"{t}.id"))
        params.append(json.dumps(f["track_ids"]))
    if f.get("search"):
        where.append(f"({t}.name LIKE ? {_ESC} OR {a}.name LIKE ? {_ESC})")
        params.extend([_like(f["search"]), _like(f["search"])])
    return " AND ".join(where), params


# Group keys shared by more than one track. Only those tracks need the merge check,
# which keeps merging cheap when most tracks are unique.
_DUP_KEYS = """dup AS MATERIALIZED (
    SELECT group_key FROM tracks WHERE group_key IS NOT NULL
     GROUP BY group_key HAVING COUNT(*) > 1)"""


async def list_tracks(
    conn: aiosqlite.Connection,
    *,
    sort: str = "priority",
    asc: bool = False,
    merge: bool = False,
    limit: int = 500,
    offset: int = 0,
    **filters: Any,
) -> list[dict]:
    d = "ASC" if asc else "DESC"
    # t.id last so paging through equal timestamps never repeats or skips a row.
    # Each order matches an index (migration 017), so a page is read straight off
    # the index instead of sorting every matching track first.
    if sort == "artist":
        order = f"t.artist_name COLLATE NOCASE {'DESC' if asc else 'ASC'}, t.name COLLATE NOCASE ASC, t.id ASC"
        idx = "idx_tracks_artist_za" if asc else "idx_tracks_artist_az"
    elif sort == "date_added":
        order, idx = f"t.added_at {d}, t.id {d}", "idx_tracks_added"
    else:
        order = f"t.artist_priority DESC, t.added_at {d}, t.id {d}"
        idx = "idx_tracks_prio_added_up" if asc else "idx_tracks_prio_added"
    # A few artists' tracks are quicker to fetch by artist and sort.
    if filters.get("artist_ids") or filters.get("track_ids"):
        idx = None
    t_from = f"tracks t INDEXED BY {idx}" if idx else "tracks t"

    where_sql, params = _track_where(filters)
    if merge:
        # Collapse cross-artist duplicates (same canonical URL) to one row.
        # Representative = the original upload (is_repost=0) if present, else the
        # lowest id, among the rows the filters keep. group_size > 1 marks a merged
        # row for the UI; it's only counted for the page that's returned.
        u_sql, u_params = _track_where(filters, "u", "ua")
        sql = f"""
            WITH {_DUP_KEYS}
            SELECT t.*, a.name AS artist_name, a.priority AS artist_priority,
                   CASE WHEN t.group_key IN dup THEN
                     (SELECT COUNT(*) FROM tracks u INDEXED BY idx_tracks_group_state
                        JOIN artists ua ON ua.id = u.artist_id
                       WHERE u.group_key = t.group_key AND {u_sql})
                   ELSE 1 END AS group_size
            FROM (
                SELECT t.id AS tid FROM {t_from}
                JOIN artists a ON a.id = t.artist_id
                WHERE {where_sql}
                  AND (t.group_key IS NULL OR t.group_key NOT IN dup OR NOT EXISTS (
                       SELECT 1 FROM tracks u INDEXED BY idx_tracks_group_state
                         JOIN artists ua ON ua.id = u.artist_id
                        WHERE u.group_key = t.group_key AND {u_sql}
                          AND (u.is_repost < t.is_repost
                               OR (u.is_repost = t.is_repost AND u.id < t.id))))
                ORDER BY {order}
                LIMIT ? OFFSET ?
            ) p
            JOIN tracks t  ON t.id = p.tid
            JOIN artists a ON a.id = t.artist_id
            ORDER BY {order}
        """
        params = u_params + params + u_params + [limit, offset]
    else:
        sql = f"""
            SELECT t.*, a.name AS artist_name, a.priority AS artist_priority,
                   1 AS group_size
            FROM {t_from}
            JOIN artists a ON a.id = t.artist_id
            WHERE {where_sql}
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """
        params = params + [limit, offset]
    async with conn.execute(sql, params) as cur:
        return _rows(await cur.fetchall())


async def count_tracks(conn: aiosqlite.Connection, *, merge: bool = False, **filters: Any) -> int:
    where_sql, params = _track_where(filters)
    if merge:
        # Unique tracks count once each, every duplicate group counts once.
        sql = f"""
            WITH {_DUP_KEYS}
            SELECT COALESCE(SUM(t.group_key IS NULL OR t.group_key NOT IN dup), 0)
                 + COUNT(DISTINCT CASE WHEN t.group_key IN dup THEN t.group_key END) AS n
            FROM tracks t JOIN artists a ON a.id = t.artist_id
            WHERE {where_sql}
        """
    else:
        sql = f"SELECT COUNT(*) AS n FROM tracks t JOIN artists a ON a.id = t.artist_id WHERE {where_sql}"
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
    sc_download: Optional[bool] = None,
    artist_name: Optional[str] = None,
) -> Optional[int]:
    """Insert if new (by artist_id+url), else just bump last_seen.

    Returns the new track's id if a NEW row was created, else None (so callers can
    both test truthiness and collect what a run added). Never touches is_checked,
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

    dl = None if sc_download is None else int(bool(sc_download))
    if existing:
        # Keep any previously captured purchase_url when a re-scrape doesn't find one
        # (COALESCE keeps the old value when the new one is NULL), so updates never
        # wipe a link that was captured before. sc_download follows the artist's
        # current setting whenever it was seen.
        await conn.execute(
            "UPDATE tracks SET last_seen = ?, name = ?, dedup_key = ?, group_key = ?, "
            "purchase_url = COALESCE(?, purchase_url), purchase_checked = 1, "
            "sc_download = COALESCE(?, sc_download) WHERE id = ?",
            (ts, name, key, gkey, purchase_url, dl, existing["id"]),
        )
        return None

    cur = await conn.execute(
        """INSERT INTO tracks (artist_id, name, url, is_repost, added_at, last_seen, dedup_key, group_key,
                               purchase_url, purchase_checked, sc_download)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (artist_id, name, url, is_repost, ts, ts, key, gkey, purchase_url, dl or 0),
    )
    return cur.lastrowid


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
    if eff.get("is_checked") == 1 and eff.get("is_revisit") == 1:
        eff["is_checked"] = 0  # both asked for: revisit wins, it's the unfinished state
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
    await conn.execute(
        f"UPDATE tracks SET {', '.join(sets)} WHERE {_in_list('id')}", params + [json.dumps(ids)]
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
    cur = await conn.execute(
        f"UPDATE tracks SET {', '.join(sets)} WHERE {_in_list('id')}",
        base_params + [json.dumps(list(ids))],
    )
    await conn.commit()
    return cur.rowcount


# Run results: what one scrape/update run found new, kept so it can be reopened.
async def save_run_result(
    conn: aiosqlite.Connection, *, session_id: Optional[int], label: str,
    track_ids: list[int], keep: int = 5,
) -> Optional[int]:
    """Store one run's new-track ids as its own entry (runs are never merged), then
    prune to the newest ``keep`` results so the list stays short."""
    ids = [int(i) for i in track_ids if i]
    if not ids:
        return None
    cur = await conn.execute(
        "INSERT INTO run_results (session_id, created_at, label, track_count, track_ids) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, now_iso(), label, len(ids), json.dumps(ids)),
    )
    new_id = cur.lastrowid
    if keep and keep > 0:
        await conn.execute(
            "DELETE FROM run_results WHERE id NOT IN "
            "(SELECT id FROM run_results ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
    await conn.commit()
    return new_id


# A run's track counts as still to review until it's heard, marked revisit, owned or
# deleted (or its artist is). Counted live, so a run never reports a stale number
# regardless of where the track was marked.
_RUN_PENDING_COUNT = """
    (SELECT COUNT(*) FROM json_each(r.track_ids) je
       JOIN tracks t  ON t.id = je.value
       JOIN artists a ON a.id = t.artist_id
      WHERE t.is_checked = 0 AND t.is_revisit = 0
        AND t.is_owned = 0 AND t.is_deleted = 0 AND a.is_deleted = 0)
"""


async def list_run_results(conn: aiosqlite.Connection, limit: int = 20) -> list[dict]:
    """Recent run results, newest first, each with its live still-to-review count.
    Runs with nothing left are omitted (they come back if a track is un-marked)."""
    async with conn.execute(
        f"SELECT r.id, r.session_id, r.created_at, r.label, "
        f"{_RUN_PENDING_COUNT} AS track_count "
        f"FROM run_results r ORDER BY r.id DESC LIMIT ?", (limit,)
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return [r for r in rows if r["track_count"] > 0]


async def delete_run_result(conn: aiosqlite.Connection, run_id: int) -> None:
    await conn.execute("DELETE FROM run_results WHERE id = ?", (run_id,))
    await conn.commit()


async def get_run_result(conn: aiosqlite.Connection, run_id: int) -> Optional[dict]:
    async with conn.execute("SELECT * FROM run_results WHERE id = ?", (run_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["track_ids"] = json.loads(out.get("track_ids") or "[]")
    except (TypeError, ValueError):
        out["track_ids"] = []
    return out


# Owned-scan rejections: (track, filename) pairs the user marked "not the same song".
async def add_owned_rejections(
    conn: aiosqlite.Connection, pairs: list[tuple[int, str]]
) -> int:
    if not pairs:
        return 0
    ts = now_iso()
    cur = await conn.executemany(
        "INSERT OR IGNORE INTO owned_rejections (track_id, filename, created_at) "
        "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM tracks WHERE id = ?)",
        [(tid, fn, ts, tid) for tid, fn in pairs if tid and fn],
    )
    await conn.commit()
    return max(cur.rowcount, 0)


async def get_owned_rejections(
    conn: aiosqlite.Connection, track_ids: Optional[list[int]] = None
) -> dict[int, set[str]]:
    """{track_id: {rejected filenames}} so the matcher can skip those exact pairs."""
    if track_ids is not None:
        if not track_ids:
            return {}
        sql = f"SELECT track_id, filename FROM owned_rejections WHERE {_in_list('track_id')}"
        args = [json.dumps(track_ids)]
    else:
        sql = "SELECT track_id, filename FROM owned_rejections"
        args = []
    out: dict[int, set[str]] = {}
    async with conn.execute(sql, args) as cur:
        async for row in cur:
            out.setdefault(row["track_id"], set()).add(row["filename"])
    return out


async def remove_owned_rejection(conn: aiosqlite.Connection, track_id: int, filename: str) -> None:
    await conn.execute(
        "DELETE FROM owned_rejections WHERE track_id = ? AND filename = ?", (track_id, filename)
    )


# Folder checks: saved scans whose matches are decided one at a time.
_SNAPSHOT_COLS = ("is_owned", "owned_at", "is_checked", "checked_at",
                  "is_revisit", "revisit_at", "is_deleted", "deleted_at")
_LEFT = "('pending', 'skipped')"


async def find_open_check(
    conn: aiosqlite.Connection, root: str, artist_id: Optional[int]
) -> Optional[int]:
    async with conn.execute(
        "SELECT id FROM owned_checks WHERE status = 'open' AND root = ? AND artist_id IS ? "
        "ORDER BY id DESC LIMIT 1", (root, artist_id),
    ) as cur:
        row = await cur.fetchone()
    return row["id"] if row else None


async def create_check(
    conn: aiosqlite.Connection, *, root: str, artist_id: Optional[int], file_count: int,
) -> int:
    ts = now_iso()
    cur = await conn.execute(
        "INSERT INTO owned_checks (root, artist_id, created_at, updated_at, file_count) "
        "VALUES (?, ?, ?, ?, ?)", (root, artist_id, ts, ts, file_count),
    )
    await conn.commit()
    return cur.lastrowid


async def add_check_items(conn: aiosqlite.Connection, check_id: int, items: list[dict]) -> int:
    """Insert matches, skipping tracks this check already holds. Returns how many were new."""
    async with conn.execute(
        "SELECT track_id FROM owned_check_items WHERE check_id = ?", (check_id,)
    ) as cur:
        have = {r["track_id"] for r in await cur.fetchall()}
    rows = [(check_id, m["track_id"], m["filename"], m.get("path"), m["score"], m["kind"],
             m.get("tags"))
            for m in items if m["track_id"] not in have]
    if rows:
        await conn.executemany(
            "INSERT INTO owned_check_items (check_id, track_id, filename, path, score, kind, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows,
        )
    await conn.execute(
        "UPDATE owned_checks SET updated_at = ?, status = 'open' WHERE id = ?", (now_iso(), check_id)
    )
    await conn.commit()
    return len(rows)


async def get_file_tags(conn: aiosqlite.Connection) -> dict[str, tuple]:
    """path -> (mtime_ns, size, artist, title, album_artist) for every cached file."""
    async with conn.execute(
        "SELECT path, mtime_ns, size, artist, title, album_artist FROM file_tags"
    ) as cur:
        return {r[0]: tuple(r[1:]) for r in await cur.fetchall()}


async def save_file_tags(conn: aiosqlite.Connection, rows: list[tuple]) -> None:
    if not rows:
        return
    await conn.executemany(
        "INSERT OR REPLACE INTO file_tags (path, mtime_ns, size, artist, title, album_artist) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows,
    )
    await conn.commit()


async def list_checks(conn: aiosqlite.Connection) -> list[dict]:
    async with conn.execute(
        f"""SELECT c.*, a.name AS artist_name,
              (SELECT COUNT(*) FROM owned_check_items i WHERE i.check_id = c.id) AS total,
              (SELECT COUNT(*) FROM owned_check_items i
                 WHERE i.check_id = c.id AND i.state IN {_LEFT}) AS left_count
            FROM owned_checks c LEFT JOIN artists a ON a.id = c.artist_id
            ORDER BY c.updated_at DESC"""
    ) as cur:
        return _rows(await cur.fetchall())


async def get_check(conn: aiosqlite.Connection, check_id: int) -> Optional[dict]:
    async with conn.execute(
        "SELECT c.*, a.name AS artist_name FROM owned_checks c "
        "LEFT JOIN artists a ON a.id = c.artist_id WHERE c.id = ?", (check_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


_ITEM_SELECT = """SELECT i.id, i.check_id, i.track_id, i.filename, i.path IS NOT NULL AS has_path,
       i.score, i.kind, i.tags, i.state, i.action, i.decided_at,
       t.name AS track_name, t.url, a.name AS artist_name
  FROM owned_check_items i
  JOIN tracks t ON t.id = i.track_id
  JOIN artists a ON a.id = t.artist_id"""


async def list_check_items(conn: aiosqlite.Connection, check_id: int) -> list[dict]:
    async with conn.execute(f"{_ITEM_SELECT} WHERE i.check_id = ? ORDER BY i.id", (check_id,)) as cur:
        return _rows(await cur.fetchall())


async def get_check_item(conn: aiosqlite.Connection, item_id: int) -> Optional[dict]:
    async with conn.execute(
        "SELECT * FROM owned_check_items WHERE id = ?", (item_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _touch_check(conn: aiosqlite.Connection, check_id: int) -> None:
    async with conn.execute(
        f"SELECT COUNT(*) AS n FROM owned_check_items WHERE check_id = ? AND state IN {_LEFT}",
        (check_id,),
    ) as cur:
        left = (await cur.fetchone())["n"]
    await conn.execute(
        "UPDATE owned_checks SET updated_at = ?, status = ? WHERE id = ?",
        (now_iso(), "open" if left else "done", check_id),
    )


async def decide_check_items(
    conn: aiosqlite.Connection, item_ids: list[int], decision: str
) -> int:
    """Record 'same' / 'different' / 'skipped' for items still undecided. 'same' tags
    the track owned right away; 'different' remembers the pair
    so it never matches again. Earlier track flags are kept for undo."""
    n = 0
    check_ids = set()
    for item_id in item_ids:
        it = await get_check_item(conn, item_id)
        if not it or it["state"] not in ("pending", "skipped"):
            continue
        check_ids.add(it["check_id"])
        ts = now_iso()
        if decision == "skipped":
            await conn.execute(
                "UPDATE owned_check_items SET state = 'skipped' WHERE id = ?", (item_id,)
            )
            n += 1
            continue
        prev = None
        action = None
        if decision == "same":
            async with conn.execute(
                f"SELECT {', '.join(_SNAPSHOT_COLS)} FROM tracks WHERE id = ?", (it["track_id"],)
            ) as cur:
                row = await cur.fetchone()
            prev = json.dumps(dict(row)) if row else None
            action = "owned"
            sets, params = _track_update_sets({"is_owned": 1})
            await conn.execute(
                f"UPDATE tracks SET {', '.join(sets)} WHERE id = ?", params + [it["track_id"]]
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO owned_rejections (track_id, filename, created_at) "
                "VALUES (?, ?, ?)", (it["track_id"], it["filename"], ts),
            )
        await conn.execute(
            "UPDATE owned_check_items SET state = ?, action = ?, prev = ?, decided_at = ? "
            "WHERE id = ?", (decision, action, prev, ts, item_id),
        )
        n += 1
    for cid in check_ids:
        await _touch_check(conn, cid)
    await conn.commit()
    return n


async def undo_check_items(conn: aiosqlite.Connection, item_ids: list[int]) -> int:
    """Put decided or skipped items back to pending and restore what each changed."""
    n = 0
    check_ids = set()
    for item_id in item_ids:
        it = await get_check_item(conn, item_id)
        if not it or it["state"] == "pending":
            continue
        if it["state"] == "same" and it["prev"]:
            prev = json.loads(it["prev"])
            cols = [c for c in _SNAPSHOT_COLS if c in prev]
            await conn.execute(
                f"UPDATE tracks SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                [prev[c] for c in cols] + [it["track_id"]],
            )
        elif it["state"] == "different":
            await remove_owned_rejection(conn, it["track_id"], it["filename"])
        await conn.execute(
            "UPDATE owned_check_items SET state = 'pending', action = NULL, prev = NULL, "
            "decided_at = NULL WHERE id = ?", (item_id,),
        )
        check_ids.add(it["check_id"])
        n += 1
    for cid in check_ids:
        await _touch_check(conn, cid)
    await conn.commit()
    return n


async def delete_check(conn: aiosqlite.Connection, check_id: int) -> None:
    """Drop a check. Decisions already made stay applied to the tracks."""
    await conn.execute("DELETE FROM owned_check_items WHERE check_id = ?", (check_id,))
    await conn.execute("DELETE FROM owned_checks WHERE id = ?", (check_id,))
    await conn.commit()


async def check_item_path(conn: aiosqlite.Connection, item_id: int) -> Optional[str]:
    async with conn.execute("SELECT path FROM owned_check_items WHERE id = ?", (item_id,)) as cur:
        row = await cur.fetchone()
    return row["path"] if row else None


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
async def stats(
    conn: aiosqlite.Connection, *, artist_ids: Optional[list[int]] = None,
    track_ids: Optional[list[int]] = None,
) -> dict:
    out: dict[str, Any] = {}
    # When scoped to an artist selection, the track buckets reflect only those
    # artists (drives the filter-aware chip counts); artist-level meta stays global.
    art_sql, art_params = "", []
    if artist_ids:
        art_sql = f" AND artist_id IN ({','.join('?' * len(artist_ids))})"
        art_params = list(artist_ids)
    # A run's results: only its tracks, and owned ones are left out like in the list.
    if track_ids:
        art_sql += " AND is_owned = 0 AND id IN (SELECT value FROM json_each(?))"
        art_params.append(json.dumps(list(track_ids)))
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
    free, buy, unknown = (LINK_SQL[k].replace("t.purchase_url", "purchase_url").replace("t.sc_download", "sc_download") for k in ("free", "buy", "unknown"))
    # Whole library or an artist selection: sum the stored per-artist counts. A run's
    # results are an arbitrary set of tracks, so those are still counted directly.
    counts_sql = f"""SELECT
              SUM(c.tracks_total) AS total, SUM(c.listened_count) AS listened,
              SUM(c.new_count) AS new, SUM(c.revisit_count) AS revisit,
              SUM(c.originals_total) AS originals_total, SUM(c.originals_listened) AS originals_listened,
              SUM(c.reposts_total) AS reposts_total, SUM(c.reposts_listened) AS reposts_listened,
              SUM(c.free_total) AS free_total, SUM(c.buy_total) AS buy_total,
              SUM(c.unknown_total) AS unknown_total, SUM(c.owned_count) AS owned
           FROM artist_counts c JOIN artists a ON a.id = c.artist_id
           WHERE a.is_deleted = 0{art_sql.replace("artist_id", "c.artist_id")}"""
    async with conn.execute(counts_sql if not track_ids else
        f"""SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN is_checked = 1 THEN 1 ELSE 0 END) AS listened,
              SUM(CASE WHEN is_checked = 0 AND is_revisit = 0 THEN 1 ELSE 0 END) AS new,
              SUM(CASE WHEN is_revisit = 1 THEN 1 ELSE 0 END) AS revisit,
              SUM(CASE WHEN is_repost = 0 THEN 1 ELSE 0 END) AS originals_total,
              SUM(CASE WHEN is_repost = 0 AND is_checked = 1 THEN 1 ELSE 0 END) AS originals_listened,
              SUM(CASE WHEN is_repost = 1 THEN 1 ELSE 0 END) AS reposts_total,
              SUM(CASE WHEN is_repost = 1 AND is_checked = 1 THEN 1 ELSE 0 END) AS reposts_listened,
              SUM(CASE WHEN {free} THEN 1 ELSE 0 END) AS free_total,
              SUM(CASE WHEN {buy} THEN 1 ELSE 0 END) AS buy_total,
              SUM(CASE WHEN {unknown} THEN 1 ELSE 0 END) AS unknown_total,
              SUM(CASE WHEN is_owned = 1 THEN 1 ELSE 0 END) AS owned
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
    out["free_total"] = r["free_total"] or 0
    out["buy_total"] = r["buy_total"] or 0
    out["unknown_total"] = r["unknown_total"] or 0
    out["listened_pct"] = round(100 * out["listened"] / out["tracks_total"]) if out["tracks_total"] else 0
    out["originals_pct"] = round(100 * out["originals_listened"] / out["originals_total"]) if out["originals_total"] else 0
    out["reposts_pct"] = round(100 * out["reposts_listened"] / out["reposts_total"]) if out["reposts_total"] else 0
    out["revisit_pct"] = round(100 * out["revisit"] / out["tracks_total"]) if out["tracks_total"] else 0
    out["owned"] = r["owned"] or 0
    # "Listened today": tracks marked listened since the start of the LOCAL calendar
    # day (now_iso stores UTC, so convert local midnight to UTC for the comparison).
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = local_midnight.astimezone(timezone.utc).isoformat()
    async with conn.execute(
        f"SELECT COUNT(*) AS n FROM tracks INDEXED BY idx_tracks_checked_at "
        f"WHERE checked_at >= ? AND is_deleted = 0 AND is_checked = 1{nd}{art_sql}",
        [today_start, *art_params],
    ) as cur:
        out["listened_today"] = (await cur.fetchone())["n"]
    async with conn.execute("SELECT MAX(finished_at) AS t FROM scrape_log") as cur:
        out["last_update"] = (await cur.fetchone())["t"]
    return out


# Same fill as migration 018, for rebuilding artist_counts if it ever drifts.
_COUNTS_FILL = """
    INSERT INTO artist_counts (artist_id, tracks_total, originals_total, originals_listened, reposts_total, reposts_listened, new_count, revisit_count, owned_count, listened_count, free_total, buy_total, unknown_total)
    SELECT t.artist_id,
           SUM(CASE WHEN t.is_deleted = 0 AND (1=1) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (t.is_repost = 0) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (t.is_repost = 0 AND t.is_checked = 1) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (t.is_repost = 1) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (t.is_repost = 1 AND t.is_checked = 1) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (t.is_checked = 0 AND t.is_revisit = 0) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (t.is_revisit = 1) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (t.is_owned = 1) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (t.is_checked = 1) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (t.sc_download = 1 OR (COALESCE(t.purchase_url, '') <> '' AND NOT (t.purchase_url LIKE '%bandcamp.com%' OR t.purchase_url LIKE '%beatport.com%'))) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND ((t.purchase_url LIKE '%bandcamp.com%' OR t.purchase_url LIKE '%beatport.com%') AND t.sc_download = 0) THEN 1 ELSE 0 END),
           SUM(CASE WHEN t.is_deleted = 0 AND (COALESCE(t.purchase_url, '') = '' AND t.sc_download = 0) THEN 1 ELSE 0 END)
      FROM tracks t
     GROUP BY t.artist_id
"""


async def check_artist_counts(conn: aiosqlite.Connection) -> bool:
    """Compare artist_counts with the tracks it summarises and rebuild it on any
    difference. The check reads one covering index, so it stays quick at startup.
    Returns True when a rebuild was needed."""
    async with conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(is_owned), 0) AS owned, COALESCE(SUM(is_checked), 0) AS heard "
        "FROM tracks INDEXED BY idx_tracks_state WHERE is_deleted = 0"
    ) as cur:
        real = tuple(await cur.fetchone())
    async with conn.execute(
        "SELECT COALESCE(SUM(tracks_total), 0), COALESCE(SUM(owned_count), 0), "
        "COALESCE(SUM(listened_count), 0) FROM artist_counts"
    ) as cur:
        stored = tuple(await cur.fetchone())
    if real == stored:
        return False
    await conn.execute("DELETE FROM artist_counts")
    await conn.execute(_COUNTS_FILL)
    await conn.commit()
    print(f"[db] artist_counts rebuilt (stored {stored}, actual {real})")
    return True


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
    for table in _INSERT_ORDER:
        async with conn.execute(f"SELECT * FROM {table}") as cur:
            out[table] = _rows(await cur.fetchall())
    return out


# Child tables before their parents (FK-safe delete order).
_DELETE_ORDER = ("owned_check_items", "owned_checks", "owned_rejections", "run_results",
                 "scrape_log", "import_sessions", "tracks", "artists", "skipped_artists", "settings")
# Parents before children (FK-safe insert order). file_tags is left out: it's a cache
# of this machine's paths and rebuilds on the next folder check.
_INSERT_ORDER = ("artists", "tracks", "import_sessions", "scrape_log", "run_results",
                 "skipped_artists", "owned_rejections", "owned_checks", "owned_check_items",
                 "settings")


async def _table_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        return {r["name"] for r in await cur.fetchall()}


async def import_all(conn: aiosqlite.Connection, data: dict) -> dict:
    """Restore from a JSON export: replace every present table's rows wholesale.

    All or nothing, so a bad file leaves the library untouched. Columns this
    version doesn't have are dropped, so a newer version's backup still loads.
    Pass a connection nobody else writes on, or their commit lands mid-restore.
    """
    if not isinstance(data, dict):
        raise ValueError("not a backup file")
    plan: dict[str, list[dict]] = {}
    for table in _INSERT_ORDER:
        rows = data.get(table)
        if rows is None:
            continue
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError(f"'{table}' is not a list of rows")
        known = await _table_columns(conn, table)
        plan[table] = [{k: v for k, v in r.items() if k in known} for r in rows]
    if not plan:
        raise ValueError("no library data in this file")

    await conn.execute("BEGIN IMMEDIATE")
    try:
        for table in _DELETE_ORDER:
            if table in plan:
                await conn.execute(f"DELETE FROM {table}")
        for table in _INSERT_ORDER:
            for row in plan.get(table, []):
                if not row:
                    continue
                cols = list(row)
                await conn.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [row[c] for c in cols],
                )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    return {t: len(rows) for t, rows in plan.items()}


async def reset_all(conn: aiosqlite.Connection) -> None:
    """Wipe all user data. Settings are cleared and re-seeded by the caller."""
    for table in ("run_results", "owned_rejections", *_DELETE_ORDER):
        await conn.execute(f"DELETE FROM {table}")
    await conn.commit()
