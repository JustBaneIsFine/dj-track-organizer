-- Migration 018: stored per-artist counts and an artist-name copy on tracks.
--
-- artist_counts holds what the sidebar, chip counts and stats used to count over
-- every track on each load. Triggers on tracks keep it current. The link
-- conditions mirror LINK_SQL in db/queries.py; change both together.
-- db.queries.check_artist_counts rebuilds the table at startup if it drifts.
-- No foreign key, so tracks left without an artist can't fail the fill.
--
-- tracks.artist_name copies the artist's name so the Artist sort can read a page
-- off an index instead of sorting the whole library.

CREATE TABLE IF NOT EXISTS artist_counts (
    artist_id INTEGER PRIMARY KEY,
    tracks_total INTEGER NOT NULL DEFAULT 0,
    originals_total INTEGER NOT NULL DEFAULT 0,
    originals_listened INTEGER NOT NULL DEFAULT 0,
    reposts_total INTEGER NOT NULL DEFAULT 0,
    reposts_listened INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    revisit_count INTEGER NOT NULL DEFAULT 0,
    owned_count INTEGER NOT NULL DEFAULT 0,
    listened_count INTEGER NOT NULL DEFAULT 0,
    free_total INTEGER NOT NULL DEFAULT 0,
    buy_total INTEGER NOT NULL DEFAULT 0,
    unknown_total INTEGER NOT NULL DEFAULT 0
);

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
 GROUP BY t.artist_id;

CREATE TRIGGER IF NOT EXISTS trg_counts_artist_gone
AFTER DELETE ON artists
BEGIN
    DELETE FROM artist_counts WHERE artist_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_counts_insert
AFTER INSERT ON tracks
BEGIN
    INSERT OR IGNORE INTO artist_counts (artist_id) VALUES (NEW.artist_id);
    UPDATE artist_counts SET
        tracks_total = tracks_total + CASE WHEN NEW.is_deleted = 0 AND (1=1) THEN 1 ELSE 0 END,
        originals_total = originals_total + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_repost = 0) THEN 1 ELSE 0 END,
        originals_listened = originals_listened + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_repost = 0 AND NEW.is_checked = 1) THEN 1 ELSE 0 END,
        reposts_total = reposts_total + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_repost = 1) THEN 1 ELSE 0 END,
        reposts_listened = reposts_listened + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_repost = 1 AND NEW.is_checked = 1) THEN 1 ELSE 0 END,
        new_count = new_count + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_checked = 0 AND NEW.is_revisit = 0) THEN 1 ELSE 0 END,
        revisit_count = revisit_count + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_revisit = 1) THEN 1 ELSE 0 END,
        owned_count = owned_count + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_owned = 1) THEN 1 ELSE 0 END,
        listened_count = listened_count + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_checked = 1) THEN 1 ELSE 0 END,
        free_total = free_total + CASE WHEN NEW.is_deleted = 0 AND (NEW.sc_download = 1 OR (COALESCE(NEW.purchase_url, '') <> '' AND NOT (NEW.purchase_url LIKE '%bandcamp.com%' OR NEW.purchase_url LIKE '%beatport.com%'))) THEN 1 ELSE 0 END,
        buy_total = buy_total + CASE WHEN NEW.is_deleted = 0 AND ((NEW.purchase_url LIKE '%bandcamp.com%' OR NEW.purchase_url LIKE '%beatport.com%') AND NEW.sc_download = 0) THEN 1 ELSE 0 END,
        unknown_total = unknown_total + CASE WHEN NEW.is_deleted = 0 AND (COALESCE(NEW.purchase_url, '') = '' AND NEW.sc_download = 0) THEN 1 ELSE 0 END
     WHERE artist_id = NEW.artist_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_counts_delete
AFTER DELETE ON tracks
BEGIN
    UPDATE artist_counts SET
        tracks_total = tracks_total - CASE WHEN OLD.is_deleted = 0 AND (1=1) THEN 1 ELSE 0 END,
        originals_total = originals_total - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_repost = 0) THEN 1 ELSE 0 END,
        originals_listened = originals_listened - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_repost = 0 AND OLD.is_checked = 1) THEN 1 ELSE 0 END,
        reposts_total = reposts_total - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_repost = 1) THEN 1 ELSE 0 END,
        reposts_listened = reposts_listened - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_repost = 1 AND OLD.is_checked = 1) THEN 1 ELSE 0 END,
        new_count = new_count - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_checked = 0 AND OLD.is_revisit = 0) THEN 1 ELSE 0 END,
        revisit_count = revisit_count - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_revisit = 1) THEN 1 ELSE 0 END,
        owned_count = owned_count - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_owned = 1) THEN 1 ELSE 0 END,
        listened_count = listened_count - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_checked = 1) THEN 1 ELSE 0 END,
        free_total = free_total - CASE WHEN OLD.is_deleted = 0 AND (OLD.sc_download = 1 OR (COALESCE(OLD.purchase_url, '') <> '' AND NOT (OLD.purchase_url LIKE '%bandcamp.com%' OR OLD.purchase_url LIKE '%beatport.com%'))) THEN 1 ELSE 0 END,
        buy_total = buy_total - CASE WHEN OLD.is_deleted = 0 AND ((OLD.purchase_url LIKE '%bandcamp.com%' OR OLD.purchase_url LIKE '%beatport.com%') AND OLD.sc_download = 0) THEN 1 ELSE 0 END,
        unknown_total = unknown_total - CASE WHEN OLD.is_deleted = 0 AND (COALESCE(OLD.purchase_url, '') = '' AND OLD.sc_download = 0) THEN 1 ELSE 0 END
     WHERE artist_id = OLD.artist_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_counts_update
