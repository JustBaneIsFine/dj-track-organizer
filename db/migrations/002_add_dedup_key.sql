-- Migration 002: cross-platform readiness.
-- A normalized "artist - title" key so that, once we add more platforms
-- (Bandcamp/YouTube/Spotify), the same track found on several platforms can be
-- grouped. Unused for matching yet; populated on insert in db/queries.py.

ALTER TABLE tracks ADD COLUMN dedup_key TEXT;

CREATE INDEX IF NOT EXISTS idx_tracks_dedup_key ON tracks(dedup_key);
