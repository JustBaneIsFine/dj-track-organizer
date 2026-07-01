-- Migration 007: composite indexes for scale (10k+ tracks).
-- Pure performance — no schema/behavior change. Single-column indexes only let
-- SQLite use one column; the hot paths filter on several at once, so it had to
-- scan-then-filter. These composites match the real query shapes.

-- Per-artist sidebar counts and artist-scoped track lists always lead with
-- artist_id + is_deleted, then split by listened/repost. This prefix serves the
-- sidebar count subqueries and the "tracks for this artist" filtered list.
-- (Supersedes the old single-column idx_tracks_artist — artist_id is its prefix.)
CREATE INDEX IF NOT EXISTS idx_tracks_artist_state
    ON tracks(artist_id, is_deleted, is_checked, is_repost);

-- Default unfiltered/global list + global stats: is_deleted=0 AND is_owned=0,
-- then bucketed by listened.
CREATE INDEX IF NOT EXISTS idx_tracks_state
    ON tracks(is_deleted, is_owned, is_checked);

-- Cross-artist repost merge groups on group_key; keep it indexed alongside the
-- delete flag the merge query also filters on.
CREATE INDEX IF NOT EXISTS idx_tracks_group_state
    ON tracks(group_key, is_deleted);

-- Old single-column idx_tracks_artist is now redundant (left prefix of
-- idx_tracks_artist_state); drop it to save write cost and space.
DROP INDEX IF EXISTS idx_tracks_artist;