AFTER UPDATE OF artist_id, is_deleted, is_checked, is_revisit, is_owned, is_repost, purchase_url, sc_download ON tracks
BEGIN
    UPDATE artist_counts SET
        tracks_total = tracks_total - CASE WHEN OLD.is_deleted = 0 AND (1=1) THEN 1 ELSE 0 END,
        originals_total = originals_total - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_repost = 0) THEN 1 ELSE 0 END,
        originals_listened = originals_listened - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_repost = 0 AND OLD.is_checked = 1) THEN 1 ELSE 0 END,
        reposts_total = reposts_total - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_repost = 1) THEN 1 ELSE 0 END,
        reposts_listened = reposts_listened - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_repost = 1 AND OLD.is_checked = 1) THEN 1 ELSE 0 END,
        new_count = new_count - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_checked = 0 AND OLD.is_revisit = 0) THEN 1 ELSE 0 END,
        revisit_count = revisit_count - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_revisit = 1) THEN 1 ELSE 0 END,
        owned_count = owned_count - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_owned = 1) THEN 1 ELSE 0 END,
        listened_count = listened_count - CASE WHEN OLD.is_deleted = 0 AND (OLD.is_checked = 1) THEN 1 ELSE 0 END,
        free_total = free_total - CASE WHEN OLD.is_deleted = 0 AND (OLD.sc_download = 1 OR (COALESCE(OLD.purchase_url, '') <> '' AND NOT (OLD.purchase_url LIKE '%bandcamp.com%' OR OLD.purchase_url LIKE '%beatport.com%'))) THEN 1 ELSE 0 END,
        buy_total = buy_total - CASE WHEN OLD.is_deleted = 0 AND ((OLD.purchase_url LIKE '%bandcamp.com%' OR OLD.purchase_url LIKE '%beatport.com%') AND OLD.sc_download = 0) THEN 1 ELSE 0 END,
        unknown_total = unknown_total - CASE WHEN OLD.is_deleted = 0 AND (COALESCE(OLD.purchase_url, '') = '' AND OLD.sc_download = 0) THEN 1 ELSE 0 END
     WHERE artist_id = OLD.artist_id;
    INSERT OR IGNORE INTO artist_counts (artist_id) VALUES (NEW.artist_id);
    UPDATE artist_counts SET
        tracks_total = tracks_total + CASE WHEN NEW.is_deleted = 0 AND (1=1) THEN 1 ELSE 0 END,
        originals_total = originals_total + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_repost = 0) THEN 1 ELSE 0 END,
        originals_listened = originals_listened + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_repost = 0 AND NEW.is_checked = 1) THEN 1 ELSE 0 END,
        reposts_total = reposts_total + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_repost = 1) THEN 1 ELSE 0 END,
        reposts_listened = reposts_listened + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_repost = 1 AND NEW.is_checked = 1) THEN 1 ELSE 0 END,
        new_count = new_count + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_checked = 0 AND NEW.is_revisit = 0) THEN 1 ELSE 0 END,
        revisit_count = revisit_count + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_revisit = 1) THEN 1 ELSE 0 END,
        owned_count = owned_count + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_owned = 1) THEN 1 ELSE 0 END,
        listened_count = listened_count + CASE WHEN NEW.is_deleted = 0 AND (NEW.is_checked = 1) THEN 1 ELSE 0 END,
        free_total = free_total + CASE WHEN NEW.is_deleted = 0 AND (NEW.sc_download = 1 OR (COALESCE(NEW.purchase_url, '') <> '' AND NOT (NEW.purchase_url LIKE '%bandcamp.com%' OR NEW.purchase_url LIKE '%beatport.com%'))) THEN 1 ELSE 0 END,
        buy_total = buy_total + CASE WHEN NEW.is_deleted = 0 AND ((NEW.purchase_url LIKE '%bandcamp.com%' OR NEW.purchase_url LIKE '%beatport.com%') AND NEW.sc_download = 0) THEN 1 ELSE 0 END,
        unknown_total = unknown_total + CASE WHEN NEW.is_deleted = 0 AND (COALESCE(NEW.purchase_url, '') = '' AND NEW.sc_download = 0) THEN 1 ELSE 0 END
     WHERE artist_id = NEW.artist_id;
END;

-- "Listened today" in stats.
CREATE INDEX IF NOT EXISTS idx_tracks_checked_at ON tracks(checked_at);

ALTER TABLE tracks ADD COLUMN artist_name TEXT NOT NULL DEFAULT '';

UPDATE tracks SET artist_name =
    COALESCE((SELECT a.name FROM artists a WHERE a.id = tracks.artist_id), '');

CREATE TRIGGER IF NOT EXISTS trg_tracks_name_insert
AFTER INSERT ON tracks
BEGIN
    UPDATE tracks SET artist_name =
        COALESCE((SELECT name FROM artists WHERE id = NEW.artist_id), '')
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_tracks_name_move
AFTER UPDATE OF artist_id ON tracks
BEGIN
    UPDATE tracks SET artist_name =
        COALESCE((SELECT name FROM artists WHERE id = NEW.artist_id), '')
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_artists_name
AFTER UPDATE OF name ON artists
WHEN NEW.name IS NOT OLD.name
BEGIN
    UPDATE tracks SET artist_name = COALESCE(NEW.name, '') WHERE artist_id = NEW.id;
END;

-- Artist sort, A-Z and Z-A.
CREATE INDEX IF NOT EXISTS idx_tracks_artist_az
    ON tracks(artist_name COLLATE NOCASE, name COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS idx_tracks_artist_za
    ON tracks(artist_name COLLATE NOCASE DESC, name COLLATE NOCASE ASC, id ASC);
