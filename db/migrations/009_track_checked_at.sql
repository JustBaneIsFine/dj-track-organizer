-- Timestamp of when a track was marked listened (is_checked -> 1), so we can show
-- a "listened today" counter. NULL = not currently listened / pre-existing rows.
ALTER TABLE tracks ADD COLUMN checked_at TEXT;
