-- Migration 006: "Revisit" track state.
-- A third, mutually-exclusive review state alongside new/listened: a track the
-- user has heard but wants to come back to (to buy/download later). Marking
-- revisit removes a track from "new" without marking it listened; it surfaces in
-- its own Revisit list. Exclusivity (revisit vs listened) is enforced in queries.

ALTER TABLE tracks ADD COLUMN is_revisit INTEGER DEFAULT 0;
ALTER TABLE tracks ADD COLUMN revisit_at TEXT;

CREATE INDEX IF NOT EXISTS idx_tracks_revisit ON tracks(is_revisit);
