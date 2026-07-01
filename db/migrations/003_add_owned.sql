-- Migration 003: "tracks I already own" support.
-- Set when a track is matched (by filename) to a file in the user's music folder.
-- We only ever read file NAMES; nothing here touches the user's files.

ALTER TABLE tracks ADD COLUMN is_owned INTEGER DEFAULT 0;  -- 1 = user already has it
ALTER TABLE tracks ADD COLUMN owned_at TEXT;               -- ISO8601, NULL if not owned

CREATE INDEX IF NOT EXISTS idx_tracks_owned ON tracks(is_owned);
