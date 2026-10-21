-- Migration 017: copy the artist's priority onto each track so the default sort
-- can walk an index and stop after one page instead of sorting the whole library.
-- Triggers keep the copy in sync; nothing in the app writes it directly.

ALTER TABLE tracks ADD COLUMN artist_priority INTEGER NOT NULL DEFAULT 0;

UPDATE tracks SET artist_priority =
    COALESCE((SELECT a.priority FROM artists a WHERE a.id = tracks.artist_id), 0);

CREATE TRIGGER IF NOT EXISTS trg_tracks_priority_insert
AFTER INSERT ON tracks
BEGIN
    UPDATE tracks SET artist_priority =
        COALESCE((SELECT priority FROM artists WHERE id = NEW.artist_id), 0)
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_tracks_priority_move
AFTER UPDATE OF artist_id ON tracks
BEGIN
    UPDATE tracks SET artist_priority =
        COALESCE((SELECT priority FROM artists WHERE id = NEW.artist_id), 0)
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_artists_priority
AFTER UPDATE OF priority ON artists
WHEN NEW.priority IS NOT OLD.priority
BEGIN
    UPDATE tracks SET artist_priority = COALESCE(NEW.priority, 0) WHERE artist_id = NEW.id;
END;

-- Priority sort, arrow down (newest first) walks this backwards.
CREATE INDEX IF NOT EXISTS idx_tracks_prio_added
    ON tracks(artist_priority, added_at, id);
-- Priority sort, arrow up (oldest first within each level).
CREATE INDEX IF NOT EXISTS idx_tracks_prio_added_up
    ON tracks(artist_priority DESC, added_at ASC, id ASC);
-- Date added sort, either direction.
CREATE INDEX IF NOT EXISTS idx_tracks_added
    ON tracks(added_at, id);
