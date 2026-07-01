-- Migration 001: initial schema
-- One .sql file per schema version. The runner in db/schema.py applies any file
-- whose numeric prefix is greater than the current db_version, in filename order.

CREATE TABLE IF NOT EXISTS artists (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    url              TEXT NOT NULL UNIQUE,
    platform         TEXT DEFAULT 'soundcloud',
    priority         INTEGER DEFAULT 0,        -- 0 = none, 1-4 stars
    is_active        INTEGER DEFAULT 1,        -- 0 = skip during updates
    is_deleted       INTEGER DEFAULT 0,        -- soft delete: hidden but preserved
    deleted_at       TEXT,                     -- ISO8601, NULL if not deleted
    added_at         TEXT NOT NULL,            -- ISO8601
    last_scraped     TEXT,                     -- ISO8601, NULL if never scraped
    track_count      INTEGER DEFAULT 0,        -- cached count, refreshed after each scrape
    include_reposts  INTEGER DEFAULT -1,       -- -1 = inherit global; 0 = off; 1 = on
    repost_limit     INTEGER DEFAULT -1,       -- -1 = use global default; 0 = no limit; N = cap
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id     INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    url           TEXT,
    is_checked    INTEGER DEFAULT 0,           -- 1 = listened/marked by user
    is_deleted    INTEGER DEFAULT 0,           -- soft delete: hidden but preserved
    deleted_at    TEXT,                        -- ISO8601, NULL if not deleted
    is_repost     INTEGER DEFAULT 0,           -- 1 = this track was reposted by the artist
    added_at      TEXT NOT NULL,               -- ISO8601: when first scraped
    last_seen     TEXT NOT NULL,               -- ISO8601: last scrape it appeared in
    notes         TEXT,
    UNIQUE(artist_id, url)                     -- dedup by URL
);

CREATE INDEX IF NOT EXISTS idx_tracks_artist     ON tracks(artist_id);
CREATE INDEX IF NOT EXISTS idx_tracks_checked    ON tracks(is_checked);
CREATE INDEX IF NOT EXISTS idx_tracks_deleted    ON tracks(is_deleted);
CREATE INDEX IF NOT EXISTS idx_artists_deleted   ON artists(is_deleted);

CREATE TABLE IF NOT EXISTS import_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    mode            TEXT NOT NULL,              -- 'interactive' | 'batch'
    status          TEXT DEFAULT 'paused',      -- 'in_progress' | 'paused' | 'complete' | 'abandoned'
    source_url      TEXT,
    total_artists   INTEGER DEFAULT 0,
    processed_count INTEGER DEFAULT 0,
    skipped_count   INTEGER DEFAULT 0,
    artist_queue    TEXT NOT NULL,              -- JSON: [{url, name, status, artist_id|null}]
    current_index   INTEGER DEFAULT 0,
    tracks_added    INTEGER DEFAULT 0,
    session_notes   TEXT
);

CREATE TABLE IF NOT EXISTS scrape_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER REFERENCES import_sessions(id),
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    mode            TEXT NOT NULL,              -- 'full' | 'priority' | 'selected' | 'import' | 'single'
    artist_ids      TEXT,                       -- JSON array
    tracks_added    INTEGER DEFAULT 0,
    tracks_removed  INTEGER DEFAULT 0,
    errors          TEXT                        -- JSON array of {artist, message}
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
