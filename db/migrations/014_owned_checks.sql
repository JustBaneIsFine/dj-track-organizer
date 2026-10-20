-- Saved folder checks: a scan's matches are kept and decided one by one, so a
-- review can be paused and picked up later.
CREATE TABLE IF NOT EXISTS owned_checks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    root          TEXT NOT NULL,                 -- folder path, or just its name (browser picker)
    artist_id     INTEGER,                       -- set for a per-artist check
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'done'
    file_count    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS owned_check_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id   INTEGER NOT NULL REFERENCES owned_checks(id) ON DELETE CASCADE,
    track_id   INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    path       TEXT,                             -- absolute path when known, for playing
    score      INTEGER NOT NULL,
    kind       TEXT NOT NULL,                    -- 'exact' | 'title' | 'fuzzy'
    state      TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'skipped' | 'same' | 'different'
    action     TEXT,                             -- what 'same' did to the track ('owned')
    prev       TEXT,                             -- JSON snapshot of the track flags, for undo
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_check_items_check ON owned_check_items(check_id, state);

-- Checks now keep matches down to 85%; the review's slider decides what shows.
UPDATE settings SET value = '85' WHERE key = 'owned_match_floor' AND value = '90';
UPDATE settings SET value = '85' WHERE key = 'owned_match_floor_artist' AND value = '88';
