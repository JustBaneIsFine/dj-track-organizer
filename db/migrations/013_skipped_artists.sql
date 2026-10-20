-- Artists skipped during an import review, left out of later imports.
CREATE TABLE IF NOT EXISTS skipped_artists (
    url        TEXT PRIMARY KEY,
    name       TEXT,
    skipped_at TEXT NOT NULL
);
