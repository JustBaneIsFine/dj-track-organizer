-- Migration 005: cross-artist repost merge.
-- The same song reposted by several followed artists is stored as multiple
-- tracks rows (same underlying SoundCloud permalink, different artist_id; the
-- UNIQUE(artist_id, url) constraint is per-artist). `group_key` is the
-- normalized canonical track URL, so those duplicates can collapse to a single
-- row in the unfiltered view. Computed in Python (queries.canonical_url_key) on
-- insert; existing rows are backfilled on startup (queries.backfill_group_keys).

ALTER TABLE tracks ADD COLUMN group_key TEXT;

CREATE INDEX IF NOT EXISTS idx_tracks_group_key ON tracks(group_key);
