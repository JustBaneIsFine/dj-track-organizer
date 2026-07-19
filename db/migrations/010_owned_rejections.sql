-- Remembers (track, filename) pairs the user said are NOT the same song in the
-- check-folder review, so that exact pair is never offered as a match again.
CREATE TABLE IF NOT EXISTS owned_rejections (
    track_id   INTEGER NOT NULL,
    filename   TEXT    NOT NULL,
    created_at TEXT,
    PRIMARY KEY (track_id, filename),
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
);
