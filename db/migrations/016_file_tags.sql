-- Tags read during folder checks, so a recheck only opens new or changed files.
CREATE TABLE IF NOT EXISTS file_tags (
    path         TEXT PRIMARY KEY,
    mtime_ns     INTEGER NOT NULL,
    size         INTEGER NOT NULL,
    artist       TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    album_artist TEXT NOT NULL DEFAULT ''
);
